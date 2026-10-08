"""Tests for the TypeScript type generation script.

Verifies:
- generate_types.py main() produces generated-types.ts containing every model
  listed in the allowlist (read from ``_frontend_export.__all__``).
- --check mode exits 0 when generated-types.ts is up to date.
- --check mode exits 1 when generated-types.ts is stale.
- Output file contains a header comment.
- Output is deterministic (running twice produces identical files).
- The closed overlay interfaces render without a ``[k: string]: unknown`` tail,
  and exactly the documented models stay open (audit sections 53.49-53.51).
- ``api.ts`` re-declares no overlay of its own and reads the generated, closed
  interfaces for the three quality overlays (audit section 53.52).
- ``FreshEdgesResponse`` names its list element, that element keeps the
  required/optional split its producers imply, and both ``/events/edges/fresh``
  call sites read the published envelope (audit section 53.54).
- ``EventMoversResponse`` does the same for ``GET /events/movers``, and the
  ``/movers`` route keeps the ``response_model_exclude_unset`` flag that keeps
  its serialized bytes identical (audit section 53.55).
- ``SimilarEventsResponse`` does the same for ``GET /events/{id}/similar``, where
  no such flag is needed because every element key is written unconditionally
  (audit section 53.56).
"""
from __future__ import annotations

import subprocess
import sys
import typing
import unittest
from pathlib import Path


_BACKEND_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND_DIR.parent
_OUTPUT_PATH = _REPO_ROOT / "frontend" / "src" / "lib" / "generated-types.ts"
_API_TS_PATH = _REPO_ROOT / "frontend" / "src" / "lib" / "api.ts"
_TYPES_TS_PATH = _REPO_ROOT / "frontend" / "src" / "lib" / "types.ts"
_EVENTS_ROUTE_PATH = _REPO_ROOT / "backend" / "app" / "api" / "routes" / "events.py"

# Models whose own config opts into ``extra="allow"`` on purpose, so the
# generator deliberately skips the ``extra="forbid"`` override and the emitted
# interface keeps its ``[k: string]: unknown`` tail. Adding a name here is a
# contract decision, not a cleanup: it says "producers really do write keys this
# model does not declare". ``FlexibleResponse`` ships that on purpose too and its
# subclasses inherit it, so the family is excluded by subclass rather than by
# name -- listing sixteen of them here would drift.
DELIBERATELY_OPEN = frozenset({"EventRecord", "EventSource"})

# ``api.ts`` is hand-maintained -- no generator writes it -- so nothing stops a
# local copy of a shape that ``generated-types.ts`` already emits. Since audit
# 53.52 the three quality overlays are read from the generated, closed
# interfaces instead, and this set says which local copies are allowed back:
# none. The copies it replaced ended in ``[key: string]: unknown``, so
# ``DecisionReport`` accepted keys the backend never sends -- and ``tsc`` could
# not tell, because the report arrives through ``res.json() as T``. Expressed as
# set equality, so both restoring a copy and dropping a legitimate one fail.
#
# Detection is deliberately textual: an interface counts as a copy when its name
# ends in ``Overlay`` or its body carries an index signature. That covers the
# loose copy that was there and the narrow one that would hide a field the
# backend does declare -- the mirror-image mistake, and the one worth naming
# ``Overlay`` for.
ALLOWED_OVERLAY_COPIES_IN_API_TS: frozenset[str] = frozenset()

# The two spellings that exist in this repo: the generator emits ``[k: string]``
# while the hand-written ``api.ts`` used ``[key: string]``. Matching on one
# silently reports an empty set for the other. Matched unindented, so an inline
# occurrence inside a field's own braces counts as well.
_INDEX_SIGNATURE_MARKERS = ("[key: string]: unknown", "[k: string]: unknown")


def _interface_bodies(text: str) -> dict[str, list[str]]:
    """Map each top-level ``export interface`` name to its body lines.

    Line-based because the only reliable delimiter is the closing brace at
    column zero; a regex would happily run past one ``}`` into the next
    interface and then read the neighbour's tail as this one's.
    """
    prefix = "export interface "
    bodies: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        if line.startswith(prefix):
            current = line[len(prefix):].split(" ", 1)[0]
            bodies[current] = []
            continue
        if current is not None:
            if line == "}":
                current = None
                continue
            bodies[current].append(line)
    return bodies


def _overlay_copies(bodies: dict[str, list[str]]) -> list[str]:
    """Names in ``bodies`` that look like a hand-written copy of an overlay.

    Either the name ends in ``Overlay`` or the body carries an index signature.
    Sorted, so the assertion's failure message is stable and a diff keeps reading
    as a set difference.

    The index signature is looked for **anywhere in the body** rather than only
    on the last line: a mid-body marker silences exactly the same type error, and
    after audit 53.52 no ``api.ts`` interface *body* contains either spelling, so
    "anywhere" costs nothing there. ``generated-types.ts`` is the opposite case
    -- a readable ``dict[str, Any]`` field is a *legitimate* inline index
    signature -- which is why this helper is not pointed at it.

    Scoped to interface bodies on purpose, not grepped over the whole file: the
    comment above ``DecisionReport`` in ``api.ts`` spells ``[key: string]:
    unknown`` out in prose, describing the copies that were retired. A whole-file
    search would fire on that documentation. Bodies are where the signature can
    actually loosen a type.
    """
    return sorted(
        {name for name in bodies if name.endswith("Overlay")}
        | {
            name
            for name, body in bodies.items()
            if any(marker in line for line in body for marker in _INDEX_SIGNATURE_MARKERS)
        }
    )


