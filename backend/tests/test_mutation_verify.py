"""The mutation inventory must be well-formed before a manual run finds out.

``scripts/mutation_verify.py`` only checks a needle when someone runs it, and a
full ``verify`` over every set takes minutes. Two failure modes it therefore
reports too late:

* a needle that no longer occurs in its file (the code it pointed at moved), and
* a needle that occurs more than once -- ``apply`` refuses it, but only after the
  run has already started.

Both are static properties of the inventory, so they are asserted here instead.
The line-ending rule is pinned for the same reason: this repo commits LF and
checks out CRLF, so a needle containing a line break must carry CRLF or it can
never match (and the failure looks like "needle appears 0x", not like a bug in
the script).
"""
import unittest
from pathlib import Path

from scripts import mutation_verify

REPO_ROOT = Path(mutation_verify.REPO_ROOT)


def _every_mutation():
    for mutation_set in mutation_verify.SETS:
        for index, mutation in enumerate(mutation_set.mutations, 1):
            yield mutation_set.key, index, mutation


class InventoryTests(unittest.TestCase):
    def test_the_four_sets_are_present(self):
        """Pins the inventory, so a whole set cannot be dropped silently.

        Deliberately exact and ordered: adding a set means editing this test,
        which is the point -- an inventory nobody has to touch is an inventory
        that can rot.
        """
        self.assertEqual(
            [s.key for s in mutation_verify.SETS],
            ["daily-digest", "review-queue", "probability-probe", "voided-trade"],
        )

    def test_set_keys_are_unique(self):
        keys = [s.key for s in mutation_verify.SETS]
        self.assertEqual(len(keys), len(set(keys)), keys)

    def test_every_needle_occurs_exactly_once_in_its_file(self):
        offenders = []
        for key, index, mutation in _every_mutation():
            path = REPO_ROOT / mutation.path
            if not path.is_file():
                offenders.append(f"{key} {index}: missing file {mutation.path}")
                continue
            found = path.read_bytes().count(mutation.old)
            if found != 1:
                offenders.append(
                    f"{key} {index}: needle occurs {found}x in {mutation.path}"
                )
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_no_needle_carries_a_bare_lf(self):
        offenders = [
            f"{key} {index}: {mutation.label}"
            for key, index, mutation in _every_mutation()
            if mutation.old.count(b"\n") != mutation.old.count(b"\r\n")
        ]
        self.assertEqual(
            offenders,
            [],
            "a needle with a bare LF can never match a CRLF checkout:\n"
            + "\n".join(offenders),
        )

    def test_every_guard_file_exists_and_every_guard_is_named(self):
        offenders = []
        for key, index, mutation in _every_mutation():
            if not (mutation_verify.BACKEND / mutation.guard_file).is_file():
                offenders.append(f"{key} {index}: missing {mutation.guard_file}")
            if not mutation.guards:
                offenders.append(f"{key} {index}: no guards selected")
            for guard in mutation.guards:
                if not guard.startswith("test_"):
                    offenders.append(f"{key} {index}: {guard!r} is not a test name")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_grouped_mutations_rewrite_the_same_bytes(self):
        """``apply <set>`` skips later members of a group because applying both
        would fail the second with "expected 1 occurrence, found 0" -- which only
        holds while a group's members share one path and one needle."""
        grouped: dict[tuple[str, str], list] = {}
        for key, _index, mutation in _every_mutation():
            if mutation.group:
                grouped.setdefault((key, mutation.group), []).append(mutation)
        self.assertTrue(grouped, "no group is declared, so this test proves nothing")
        for (key, group), members in grouped.items():
            with self.subTest(set=key, group=group):
                self.assertEqual({m.path for m in members}, {members[0].path})
                self.assertEqual({m.old for m in members}, {members[0].old})

    def test_every_mutation_actually_changes_something(self):
        offenders = [
            f"{key} {index}: old == new"
            for key, index, mutation in _every_mutation()
            if mutation.old == mutation.new
        ]
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_every_mutation_carries_a_note(self):
        offenders = [
            f"{key} {index}: {mutation.label}"
            for key, index, mutation in _every_mutation()
            if not mutation.note.strip()
        ]
        self.assertEqual(offenders, [], "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
