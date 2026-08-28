"""The store classification must be an exact partition of the path settings.

`app/core/runtime_stores.py` exists because two hand-maintained lists decided what
disaster recovery covered, and both had fallen four stores behind. Declaring the
classification as data only helps if something asserts it stays complete — so
these tests compare it against a scan of `Settings` in **both** directions:

* a path setting nobody classified fails the test, naming the setting;
* a classified row whose setting no longer exists fails as a phantom.

Without the second direction a renamed setting leaves a row that looks like
coverage. Without the first, the next `*_FILE` added to the config repeats the
original defect.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

from app.core import runtime_stores
from app.core.config import settings

_CONFIG_PATH = Path(runtime_stores.__file__).resolve().parent / "config.py"


def _path_settings_from_source() -> set[str]:
    """Annotated `Settings` attributes whose names look like paths, via AST.

    Read from source rather than `dir()` so the population cannot be changed by
    anything a test did to the live object. `utf-8-sig` because at least one
    module in this tree carries a BOM that `ast.parse` rejects.
    """
    tree = ast.parse(_CONFIG_PATH.read_text(encoding="utf-8-sig"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != "Settings":
            continue
        for stmt in node.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                name = stmt.target.id
                if name.endswith(("_FILE", "_DIR", "_PATH")):
                    names.add(name)
    return names


class StoreClassificationPartitionTests(unittest.TestCase):
    def test_the_scan_finds_something(self):
        """Guard the denominator.

        Every assertion below compares two sets. If the scan silently returned
        nothing — a moved `config.py`, a renamed class, a parse error swallowed
        somewhere — then "unclassified == empty" holds trivially and this whole
        file goes permanently green while covering nothing.
        """
        scanned = _path_settings_from_source()
        self.assertGreaterEqual(
            len(scanned), 10,
            f"the AST scan of {_CONFIG_PATH.name} found only {len(scanned)} path "
            f"settings, which is too few to be real; the partition assertions "
            f"below would pass vacuously",
        )
        self.assertIn("LOOP_DB_FILE", scanned)
        self.assertIn("KERNEL_DB_FILE", scanned)

    def test_every_path_setting_is_classified(self):
        """A new store must be classified before it can ship.

        This is the assertion the original defect needed: `KERNEL_DB_FILE` became
        a setting and nothing said it was missing from the backup.
        """
        unclassified = _path_settings_from_source() - runtime_stores.classified_setting_names()
        self.assertEqual(
            unclassified,
            set(),
            f"{sorted(unclassified)} are path settings that no category in "
            f"app/core/runtime_stores.py claims. Add each to STATE_STORES (it "
            f"holds data a restore must bring back), DERIVED_STORES (re-fetchable) "
            f"or EPHEMERAL_STORES (restoring it would be wrong), with a reason.",
        )

    def test_no_classified_row_names_a_setting_that_is_gone(self):
        """The reverse direction: a row that rotted after a rename."""
        phantom = runtime_stores.classified_setting_names() - _path_settings_from_source()
        self.assertEqual(
            phantom,
            set(),
            f"{sorted(phantom)} are classified in app/core/runtime_stores.py but "
            f"are not path settings on Settings. A row naming a setting that no "
            f"longer exists looks like coverage and provides none.",
        )

    def test_the_runtime_view_agrees_with_the_source_scan(self):
        """`path_setting_names()` drives production; the AST drives the test.

        If they disagree, one of them is wrong and the partition above is proving
        something about the wrong population.
        """
        self.assertEqual(runtime_stores.path_setting_names(), _path_settings_from_source())

    def test_the_categories_are_disjoint(self):
        """A partition, not just a cover — a store in two categories has no answer."""
        cats = {
            "STATE": set(runtime_stores.STATE_STORES),
            "DERIVED": set(runtime_stores.DERIVED_STORES),
            "EPHEMERAL": set(runtime_stores.EPHEMERAL_STORES),
        }
        for left, right in (("STATE", "DERIVED"), ("STATE", "EPHEMERAL"), ("DERIVED", "EPHEMERAL")):
            with self.subTest(pair=f"{left}/{right}"):
                self.assertEqual(
                    cats[left] & cats[right], set(),
                    f"a setting is in both {left} and {right}",
                )

    def test_every_row_carries_a_reason(self):
        """The reason is the point: it is why the next reader trusts the row."""
        for label, table in (
            ("STATE_STORES", runtime_stores.STATE_STORES),
            ("DERIVED_STORES", runtime_stores.DERIVED_STORES),
            ("EPHEMERAL_STORES", runtime_stores.EPHEMERAL_STORES),
        ):
            for name, reason in table.items():
                with self.subTest(table=label, setting=name):
                    self.assertGreaterEqual(
                        len(reason.strip()), 15,
                        f"{label}[{name!r}] needs a reason a reader can act on",
                    )


class StatePathsTests(unittest.TestCase):
    def test_the_four_stores_the_backup_used_to_miss_are_state(self):
        """Pin the specific regression, by name.

        The partition test above would stay green if someone reclassified these
        as DERIVED — that is a legal partition and a silent return of the defect.
        Each of these held live data with no re-derivation path.
        """
        for name in (
            "KERNEL_DB_FILE",
            "WORLD_CUP_PREDICTION_DB_FILE",
            "DOMAIN_RELIABILITY_DB_PATH",
            "SPORTS_FACT_FILE",
        ):
            with self.subTest(setting=name):
                self.assertIn(name, runtime_stores.STATE_STORES)

    def test_the_scheduler_lock_is_never_backed_up(self):
        """Restoring a lock file advertises a scheduler that is not running."""
        self.assertIn("SCHEDULER_LOCK_FILE", runtime_stores.EPHEMERAL_STORES)
        self.assertNotIn("SCHEDULER_LOCK_FILE", runtime_stores.STATE_STORES)
        names = {p.name for p in runtime_stores.state_paths()}
        self.assertNotIn(Path(settings.SCHEDULER_LOCK_FILE).name, names)

    def test_state_paths_covers_every_state_setting(self):
        """No state store may drop out between the table and the path list."""
        self.assertEqual(
            len(runtime_stores.state_paths()),
            len(runtime_stores.STATE_STORES),
            "a STATE_STORES row produced no path; an empty setting value would "
            "silently remove it from every backup",
        )

    def test_sidecars_only_for_sqlite(self):
        self.assertEqual(runtime_stores.sidecar_paths(Path("/x/event_store.json")), [])
        self.assertEqual(
            [p.name for p in runtime_stores.sidecar_paths(Path("/x/v2_loop.db"))],
            ["v2_loop.db-wal", "v2_loop.db-shm"],
        )

    def test_more_than_one_sqlite_state_store_exists(self):
        """Otherwise "sidecars for every SQLite store" is untested by construction."""
        dbs = [p for p in runtime_stores.state_paths() if p.suffix == ".db"]
        self.assertGreaterEqual(len(dbs), 4, f"only {len(dbs)} SQLite state stores")