def _referenced_models(roots: list[type]) -> set[type]:
    """Every Pydantic model reachable from ``roots`` through field annotations.

    Producer-driven on purpose: rather than reading the generated file, this asks
    the models what they actually reference, unwrapping ``Optional[...]``,
    ``list[...]`` and friends with ``typing.get_args``. ``_frontend_export`` is
    the entry point because ``pydantic2ts`` collects exactly the BaseModel
    subclasses sitting in that module's namespace.
    """
    from pydantic import BaseModel

    def _walk(annotation: object):
        yield annotation
        for arg in typing.get_args(annotation):
            yield from _walk(arg)

    seen: set[type] = set()
    stack: list[type] = list(roots)
    while stack:
        model = stack.pop()
        if model in seen:
            continue
        seen.add(model)
        for field in model.model_fields.values():
            for candidate in _walk(field.annotation):
                if (
                    isinstance(candidate, type)
                    and issubclass(candidate, BaseModel)
                    and candidate is not BaseModel
                ):
                    stack.append(candidate)
    return seen


def _json2ts_available() -> bool:
    """Whether the json2ts CLI the script shells out to is installed.

    The generator needs ``json2ts`` from ``frontend/node_modules``, so these
    tests only run where the frontend deps are installed. The CI backend job
    installs Python only (Node lives in the frontend and type-sync jobs), and
    without this guard every case here fails on a RuntimeError that says
    nothing about the code under test. The dedicated ``type-sync-check`` job
    keeps the generator itself covered in CI.
    """
    from scripts.generate_types import _find_json2ts_cmd

    try:
        _find_json2ts_cmd()
    except RuntimeError:
        return False
    return True


@unittest.skipUnless(_json2ts_available(), "json2ts CLI not installed (frontend deps missing)")
class TestGenerateTypesScript(unittest.TestCase):
    """Tests run the actual generate_types.py script via subprocess."""

    def _run_script(self, *args: str) -> subprocess.CompletedProcess:
        cmd = [sys.executable, "-m", "scripts.generate_types", *args]
        return subprocess.run(
            cmd,
            cwd=_BACKEND_DIR,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def test_generates_file_with_all_allowlist_models(self):
        """Default mode writes generated-types.ts containing every allowlisted model.

        The names are read from ``_frontend_export.__all__`` rather than
        hard-coded. The old hard-coded list had drifted to 16 names while the
        allowlist held 23, so it had quietly stopped covering everything added
        since (``SimTrade`` / ``SimTradeListResponse`` / ``TradeStatBucket`` /
        ``TradeStats`` and the three ``MarketSnapshot*`` models) -- audit
        section 53.50.
        """
        from app.models import _frontend_export

        result = self._run_script()
        self.assertEqual(result.returncode, 0, f"Script failed: {result.stderr}")
        self.assertTrue(_OUTPUT_PATH.exists(), "generated-types.ts not created")
        content = _OUTPUT_PATH.read_text(encoding="utf-8")
        for model_name in _frontend_export.__all__:
            self.assertIn(model_name, content, f"{model_name} missing from generated types")

    def test_output_contains_header_comment(self):
        """Generated file should have a 'do not edit' header."""
        result = self._run_script()
        self.assertEqual(result.returncode, 0, f"Script failed: {result.stderr}")
        content = _OUTPUT_PATH.read_text(encoding="utf-8")
        self.assertIn("generated", content.lower())
        self.assertIn("do not edit", content.lower())

    def test_check_mode_exit_0_when_up_to_date(self):
        """--check exits 0 when generated-types.ts matches fresh generation."""
        # First ensure file is up to date
        self._run_script()
        # Then check
        result = self._run_script("--check")
        self.assertEqual(result.returncode, 0, f"--check failed on up-to-date file: {result.stderr}")

    def test_check_mode_exit_1_when_stale(self):
        """--check exits 1 when generated-types.ts differs from fresh generation."""
        # Ensure file is up to date first
        self._run_script()
        original = _OUTPUT_PATH.read_text(encoding="utf-8")
        # Tamper: append a fake interface
        _OUTPUT_PATH.write_text(original + "\nexport interface FakeTampered { x: number; }\n", encoding="utf-8")
        try:
            result = self._run_script("--check")
            self.assertEqual(result.returncode, 1, f"--check should exit 1 on stale file, got {result.returncode}")
        finally:
            # Restore
            self._run_script()

    def test_output_is_deterministic(self):
        """Running generate twice produces identical output."""
        self._run_script()
        first = _OUTPUT_PATH.read_text(encoding="utf-8")
        self._run_script()
        second = _OUTPUT_PATH.read_text(encoding="utf-8")
        self.assertEqual(first, second, "Generated output is not deterministic")

    def test_closed_overlay_interfaces_have_no_tail(self):
        """The overlays section 53.51 closed must render without a tail.

        Declaring ``model_config`` is only half of what closes an interface: the
        tail comes from ``pydantic2ts`` temporarily setting ``extra='forbid'`` on
        the models it collects as *roots*, and skipping that for anything opting
        into ``extra='allow'``. Reading the generated bytes is the direct
        evidence for that second half; the module-level counterpart -- which is
        the half that also runs where ``json2ts`` is not installed -- is
        ``AllowlistCompositionTests.test_only_the_documented_models_stay_open``.

        ``EventSource`` is asserted the other way round on purpose: its tail is
        the contract (nine adapters add keys of their own), so a change that
        closed it has to be caught here rather than celebrated.

        The three shapes audit 53.53 published for GET
        /events/{id}/history join the closed list: the route binds them as its
        response models, so a tail on any of them would let the frontend reach
        for keys the endpoint does not promise. Audit 53.54 adds
        ``EdgeSeriesPoint`` and ``FreshEdgeItem`` -- the point and the list
        element of GET /events/edges/fresh -- for the same reason. They are
        listed as *roots* in the allowlist, which is what makes them closed;
        being reachable only by ``$ref`` from ``FreshEdgesResponse.edges`` would
        have left the tail on exactly the element the frontend could not read.
        Audit 53.55 adds ``EventMoverItem`` -- the element of
        ``EventMoversResponse.movers`` -- on the same reasoning.
        """
        self._run_script()
        bodies = _interface_bodies(_OUTPUT_PATH.read_text(encoding="utf-8"))
        for name in (
            "ConfidenceBreakdown",
            "CrossValidation",
            "DecisionQuality",
            "EdgeSeriesPoint",
            "EdgeTrajectorySummary",
            "EventAuditSnapshot",
            "EventMoverItem",
            "EvidenceItem",
            "FreshEdgeItem",
            "LLMTelemetry",
            "MarketQuality",
            "SimilarEventItem",
            "SourceReliability",
            "TrendSummary",
        ):
            with self.subTest(interface=name):
                self.assertIn(name, bodies, f"{name} missing from generated types")
                self.assertNotIn(
                    "  [k: string]: unknown;",
                    bodies[name],
                    f"{name} renders open, but audit 53.51 made it a closed contract",
                )
        self.assertIn(
            "  [k: string]: unknown;",
            bodies["EventSource"],
            "EventSource is deliberately open; losing its tail hides keys the API sends",
        )


class AllowlistCompositionTests(unittest.TestCase):
    """What the allowlist *exports*, asking nothing of json2ts.

    Kept out of the skip-decorated class above so it still runs where the CLI is
    not installed -- it only inspects the module, and it is the guard that keeps
    the snapshot interfaces closed.
    """

    def test_market_snapshot_models_are_export_roots(self):
        """The three snapshot models must be *imported*, not merely referenced.

        ``pydantic2ts`` collects the models in this module's namespace
        (``inspect.getmembers``), not its ``__all__``. Being in that set is what
        makes a model a root, and only root models get ``extra='forbid'`` during
        schema generation -- which is what suppresses the ``[k: string]:
        unknown`` tail ``json2ts`` otherwise appends. Reachable-by-reference is
        not enough: ``MarketSnapshotSeries`` / ``MarketSnapshotPoint`` are also
        fields of ``MarketSnapshotsResponse``, so dropping them from this module
        would still emit interfaces, just looser ones, and ``tsc`` would stay
        green (audit section 53.49).
        """
        from app.models import _frontend_export

        for name in (
            "MarketSnapshotsResponse",
            "MarketSnapshotSeries",
            "MarketSnapshotPoint",
        ):
            self.assertTrue(
                hasattr(_frontend_export, name),
                f"{name} is not a root model in {_frontend_export.__name__}",
            )

    def test_every_reachable_plain_model_is_registered(self):
        """Every reachable model that did *not* opt into ``extra='allow'`` must be a root.

        A model that is merely referenced renders as a *nested* interface: no
        ``additionalProperties: false``, so ``json2ts`` appends a
        ``[k: string]: unknown`` tail and the generated type is looser than the
        backend contract -- a plain ``BaseModel`` ignores extra keys, so "closed"
        is the faithful shape. Only models that opt into ``extra='allow'`` may
        legitimately stay open, and *which* ones those are is pinned separately
        by ``test_only_the_documented_models_stay_open``: the ``FlexibleResponse``
        family, plus ``EventRecord`` and ``EventSource``. Audit section 53.51
        closed the seven overlays that had no reason to be open.

        So everything the frontend can reach and that has *not* opted in has to
        be imported into ``_frontend_export`` (audit sections 53.49, 53.50). This
        asks the models what they reference rather than reading the generated
        file, so it fires the moment a model is dropped from the allowlist --
        before anyone regenerates.
        """
        from pydantic import BaseModel

        from app.models import _frontend_export

        roots = [
            value
            for value in vars(_frontend_export).values()
            if isinstance(value, type) and issubclass(value, BaseModel) and value is not BaseModel
        ]
        self.assertTrue(roots, "no root models found -- _frontend_export looks empty")
        unregistered = sorted(
            model.__name__
            for model in _referenced_models(roots)
            if (model.model_config or {}).get("extra") != "allow"
            and not hasattr(_frontend_export, model.__name__)
        )
        self.assertEqual(
            unregistered,
            [],
            "these models are reachable from the allowlist but are neither "
            f"registered as roots nor opted into extra='allow': {unregistered}",
        )

    def test_only_the_documented_models_stay_open(self):
        """Which reachable models render open is a decision, not an accident.

        The generator closes every model it collects as a root *unless* that
        model opts into ``extra='allow'``. So the set of open interfaces is
        exactly the set of reachable models whose own config says ``allow`` --
        minus the ``FlexibleResponse`` family, which is open by design and is
        excluded by subclass rather than by name.

        Pinned as an exact set, which makes it fail in *both* directions:
        re-opening one of the overlays section 53.51 closed fails here, and so
        does closing ``EventSource`` (whose producers really do add undeclared
        keys) or ``EventRecord`` (load-bearing for schema evolution). A guard
        that only forbade re-opening would let the over-tightening direction
        through unnoticed.
        """
        from pydantic import BaseModel

        from app.models import _frontend_export
        from app.models.event import FlexibleResponse

        roots = [
            value
            for value in vars(_frontend_export).values()
            if isinstance(value, type)
            and issubclass(value, BaseModel)
            and value is not BaseModel
        ]
        open_models = sorted(
            model.__name__
            for model in _referenced_models(roots)
            if (model.model_config or {}).get("extra") == "allow"
            and not issubclass(model, FlexibleResponse)
        )
        self.assertEqual(
            open_models,
            sorted(DELIBERATELY_OPEN),
            "the set of deliberately-open interfaces changed; if that was "
            "intended, edit DELIBERATELY_OPEN in the same commit",
        )


class EventHistoryContractTests(unittest.TestCase):
    """The GET /events/{id}/history contract, from both ends (audit 53.53).

    Module-level and static rather than model-driven only, because the change it
    guards spans three files: ``event.py`` published three named models, and
    ``api.ts`` / ``types.ts`` stopped re-declaring the shapes those models
    replace. The model half needs no ``json2ts``; the text half needs no
    regeneration either, since hand-written modules are never generated.
    """

    def test_event_history_response_points_at_the_published_models(self):
        """The envelope's three payload fields must name their models, not ``dict``.

        Identity, not ``issubclass``: ``EventHistoryResponse`` is a
        ``FlexibleResponse``, so a regression to ``dict[str, Any]`` would still
        validate, still return the same bytes, and still let ``tsc`` pass --
        the generated ``trend`` would simply go back to an inline
        ``{ [k: string]: unknown }``. That is the whole failure mode this
        endpoint's naming work removes, and only an identity check sees it.
        """
        from app.models.event import (
            EventAuditSnapshot,
            EventHistoryResponse,
            EdgeTrajectorySummary,
            TrendSummary,
        )

        expected = {
            "trend": TrendSummary,
            "edge": EdgeTrajectorySummary,
            "history": list[EventAuditSnapshot],
        }
        fields = EventHistoryResponse.model_fields
        for name, annotation in expected.items():
            with self.subTest(field=name):
                self.assertIn(name, fields, f"EventHistoryResponse.{name} vanished")
                self.assertEqual(
                    fields[name].annotation,
                    annotation,
                    f"EventHistoryResponse.{name} no longer names its published model",
                )

    def test_history_endpoint_reads_the_generated_envelope(self):
        """No local copy of the three retired shapes, and the call site reads the envelope.

        The two halves fail in opposite directions. The negative half catches a
        hand-written ``interface`` coming back under any of the six names the
        frontend has used for these shapes (three generated, three retired
        local aliases) -- and it has to be a separate check from
        ``ALLOWED_OVERLAY_COPIES_IN_API_TS``, whose criteria are "name ends in
        ``Overlay``" or "body carries an index signature". A re-declared
        ``TrendSummary`` is neither, so that guard is blind to exactly this
        regression; name-based is the weaker-looking rule that actually fires.

        The positive half catches the opposite slip -- keeping the types but
        bypassing the published envelope at the call site, which is how
        ``EventHistoryResponse`` ended up with zero consumers in the first
        place. ``api.ts`` has exactly one ``/history`` path, so finding one line
        and requiring the generic on it is unambiguous.
        """
        api_text = _API_TS_PATH.read_bytes().decode("utf-8")
        types_text = _TYPES_TS_PATH.read_bytes().decode("utf-8")
        api_bodies = _interface_bodies(api_text)
        types_bodies = _interface_bodies(types_text)

        for name in (
            "EdgeTrajectory",
            "EdgeTrajectorySummary",
            "EventAuditSnapshot",
            "HistorySnapshot",
            "Trend",
            "TrendSummary",
        ):
            with self.subTest(retired=name):
                self.assertNotIn(
                    name,
                    api_bodies,
                    f"api.ts re-declared {name}; generated-types.ts is the source "
                    "of truth. Read the generated name instead.",
                )
                self.assertNotIn(
                    name,
                    types_bodies,
                    f"types.ts re-declared {name}; alias the generated one instead.",
                )

        for path, needle in (
            (_API_TS_PATH, "export type EdgeTrajectory = GeneratedEdgeTrajectorySummary;"),
            (_TYPES_TS_PATH, "export type HistorySnapshot = EventAuditSnapshot;"),
            (_TYPES_TS_PATH, "export type Trend = TrendSummary;"),
        ):
            with self.subTest(alias=needle):
                text = api_text if path == _API_TS_PATH else types_text
                self.assertIn(
                    needle,
                    text,
                    f"{path.name} no longer aliases the generated name "
                    f"(expected: {needle})",
                )

        history_lines = [ln for ln in api_text.splitlines() if "/history`)" in ln]
        self.assertEqual(
            len(history_lines),
            1,
            f"expected one /history call site in api.ts, found {len(history_lines)}",
        )
        self.assertIn(
            "api<EventHistoryResponse>(",
            history_lines[0],
            "the /history call site bypasses the published envelope again: "
            "api.ts must read api<EventHistoryResponse>",
        )


class FreshEdgesContractTests(unittest.TestCase):
    """The GET /events/edges/fresh contract, from both ends (audit 53.54).

    Same split as ``EventHistoryContractTests``: the model half reads
    ``model_fields`` and needs no ``json2ts``, and the text half reads modules
    nothing generates, so a stale ``generated-types.ts`` can neither make it pass
    nor make it fail.
    """

    def test_fresh_edges_response_points_at_the_published_element(self):
        """The envelope names its element, and both optionality directions hold.

        Three groups, each aimed at a different way this endpoint loses its
        contract.

        ``edges`` must name ``FreshEdgeItem``. Identity, not ``issubclass``:
        ``FreshEdgesResponse`` is a ``FlexibleResponse``, so a regression to
        ``list[dict[str, Any]]`` still validates, still returns the same bytes,
        and still passes ``tsc`` -- the generated element would simply go back to
        ``{ [k: string]: unknown }``, which is the shape the frontend had to work
        around by hand-writing ``FreshEdge`` in the first place.

        The element's ``edge`` and ``series`` must name 53.53's
        ``EdgeTrajectorySummary`` and this section's ``EdgeSeriesPoint``: those
        two are what is left of an element once the wrapper is stripped.

        Finally the required/optional split, which fails in *opposite*
        directions and therefore needs both halves. Dropping the default from
        ``event_title_zh`` turns an event with no Chinese title into a 500, while
        giving one back to ``count`` silently re-widens the published envelope to
        ``count?:``. ``model_fields[...].is_required()`` is the only reading that
        sees the second mistake: the generated text stays "valid", just looser
        than the endpoint promises.
        """
        from app.models.event import (
            EdgeSeriesPoint,
            EdgeTrajectorySummary,
            FreshEdgeItem,
            FreshEdgesResponse,
        )

        fields = FreshEdgesResponse.model_fields
        self.assertIn("edges", fields, "FreshEdgesResponse.edges vanished")
        self.assertEqual(
            fields["edges"].annotation,
            list[FreshEdgeItem],
            "FreshEdgesResponse.edges no longer names its published element",
        )

        item = FreshEdgeItem.model_fields
        self.assertEqual(
            item["edge"].annotation,
            EdgeTrajectorySummary,
            "FreshEdgeItem.edge no longer names the 53.53 trajectory model",
        )
        self.assertEqual(
            item["series"].annotation,
            list[EdgeSeriesPoint] | None,
            "FreshEdgeItem.series no longer names the published point model",
        )

        for name in ("event_id", "event_title", "edge"):
            with self.subTest(always_written=name):
                self.assertTrue(
                    item[name].is_required(),
                    f"FreshEdgeItem.{name} gained a default, but both producers "
                    "write that key on every item",
                )
        for name in ("event_title_zh", "series"):
            with self.subTest(conditionally_written=name):
                self.assertFalse(
                    item[name].is_required(),
                    f"FreshEdgeItem.{name} lost its default. The route writes "
                    "that key conditionally, so requiring it makes the 500 the "
                    "normal case -- see the model docstring for the probe.",
                )
        for name in (
            "count",
            "total",
            "limit",
            "offset",
            "classification",
            "classification_totals",
        ):
            with self.subTest(envelope=name):
                self.assertTrue(
                    fields[name].is_required(),
                    f"FreshEdgesResponse.{name} gained a default, but the one "
                    "route serving this model sets that key unconditionally "
                    "(audit 53.54 tightened the envelope)",
                )

    def test_fresh_edges_callsites_read_the_generated_envelope(self):
        """No local copy of the retired element, and both call sites use it.

        The negative half is name-based out of necessity. The criterion already
        in place -- ``ALLOWED_OVERLAY_COPIES_IN_API_TS``, which counts an
        interface as a copy when its name ends in ``Overlay`` or its body carries
        an index signature -- is blind to a re-declared ``FreshEdge``, which is
        neither. ``_interface_bodies`` only collects ``export interface``, so the
        ``export type`` aliases asserted below are not mistaken for declarations.

        The positive half walks *back* from each ``/events/edges/fresh`` line to
        the nearest ``api<`` generic rather than searching a fixed window: the
        ``edgeMonitor`` call site sits a few lines under ``resetData``'s inline
        ``api<{ ... }>``, so any window wide enough to be safe would have matched
        that neighbour instead of the generic actually in use.
        """
        api_text = _API_TS_PATH.read_bytes().decode("utf-8")
        types_text = _TYPES_TS_PATH.read_bytes().decode("utf-8")
        api_bodies = _interface_bodies(api_text)
        types_bodies = _interface_bodies(types_text)

        for name in ("EdgePoint", "EdgeSeriesPoint", "FreshEdge", "FreshEdgeItem"):
            with self.subTest(retired=name):
                self.assertNotIn(
                    name,
                    api_bodies,
                    f"api.ts re-declared {name}; generated-types.ts is the "
                    "source of truth. Alias the generated name instead.",
                )
                self.assertNotIn(
                    name,
                    types_bodies,
                    f"types.ts re-declared {name}; generated-types.ts is the "
                    "source of truth. Alias the generated name instead.",
                )

        for needle in (
            "export type FreshEdge = GeneratedFreshEdgeItem;",
            "export type EdgePoint = GeneratedEdgeSeriesPoint;",
            "export type FreshEdgesResponse = GeneratedFreshEdgesResponse;",
        ):
            with self.subTest(alias=needle):
                self.assertIn(
                    needle,
                    api_text,
                    f"api.ts no longer aliases the generated name "
                    f"(expected: {needle})",
                )

        lines = api_text.splitlines()
        paths = [i for i, ln in enumerate(lines) if "/events/edges/fresh" in ln]
        self.assertEqual(
            len(paths),
            2,
            "expected the two /events/edges/fresh call sites in api.ts, "
            f"found {len(paths)}",
        )
        for idx in paths:
            generic = ""
            for back in range(idx, -1, -1):
                if "api<" in lines[back]:
                    generic = lines[back]
                    break
            with self.subTest(callsite=lines[idx].strip()[:40]):
                self.assertTrue(generic, "no api< generic found above this call site")
                self.assertIn(
                    "api<FreshEdgesResponse>(",
                    generic,
                    "this call site bypasses the published envelope again: "
                    "api.ts must read api<FreshEdgesResponse>",
                )


class EventMoversContractTests(unittest.TestCase):
    """The GET /events/movers contract, from all three ends (audit 53.55).

    Same split as ``EventHistoryContractTests`` / ``FreshEdgesContractTests``:
    the model half reads ``model_fields`` and needs no ``json2ts``, the text half
    reads modules nothing generates, so a stale ``generated-types.ts`` can
    neither make it pass nor make it fail. This endpoint adds a third half --
    the route's own decorator -- because one of the three decisions section 53.55
    made is not visible in either of the other two.
    """

    def test_event_movers_response_points_at_the_published_element(self):
        """The envelope names its element, and both optionality directions hold.

        ``movers`` must name ``EventMoverItem``. Identity, not ``issubclass``:
        ``EventMoversResponse`` is a ``FlexibleResponse``, so a regression to
        ``list[dict[str, Any]]`` still validates, still returns the same bytes and
        still passes ``tsc`` -- the generated element would simply go back to
        ``{ [k: string]: unknown }``, which is the shape ``lib/types.ts`` had to
        work around by hand-writing ``Mover``.

        The element's ``trend`` must name 53.53's ``TrendSummary``. That is the
        half that was already published, and the probe put 1 334 producer outputs
        (both branches, every event in five corpora) through it with no rejection,
        so binding it cannot turn a 200 into a 500.

        The required/optional split fails in *opposite* directions, so it needs
        both halves. Dropping the default from ``event_title_zh`` turns an event
        with no Chinese title into a 500, while giving one back to ``count``
        silently re-widens the published envelope to ``count?:``.
        ``model_fields[...].is_required()`` is the only reading that sees the
        second mistake: the generated text stays "valid", just looser than the
        endpoint promises.
        """
        from app.models.event import (
            EventMoverItem,
            EventMoversResponse,
            TrendSummary,
        )

        fields = EventMoversResponse.model_fields
        self.assertIn("movers", fields, "EventMoversResponse.movers vanished")
        self.assertEqual(
            fields["movers"].annotation,
            list[EventMoverItem],
            "EventMoversResponse.movers no longer names its published element",
        )

        item = EventMoverItem.model_fields
        self.assertEqual(
            item["trend"].annotation,
            TrendSummary,
            "EventMoverItem.trend no longer names the 53.53 trend model",
        )

        for name in ("event_id", "event_title", "trend"):
            with self.subTest(always_written=name):
                self.assertTrue(
                    item[name].is_required(),
                    f"EventMoverItem.{name} gained a default, but rank_movers "
                    "writes that key on every item",
                )
        self.assertFalse(
            item["event_title_zh"].is_required(),
            "EventMoverItem.event_title_zh lost its default. The route writes "
            "that key conditionally, so requiring it makes the 500 the normal "
            "case -- see the model docstring for the probe.",
        )
        self.assertTrue(
            fields["count"].is_required(),
            "EventMoversResponse.count gained a default, but the one route "
            "serving this model sets that key unconditionally (audit 53.55 "
            "tightened the envelope)",
        )

    def test_event_movers_callsite_reads_the_generated_envelope(self):
        """No local copy of the retired element, and the call site uses it.

        The negative half is name-based out of necessity. The criterion already in
        place -- ``ALLOWED_OVERLAY_COPIES_IN_API_TS``, which counts an interface
        as a copy when its name ends in ``Overlay`` or its body carries an index
        signature -- is blind to a re-declared ``Mover``, which is neither.
        ``_interface_bodies`` only collects ``export interface``, so the
        ``export type`` alias asserted below is not mistaken for a declaration.

        The positive half walks *back* from the ``/events/movers`` line to the
        nearest ``api<`` generic, the same way 53.54 does, rather than searching a
        fixed window.
        """
        api_text = _API_TS_PATH.read_bytes().decode("utf-8")
        types_text = _TYPES_TS_PATH.read_bytes().decode("utf-8")
        api_bodies = _interface_bodies(api_text)
        types_bodies = _interface_bodies(types_text)

        for name in ("Mover", "EventMoverItem"):
            with self.subTest(retired=name):
                self.assertNotIn(
                    name,
                    api_bodies,
                    f"api.ts re-declared {name}; generated-types.ts is the "
                    "source of truth. Alias the generated name instead.",
                )
                self.assertNotIn(
                    name,
                    types_bodies,
                    f"types.ts re-declared {name}; generated-types.ts is the "
                    "source of truth. Alias the generated name instead.",
                )

        needle = "export type Mover = EventMoverItem;"
        with self.subTest(alias=needle):
            self.assertIn(
                needle,
                types_text,
                "types.ts no longer aliases the generated element "
                f"(expected: {needle})",
            )

        lines = api_text.splitlines()
        paths = [i for i, ln in enumerate(lines) if "/events/movers" in ln]
        self.assertEqual(
            len(paths),
            1,
            "expected the single /events/movers call site in api.ts, "
            f"found {len(paths)}",
        )
        for idx in paths:
            generic = ""
            for back in range(idx, -1, -1):
                if "api<" in lines[back]:
                    generic = lines[back]
                    break
            with self.subTest(callsite=lines[idx].strip()[:40]):
                self.assertTrue(generic, "no api< generic found above this call site")
                self.assertIn(
                    "api<EventMoversResponse>(",
                    generic,
                    "this call site bypasses the published envelope again: "
                    "api.ts must read api<EventMoversResponse>",
                )

    def test_the_movers_route_keeps_the_exclude_unset_flag(self):
        """The half that keeps the wire bytes identical (audit 53.55).

        This endpoint's element used to be a bare dict, so pydantic never touched
        it and a mover with no Chinese title simply omitted ``event_title_zh``.
        Naming the element puts it through the model, and because this route --
        unlike its sibling ``/events/edges/fresh`` -- declared no
        ``response_model_exclude_unset``, the key would have come back as an
        explicit ``null``. Probe ``probe_movers_exclude_unset.py`` measured the
        three shapes, and its control shows ``count`` / ``movers`` survive either
        way, so adding the flag drops nothing.

        Nothing else can see this. The model half above passes with or without the
        flag, ``tsc`` never looks at test files, and no frontend consumer cares,
        because ``m.event_title_zh || m.event_title || m.event_id`` folds both.
        The failure would be silent and would surface only as a wire diff.

        Line-based rather than a multi-line substring, so the assertion does not
        depend on the checkout's line endings (HEAD is LF, the worktree is CRLF).
        """
        lines = _EVENTS_ROUTE_PATH.read_bytes().decode("utf-8").splitlines()
        anchors = [i for i, ln in enumerate(lines) if ln.strip() == '"/movers",']
        self.assertEqual(
            len(anchors),
            1,
            f"expected one '/movers' decorator in the events router, found {len(anchors)}",
        )
        decorator = [ln.strip() for ln in lines[anchors[0] : anchors[0] + 4]]
        self.assertIn(
            "response_model=EventMoversResponse,",
            decorator,
            "the /movers route no longer binds EventMoversResponse",
        )
        self.assertIn(
            "response_model_exclude_unset=True,",
            decorator,
            "the /movers route lost response_model_exclude_unset. Naming the "
            "element turns an absent event_title_zh into an explicit null without "
            "it (audit 53.55).",
        )


class SimilarEventsContractTests(unittest.TestCase):
    """The GET /events/{id}/similar contract (audit 53.56).

    Same split as the 53.53/53.54/53.55 contract tests, minus the route-decorator
    half: this endpoint needs no ``response_model_exclude_unset``, and the reason
    is a property of the element itself -- every key is written unconditionally,
    so no field may carry a default. That property is what the second case pins,
    and it is also what makes the third case (the text half) a pure type change.
    """

    def test_similar_response_points_at_the_published_element(self):
        """The envelope names its element, and all three keys are required.

        Identity, not ``issubclass``: ``SimilarEventsResponse`` is a
        ``FlexibleResponse``, so a regression to ``list[dict[str, Any]]`` still
        validates, still returns the same bytes and still passes ``tsc`` -- the
        generated element would simply go back to ``{ [k: string]: unknown }``,
        the shape ``lib/types.ts`` worked around by hand-writing ``SimilarEvent``.
        """
        from app.models.event import SimilarEventItem, SimilarEventsResponse

        fields = SimilarEventsResponse.model_fields
        self.assertIn("similar", fields, "SimilarEventsResponse.similar vanished")
        self.assertEqual(
            fields["similar"].annotation,
            list[SimilarEventItem],
            "SimilarEventsResponse.similar no longer names its published element",
        )
        for name in ("event_id", "count", "similar"):
            with self.subTest(always_written=name):
                self.assertTrue(
                    fields[name].is_required(),
                    f"SimilarEventsResponse.{name} gained a default, but the one "
                    "route serving this model writes that key unconditionally "
                    "(audit 53.56 tightened the envelope)",
                )

    def test_the_similar_element_requires_all_eight_keys(self):
        """Every key is written unconditionally, so none may carry a default.

        This is the property that separates this endpoint from ``/events/movers``
        (53.55). There, four of five keys were written unconditionally and the
        fifth conditionally, so the route had to keep
        ``response_model_exclude_unset`` to avoid turning an absent key into an
        explicit ``null``. Here ``find_similar`` sets all eight keys on every item
        it returns -- the four "nullable" ones included, as ``None`` rather than
        by omission -- so no default is needed and no flag is needed. A default
        appearing here would be the mistake: it would silently widen the element
        to ``X | null | undefined`` and re-introduce the ambiguity this endpoint
        does not have.

        The four nullable fields must therefore be *required but nullable*; with
        no default, the generated TS is ``number | null`` / ``string | null``,
        which is exactly the producer's contract.

        ``direction`` is checked to be ``str | None`` rather than a ``Literal`` of
        the three values the census observed (22 205 items, exactly
        ``{rising, stable, falling}``). The record's own ``Probability.direction``
        is declared ``str``, so narrowing it here would be *tighter than the
        contract this mirrors* and would turn a future direction value into a 422.
        """
        import typing

        from app.models.event import SimilarEventItem

        item = SimilarEventItem.model_fields
        expected = {
            "event_id": str,
            "event_title": str,
            "event_title_zh": str,
            "similarity": float,
            "estimated_probability": (float, type(None)),
            "change": (float, type(None)),
            "direction": (str, type(None)),
            "last_updated": (str, type(None)),
        }
        self.assertEqual(
            set(item),
            set(expected),
            "SimilarEventItem's field set no longer matches find_similar's "
            "eight-key dict",
        )
        for name, want in expected.items():
            with self.subTest(field=name):
                self.assertTrue(
                    item[name].is_required(),
                    f"SimilarEventItem.{name} gained a default. find_similar "
                    "writes that key on every item, so a default only widens the "
                    "published element and would need exclude_unset to undo",
                )
                if isinstance(want, tuple):
                    self.assertEqual(
                        typing.get_args(item[name].annotation),
                        want,
                        f"SimilarEventItem.{name} is no longer {want}",
                    )
                else:
                    self.assertEqual(
                        item[name].annotation,
                        want,
                        f"SimilarEventItem.{name} is no longer {want.__name__}",
                    )

    def test_similar_callsite_reads_the_generated_envelope(self):
        """No local copy of the retired element, and the call site uses it.

        The negative half is name-based out of necessity, exactly as in 53.55:
        ``ALLOWED_OVERLAY_COPIES_IN_API_TS`` counts an interface as a copy when
        its name ends in ``Overlay`` or its body carries an index signature, and
        a re-declared ``SimilarEvent`` is neither. ``_interface_bodies`` collects
        only ``export interface``, so the ``export type`` alias is not mistaken
        for a declaration.

        The positive half walks *back* from the ``/similar`` line to the nearest
        ``api<`` generic rather than searching a fixed window.
        """
        api_text = _API_TS_PATH.read_bytes().decode("utf-8")
        types_text = _TYPES_TS_PATH.read_bytes().decode("utf-8")
        api_bodies = _interface_bodies(api_text)
        types_bodies = _interface_bodies(types_text)

        for name in ("SimilarEvent", "SimilarEventItem"):
            with self.subTest(retired=name):
                self.assertNotIn(
                    name,
                    api_bodies,
                    f"api.ts re-declared {name}; generated-types.ts is the "
                    "source of truth. Alias the generated name instead.",
                )
                self.assertNotIn(
                    name,
                    types_bodies,
                    f"types.ts re-declared {name}; generated-types.ts is the "
                    "source of truth. Alias the generated name instead.",
                )

        needle = "export type SimilarEvent = SimilarEventItem;"
        with self.subTest(alias=needle):
            self.assertIn(
                needle,
                types_text,
                "types.ts no longer aliases the generated element "
                f"(expected: {needle})",
            )

        lines = api_text.splitlines()
        paths = [i for i, ln in enumerate(lines) if "/similar`" in ln]
        self.assertEqual(
            len(paths),
            1,
            f"expected the single /similar call site in api.ts, found {len(paths)}",
        )
        for idx in paths:
            generic = ""
            for back in range(idx, -1, -1):
                if "api<" in lines[back]:
                    generic = lines[back]
                    break
            with self.subTest(callsite=lines[idx].strip()[:40]):
                self.assertTrue(generic, "no api< generic found above this call site")
                self.assertIn(
                    "api<SimilarEventsResponse>(",
                    generic,
                    "this call site bypasses the published envelope again: "
                    "api.ts must read api<SimilarEventsResponse>",
                )


class ApiTsTypeSourceTests(unittest.TestCase):
    """``api.ts`` reads generated shapes instead of re-declaring them.

    Nothing generates ``api.ts``, so a divergent local copy of a generated
    interface compiles, lints and type-checks cleanly -- the report reaches the
    browser through ``res.json() as T``, a cast that asserts nothing about the
    shape. Audit 53.52 retired the three hand-written overlay copies for that
    reason; the header of ``types.ts`` states the same rule ("DO NOT hand-write
    fields that exist in the Pydantic models").

    Module-level rather than in the ``json2ts``-decorated class above, and static
    rather than model-driven: it only reads the file, so it runs everywhere the
    suite does and it fires on the edit that reintroduces a copy, not on the next
    regeneration.
    """

    def test_decision_report_reads_the_generated_overlays(self):
        """No local overlay copy, and each field points at the generated name.

        The two halves fail in opposite directions on purpose. The first catches
        a copy coming back -- loose (``[key: string]: unknown``, which is what
        was there) or narrow (which would hide a field the backend declares) --
        and the second catches ``DecisionReport`` being repointed at something
        else, including a copy that slips past the first half because it is
        neither suffixed ``Overlay`` nor tailed.
        """
        bodies = _interface_bodies(_API_TS_PATH.read_bytes().decode("utf-8"))
        self.assertIn("DecisionReport", bodies, "DecisionReport vanished from api.ts")
        self.assertEqual(
            _overlay_copies(bodies),
            sorted(ALLOWED_OVERLAY_COPIES_IN_API_TS),
            "api.ts re-declared an overlay; generated-types.ts is the source of "
            "truth. If a local copy is genuinely needed, add its name to "
            "ALLOWED_OVERLAY_COPIES_IN_API_TS in the same commit.",
        )
        report_body = "\n".join(bodies["DecisionReport"])
        for field, generated in (
            ("decision_quality", "DecisionQuality"),
            ("market_quality", "MarketQuality"),
            ("source_reliability", "SourceReliability"),
        ):
            with self.subTest(field=field):
                self.assertIn(
                    f"  {field}?: {generated} | null;",
                    report_body,
                    f"DecisionReport.{field} no longer reads the generated {generated}",
                )


if __name__ == "__main__":
    unittest.main()
