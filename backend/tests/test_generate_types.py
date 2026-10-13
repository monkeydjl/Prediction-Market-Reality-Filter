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
- ``RecentPredictionsResponse`` does the same for ``GET /events/predictions/recent``.
  Its element is wider than any before it -- the store's 33 columns plus two
  joined titles -- and each of the 35 keys is required (audit section 53.58).
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
    "anywhere" costs nothing there. Re-measured at audit 53.59 rather than carried
    forward: 36 ``export interface``, 36 bodies, none carrying either spelling.
    ``generated-types.ts`` is the opposite case -- a readable ``dict[str, Any]``
    field is a *legitimate* inline index signature -- which is why this helper is
    not pointed at it.

    Scoped to interface bodies on purpose, not grepped over the whole file: a
    whole-file search would fire on documentation. ``api.ts`` still spells ``[k:
    string]: unknown`` once in prose -- the ``FreshEdge`` comment saying what the
    generator renders for ``list[dict[str, Any]]`` before audit 53.54 published
    the element -- and that line is not inside any interface body. The example
    here used to be the comment above ``DecisionReport``; audit 53.59 retired the
    interface it described, and its ``[key: string]`` spelling left the file with
    it, so the example was re-derived rather than reworded. Bodies are where the
    signature can actually loosen a type.
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
        ``EventMoversResponse.movers`` -- on the same reasoning. Audit 53.57 adds
        ``PendingLinkItem`` (the element of ``PendingLinksResponse.pending``) and
        ``EventMarketLink`` (the row the link-verify route returns); the latter is
        not an element of anything, so being a root is the only thing that closes
        it. Audit 53.58 adds ``RecentPredictionItem`` -- the element of
        ``RecentPredictionsResponse.predictions``, and a root for the same reason
        the others are: reachable only by ``$ref`` it would keep its tail.
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
            "EventMarketLink",
            "EventMoverItem",
            "EvidenceItem",
            "FreshEdgeItem",
            "LLMTelemetry",
            "MarketQuality",
            "PendingLinkItem",
            "RecentPredictionItem",
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


class PendingLinkContractTests(unittest.TestCase):
    """The pending-links contract, and its sibling (audit 53.57).

    Two shapes share one store table and, until this section, one hand-written
    frontend type: the review-queue element (the stored row plus four enrichment
    fields, 14 keys) and the row the link-verify endpoint returns (the ten
    ``event_market_links`` columns). The old copy declared all fourteen with
    thirteen of them optional, so it was at once too loose for the queue and a
    claim of four keys the verify endpoint never returns.

    Four cases: the envelope, the element, the ten shared fields (the
    duplication is deliberate, so it is asserted rather than trusted), and the
    text half.
    """

    # (name, annotation) for the ten event_market_links columns.
    _ROW_FIELDS = {
        "id": str,
        "event_id": str,
        "market_name": str,
        "contract_id": str,
        "market_question": str,
        "resolution_criteria": str,
        "link_method": str,
        "link_confidence": float,
        "verified": bool,
        "linked_at": str,
    }
    _ENRICHED_FIELDS = {
        "event_title": str,
        "event_title_zh": str,
        "event_summary": str,
        "event_resolution_criteria": str,
    }

    def test_pending_response_points_at_the_published_element(self):
        """The envelope names its element, and ``pending`` is required.

        Identity, not ``issubclass``: ``PendingLinksResponse`` is a
        ``FlexibleResponse``, so a regression to ``list[dict[str, Any]]`` still
        validates, still returns the same bytes and still passes ``tsc`` -- the
        element would just go back to ``{ [k: string]: unknown }``, the shape
        ``lib/api.ts`` used to work around by hand-writing ``PendingLink``.
        """
        from app.models.event import PendingLinkItem, PendingLinksResponse

        fields = PendingLinksResponse.model_fields
        self.assertIn("pending", fields, "PendingLinksResponse.pending vanished")
        self.assertEqual(
            fields["pending"].annotation,
            list[PendingLinkItem],
            "PendingLinksResponse.pending no longer names its published element",
        )
        self.assertTrue(
            fields["pending"].is_required(),
            "PendingLinksResponse.pending gained a default, but the one route "
            'serving this model always writes {"pending": [...]} (audit 53.57)',
        )

    def test_the_element_requires_all_fourteen_keys(self):
        """Every key is written unconditionally, so none may carry a default.

        ``list_pending_links`` writes all fourteen on every item: the ten store
        columns via ``_row_to_dict``, then the four enrichment fields, which fall
        back to ``""`` rather than being omitted. So this endpoint is the third
        position in the family -- 53.55 needed ``response_model_exclude_unset``
        because one key was conditional, 53.56 did not, and neither does this
        one. A default appearing here would silently widen the element to
        ``X | undefined`` and re-open an ambiguity the producer does not have.

        ``verified`` is checked to be ``bool`` and not ``int``: the store coerces
        SQLite's 0/1 through ``_row_to_dict``, and the wire carries a JSON
        boolean.

        The four enrichment fields' annotations are pinned here; the ten shared
        ones are pinned in ``test_the_element_is_the_row_plus_four`` instead. That
        split is deliberate: with both halves checking everything, a single
        defect would redden two cases and the four-phase runs would lose the
        attribution they exist to provide.
        """
        from app.models.event import PendingLinkItem

        item = PendingLinkItem.model_fields
        expected = {**self._ROW_FIELDS, **self._ENRICHED_FIELDS}
        self.assertEqual(
            set(item),
            set(expected),
            "PendingLinkItem's field set no longer matches the route's "
            "fourteen-key dict",
        )
        for name in expected:
            with self.subTest(field=name):
                self.assertTrue(
                    item[name].is_required(),
                    f"PendingLinkItem.{name} gained a default. list_pending_links "
                    "writes that key on every item, so a default only widens the "
                    "published element",
                )
        for name, want in self._ENRICHED_FIELDS.items():
            with self.subTest(enrichment=name):
                self.assertEqual(
                    item[name].annotation,
                    want,
                    f"PendingLinkItem.{name} is no longer {want.__name__}; the "
                    "four enrichment fields are all strings (the route falls back "
                    'to "" rather than omitting the key)',
                )

    def test_the_element_is_the_row_plus_four(self):
        """The ten shared fields are duplicated on purpose -- so they are pinned.

        ``PendingLinkItem`` restates the ten ``EventMarketLink`` columns instead
        of inheriting them, because the two are separate published contracts (a
        stored row vs. a queue element) and the generator treats them as
        independent roots. That choice is only safe if the overlap cannot drift,
        which is what this case asserts: same names, same annotations, same
        required-ness. Without it the duplication would be a silent liability --
        add a column to the table and only one of the two models would grow it.

        Only the *annotations* are compared across the two models. Requiredness is
        already pinned twice over (all fourteen in the element case, all ten on
        the row here), so re-checking it in the loop would let one defect -- a
        default on a shared field -- redden two cases at once, which is exactly
        the attribution the four-phase mutation runs depend on.
        """
        from app.models.event import EventMarketLink, PendingLinkItem

        row = EventMarketLink.model_fields
        item = PendingLinkItem.model_fields

        self.assertEqual(
            set(row),
            set(self._ROW_FIELDS),
            "EventMarketLink is no longer the ten event_market_links columns",
        )
        for name, want in self._ROW_FIELDS.items():
            with self.subTest(row_field=name):
                self.assertTrue(
                    row[name].is_required(),
                    f"EventMarketLink.{name} gained a default; every column is "
                    "NOT NULL and _row_to_dict writes them all",
                )
                self.assertEqual(
                    row[name].annotation,
                    want,
                    f"EventMarketLink.{name} is no longer {want.__name__}",
                )

        for name in row:
            with self.subTest(shared=name):
                self.assertIn(
                    name,
                    item,
                    f"PendingLinkItem dropped {name}, which the store's row "
                    "always carries",
                )
                self.assertEqual(
                    item[name].annotation,
                    row[name].annotation,
                    f"PendingLinkItem.{name} drifted from EventMarketLink.{name}",
                )

    def test_pending_callsites_read_the_generated_contract(self):
        """Neither copy is back, and both call sites read published types.

        The negative half is name-based for the same reason as 53.55/53.56:
        ``ALLOWED_OVERLAY_COPIES_IN_API_TS`` counts an interface as a copy only
        when its name ends in ``Overlay`` or its body carries an index signature,
        and a re-declared ``PendingLink`` is neither. ``_interface_bodies``
        collects only ``export interface``, so the ``export type`` alias is not
        mistaken for a declaration.

        Both positives walk *back* from the path line to the nearest ``api<``
        generic rather than searching a fixed window -- and there are two of
        them, because the two endpoints must not read each other's shape.
        """
        api_text = _API_TS_PATH.read_bytes().decode("utf-8")
        api_bodies = _interface_bodies(api_text)

        for name in ("PendingLink", "PendingLinkItem", "EventMarketLink"):
            with self.subTest(retired=name):
                self.assertNotIn(
                    name,
                    api_bodies,
                    f"api.ts re-declared {name}; generated-types.ts is the source "
                    "of truth. Alias the generated name instead.",
                )

        needle = "export type PendingLink = GeneratedPendingLinkItem;"
        with self.subTest(alias=needle):
            self.assertIn(
                needle,
                api_text,
                "api.ts no longer aliases the generated element "
                f"(expected: {needle})",
            )

        lines = api_text.splitlines()
        expected_generic = {
            "/events/links/pending": "api<PendingLinksResponse>(",
            "/link/verify`": "api<EventMarketLink>(",
        }
        for path, want in expected_generic.items():
            idxs = [i for i, ln in enumerate(lines) if path in ln]
            self.assertEqual(
                len(idxs),
                1,
                f"expected the single {path} call site in api.ts, found {len(idxs)}",
            )
            for idx in idxs:
                generic = ""
                for back in range(idx, -1, -1):
                    if "api<" in lines[back]:
                        generic = lines[back]
                        break
                with self.subTest(callsite=lines[idx].strip()[:40]):
                    self.assertTrue(generic, "no api< generic found above this call site")
                    self.assertIn(
                        want,
                        generic,
                        f"this call site does not read {want}",
                    )


class RecentPredictionsContractTests(unittest.TestCase):
    """The recent-predictions contract (audit 53.58).

    ``GET /events/predictions/recent`` published an envelope whose element was a
    bare ``list[dict[str, Any]]`` -- no field contract at all -- and the frontend
    answered with a hand-written ``PredictionRecord`` of 18 keys. The route
    spreads ``dict(row)`` from ``SELECT * FROM predictions`` (33 columns) and
    appends two title enrichments, so the local copy was narrower than the
    payload it typed. Naming the element is what makes the other 17 columns
    readable; the reverse mistake is the more dangerous one, though: a *closed*
    model declaring fewer keys than the route returns makes pydantic drop the
    surplus from the response, changing the wire bytes with nothing to catch it
    -- ``tsc`` cannot tell, because the report arrives through
    ``res.json() as T``.

    Three cases: the envelope, the element, and the text half.
    """

    # (name, annotation) for the table's 33 columns plus the two joined titles.
    # A key is bare when the producer cannot hand back null, and annotated
    # ``| None`` otherwise -- the table's twelve ``NOT NULL`` columns, plus ``id``
    # (a ``TEXT PRIMARY KEY``, which SQLite does *not* enforce as NOT NULL, so it
    # rests on the store's INSERT) and the two titles (the route defaults them to
    # ``""``). Every other column keeps ``| None`` even where no live row has ever
    # been null (``trust`` / ``adjusted_edge`` / ``liquidity_factor`` /
    # ``qualified`` / ``segment_n`` / ``segment_min_samples``): the conservative
    # direction -- "not observed" is not "cannot happen" -- and it is the decision
    # this section had to make, so it is pinned rather than trusted.
    # ``direction_correct`` stays ``int | None`` because the store hands back
    # SQLite's raw 0/1/NULL and the route does not convert it; declaring ``bool``
    # would rewrite the value, the opposite of ``EventMarketLink.verified``, which
    # the store *does* convert (audit 53.57).
    _ELEMENT_FIELDS = {
        "id": str,
        "event_id": str,
        "contract_id": str,
        "platform": str,
        "base_rate_category": str,
        "ai_probability": float,
        "market_probability": float,
        "raw_edge": float,
        "trust": float | None,
        "adjusted_edge": float | None,
        "liquidity": float,
        "volume": float,
        "decision": str,
        "liquidity_factor": float | None,
        "qualified": int | None,
        "segment_n": int | None,
        "segment_min_samples": int | None,
        "segment_skill": float | None,
        "created_at": str,
        "status": str,
        "actual_outcome": float | None,
        "brier_score": float | None,
        "resolved_at": str | None,
        "snapshot_question": str | None,
        "snapshot_recommendation": str | None,
        "snapshot_confidence": str | None,
        "snapshot_evidence_strength": float | None,
        "snapshot_conflict_score": float | None,
        "snapshot_market_quality_score": float | None,
        "snapshot_source_platform": str | None,
        "direction_correct": int | None,
        "edge_bucket": str | None,
        "confidence_bucket": str | None,
        "event_title": str,
        "event_title_zh": str,
    }

    def test_recent_response_points_at_the_published_element(self):
        """The envelope names its element, and all five keys are required.

        Identity, not ``issubclass``, and not just "a list": a regression to
        ``list[dict[str, Any]]`` still validates, still returns the same bytes
        and still passes ``tsc`` -- the element would just go back to
        ``{ [k: string]: unknown }``, which is the shape ``lib/api.ts`` used to
        work around by hand-writing a subset.
        """
        from app.models.event import RecentPredictionItem, RecentPredictionsResponse

        fields = RecentPredictionsResponse.model_fields
        self.assertEqual(
            set(fields),
            {"count", "total", "limit", "offset", "predictions"},
            "RecentPredictionsResponse is no longer the five-key envelope the "
            "route returns",
        )
        for name, want in (
            ("count", int),
            ("total", int),
            ("limit", int),
            ("offset", int),
        ):
            with self.subTest(key=name):
                self.assertEqual(
                    fields[name].annotation,
                    want,
                    f"RecentPredictionsResponse.{name} is no longer {want.__name__}",
                )
        self.assertEqual(
            fields["predictions"].annotation,
            list[RecentPredictionItem],
            "RecentPredictionsResponse.predictions no longer names its published "
            "element, so the frontend can only hand-write a subset again",
        )
        for name in fields:
            with self.subTest(required=name):
                self.assertTrue(
                    fields[name].is_required(),
                    f"RecentPredictionsResponse.{name} gained a default. The one "
                    "route serving this model writes all five keys on every "
                    "return, so a default only widens the envelope (audit 53.58)",
                )

    def test_the_element_requires_all_thirty_five_keys(self):
        """Every key is written unconditionally, so none may carry a default.

        The route spreads ``dict(row)`` -- all 33 columns -- then writes both
        titles, so there is no "absent" key for a default to describe, and
        ``response_model_exclude_unset`` is not needed here (53.55 needed the
        flag because one key was conditional; 53.56 and 53.57 did not). A default
        would widen the element to ``X | undefined`` for a producer that never
        omits anything.

        Annotations are pinned here, not only the field names: the nullability
        split *is* this section's decision, and a name-only check would let
        ``float | None`` collapse back to ``float`` unnoticed -- ``tsc`` cannot
        see it, because the report arrives through ``res.json() as T``.
        """
        from app.models.event import RecentPredictionItem

        item = RecentPredictionItem.model_fields
        self.assertEqual(
            set(item),
            set(self._ELEMENT_FIELDS),
            "RecentPredictionItem's field set no longer matches the 33 columns "
            "plus two titles the route returns",
        )
        for name, want in self._ELEMENT_FIELDS.items():
            with self.subTest(field=name):
                self.assertTrue(
                    item[name].is_required(),
                    f"RecentPredictionItem.{name} gained a default; the route "
                    "writes that key on every element",
                )
                self.assertEqual(
                    item[name].annotation,
                    want,
                    f"RecentPredictionItem.{name} is no longer {want}; the types "
                    "mirror the schema, so a nullable column keeps '| None'",
                )

    def test_recent_callsite_reads_the_generated_contract(self):
        """No copy back in ``api.ts``, and the call site reads the envelope.

        The negative half is name-based for the same reason as 53.55-53.57:
        ``ALLOWED_OVERLAY_COPIES_IN_API_TS`` counts an interface as a copy only
        when its name ends in ``Overlay`` or its body carries an index signature,
        and a re-declared element is neither. ``_interface_bodies`` collects only
        ``export interface``, so the two ``export type`` aliases are not mistaken
        for declarations. The element's old spelling is checked too, even though
        it never matched that detector: reviving it is the likely regression.

        The positive half walks *back* from the path line to the nearest ``api<``
        generic rather than searching a fixed window.
        """
        api_text = _API_TS_PATH.read_bytes().decode("utf-8")
        api_bodies = _interface_bodies(api_text)

        for name in (
            "PredictionRecord",
            "RecentPredictionItem",
            "RecentPredictionsResponse",
        ):
            with self.subTest(retired=name):
                self.assertNotIn(
                    name,
                    api_bodies,
                    f"api.ts re-declared {name}; generated-types.ts is the source "
                    "of truth. Alias the generated name instead.",
                )

        for needle in (
            "export type PredictionRecord = GeneratedRecentPredictionItem;",
            "export type RecentPredictionsResponse = GeneratedRecentPredictionsResponse;",
        ):
            with self.subTest(alias=needle):
                self.assertIn(
                    needle,
                    api_text,
                    "api.ts no longer aliases the generated type "
                    f"(expected: {needle})",
                )

        lines = api_text.splitlines()
        idxs = [i for i, ln in enumerate(lines) if "/predictions/recent" in ln]
        self.assertEqual(
            len(idxs),
            1,
            "expected the single /predictions/recent call site in api.ts, "
            f"found {len(idxs)}",
        )
        for idx in idxs:
            generic = ""
            for back in range(idx, -1, -1):
                if "api<" in lines[back]:
                    generic = lines[back]
                    break
            with self.subTest(callsite=lines[idx].strip()[:40]):
                self.assertTrue(generic, "no api< generic found above this call site")
                self.assertIn(
                    "api<RecentPredictionsResponse>(",
                    generic,
                    "this call site bypasses the published envelope again: api.ts "
                    "must read api<RecentPredictionsResponse>",
                )


class EventDiscoveryContractTests(unittest.TestCase):
    """The discovery envelope (audit 53.59).

    ``GET /events/discover`` published ``events: list[dict[str, Any]]`` -- no
    element contract at all -- and ``lib/api.ts`` answered with an inline
    hand-written ``{ events: EventRecord[]; source?: string; count?: number }``.
    That is the same "hand-written because the backend stayed untyped" symptom
    audit 53.58 removed for the recent-predictions element, and the local copy
    was narrower still: it dropped the ``platform`` key the route always sends.

    Naming the element is safe here *only* because ``EventRecord`` opts into
    ``extra="allow"`` -- the mirror image of 53.58, where a closed model
    declaring fewer keys than the producer writes would have made pydantic drop
    the surplus. That precondition is already pinned from the generated-file
    side by ``AllowlistCompositionTests.test_only_the_documented_models_stay_open``
    (``EventRecord`` is in ``DELIBERATELY_OPEN``), so the second case below
    guards the *other* half instead: the eleven required keys are the
    200-versus-500 boundary now that the element is validated on the way out.

    Three cases: the envelope, the boundary, and the text half.
    """

    def test_discovery_response_points_at_the_published_element(self):
        """Four required envelope keys, and ``events`` names ``EventRecord``.

        Identity, not ``issubclass``: a regression to ``list[dict[str, Any]]``
        still validates, still returns the same bytes and still passes ``tsc``
        -- the element would just go back to ``{ [k: string]: unknown }[]``,
        which is the shape ``lib/api.ts`` used to work around by hand.
        """
        from app.models.event import EventDiscoveryResponse, EventRecord

        fields = EventDiscoveryResponse.model_fields
        self.assertEqual(
            set(fields),
            {"platform", "source", "count", "events"},
            "EventDiscoveryResponse is no longer the four-key envelope "
            "/discover returns",
        )
        for name, want in (
            ("platform", str),
            ("source", str),
            ("count", int),
        ):
            with self.subTest(key=name):
                self.assertEqual(
                    fields[name].annotation,
                    want,
                    f"EventDiscoveryResponse.{name} is no longer {want.__name__}",
                )
        self.assertEqual(
            fields["events"].annotation,
            list[EventRecord],
            "EventDiscoveryResponse.events no longer names its published "
            "element, so the frontend can only hand-write a subset again",
        )
        for name in fields:
            with self.subTest(required=name):
                self.assertTrue(
                    fields[name].is_required(),
                    f"EventDiscoveryResponse.{name} gained a default. "
                    "``discover_events`` writes all four keys on both of its "
                    "return paths, so a default only widens the envelope "
                    "(audit 53.59)",
                )

    def test_the_element_boundary_is_the_eleven_required_keys(self):
        """The naming is only as safe as ``EventRecord``'s required set.

        Two directions, and the second is the one this node created. The model
        being ``extra="allow"`` is what lets an untyped producer's extra keys
        (``news_filter``, ``schema_version``, ...) survive serialisation; that
        half is pinned against the generated file elsewhere. What is new here is
        that a *missing required* key used to be a silent, unvalidated pass and
        is now a 500, so the set is pinned: ``build_event_record`` writes
        exactly these eleven, and giving any of them a default would quietly
        move the boundary.

        ``event_title_zh`` is deliberately absent from the set -- it has ``""``
        as a default, which is why it is not part of the boundary.
        """
        from app.models.event import EventRecord

        self.assertEqual(
            {n for n, f in EventRecord.model_fields.items() if f.is_required()},
            {
                "event_id",
                "event_title",
                "event_summary",
                "probability",
                "credibility",
                "impact",
                "risk",
                "evidence",
                "source",
                "value_score",
                "intelligence_report",
            },
            "EventRecord's required set changed. /events/discover validates every "
            "element against it (audit 53.59), so this set is the 200-versus-500 "
            "boundary -- add a default and the check silently widens.",
        )

    def test_discovery_callsite_reads_the_generated_contract(self):
        """No envelope copy in ``api.ts``, and the call site reads the alias.

        The negative half checks the retired inline spelling too, even though it
        never matched the overlay-copy detector: reviving it is the likely
        regression. ``_interface_bodies`` collects only ``export interface``, so
        the ``export type`` alias is not mistaken for a declaration. The
        positive half walks *back* from the path line to the nearest ``api<``
        generic rather than searching a fixed window.
        """
        api_text = _API_TS_PATH.read_bytes().decode("utf-8")
        api_bodies = _interface_bodies(api_text)

        for name in ("EventDiscoveryResponse", "EventRecord"):
            with self.subTest(retired=name):
                self.assertNotIn(
                    name,
                    api_bodies,
                    f"api.ts re-declared {name}; generated-types.ts is the "
                    "source of truth. Alias the generated name instead.",
                )
        self.assertNotIn(
            "{ events: EventRecord[]; source?: string; count?: number }",
            api_text,
            "the hand-written /events/discover envelope is back; it omitted the "
            "``platform`` key the route always sends (audit 53.59)",
        )
        self.assertIn(
            "export type EventDiscoveryResponse = GeneratedEventDiscoveryResponse;",
            api_text,
            "api.ts no longer aliases the generated /events/discover envelope",
        )

        lines = api_text.splitlines()
        idxs = [i for i, ln in enumerate(lines) if "/events/discover?limit=" in ln]
        self.assertEqual(
            len(idxs),
            1,
            "expected the single /events/discover call site in api.ts, "
            f"found {len(idxs)}",
        )
        for idx in idxs:
            generic = ""
            for back in range(idx, -1, -1):
                if "api<" in lines[back]:
                    generic = lines[back]
                    break
            with self.subTest(callsite=lines[idx].strip()[:40]):
                self.assertTrue(generic, "no api< generic found above this call site")
                self.assertIn(
                    "api<EventDiscoveryResponse>(",
                    generic,
                    "this call site bypasses the published envelope again: "
                    "api.ts must read api<EventDiscoveryResponse>",
                )


class DecisionReportContractTests(unittest.TestCase):
    """The decision-report contract (audit 53.59).

    ``GET /events/decisions/open`` published ``decisions: list[dict[str, Any]]``
    -- no element contract -- and ``lib/api.ts`` answered with a hand-written
    ``DecisionReport`` of seventeen fields. The producer
    (``decision_report_service.build_decision_report``) writes **eighteen** keys
    on every call, so that copy was not merely narrow in places: it omitted
    ``llm_telemetry`` outright, and a key was reaching the browser that the local
    type could not name. Three of the seventeen were looser than the producer
    (``recommendation.calibration_status`` is computed and never null,
    ``diagnosis.segment_min_samples`` is always written), and ``risk.flags`` was
    *narrower* than the store model's ``list[Any]`` -- the direction audit 53.58
    pinned as the dangerous one.

    The report is assembled key by key rather than spread from a row, so there is
    no "extra keys survive" story here: every key the producer writes is
    declared, which is why the model is closed and why the key set is pinned
    rather than trusted.

    Four cases: the envelope, the eighteen keys, the nine nested blocks, and the
    text half.
    """

    _KEYS = (
        "event_id",
        "event",
        "probability",
        "market_view",
        "edge",
        "diagnosis",
        "confidence",
        "recommendation",
        "risk",
        "category",
        "status",
        "actionable_recommendation",
        "decision_quality",
        "market_quality",
        "source_reliability",
        "llm_telemetry",
        "final_displayed_direction",
        "final_downgrade_reason",
    )

    def test_open_decisions_response_points_at_the_published_element(self):
        """Six required envelope keys, and ``decisions`` names ``DecisionReport``.

        Identity, not ``issubclass``: a regression to ``list[dict[str, Any]]``
        still validates, still returns the same bytes and still passes ``tsc``.
        """
        from app.models.event import DecisionReport, OpenDecisionsResponse

        fields = OpenDecisionsResponse.model_fields
        self.assertEqual(
            set(fields),
            {"count", "total", "limit", "offset", "decision_totals", "decisions"},
            "OpenDecisionsResponse is no longer the six-key envelope "
            "/events/decisions/open returns",
        )
        for name, want in (
            ("count", int),
            ("total", int),
            ("limit", int),
            ("offset", int),
            ("decision_totals", dict[str, int]),
        ):
            with self.subTest(key=name):
                self.assertEqual(
                    fields[name].annotation,
                    want,
                    f"OpenDecisionsResponse.{name} changed type",
                )
        self.assertEqual(
            fields["decisions"].annotation,
            list[DecisionReport],
            "OpenDecisionsResponse.decisions no longer names its published "
            "element, so the frontend can only hand-write a copy again",
        )
        for name in fields:
            with self.subTest(required=name):
                self.assertTrue(
                    fields[name].is_required(),
                    f"OpenDecisionsResponse.{name} gained a default. "
                    "``get_open_decisions`` writes all six keys on its single "
                    "return path, so a default only widens the envelope "
                    "(audit 53.59)",
                )

    def test_the_report_writes_all_eighteen_keys(self):
        """All eighteen are written unconditionally, so none may carry a default.

        The last six are ``record.get(...)`` reads, which is why they are
        ``X | None`` -- but the *key* is still written every time, so a default
        would widen them to ``X | undefined`` for a producer that never omits
        anything.

        Pinned by name rather than by count on purpose: ``llm_telemetry`` is the
        key the retired hand-written copy omitted, and a count would not have
        caught that omission.
        """
        from app.models.event import DecisionReport

        fields = DecisionReport.model_fields
        self.assertEqual(
            set(fields),
            set(self._KEYS),
            "DecisionReport's key set no longer matches the eighteen keys "
            "build_decision_report writes",
        )
        for name in self._KEYS:
            with self.subTest(required=name):
                self.assertTrue(
                    fields[name].is_required(),
                    f"DecisionReport.{name} gained a default; the producer "
                    "writes that key on every call",
                )

    def test_the_nested_blocks_are_named_models(self):
        """The nine nested objects are the models the producer's dicts are.

        Identity, not "a dict": every one of these was ``dict[str, Any]`` before
        this section, and a regression still validates, still returns the same
        bytes and still passes ``tsc``.

        ``risk.flags`` is pinned in the *other* direction. It stays ``list[Any]``
        because the store model declares ``Risk.flags: list[Any]`` and the
        producer only guards ``isinstance(..., list)``; declaring ``list[str]``
        would be narrowing by observation, which is exactly the mistake audit
        53.58 pinned in the opposite direction.
        """
        from typing import Any

        from app.models.event import (
            ActionableRecommendation,
            DecisionConfidence,
            DecisionDiagnosis,
            DecisionEdge,
            DecisionEventRef,
            DecisionMarketView,
            DecisionProbability,
            DecisionRecommendation,
            DecisionReport,
            DecisionRisk,
        )

        fields = DecisionReport.model_fields
        for name, want in (
            ("event", DecisionEventRef),
            ("probability", DecisionProbability),
            ("market_view", DecisionMarketView),
            ("edge", DecisionEdge),
            ("diagnosis", DecisionDiagnosis),
            ("confidence", DecisionConfidence),
            ("recommendation", DecisionRecommendation),
            ("risk", DecisionRisk),
            ("actionable_recommendation", ActionableRecommendation | None),
        ):
            with self.subTest(block=name):
                self.assertEqual(
                    fields[name].annotation,
                    want,
                    f"DecisionReport.{name} no longer names the block model the "
                    "producer assembles",
                )
        self.assertEqual(
            DecisionRisk.model_fields["flags"].annotation,
            list[Any],
            "DecisionRisk.flags is no longer list[Any]. The store model declares "
            "Risk.flags: list[Any] and the producer only guards "
            "isinstance(..., list), so list[str] narrows by observation",
        )

    def test_the_event_decision_route_binds_the_report_model(self):
        """The second consumer of ``build_decision_report`` publishes the same
        contract (audit 53.60).

        ``/decisions/open`` names its element through ``OpenDecisionsResponse``.
        ``GET /events/{event_id}/decision`` returns ``build_decision_report``'s
        dict *directly* -- there is no envelope to name -- so before 53.60 its
        ``response_model`` was the bare ``FlexibleResponse`` and OpenAPI
        published ``#/components/schemas/FlexibleResponse``: a body reaching the
        browser with no declared shape, exactly the state 53.59 ended for the
        sibling endpoint.

        Identity, not ``issubclass``: ``FlexibleResponse`` is ``extra="allow"``
        with no declared fields, so a regression to it still validates, still
        answers 200 and still passes ``tsc`` -- the only thing that changes is
        the published schema. So the intercept is the *route object* read off
        the router, not the source text: a reformatting, a renamed kwarg or a
        decorator comment cannot fool it, and the failure names the endpoint
        that regressed.

        Read off ``router.routes`` rather than ``app.routes``: the prefix is
        applied at ``include_router`` time, so inside the module the path is
        still ``/{event_id}/decision``.
        """
        from fastapi.routing import APIRoute

        from app.api.routes import events as events_routes
        from app.models.event import DecisionReport, FlexibleResponse

        routes = [
            route
            for route in events_routes.router.routes
            if isinstance(route, APIRoute) and route.path == "/{event_id}/decision"
        ]
        self.assertEqual(
            len(routes),
            1,
            "expected exactly one /{event_id}/decision route in the events router",
        )
        self.assertIs(
            routes[0].response_model,
            DecisionReport,
            "the /{event_id}/decision route no longer binds DecisionReport. It "
            "is the second consumer of build_decision_report and must publish "
            "the same contract /decisions/open publishes (audit 53.60).",
        )
        self.assertIsNot(
            routes[0].response_model,
            FlexibleResponse,
            "the /{event_id}/decision route regressed to the bare "
            "FlexibleResponse: the report reaches the browser with no declared "
            "shape again, which is the state audit 53.60 closed",
        )

    def test_the_report_callsite_reads_the_generated_contract(self):
        """No copy in ``api.ts``, and the call site reads the published envelope.

        ``_interface_bodies`` collects only ``export interface``, so the two
        ``export type`` aliases are not mistaken for declarations. The positive
        half walks *back* from the path line to the nearest ``api<`` generic.
        """
        api_text = _API_TS_PATH.read_bytes().decode("utf-8")
        api_bodies = _interface_bodies(api_text)

        for name in ("DecisionReport", "OpenDecisionsResponse"):
            with self.subTest(retired=name):
                self.assertNotIn(
                    name,
                    api_bodies,
                    f"api.ts re-declared {name}; generated-types.ts is the source "
                    "of truth. Alias the generated name instead.",
                )
        self.assertIn(
            "export type OpenDecisionsResponse = GeneratedOpenDecisionsResponse;",
            api_text,
            "api.ts no longer aliases the generated /events/decisions/open "
            "envelope",
        )

        lines = api_text.splitlines()
        idxs = [
            i for i, ln in enumerate(lines) if "/events/decisions/open?limit=" in ln
        ]
        self.assertEqual(
            len(idxs),
            1,
            "expected the single /events/decisions/open call site in api.ts, "
            f"found {len(idxs)}",
        )
        for idx in idxs:
            generic = ""
            for back in range(idx, -1, -1):
                if "api<" in lines[back]:
                    generic = lines[back]
                    break
            with self.subTest(callsite=lines[idx].strip()[:40]):
                self.assertTrue(generic, "no api< generic found above this call site")
                self.assertIn(
                    "api<OpenDecisionsResponse>(",
                    generic,
                    "this call site bypasses the published envelope again: "
                    "api.ts must read api<OpenDecisionsResponse>",
                )


class PredictionCalibrationContractTests(unittest.TestCase):
    """The two calibration GET contracts, from both ends (audit 53.61).

    ``GET /events/predictions/calibration`` and
    ``GET /events/predictions/calibration/buckets`` published bare
    ``FlexibleResponse`` envelopes -- no field contract at all -- while
    ``api.ts`` hand-wrote ``PredictionCalibration`` /
    ``CalibrationBucketSummary`` / ``CalibrationBucketCell``. The hand copy was
    *looser* than the producer in four places: ``segment_min_samples`` and the
    whole ``segments`` map claimed optional (both producers write them on
    every return path), the same for three segment statistics, and
    ``segment_min_samples`` claimed nullable inside ``segments`` although it
    is a settings int, never a column read. Naming the envelopes retires the
    copies and makes the real shape readable.

    Four cases: the summary envelope with its two element models, the buckets
    envelope with its cell, the two route bindings, and the text half in
    ``api.ts``.
    """

    def test_the_summary_envelope_and_elements(self):
        """The nine-key envelope, its two named element maps, and every field
        required.

        Both return paths of ``calibration_summary`` (the scored aggregate and
        the ``no_data`` early return) write the same nine keys, so a default
        would only widen the envelope. The element annotations are pinned per
        key because the hand copy got three of them wrong: ``brier_score`` is
        bare in ``CalibrationCategory`` (``round(None, 4)`` raises before the
        dict literal is built, so a null is out of the producer's domain),
        while the segment statistics are ``| None`` because
        ``_segment_skill_from_rows`` returns ``None`` for all four on a
        category with no resolved rows. ``qualified`` stays ``bool`` -- a
        Python comparison, not a column read; declaring ``int | None`` would
        rewrite ``True`` to ``1`` on the wire (the 53.58 direction).
        """
        from app.models.event import (
            CalibrationCategory,
            CalibrationSegment,
            PredictionCalibrationResponse,
        )

        fields = PredictionCalibrationResponse.model_fields
        self.assertEqual(
            set(fields),
            {"n", "brier_score", "grade", "mean_raw_edge", "realized_edge",
             "directional_hit_rate", "by_category", "segment_min_samples",
             "segments"},
            "PredictionCalibrationResponse is no longer the nine-key envelope "
            "both paths of calibration_summary return",
        )
        for name, want in (
            ("n", int),
            ("brier_score", float | None),
            ("grade", str),
            ("mean_raw_edge", float | None),
            ("realized_edge", float | None),
            ("directional_hit_rate", float | None),
            ("segment_min_samples", int),
        ):
            with self.subTest(key=name):
                self.assertEqual(fields[name].annotation, want)
        self.assertEqual(
            fields["by_category"].annotation,
            dict[str, CalibrationCategory],
            "by_category no longer names its element; a bare dict publishes "
            "{ [k: string]: unknown } and the frontend can only hand-write a "
            "subset again",
        )
        self.assertEqual(
            fields["segments"].annotation,
            dict[str, CalibrationSegment],
            "segments no longer names its element",
        )
        for name in fields:
            with self.subTest(required=name):
                self.assertTrue(
                    fields[name].is_required(),
                    f"PredictionCalibrationResponse.{name} gained a default. "
                    "Both return paths write all nine keys, so a default only "
                    "widens the envelope (audit 53.61)",
                )

        cat = CalibrationCategory.model_fields
        self.assertEqual(
            set(cat), {"n", "brier_score", "skill_score", "grade"},
            "CalibrationCategory is no longer the four-key element the "
            "producer writes",
        )
        for name, want in (
            ("n", int),
            ("brier_score", float),
            ("skill_score", float),
            ("grade", str),
        ):
            with self.subTest(category=name):
                self.assertEqual(cat[name].annotation, want)
        for name in cat:
            with self.subTest(category_required=name):
                self.assertTrue(cat[name].is_required())

        seg = CalibrationSegment.model_fields
        self.assertEqual(
            set(seg),
            {"n", "brier_score", "market_brier_score", "skill_score",
             "market_relative_skill", "grade", "segment_min_samples",
             "qualified"},
            "CalibrationSegment is no longer the eight-key element the "
            "producer writes",
        )
        for name, want in (
            ("n", int),
            ("brier_score", float | None),
            ("market_brier_score", float | None),
            ("skill_score", float | None),
            ("market_relative_skill", float | None),
            ("grade", str),
            ("segment_min_samples", int),
            ("qualified", bool),
        ):
            with self.subTest(segment=name):
                self.assertEqual(seg[name].annotation, want)
        for name in seg:
            with self.subTest(segment_required=name):
                self.assertTrue(seg[name].is_required())

    def test_the_buckets_envelope_and_cell(self):
        """The four-key buckets envelope and the three-key cell.

        Both return paths of ``calibration_bucket_summary`` (the grouped
        aggregate and the ``no_data`` early return) write the same four keys;
        ``no_data`` is the maps *empty*, not the keys absent. The cell's two
        statistics are ``round(...)`` over lists that may be empty, so either
        can be null while ``n`` (the group's ``len``) never can -- and the
        live corpus already holds ``null`` for ``direction_correct_rate``.
        """
        from app.models.event import (
            CalibrationBucketCell,
            CalibrationBucketSummaryResponse,
        )

        fields = CalibrationBucketSummaryResponse.model_fields
        self.assertEqual(
            set(fields),
            {"n", "by_edge_bucket", "by_confidence_bucket",
             "by_edge_x_confidence"},
            "CalibrationBucketSummaryResponse is no longer the four-key "
            "envelope both paths of calibration_bucket_summary return",
        )
        self.assertEqual(fields["n"].annotation, int)
        for name in ("by_edge_bucket", "by_confidence_bucket",
                     "by_edge_x_confidence"):
            with self.subTest(map=name):
                self.assertEqual(
                    fields[name].annotation,
                    dict[str, CalibrationBucketCell],
                    f"{name} no longer names its element",
                )
        for name in fields:
            with self.subTest(required=name):
                self.assertTrue(
                    fields[name].is_required(),
                    f"CalibrationBucketSummaryResponse.{name} gained a "
                    "default. Both return paths write all four keys (audit "
                    "53.61)",
                )

        cell = CalibrationBucketCell.model_fields
        self.assertEqual(
            set(cell), {"n", "brier_score", "direction_correct_rate"},
            "CalibrationBucketCell is no longer the three-key cell the "
            "producer writes",
        )
        for name, want in (
            ("n", int),
            ("brier_score", float | None),
            ("direction_correct_rate", float | None),
        ):
            with self.subTest(cell=name):
                self.assertEqual(cell[name].annotation, want)
        for name in cell:
            with self.subTest(cell_required=name):
                self.assertTrue(cell[name].is_required())

    def test_the_calibration_routes_bind_the_named_models(self):
        """Both routes must bind their named response models.

        Identity, not ``issubclass``: the bare ``FlexibleResponse`` is
        ``extra="allow"`` with no declared fields, so a regression to it still
        validates, still answers 200 and still passes ``tsc`` -- the only
        thing that changes is the published schema (the same criterion
        53.60 pinned for ``/{event_id}/decision``). Read off
        ``router.routes`` rather than ``app.routes``: the prefix is applied
        at ``include_router`` time, so inside the module the paths are still
        ``/predictions/calibration`` and ``/predictions/calibration/buckets``.
        """
        from fastapi.routing import APIRoute

        from app.api.routes import events as events_routes
        from app.models.event import (
            CalibrationBucketSummaryResponse,
            FlexibleResponse,
            PredictionCalibrationResponse,
        )

        by_path = {
            route.path: route
            for route in events_routes.router.routes
            if isinstance(route, APIRoute)
            and route.path in ("/predictions/calibration",
                               "/predictions/calibration/buckets")
        }
        self.assertEqual(
            sorted(by_path),
            ["/predictions/calibration", "/predictions/calibration/buckets"],
            "expected exactly one route per calibration path in the events "
            "router",
        )
        for path, model in (
            ("/predictions/calibration", PredictionCalibrationResponse),
            ("/predictions/calibration/buckets",
             CalibrationBucketSummaryResponse),
        ):
            with self.subTest(route=path):
                self.assertIs(
                    by_path[path].response_model,
                    model,
                    f"the {path} route no longer binds {model.__name__}. It "
                    "published no field contract at all before audit 53.61.",
                )
                self.assertIsNot(
                    by_path[path].response_model,
                    FlexibleResponse,
                    f"the {path} route regressed to the bare FlexibleResponse: "
                    "the report reaches the browser with no declared shape "
                    "again, which is the state audit 53.61 closed",
                )

    def test_the_calibration_callsites_read_the_generated_contract(self):
        """No copy in ``api.ts``, and each call site reads its own alias.

        ``_interface_bodies`` collects only ``export interface``, so the three
        ``export type`` aliases are not mistaken for declarations. Each
        endpoint keeps its *own* generic (the 53.57 criterion): the summary
        site must read ``api<PredictionCalibration>`` and the buckets site
        ``api<CalibrationBucketSummary>`` -- one shared generic would be too
        loose for one and a lie for the other.
        """
        api_text = _API_TS_PATH.read_bytes().decode("utf-8")
        api_bodies = _interface_bodies(api_text)

        for name in ("PredictionCalibration", "CalibrationBucketSummary",
                     "CalibrationBucketCell"):
            with self.subTest(retired=name):
                self.assertNotIn(
                    name,
                    api_bodies,
                    f"api.ts re-declared {name}; generated-types.ts is the "
                    "source of truth. Alias the generated name instead.",
                )
        for alias in (
            "export type PredictionCalibration = "
            "GeneratedPredictionCalibrationResponse;",
            "export type CalibrationBucketCell = "
            "GeneratedCalibrationBucketCell;",
            "export type CalibrationBucketSummary = "
            "GeneratedCalibrationBucketSummaryResponse;",
        ):
            self.assertIn(
                alias,
                api_text,
                f"api.ts no longer aliases the generated contract: {alias}",
            )

        lines = api_text.splitlines()
        idxs = [
            i for i, ln in enumerate(lines)
            if "/events/predictions/calibration" in ln
        ]
        self.assertEqual(
            len(idxs),
            2,
            "expected exactly the two prediction-calibration call sites in "
            f"api.ts, found {len(idxs)}",
        )
        for idx in idxs:
            line = lines[idx]
            want = (
                "api<CalibrationBucketSummary>("
                if "calibration/buckets" in line
                else "api<PredictionCalibration>("
            )
            with self.subTest(callsite=line.strip()[:52]):
                self.assertIn(
                    want,
                    line,
                    "this call site bypasses its published envelope: each "
                    "calibration endpoint must keep its own generic (audit "
                    "53.57 criterion)",
                )


class LoopStatusContractTests(unittest.TestCase):
    """The /loop/status contract, from both ends (audit 53.62).

    ``GET /events/loop/status`` published a bare ``FlexibleResponse`` while
    ``api.ts`` hand-wrote ``LoopStatus`` / ``LoopRun``. The hand copy could
    not name ``review_queue`` at all, missed ``storage.event_store_bytes`` /
    ``event_store_records`` and ``counts.pending_reviews`` /
    ``breached_reviews``, and ``LoopRun`` carried two keys (``job``,
    ``duration_seconds``) nothing on the wire ever wrote -- the dashboard's
    ``resultEntries`` fallback onto ``details`` was dead code that hid the
    real ``result`` key. The producer serves *two* shapes behind one URL
    (run details behind a write key), so the route also gains
    ``response_model_exclude_unset``: without it, ``LoopRunView``'s three
    detail-only defaults would materialise null keys on the subset path.

    Four cases: the envelope and its nested blocks, the run view, the route
    binding *and its flag*, and the text half in ``api.ts``.
    """

    def test_the_envelope_and_nested_blocks(self):
        """Seven envelope keys, every nested block's key set, all required.

        Every key the producer writes is written unconditionally (the queue
        fallback writes the same eight keys as the live path), so no field
        anywhere in the tree may carry a default except the three
        detail-only run fields pinned separately. ``calibration`` must be
        the *same* model 53.61 named -- same producer, and a second
        hand-written copy of the calibration block is exactly the drift
        this family exists to prevent.
        """
        from app.models.event import (
            LoopRunView,
            LoopSchedulerState,
            LoopStatusCounts,
            LoopStatusResponse,
            LoopStatusStorage,
            PredictionCalibrationResponse,
            ReviewQueueSeverityBucket,
            ReviewQueueSummary,
        )

        fields = LoopStatusResponse.model_fields
        self.assertEqual(
            list(fields),
            ["scheduler", "storage", "runs", "recent_runs", "counts",
             "calibration", "review_queue"],
            "LoopStatusResponse is no longer the seven-key envelope in "
            "producer order",
        )
        self.assertEqual(
            fields["calibration"].annotation,
            PredictionCalibrationResponse,
            "LoopStatusResponse.calibration no longer reuses the model "
            "53.61 named; a second copy of the calibration block is the "
            "drift this family exists to prevent",
        )
        self.assertEqual(
            fields["runs"].annotation,
            dict[str, LoopRunView],
            "runs no longer names its element; a bare dict publishes "
            "{ [k: string]: unknown } values again",
        )
        self.assertEqual(
            fields["recent_runs"].annotation,
            list[LoopRunView],
            "recent_runs no longer names its element",
        )
        for name in fields:
            with self.subTest(required=name):
                self.assertTrue(
                    fields[name].is_required(),
                    f"LoopStatusResponse.{name} gained a default; the "
                    "producer writes all seven keys (audit 53.62)",
                )

        self.assertEqual(
            list(LoopStatusStorage.model_fields),
            ["loop_db_schema_versions", "event_store_bytes",
             "event_store_records"],
            "LoopStatusStorage is no longer the three-key block (the "
            "hand-written copy could not name the two E1 scale keys)",
        )
        self.assertEqual(
            list(LoopStatusCounts.model_fields),
            ["events", "resolved_events", "open_opportunities",
             "predictions", "pending_links", "pending_reviews",
             "breached_reviews", "orphan_predictions",
             "dangling_predictions", "dangling_links", "dangling_refs",
             "dangling_by_table", "calibration_n"],
            "LoopStatusCounts is no longer the thirteen-key block (the "
            "hand-written copy omitted the two review-queue depth keys)",
        )
        self.assertEqual(
            LoopStatusCounts.model_fields["events"].annotation, int,
            "counts.events must stay a bare int; the producer's len() "
            "cannot hand back null",
        )
        self.assertEqual(
            list(LoopSchedulerState.model_fields), ["running"],
            "LoopSchedulerState is no longer the one-key block",
        )
        self.assertEqual(
            LoopSchedulerState.model_fields["running"].annotation,
            bool | None,
            "scheduler.running must stay nullable: scheduler.running is "
            "None until the scheduler thread has answered once",
        )

        rq = ReviewQueueSummary.model_fields
        self.assertEqual(
            list(rq),
            ["pending_total", "oldest_age_hours", "oldest_item_id",
             "breached_total", "unknown_severity", "sla_hours",
             "by_severity", "by_trigger"],
            "ReviewQueueSummary is no longer the eight-key block",
        )
        self.assertEqual(
            rq["oldest_age_hours"].annotation,
            int | float | None,
            "oldest_age_hours must keep int | float: the census may hand "
            "back an int age, and bare float would rewrite 3 to 3.0 on the "
            "wire",
        )
        self.assertEqual(
            list(ReviewQueueSeverityBucket.model_fields),
            ["count", "oldest_age_hours", "breached", "sla_hours"],
            "ReviewQueueSeverityBucket is no longer the four-key bucket in "
            "producer order",
        )
        self.assertEqual(
            ReviewQueueSeverityBucket.model_fields["oldest_age_hours"]
            .annotation,
            int | float | None,
            "bucket oldest_age_hours must keep int | float for the same "
            "byte-stability reason",
        )

    def test_the_run_view_carries_both_shapes(self):
        """Eight fields in row order; exactly the three detail-only fields
        carry defaults.

        Declaring the full row (``id`` first) is what keeps the details
        path's key order stable; the subset path emits only the five keys
        it writes because the route sets ``response_model_exclude_unset``.
        The split is pinned per field: a default on one of the five subset
        keys would make the generated type optional for a key the producer
        always writes; a *missing* default on one of the three detail-only
        fields would make the subset path fail validation the moment the
        flag stopped dropping them.
        """
        from typing import Any

        from app.models.event import LoopRunView

        fields = LoopRunView.model_fields
        self.assertEqual(
            list(fields),
            ["id", "job_name", "status", "started_at", "finished_at",
             "duration_ms", "error", "result"],
            "LoopRunView is no longer the eight fields in loop_runs row "
            "order",
        )
        for name in ("job_name", "status", "started_at", "finished_at",
                     "duration_ms"):
            with self.subTest(subset_required=name):
                self.assertTrue(
                    fields[name].is_required(),
                    f"LoopRunView.{name} gained a default; both run shapes "
                    "write it, so the generated type must keep it required",
                )
        for name, want in (
            ("id", str | None),
            ("error", str | None),
            ("result", dict[str, Any] | None),
        ):
            with self.subTest(detail_optional=name):
                self.assertEqual(fields[name].annotation, want)
                self.assertFalse(
                    fields[name].is_required(),
                    f"LoopRunView.{name} lost its default; the subset path "
                    "does not write it, and only a default lets the flag "
                    "omit it (audit 53.62)",
                )

    def test_the_loop_status_route_binds_the_model_and_keeps_the_flag(self):
        """The route binds LoopStatusResponse by identity and keeps
        ``response_model_exclude_unset``.

        Both criteria read the *route object* off ``router.routes``, not the
        source text: a regression to the bare ``FlexibleResponse`` still
        validates, still answers 200 and still passes ``tsc`` (53.60
        criterion), and losing the flag still answers byte-differently only
        on the subset path -- neither is visible anywhere else.
        """
        from fastapi.routing import APIRoute

        from app.api.routes import events as events_routes
        from app.models.event import FlexibleResponse, LoopStatusResponse

        routes = [
            route
            for route in events_routes.router.routes
            if isinstance(route, APIRoute) and route.path == "/loop/status"
        ]
        self.assertEqual(
            len(routes), 1,
            "expected exactly one /loop/status route in the events router",
        )
        self.assertIs(
            routes[0].response_model,
            LoopStatusResponse,
            "the /loop/status route no longer binds LoopStatusResponse. It "
            "published no field contract at all before audit 53.62.",
        )
        self.assertIsNot(
            routes[0].response_model,
            FlexibleResponse,
            "the /loop/status route regressed to the bare FlexibleResponse",
        )
        self.assertIs(
            routes[0].response_model_exclude_unset,
            True,
            "the /loop/status route lost response_model_exclude_unset: the "
            "subset path would grow three null keys (id/error/result) the "
            "producer never wrote, changing the wire bytes (audit 53.62)",
        )

    def test_the_loop_status_callsite_reads_the_generated_contract(self):
        """No hand-written copy in ``api.ts``, and the call site reads the
        alias.

        ``_interface_bodies`` collects only ``export interface``, so the two
        ``export type`` aliases are not mistaken for declarations. The
        consumer reads ``status.runs`` / ``status.recent_runs`` through the
        published model now -- a hand copy coming back is the regression.
        """
        api_text = _API_TS_PATH.read_bytes().decode("utf-8")
        api_bodies = _interface_bodies(api_text)

        for name in ("LoopStatus", "LoopRun"):
            with self.subTest(retired=name):
                self.assertNotIn(
                    name,
                    api_bodies,
                    f"api.ts re-declared {name}; generated-types.ts is the "
                    "source of truth. Alias the generated name instead.",
                )
        for alias in (
            "export type LoopRun = GeneratedLoopRunView;",
            "export type LoopStatus = GeneratedLoopStatusResponse;",
        ):
            self.assertIn(
                alias,
                api_text,
                f"api.ts no longer aliases the generated contract: {alias}",
            )

        lines = api_text.splitlines()
        idxs = [
            i for i, ln in enumerate(lines) if "/events/loop/status" in ln
        ]
        self.assertEqual(
            len(idxs), 1,
            "expected the single /events/loop/status call site in api.ts, "
            f"found {len(idxs)}",
        )
        for idx in idxs:
            with self.subTest(callsite=lines[idx].strip()[:52]):
                self.assertIn(
                    "api<LoopStatus>(",
                    lines[idx],
                    "this call site bypasses the published envelope: api.ts "
                    "must read api<LoopStatus>",
                )


class EventCalibrationContractTests(unittest.TestCase):
    """The /calibration contract, from both ends (audit 53.63).

    ``GET /events/calibration`` published a bare ``FlexibleResponse`` while
    ``api.ts`` hand-wrote ``CalibrationAgg`` / ``CategoryCoverage`` and an
    inline envelope at the call site. Unlike 53.61/53.62's copies these two
    were *faithful* -- no phantom keys, no missing keys -- so the retirement
    is a pure type change and the probe confirmed zero byte difference in
    domain. What the hand copy could never give anyone is the closure this
    family exists for: a second hand-written calibration aggregate would
    drift from 53.61's ``PredictionCalibrationResponse`` invisibly.

    Three cases: the envelope and its nested blocks, the route binding *and
    the flag's deliberate absence*, and the text half in ``api.ts``.
    """

    def test_the_envelope_and_nested_blocks(self):
        """Four envelope keys, both nested blocks' key sets, all required.

        ``summarize`` writes the same four keys on both of its return paths
        (live and no-data), and ``_aggregate``/``_empty_overall`` write the
        same four keys per block -- no key anywhere in this tree is written
        conditionally, so no field may carry a default (the 53.56 rule in
        its positive direction: a default here only widens the generated
        type to ``X | undefined`` for a producer that never omits).
        """
        from app.models.event import (
            CalibrationAggregate,
            CategoryCoverage,
            EventCalibrationResponse,
        )

        fields = EventCalibrationResponse.model_fields
        self.assertEqual(
            list(fields),
            ["overall", "by_source", "by_base_rate_category",
             "category_coverage"],
            "EventCalibrationResponse is no longer the four-key envelope in "
            "producer order",
        )
        self.assertEqual(
            fields["overall"].annotation,
            CalibrationAggregate,
            "overall no longer names its element; a bare dict publishes "
            "{ [k: string]: unknown } again",
        )
        for name in ("by_source", "by_base_rate_category"):
            self.assertEqual(
                fields[name].annotation,
                dict[str, CalibrationAggregate],
                f"{name} no longer names its element type",
            )
        self.assertEqual(
            fields["category_coverage"].annotation,
            CategoryCoverage,
            "category_coverage no longer names its element",
        )
        for name in fields:
            with self.subTest(required=name):
                self.assertTrue(
                    fields[name].is_required(),
                    f"EventCalibrationResponse.{name} gained a default; "
                    "summarize writes all four keys on both return paths "
                    "(audit 53.63)",
                )

        agg = CalibrationAggregate.model_fields
        self.assertEqual(
            list(agg),
            ["brier_score", "skill_score", "grade", "n"],
            "CalibrationAggregate is no longer the four-key block in "
            "producer order",
        )
        for name, want in (
            ("brier_score", float | None),
            ("skill_score", float | None),
            ("grade", str),
            ("n", int),
        ):
            with self.subTest(agg_field=name):
                self.assertEqual(agg[name].annotation, want)
                self.assertTrue(
                    agg[name].is_required(),
                    f"CalibrationAggregate.{name} gained a default; "
                    "_aggregate and _empty_overall write all four keys "
                    "unconditionally",
                )
        self.assertEqual(
            agg["brier_score"].annotation,
            float | None,
            "brier_score must stay nullable: with zero usable predictions "
            "the producer writes null, not 0.0",
        )
        self.assertEqual(
            agg["n"].annotation,
            int,
            "n must stay a bare int; the producer's len() cannot hand "
            "back null",
        )

        cov = CategoryCoverage.model_fields
        self.assertEqual(
            list(cov),
            ["total", "classified", "unknown", "unknown_rate"],
            "CategoryCoverage is no longer the four-key block in producer "
            "order",
        )
        for name, want in (
            ("total", int),
            ("classified", int),
            ("unknown", int),
            ("unknown_rate", float | None),
        ):
            with self.subTest(cov_field=name):
                self.assertEqual(cov[name].annotation, want)
                self.assertTrue(
                    cov[name].is_required(),
                    f"CategoryCoverage.{name} gained a default; "
                    "_category_coverage writes all four keys unconditionally",
                )
        self.assertEqual(
            cov["unknown_rate"].annotation,
            float | None,
            "unknown_rate must stay nullable: it is None when total == 0, "
            "a value the producer writes rather than a key it omits",
        )

    def test_the_calibration_route_binds_the_model_and_declares_no_flag(self):
        """The route binds EventCalibrationResponse by identity and keeps
        ``response_model_exclude_unset`` unset (False).

        Both criteria read the *route object* off ``router.routes``, not the
        source text (53.62's reasoning). The flag half is the 53.56 decision
        in its negative form: no key in this tree is written conditionally,
        so the flag has nothing to drop -- and if someone added it together
        with a default, the absent-key-vs-null distinction would silently
        enter a wire that never had it. Pinning ``is False`` catches the
        flag arriving without reading any source.
        """
        from fastapi.routing import APIRoute

        from app.api.routes import events as events_routes
        from app.models.event import (
            EventCalibrationResponse,
            FlexibleResponse,
        )

        routes = [
            route
            for route in events_routes.router.routes
            if isinstance(route, APIRoute) and route.path == "/calibration"
        ]
        self.assertEqual(
            len(routes), 1,
            "expected exactly one /calibration route in the events router",
        )
        self.assertIs(
            routes[0].response_model,
            EventCalibrationResponse,
            "the /calibration route no longer binds EventCalibrationResponse. "
            "It published no field contract at all before audit 53.63.",
        )
        self.assertIsNot(
            routes[0].response_model,
            FlexibleResponse,
            "the /calibration route regressed to the bare FlexibleResponse",
        )
        self.assertIs(
            routes[0].response_model_exclude_unset,
            False,
            "the /calibration route gained response_model_exclude_unset; "
            "every key in this tree is written unconditionally, so the flag "
            "has nothing to drop and only invites absent-vs-null drift "
            "(audit 53.63, the 53.56 rule)",
        )

    def test_the_calibration_callsite_reads_the_generated_contract(self):
        """No hand-written copy in ``api.ts``, and the call site reads the
        envelope.

        ``_interface_bodies`` collects only ``export interface``, so the two
        ``export type`` aliases are not mistaken for declarations. These two
        copies were faithful (unlike 53.61/53.62's), so their retirement is
        checked as aliases existing, not consumers changing shape.
        """
        api_text = _API_TS_PATH.read_bytes().decode("utf-8")
        api_bodies = _interface_bodies(api_text)

        for name in ("CalibrationAgg", "CategoryCoverage"):
            with self.subTest(retired=name):
                self.assertNotIn(
                    name,
                    api_bodies,
                    f"api.ts re-declared {name}; generated-types.ts is the "
                    "source of truth. Alias the generated name instead.",
                )
        for alias in (
            "export type CalibrationAgg = GeneratedCalibrationAggregate;",
            "export type CategoryCoverage = GeneratedCategoryCoverage;",
        ):
            self.assertIn(
                alias,
                api_text,
                f"api.ts no longer aliases the generated contract: {alias}",
            )

        lines = api_text.splitlines()
        idxs = [
            i for i, ln in enumerate(lines) if "/events/calibration" in ln
        ]
        self.assertEqual(
            len(idxs), 1,
            "expected the single /events/calibration call site in api.ts, "
            f"found {len(idxs)}",
        )
        for idx in idxs:
            with self.subTest(callsite=lines[idx].strip()[:52]):
                self.assertIn(
                    "api<EventCalibrationResponse>(",
                    lines[idx],
                    "this call site bypasses the published envelope: api.ts "
                    "must read api<EventCalibrationResponse>",
                )


class ApiHealthContractTests(unittest.TestCase):
    """The /api/health contract, from both ends (audit 53.64).

    ``GET /api/health`` (main.py, not the events router) published no
    response model at all while ``api.ts`` hand-wrote ``ApiHealth``: five
    named keys for the seven the producer writes -- ``scheduler_enabled``
    and ``scheduler_lock_skipped`` reached the browser unnamed -- and two
    always-written keys (``scheduler_running``, ``failed_runs``) marked
    optional. The handler's ``loop`` value is the exact dict
    ``loop_status`` returns, so the field reuses the model 53.62 named; a
    second copy of the loop envelope is the drift this family exists to
    prevent.

    The route needs ``response_model_exclude_unset`` for the same reason
    /loop/status does, with a sharper edge: healthchecks poll WITHOUT a
    write key, so the five-key run subset is the dominant path and losing
    the flag would grow three null keys on nearly every response. The 503
    degraded status is set on the Response parameter and must survive the
    model (the probe pins a 503 case).

    Three cases: the envelope and its reuse of the loop model, the route
    binding and its flag, and the text half in ``api.ts``.
    """

    def test_the_envelope_and_its_loop_reuse(self):
        """Seven envelope keys in producer order, none with a default.

        The handler builds all seven keys in one dict literal, so no field
        may carry a default. ``scheduler_running`` is ``bool | None``
        because ``scheduler.running`` is None until the scheduler thread
        has answered once -- the same value 53.62 pinned on
        ``LoopSchedulerState.running``; narrowing it to ``bool`` would 500
        that window, and an int value reaching it would be rewritten on
        the wire. ``loop`` must be the *same* model 53.62 named -- same
        producer dict.
        """
        from app.models.event import (
            ApiHealthResponse,
            LoopStatusResponse,
        )

        fields = ApiHealthResponse.model_fields
        self.assertEqual(
            list(fields),
            ["status", "version", "scheduler_running", "scheduler_enabled",
             "scheduler_lock_skipped", "failed_runs", "loop"],
            "ApiHealthResponse is no longer the seven-key envelope in "
            "producer order",
        )
        for name, want in (
            ("status", str),
            ("version", str),
            ("scheduler_running", bool | None),
            ("scheduler_enabled", bool),
            ("scheduler_lock_skipped", bool),
            ("failed_runs", list[str]),
            ("loop", LoopStatusResponse),
        ):
            with self.subTest(field=name):
                self.assertEqual(
                    fields[name].annotation, want,
                    f"ApiHealthResponse.{name} annotation drifted",
                )
                self.assertTrue(
                    fields[name].is_required(),
                    f"ApiHealthResponse.{name} gained a default; the "
                    "handler writes all seven keys in one dict literal "
                    "(audit 53.64)",
                )
        self.assertEqual(
            fields["scheduler_running"].annotation,
            bool | None,
            "scheduler_running must stay nullable: scheduler.running is "
            "None until the scheduler thread has answered once (the same "
            "value 53.62 pinned on LoopSchedulerState.running)",
        )
        self.assertIs(
            fields["loop"].annotation,
            LoopStatusResponse,
            "loop no longer reuses the model 53.62 named; a second copy "
            "of the loop envelope is the drift this family exists to "
            "prevent",
        )

    def test_the_health_route_binds_the_model_and_keeps_the_flag(self):
        """The route binds ApiHealthResponse by identity and keeps
        ``response_model_exclude_unset``.

        The route lives on the root app (main.py), not the events router,
        so the lookup reads ``app.main.app.routes``. Both criteria read
        the *route object*, not the source text. The flag is the 53.62
        reasoning with a sharper edge: the unauthenticated five-key run
        subset is the dominant path (container healthchecks poll without
        a write key), so losing the flag would grow three null keys on
        nearly every response.
        """
        from fastapi.routing import APIRoute

        from app.main import app
        from app.models.event import ApiHealthResponse, FlexibleResponse

        routes = [
            route
            for route in app.routes
            if isinstance(route, APIRoute) and route.path == "/api/health"
        ]
        self.assertEqual(
            len(routes), 1,
            "expected exactly one /api/health route on the root app",
        )
        self.assertIs(
            routes[0].response_model,
            ApiHealthResponse,
            "the /api/health route no longer binds ApiHealthResponse. It "
            "published no field contract at all before audit 53.64.",
        )
        self.assertIsNot(
            routes[0].response_model,
            FlexibleResponse,
            "the /api/health route regressed to the bare FlexibleResponse",
        )
        self.assertIs(
            routes[0].response_model_exclude_unset,
            True,
            "the /api/health route lost response_model_exclude_unset: "
            "healthchecks poll without a write key, so the subset path "
            "would grow three null keys (id/error/result) on nearly every "
            "response (audit 53.64)",
        )

    def test_the_health_callsite_reads_the_generated_contract(self):
        """No hand-written copy in ``api.ts``, and the call site reads the
        alias with its 503 tolerance intact.

        ``_interface_bodies`` collects only ``export interface``, so the
        ``export type`` alias is not mistaken for a declaration. The
        ``acceptStatuses: [503]`` on the call site is load-bearing: the
        endpoint answers 503 when degraded, and the helper must not treat
        that as a transport error.
        """
        api_text = _API_TS_PATH.read_bytes().decode("utf-8")
        api_bodies = _interface_bodies(api_text)

        self.assertNotIn(
            "ApiHealth",
            api_bodies,
            "api.ts re-declared ApiHealth; generated-types.ts is the "
            "source of truth. Alias the generated name instead.",
        )
        self.assertIn(
            "export type ApiHealth = GeneratedApiHealthResponse;",
            api_text,
            "api.ts no longer aliases the generated health contract",
        )

        lines = api_text.splitlines()
        idxs = [
            i for i, ln in enumerate(lines) if '"/health"' in ln
        ]
        self.assertEqual(
            len(idxs), 1,
            "expected the single /health call site in api.ts, "
            f"found {len(idxs)}",
        )
        for idx in idxs:
            with self.subTest(callsite=lines[idx].strip()[:52]):
                self.assertIn(
                    "api<ApiHealth>(",
                    lines[idx],
                    "this call site bypasses the published envelope: api.ts "
                    "must read api<ApiHealth>",
                )
                self.assertIn(
                    "acceptStatuses: [503]",
                    lines[idx],
                    "the /health call site lost its 503 tolerance; the "
                    "endpoint answers 503 when degraded (audit 53.64)",
                )


class SmallInlineContractTests(unittest.TestCase):
    """The five small POST contracts named in audit 53.65, from both ends.

    ``/reset``, ``/batch-sparklines``, ``/{event_id}/translate``,
    ``/translate-all`` and ``/resolve-expired`` all published bare
    ``FlexibleResponse`` while ``api.ts`` spelled the shapes inline at the
    call sites. The five inline copies were faithful, so the retirement is
    a pure type change and the probe confirmed zero byte difference across
    all five (including reset's dynamic ``cleared`` map and translate's
    three return paths).

    ``reset.cleared`` is the 53.53 inline-tail lesson applied on naming:
    the map is dynamic-key by design, so the *element type* is the
    contract -- ``dict[str, int | str]``, not ``dict[str, Any]``.

    None of the five carries a conditional key (translate-event's three
    return paths write the same three keys), so per the 53.56 rule none of
    the routes may set ``response_model_exclude_unset`` -- pinned as
    ``is False`` per route.

    Three cases: the five envelopes, the five route bindings and their
    flag absence, and the five call sites in ``api.ts``.
    """

    def test_the_five_envelopes(self):
        """Key sets in producer order, annotations pinned, all required.

        ``sparklines`` values are ``list[float]`` because the producer runs
        every snapshot through ``float(...)``; ``cleared`` values are
        ``int | str`` because the map mixes row counts (int) with paths,
        CSVs and markers (str). All five envelopes write every key on
        every return path, so no field anywhere may carry a default.
        """
        from app.models.event import (
            ResetEventDataResponse,
            ResolveExpiredResponse,
            SparklinesResponse,
            TranslateAllResponse,
            TranslateEventResponse,
        )

        cases = [
            (SparklinesResponse, ["sparklines"],
             {"sparklines": dict[str, list[float]]}),
            (ResolveExpiredResponse,
             ["total", "resolved", "archived", "parsed_dates", "message"],
             {"total": int, "resolved": int, "archived": int,
              "parsed_dates": int, "message": str}),
            (TranslateAllResponse, ["total", "translated", "message"],
             {"total": int, "translated": int, "message": str}),
            (TranslateEventResponse,
             ["event_id", "event_title_zh", "message"],
             {"event_id": str, "event_title_zh": str, "message": str}),
            (ResetEventDataResponse, ["message", "cleared"],
             {"message": str, "cleared": dict[str, int | str]}),
        ]
        for model, keys, annotations in cases:
            fields = model.model_fields
            self.assertEqual(
                list(fields), keys,
                f"{model.__name__} is no longer its producer key order",
            )
            for name, want in annotations.items():
                with self.subTest(model=model.__name__, field=name):
                    self.assertEqual(
                        fields[name].annotation, want,
                        f"{model.__name__}.{name} annotation drifted",
                    )
                    self.assertTrue(
                        fields[name].is_required(),
                        f"{model.__name__}.{name} gained a default; the "
                        "producer writes every key unconditionally "
                        "(audit 53.65)",
                    )

    def test_the_five_routes_bind_their_models_and_declare_no_flag(self):
        """Each route binds its model by identity and keeps no flag.

        Every key in all five trees is written unconditionally, so the
        flag has nothing to drop (53.56); pinning ``is False`` per route
        catches a flag arriving silently. All criteria read the *route
        objects* off ``router.routes``, not the source text.
        """
        from fastapi.routing import APIRoute

        from app.api.routes import events as events_routes
        from app.models.event import (
            FlexibleResponse,
            ResetEventDataResponse,
            ResolveExpiredResponse,
            SparklinesResponse,
            TranslateAllResponse,
            TranslateEventResponse,
        )

        by_path = {
            "/reset": ResetEventDataResponse,
            "/batch-sparklines": SparklinesResponse,
            "/{event_id}/translate": TranslateEventResponse,
            "/translate-all": TranslateAllResponse,
            "/resolve-expired": ResolveExpiredResponse,
        }
        routes = {
            route.path: route
            for route in events_routes.router.routes
            if isinstance(route, APIRoute) and route.path in by_path
        }
        self.assertEqual(
            sorted(routes), sorted(by_path),
            "expected exactly one events-router route per named path",
        )
        for path, model in by_path.items():
            route = routes[path]
            with self.subTest(route=path):
                self.assertIs(
                    route.response_model, model,
                    f"the {path} route no longer binds {model.__name__}. "
                    "It published no field contract before audit 53.65.",
                )
                self.assertIsNot(
                    route.response_model, FlexibleResponse,
                    f"the {path} route regressed to the bare "
                    "FlexibleResponse",
                )
                self.assertIs(
                    route.response_model_exclude_unset, False,
                    f"the {path} route gained response_model_exclude_unset; "
                    "every key in this tree is written unconditionally, so "
                    "the flag has nothing to drop (audit 53.65, the 53.56 "
                    "rule)",
                )

    def test_the_five_callsites_read_the_generated_contracts(self):
        """No inline shape left at the five call sites in ``api.ts``.

        Each call site must read the named envelope; the check is textual
        because ``api<T>`` is a cast that asserts nothing. Each needle
        includes the URL so it is unique in the file.
        """
        api_text = _API_TS_PATH.read_bytes().decode("utf-8")

        for needle in (
            'api<SparklinesResponse>("/events/batch-sparklines"',
            'api<ResolveExpiredResponse>("/events/resolve-expired"',
            "api<TranslateAllResponse>(`/events/translate-all?force=",
            "api<TranslateEventResponse>(\r\n"
            "      `/events/${encodeURIComponent(id)}/translate?force=",
            'api<ResetEventDataResponse>(\r\n      "/events/reset",',
        ):
            with self.subTest(needle=needle[:52]):
                self.assertIn(
                    needle, api_text,
                    "this call site no longer reads the named envelope: "
                    "api.ts must keep the generated generic",
                )
        for inline in (
            "api<{ sparklines: Record<string, number[]> }>",
            "api<{ total: number; resolved: number;",
            "api<{ total: number; translated: number;",
            "api<{ event_id: string; event_title_zh: string;",
            "api<{ message: string; cleared: Record<string,",
        ):
            with self.subTest(inline=inline[:52]):
                self.assertNotIn(
                    inline, api_text,
                    "an inline response shape came back at a call site "
                    "this family named; read the generated contract "
                    "instead (audit 53.65)",
                )


class DeleteEventContractTests(unittest.TestCase):
    """The DELETE /{event_id} contract named in audit 53.66, both ends.

    ``DELETE /api/events/{event_id}`` published bare ``FlexibleResponse``
    while ``api.ts`` spelled its four keys inline at the call site. The
    inline copy was faithful, so the retirement is a pure type change:
    the probe confirmed zero byte difference on both branches (200: 108B,
    404: 28B). CONTROL is unreachable by premise -- the handler builds
    all four keys by hand (no key to drop) and ``refs_for_event``
    ``int()``-coerces every count before ``sum()`` (no rewritten value
    survives); the premise is "the producer does not pass a raw dict
    through to the model" -- if that changes, the two CONTROL modes come
    back (audit 53.65, the same premise-carrying form).

    No conditional keys, so per the 53.56 rule the route must not set
    ``response_model_exclude_unset`` -- pinned ``is False``. ``GET
    /{event_id}`` shares the path, so the route criterion filters on the
    DELETE method, not the path alone.

    Three cases: the envelope, the route binding, and the call site in
    ``api.ts``.
    """

    def test_the_envelope(self):
        """Key set in producer order, annotations pinned, all required.

        ``stranded_refs`` values are ``int`` all the way down:
        ``refs_for_event`` runs every count through ``int(...)`` and
        degrades to ``{}``, so unlike reset's ``cleared`` there is no
        ``str`` member in the union. All four keys are written on the
        single return path, so no field may carry a default.
        """
        from app.models.event import DeleteEventResponse

        fields = DeleteEventResponse.model_fields
        self.assertEqual(
            list(fields),
            ["event_id", "message", "stranded_refs", "stranded_total"],
            "DeleteEventResponse is no longer its producer key order",
        )
        want_types = {
            "event_id": str,
            "message": str,
            "stranded_refs": dict[str, int],
            "stranded_total": int,
        }
        for name, want in want_types.items():
            with self.subTest(field=name):
                self.assertEqual(
                    fields[name].annotation, want,
                    f"DeleteEventResponse.{name} annotation drifted",
                )
                self.assertTrue(
                    fields[name].is_required(),
                    f"DeleteEventResponse.{name} gained a default; the "
                    "producer writes every key unconditionally "
                    "(audit 53.66)",
                )

    def test_the_route_binds_its_model_and_declares_no_flag(self):
        """The DELETE route binds its model by identity and keeps no flag.

        The criterion reads the *route object* off ``router.routes`` and
        must pick the DELETE member of the ``/{event_id}`` path -- the
        GET route with the same path template must not shadow it.
        """
        from fastapi.routing import APIRoute

        from app.api.routes import events as events_routes
        from app.models.event import (
            DeleteEventResponse,
            FlexibleResponse,
        )

        candidates = [
            route
            for route in events_routes.router.routes
            if isinstance(route, APIRoute)
            and route.path == "/{event_id}"
            and "DELETE" in route.methods
        ]
        self.assertEqual(
            len(candidates), 1,
            "expected exactly one DELETE route on /{event_id}",
        )
        route = candidates[0]
        self.assertIs(
            route.response_model, DeleteEventResponse,
            "the DELETE /{event_id} route no longer binds "
            "DeleteEventResponse. It published no field contract before "
            "audit 53.66.",
        )
        self.assertIsNot(
            route.response_model, FlexibleResponse,
            "the DELETE /{event_id} route regressed to the bare "
            "FlexibleResponse",
        )
        self.assertIs(
            route.response_model_exclude_unset, False,
            "the DELETE /{event_id} route gained "
            "response_model_exclude_unset; every key in this tree is "
            "written unconditionally, so the flag has nothing to drop "
            "(audit 53.66, the 53.56 rule)",
        )

    def test_the_callsite_reads_the_generated_contract(self):
        """The ``api.ts`` call site must read the named envelope.

        Textual because ``api<T>`` is a cast that asserts nothing. The
        needle spans two lines and api.ts is a CRLF file, so the needle
        carries ``\\r\\n`` (the 53.65 lesson: the needle's line endings
        match the target file, not the script).
        """
        api_text = _API_TS_PATH.read_bytes().decode("utf-8")

        self.assertIn(
            "api<DeleteEventResponse>(\r\n"
            "      `/events/${encodeURIComponent(id)}`,",
            api_text,
            "the DELETE call site no longer reads the named envelope: "
            "api.ts must keep the generated generic",
        )
        self.assertNotIn(
            "api<{ event_id: string; message: string; stranded_refs:",
            api_text,
            "the inline response shape came back at the DELETE call "
            "site; read the generated contract instead (audit 53.66)",
        )


class FlexibleResponseIdentityTests(unittest.TestCase):
    """Every route publishing a FlexibleResponse binds *the* class.

    Audit 53.68 found a second definition: ``world_cup_predictions.py``
    declared its own ``class FlexibleResponse`` (same name, same shape,
    **different object**), so a census keyed on ``route.response_model is
    FlexibleResponse`` silently missed those 18 routes -- it counted 45 of
    63. Audit 53.69 (L0) deleted the duplicate; the module now imports the
    canonical class from ``app.models.event``.

    The criterion is *identity across the whole app*, deliberately
    name-agnostic about which module a copy might live in: whatever a
    route binds, if it is named ``FlexibleResponse`` it must be the
    canonical object. An unused duplicate is dead code (not a contract
    regression), which is why the criterion is the binding rather than a
    repo-wide definition count.
    """

    def test_every_flexible_response_route_binds_the_canonical_class(self):
        from app.main import app
        from app.models.event import FlexibleResponse

        named = [
            route for route in app.routes
            if getattr(getattr(route, "response_model", None), "__name__", "")
            == "FlexibleResponse"
        ]
        self.assertGreater(
            len(named), 50,
            "the census baseline is 63 routes publishing a class named "
            "FlexibleResponse; a sharp drop means this walk stopped "
            "seeing them (audit 53.69)",
        )
        wrong = [
            f"{route.path} -> "
            f"{route.response_model.__module__}."
            f"{getattr(route.response_model, '__qualname__', '?')}"
            for route in named
            if route.response_model is not FlexibleResponse
        ]
        self.assertEqual(
            wrong, [],
            "these routes bind a FlexibleResponse that is not the "
            "canonical export from app.models.event -- a same-name local "
            "copy is the regression audit 53.68 found, and it makes every "
            "identity-keyed census and guard go blind: " + "; ".join(wrong),
        )


class WorldCupBatchCContractTests(unittest.TestCase):
    """The two batch-C world-cup-predictions contracts named in audit 53.71.

    ``POST /sync-fixtures`` and ``GET /engine-comparison`` published bare
    ``FlexibleResponse`` while the client read them untyped. Both producers
    were read before naming. ``sync_world_cup_fixtures`` writes all nine
    success keys unconditionally and its two ``status == "error"`` returns
    become HTTP 500 in the route, so nothing is optional and the route keeps
    no flag. ``calculate_engine_accuracy`` omits ``message`` on its
    success-with-data path, so that field carries a default and the route
    must set ``response_model_exclude_unset=True``. The byte probe
    (``probe_5371_bytes.py``) measured the bare-vs-named FastAPI bodies as
    identical on every shape (184 / 64 / 70 / 449 bytes) and the flag as
    load-bearing -- without it ``message`` materialises as ``null``.

    ``predictions`` is declared even though no component reads it: a closed
    model drops undeclared keys silently, so omitting it would change the
    response bytes.
    """

    def test_the_sync_fixtures_envelope(self):
        from app.models.event import SyncFixturesResponse

        fields = SyncFixturesResponse.model_fields
        self.assertEqual(
            list(fields),
            [
                "status",
                "source",
                "fixtures_synced",
                "fixtures_fetched",
                "fixtures_parsed",
                "created",
                "updated",
                "skipped",
                "remaining_matches",
                "season",
            ],
            "SyncFixturesResponse is no longer its producer key order",
        )
        want_types = {
            "status": str,
            "source": str,
            "fixtures_synced": int,
            "fixtures_fetched": int,
            "fixtures_parsed": int,
            "created": int,
            "updated": int,
            "skipped": int,
            "remaining_matches": int,
            "season": str,
        }
        for name, want in want_types.items():
            with self.subTest(field=name):
                self.assertEqual(
                    fields[name].annotation,
                    want,
                    f"SyncFixturesResponse.{name} annotation drifted",
                )
                self.assertTrue(
                    fields[name].is_required(),
                    f"SyncFixturesResponse.{name} gained a default; the "
                    "producer writes every key unconditionally (audit 53.71)",
                )

    def test_the_engine_comparison_envelope(self):
        """The envelope and the whole nested chain it publishes."""
        from app.models.event import (
            EngineComparisonPrediction,
            EngineComparisonResponse,
            EngineComparisonStat,
            ScorePair,
        )

        pair = ScorePair.model_fields
        self.assertEqual(
            list(pair), ["home", "away"],
            "ScorePair is no longer the producer's {home, away} pair",
        )
        for name in ("home", "away"):
            with self.subTest(score_pair=name):
                self.assertEqual(pair[name].annotation, int)
                self.assertTrue(pair[name].is_required())

        pred = EngineComparisonPrediction.model_fields
        self.assertEqual(
            list(pred),
            [
                "match_id",
                "home_team",
                "away_team",
                "predicted_score",
                "actual_score",
                "score_error",
                "outcome_correct",
                "confidence",
                "outcome_probability",
            ],
            "EngineComparisonPrediction is no longer its producer key order",
        )
        want_pred = {
            "match_id": str,
            "home_team": str,
            "away_team": str,
            "predicted_score": ScorePair,
            "actual_score": ScorePair,
            "score_error": int,
            "outcome_correct": bool,
            "confidence": float,
            "outcome_probability": float,
        }
        for name, want in want_pred.items():
            with self.subTest(prediction_field=name):
                self.assertEqual(pred[name].annotation, want)
                self.assertTrue(pred[name].is_required())

        stat = EngineComparisonStat.model_fields
        self.assertEqual(
            list(stat),
            [
                "total_matches",
                "exact_score_rate",
                "outcome_accuracy",
                "goal_diff_accuracy",
                "avg_score_error",
                "predictions",
            ],
            "EngineComparisonStat is no longer its producer key order",
        )
        for name in stat:
            with self.subTest(stat_field=name):
                self.assertTrue(
                    stat[name].is_required(),
                    f"EngineComparisonStat.{name} gained a default; the "
                    "producer writes all six keys unconditionally",
                )

        env = EngineComparisonResponse.model_fields
        self.assertEqual(list(env), ["status", "message", "engines"])
        self.assertEqual(env["status"].annotation, str)
        self.assertTrue(env["status"].is_required())
        self.assertEqual(env["message"].annotation, str | None)
        self.assertFalse(
            env["message"].is_required(),
            "EngineComparisonResponse.message must keep its default: the "
            "success-with-data producer path omits the key, and with "
            "response_model_exclude_unset the default is what keeps it off "
            "the wire (audit 53.71)",
        )
        self.assertEqual(env["engines"].annotation, dict[str, EngineComparisonStat])
        self.assertTrue(env["engines"].is_required())

    def test_the_routes_bind_their_models_and_the_flag(self):
        """Route identity, plus the flag each route must (not) carry.

        The criterion reads the route *objects* off ``router.routes`` and
        filters on the full path (this router carries its own
        ``prefix="/world-cup/predictions"``, so ``route.path`` is prefixed)
        plus method, not path alone.
        """
        from fastapi.routing import APIRoute

        from app.api.routes import world_cup_predictions as wc
        from app.models.event import (
            EngineComparisonResponse,
            FlexibleResponse,
            SyncFixturesResponse,
        )

        def route_for(path: str, method: str) -> APIRoute:
            found = [
                route
                for route in wc.router.routes
                if isinstance(route, APIRoute)
                and route.path == path
                and method in route.methods
            ]
            self.assertEqual(
                len(found), 1, f"expected exactly one {method} route on {path}"
            )
            return found[0]

        sync = route_for("/world-cup/predictions/sync-fixtures", "POST")
        self.assertIs(
            sync.response_model,
            SyncFixturesResponse,
            "POST /sync-fixtures no longer binds SyncFixturesResponse; it "
            "published no field contract before audit 53.71",
        )
        self.assertIsNot(sync.response_model, FlexibleResponse)
        self.assertIs(
            sync.response_model_exclude_unset,
            False,
            "POST /sync-fixtures gained response_model_exclude_unset; every "
            "key is written unconditionally, so the flag has nothing to drop "
            "(audit 53.71, the 53.56 rule)",
        )

        comparison = route_for("/world-cup/predictions/engine-comparison", "GET")
        self.assertIs(
            comparison.response_model,
            EngineComparisonResponse,
            "GET /engine-comparison no longer binds EngineComparisonResponse",
        )
        self.assertIsNot(comparison.response_model, FlexibleResponse)
        self.assertIs(
            comparison.response_model_exclude_unset,
            True,
            "GET /engine-comparison lost response_model_exclude_unset; its "
            "success-with-data path omits `message`, so without the flag the "
            "default materialises a `message: null` key the producer never "
            "wrote (audit 53.71)",
        )

    def test_the_client_reads_the_generated_contract(self):
        """The world-cup client must not re-declare generated shapes.

        Textual: the envelope is read through the generated generic, and the
        two hand-written duplicates (``PredictedScore``,
        ``EngineComparisonStat``) are gone -- replaced by re-exports of the
        generated types rather than second copies.
        """
        text = (
            _REPO_ROOT / "frontend" / "src" / "lib" / "world-cup"
            / "predictions-api.ts"
        ).read_bytes().decode("utf-8")

        self.assertNotIn(
            "export interface PredictedScore",
            text,
            "predictions-api.ts re-declared PredictedScore; the backend "
            "publishes that shape as ScorePair (audit 53.71)",
        )
        self.assertNotIn(
            "export interface EngineComparisonStat",
            text,
            "predictions-api.ts re-declared EngineComparisonStat; read the "
            "generated contract instead (audit 53.71)",
        )
        self.assertIn(
            "export type { EngineComparisonStat, PredictedScore };",
            text,
            "the old names must stay exported as re-exports of the generated "
            "types so existing consumers keep compiling",
        )
        self.assertIn(
            'export type EngineComparisonData = EngineComparisonResponse["engines"];',
            text,
            "EngineComparisonData must be derived from the generated "
            "envelope, not re-declared",
        )
        self.assertIn("as EngineComparisonResponse", text)
        self.assertIn("Promise<SyncFixturesResponse>", text)


class WorldCupCalibrationContractTests(unittest.TestCase):
    """The two calibration routes named in audit 53.71 (batch A, first slice).

    ``GET /calibration/{engine_name}`` and
    ``GET /calibration-patterns/{engine_name}`` published the bare
    ``FlexibleResponse`` envelope. Their producers return *disjoint* shapes --
    two and three respectively -- so every field but ``status`` is optional and
    **both** routes must set ``response_model_exclude_unset=True``; without the
    flag the model defaults would serialise ``null`` keys the producer never
    wrote.

    ``CalibrationParams`` closes a namespace the database owns:
    ``engine_calibration.calibration_params`` is a generic JSON column. A
    read-only census of the live database found 29 rows and exactly one
    distinct key set -- these six float keys, in this order -- which is what the
    writer (``calculate_optimization_patterns``) produces and the reader
    (``apply_calibration_to_prediction``) reads back. The DDL comment above the
    column documents a *different* example key set (``home_advantage_bias`` /
    ``draw_probability_shift`` / ...) that no code reads or writes, so it is
    stale documentation rather than a second live shape. "Not observed" is not
    "impossible": ``save_engine_calibration`` accepts a ``dict[str, Any]``, so a
    foreign row could carry other keys and this model would drop them. That
    premise is recorded in the 53.71 audit section.
    """

    def test_the_calibration_envelope_and_its_detail(self):
        from app.models.event import (
            CalibrationDetail,
            CalibrationParams,
            EngineCalibrationResponse,
            FlexibleResponse,
        )

        params = CalibrationParams.model_fields
        self.assertEqual(
            list(params),
            [
                "home_score_bias",
                "away_score_bias",
                "home_win_prob_shift",
                "draw_prob_shift",
                "away_win_prob_shift",
                "confidence_multiplier",
            ],
            "CalibrationParams is no longer the six keys the writer emits and "
            "the reader consumes (audit 53.71)",
        )
        for name, field in params.items():
            with self.subTest(params=name):
                self.assertEqual(
                    field.annotation, float,
                    f"CalibrationParams.{name} is no longer a float",
                )
                self.assertTrue(
                    field.is_required(),
                    f"CalibrationParams.{name} gained a default; "
                    "calculate_optimization_patterns rounds every one",
                )

        detail = CalibrationDetail.model_fields
        self.assertEqual(
            list(detail),
            [
                "engine",
                "version",
                "params",
                "based_on_matches",
                "avg_improvement",
                "is_verified",
                "created_at",
            ],
            "CalibrationDetail is no longer get_active_calibration's key order",
        )
        detail_types = {
            "engine": str,
            "version": int,
            "params": CalibrationParams,
            "based_on_matches": int,
            "avg_improvement": float | None,
            "is_verified": bool,
            "created_at": str,
        }
        for name, want in detail_types.items():
            with self.subTest(detail=name):
                self.assertEqual(
                    detail[name].annotation, want,
                    f"CalibrationDetail.{name} annotation drifted",
                )
                self.assertTrue(
                    detail[name].is_required(),
                    f"CalibrationDetail.{name} gained a default; "
                    "get_active_calibration writes all seven keys every time",
                )

        envelope = EngineCalibrationResponse.model_fields
        self.assertEqual(
            list(envelope),
            ["status", "engine", "message", "calibration"],
            "EngineCalibrationResponse is no longer the union of its two "
            "producer shapes (audit 53.71)",
        )
        self.assertEqual(envelope["status"].annotation, str)
        self.assertTrue(envelope["status"].is_required())
        optional = {
            "engine": str | None,
            "message": str | None,
            "calibration": CalibrationDetail | None,
        }
        for name, want in optional.items():
            with self.subTest(envelope=name):
                self.assertEqual(envelope[name].annotation, want)
                self.assertFalse(
                    envelope[name].is_required(),
                    f"EngineCalibrationResponse.{name} lost its default; the "
                    "other shape never writes it, so the flag can no longer "
                    "keep it out of the body (audit 53.71)",
                )
        self.assertTrue(
            issubclass(EngineCalibrationResponse, FlexibleResponse),
            "EngineCalibrationResponse must stay on FlexibleResponse so "
            "undeclared keys still pass through",
        )

    def test_the_calibration_patterns_envelope(self):
        from app.models.event import (
            CalibrationAnalysis,
            CalibrationParams,
            CalibrationPatternsResponse,
            FlexibleResponse,
        )

        analysis = CalibrationAnalysis.model_fields
        self.assertEqual(
            list(analysis),
            [
                "avg_home_score_adjustment",
                "avg_away_score_adjustment",
                "avg_home_win_prob_adjustment",
                "avg_draw_prob_adjustment",
                "avg_away_win_prob_adjustment",
                "avg_confidence_adjustment",
            ],
            "CalibrationAnalysis is no longer its producer's key order",
        )
        for name, field in analysis.items():
            with self.subTest(analysis=name):
                self.assertEqual(field.annotation, float)
                self.assertTrue(field.is_required())

        envelope = CalibrationPatternsResponse.model_fields
        self.assertEqual(
            list(envelope),
            [
                "status",
                "engine",
                "samples",
                "unverified_samples",
                "avg_improvement",
                "calibration_params",
                "top_blind_spots",
                "top_calibration_issues",
                "analysis",
                "message",
            ],
            "CalibrationPatternsResponse must keep the `ok` shape's key order "
            "with `message` last: that is also the order of the `no_data` / "
            "`unverified` shapes, which is what keeps the serialised bytes of "
            "all three paths unchanged (audit 53.71)",
        )
        self.assertTrue(envelope["status"].is_required())
        for name in list(envelope)[1:]:
            with self.subTest(envelope=name):
                self.assertFalse(
                    envelope[name].is_required(),
                    f"CalibrationPatternsResponse.{name} lost its default; "
                    "only the `ok` shape writes it, so the flag must be able "
                    "to drop it (audit 53.71)",
                )
        self.assertEqual(envelope["calibration_params"].annotation,
                         CalibrationParams | None)
        self.assertEqual(envelope["analysis"].annotation,
                         CalibrationAnalysis | None)
        self.assertTrue(
            issubclass(CalibrationPatternsResponse, FlexibleResponse),
        )

        generated = _OUTPUT_PATH.read_bytes().decode("utf-8")
        self.assertIn(
            "top_blind_spots?: [unknown, unknown][] | null;",
            generated,
            "json2ts stopped rendering the [label, count] pair as a "
            "two-element array. If that is a deliberate improvement (e.g. "
            "[string, number][]), update this assertion in the same commit; "
            "if it degraded to unknown[] the console's arity assumption is "
            "gone (audit 53.71)",
        )

    def test_the_routes_bind_their_models_and_the_flag(self):
        """Route identity, plus the flag both calibration routes must carry.

        Filtered on the full path plus method: this router carries
        ``prefix="/world-cup/predictions"``, so ``route.path`` is already
        prefixed and a bare template would match nothing (audit 53.71 batch C).
        """
        from fastapi.routing import APIRoute

        from app.api.routes import world_cup_predictions as wc
        from app.models.event import (
            CalibrationPatternsResponse,
            EngineCalibrationResponse,
            FlexibleResponse,
        )

        def route_for(path: str, method: str) -> APIRoute:
            found = [
                route
                for route in wc.router.routes
                if isinstance(route, APIRoute)
                and route.path == path
                and method in route.methods
            ]
            self.assertEqual(
                len(found), 1, f"expected exactly one {method} route on {path}"
            )
            return found[0]

        for path, model in (
            ("/world-cup/predictions/calibration/{engine_name}",
             EngineCalibrationResponse),
            ("/world-cup/predictions/calibration-patterns/{engine_name}",
             CalibrationPatternsResponse),
        ):
            with self.subTest(path=path):
                route = route_for(path, "GET")
                self.assertIs(
                    route.response_model, model,
                    f"GET {path} no longer binds {model.__name__}; it "
                    "published no field contract before audit 53.71",
                )
                self.assertIsNot(route.response_model, FlexibleResponse)
                self.assertIs(
                    route.response_model_exclude_unset,
                    True,
                    f"GET {path} lost response_model_exclude_unset; its "
                    "producer returns disjoint shapes, so without the flag the "
                    "defaults serialise null keys it never wrote (audit 53.71)",
                )

    def test_the_client_reads_the_generated_contract(self):
        """The engine client must not re-declare generated shapes -- and the
        console must read the keys the producer actually sends.

        The second half is the point: the retired ``CalibrationPatterns``
        declared ``sample_count`` / ``patterns`` / ``suggestions``, none of
        which ``calculate_optimization_patterns`` has ever sent, so
        ``patterns.patterns || patterns.suggestions`` was permanently false and
        the panel could only ever render its fallback text.
        """
        client = (
            _REPO_ROOT / "frontend" / "src" / "lib" / "world-cup"
            / "engine-api.ts"
        ).read_bytes().decode("utf-8")
        console = (
            _REPO_ROOT / "frontend" / "src" / "components" / "sports"
            / "world-cup" / "engine-console.tsx"
        ).read_bytes().decode("utf-8")

        self.assertNotIn(
            "export interface EngineCalibration", client,
            "engine-api.ts re-declared EngineCalibration; read the generated "
            "EngineCalibrationResponse instead (audit 53.71)",
        )
        self.assertNotIn(
            "export interface CalibrationPatterns", client,
            "engine-api.ts re-declared CalibrationPatterns; that copy named "
            "keys the producer never sends (audit 53.71)",
        )
        self.assertIn(
            "export type { CalibrationPatternsResponse, EngineCalibrationResponse };",
            client,
            "the generated calibration contracts must be re-exported from the "
            "client module so consumers keep one import path",
        )
        self.assertIn("Promise<EngineCalibrationResponse>", client)
        self.assertIn("Promise<CalibrationPatternsResponse>", client)

        self.assertNotIn(
            ".suggestions", console,
            "the console still reads `suggestions`, which no producer writes",
        )
        self.assertNotIn(
            "patterns.patterns", console,
            "the console is back to reading `patterns.patterns`, a key the "
            "producer never sends (audit 53.71)",
        )
        self.assertIn("patterns.calibration_params", console)
        self.assertIn("patterns.top_blind_spots", console)


class WorldCupAutoTuneContractTests(unittest.TestCase):
    """The two auto-tune routes named in audit 53.72 (batch A, second slice).

    ``POST /auto-tune/{engine_name}`` and ``GET /auto-tune/status/{task_id}``
    published the bare ``FlexibleResponse`` envelope. They need opposite
    treatment: the POST has five disjoint shapes across its two branches, so
    every field but ``status`` is optional and it **must** set
    ``response_model_exclude_unset=True``; the GET writes both of its keys on
    the only non-error path, so nothing is optional and it must **not** set the
    flag.

    ``AutoTuneTask.result`` stays an opaque map on purpose. Two producers share
    one process-wide task manager (``engine_auto_tuning_async`` stores the
    auto-tune summary, ``sport_optimization`` stores ``{"sports": ...}``), and
    this route accepts any ``task_id`` -- closing the field would silently drop
    the other producer's keys (audit 53.58).
    """

    def test_the_auto_tune_task_view_is_the_keys_to_dict_writes(self):
        from app.models.event import (
            AutoTuneLogEntry,
            AutoTuneStatusResponse,
            AutoTuneTask,
        )

        fields = AutoTuneTask.model_fields
        self.assertEqual(
            list(fields),
            [
                "task_id",
                "engine_name",
                "status",
                "progress",
                "total",
                "current_match",
                "result",
                "error",
                "created_at",
                "started_at",
                "completed_at",
                "logs",
            ],
            "AutoTuneTask is no longer OptimizationTask.to_dict()'s key order "
            "(audit 53.72)",
        )
        for name, field in fields.items():
            with self.subTest(field=name):
                self.assertTrue(
                    field.is_required(),
                    f"AutoTuneTask.{name} gained a default; to_dict() writes "
                    "all twelve keys unconditionally, so a default could only "
                    "hide a regression (audit 53.72)",
                )
        types = {
            "task_id": str,
            "engine_name": str,
            "status": str,
            "progress": int,
            "total": int,
            "current_match": str | None,
            "result": dict[str, typing.Any] | None,
            "error": str | None,
            "created_at": str | None,
            "started_at": str | None,
            "completed_at": str | None,
            "logs": list[AutoTuneLogEntry],
        }
        for name, want in types.items():
            with self.subTest(annotation=name):
                self.assertEqual(
                    fields[name].annotation, want,
                    f"AutoTuneTask.{name} annotation drifted (audit 53.72)",
                )

        # The opaque `result` is the whole point: two producers, one of them
        # open, feeding one shared manager. Pin both producers so the
        # docstring's premise cannot rot silently.
        async_src = (
            _BACKEND_DIR / "app" / "services" / "engine_auto_tuning_async.py"
        ).read_bytes().decode("utf-8")
        sport_src = (
            _BACKEND_DIR / "app" / "api" / "routes" / "sport_optimization.py"
        ).read_bytes().decode("utf-8")
        self.assertIn('"total_processed"', async_src)
        self.assertIn("mark_completed(task_id, final_result)", async_src)
        self.assertIn('mark_completed(task.task_id, {"sports": results})', sport_src)
        self.assertNotIn(
            "total_processed", fields,
            "AutoTuneTask grew a total_processed key; that belongs to the async "
            "summary inside the opaque `result`, not to the task view",
        )

        log_fields = AutoTuneLogEntry.model_fields
        self.assertEqual(list(log_fields), ["timestamp", "message"])
        for name, field in log_fields.items():
            with self.subTest(log=name):
                self.assertEqual(field.annotation, str)
                self.assertTrue(field.is_required())

        status_fields = AutoTuneStatusResponse.model_fields
        self.assertEqual(
            list(status_fields), ["status", "task"],
            "AutoTuneStatusResponse is no longer {status, task}",
        )
        self.assertEqual(status_fields["task"].annotation, AutoTuneTask)

    def test_the_auto_tune_envelope_keeps_every_shape_byte_for_byte(self):
        import json

        from app.models.event import (
            AutoTuneCalibrationResult,
            AutoTuneOptimizationSummary,
            AutoTuneResponse,
            CalibrationAnalysis,
            CalibrationParams,
            FlexibleResponse,
        )

        summary = AutoTuneOptimizationSummary.model_fields
        self.assertEqual(
            list(summary),
            ["matches_processed", "optimizations_generated", "errors"],
            "AutoTuneOptimizationSummary is no longer the *synchronous* "
            "run_full_auto_tuning_cycle block. Same field name, different "
            "shape: the async block starts at total_processed and its `errors` "
            "is a list (audit 53.72)",
        )
        for name, field in summary.items():
            with self.subTest(summary=name):
                self.assertEqual(field.annotation, int)
                self.assertTrue(field.is_required())

        calibration = AutoTuneCalibrationResult.model_fields
        self.assertEqual(
            list(calibration),
            [
                "status",
                "calibration_id",
                "engine",
                "version",
                "params",
                "avg_improvement",
                "confidence_score",
            ],
            "AutoTuneCalibrationResult is no longer save_engine_calibration's "
            "key order (audit 53.72)",
        )
        for name, field in calibration.items():
            with self.subTest(calibration=name):
                self.assertTrue(field.is_required())
        self.assertEqual(calibration["params"].annotation, CalibrationParams)
        self.assertEqual(calibration["avg_improvement"].annotation, float | None)
        self.assertEqual(
            calibration["confidence_score"].annotation, float,
            "confidence_score is resolved on every return (the function fills "
            "it in from the sample size when the caller omits it), so making "
            "it nullable would be looser than the producer",
        )

        envelope = AutoTuneResponse.model_fields
        self.assertEqual(
            list(envelope),
            [
                "status",
                "task_id",
                "engine",
                "samples",
                "unverified_samples",
                "optimization_summary",
                "pattern_analysis",
                "calibration",
                "top_blind_spots",
                "top_calibration_issues",
                "message",
                "poll_url",
            ],
            "AutoTuneResponse must keep this field order: it is the only order "
            "in which all five producer shapes serialise the way the producer "
            "wrote them (`message` and `poll_url` last, audit 53.72)",
        )
        self.assertTrue(envelope["status"].is_required())
        for name in list(envelope)[1:]:
            with self.subTest(envelope=name):
                self.assertFalse(
                    envelope[name].is_required(),
                    f"AutoTuneResponse.{name} lost its default; only some "
                    "shapes write it, so the flag must be able to drop it "
                    "(audit 53.72)",
                )
        self.assertTrue(issubclass(AutoTuneResponse, FlexibleResponse))
        annotations = {
            "optimization_summary": AutoTuneOptimizationSummary | None,
            "pattern_analysis": CalibrationAnalysis | None,
            "calibration": AutoTuneCalibrationResult | None,
            "top_blind_spots": list[tuple[str, int]] | None,
            "top_calibration_issues": list[tuple[str, int]] | None,
            "poll_url": str | None,
        }
        for name, want in annotations.items():
            with self.subTest(annotation=name):
                self.assertEqual(
                    envelope[name].annotation, want,
                    f"AutoTuneResponse.{name} annotation drifted (audit 53.72)",
                )

        # The five shapes, verbatim from the producers. Each must round-trip
        # through the model with identical bytes under the flag, and must
        # change without it -- that is what makes the flag load-bearing.
        shapes = [
            # 1. background=True ack (the handler's own dict)
            {
                "status": "accepted",
                "task_id": "t-1",
                "message": "后台优化任务已启动，任务ID: t-1",
                "poll_url": "/api/world-cup/predictions/auto-tune/status/t-1",
            },
            # 2. sync, run_full_auto_tuning_cycle no-data early return
            {"status": "no_data", "message": "No optimizations generated"},
            # 3. sync, calculate_optimization_patterns passthrough no-data
            {"status": "no_data", "message": "No optimizations found"},
            # 4. sync, that passthrough's unverified shape
            {
                "status": "unverified",
                "engine": "hybrid",
                "samples": 0,
                "unverified_samples": 12,
                "message": "not verified against finished match results",
            },
            # 5. sync ok
            {
                "status": "ok",
                "engine": "elo_odds",
                "optimization_summary": {
                    "matches_processed": 12,
                    "optimizations_generated": 9,
                    "errors": 3,
                },
                "pattern_analysis": {
                    "avg_home_score_adjustment": 0.356,
                    "avg_away_score_adjustment": -0.259,
                    "avg_home_win_prob_adjustment": 0.097,
                    "avg_draw_prob_adjustment": 0.024,
                    "avg_away_win_prob_adjustment": -0.123,
                    "avg_confidence_adjustment": -0.013,
                },
                "calibration": {
                    "status": "ok",
                    "calibration_id": 7,
                    "engine": "elo_odds",
                    "version": 3,
                    "params": {
                        "home_score_bias": 0.356,
                        "away_score_bias": -0.259,
                        "home_win_prob_shift": 0.097,
                        "draw_prob_shift": 0.024,
                        "away_win_prob_shift": -0.123,
                        "confidence_multiplier": 0.987,
                    },
                    "avg_improvement": 0.31,
                    "confidence_score": 0.7,
                },
                "top_blind_spots": [["draw_underestimated", 5]],
                "top_calibration_issues": [["low_sample_bucket", 2]],
            },
        ]
        for raw in shapes:
            with self.subTest(shape=raw["status"]):
                model = AutoTuneResponse.model_validate(raw)
                self.assertEqual(
                    json.dumps(model.model_dump(mode="json", exclude_unset=True)),
                    json.dumps(raw),
                    "with the flag the body must match the producer's dict "
                    "byte for byte, key order included (audit 53.72)",
                )
                self.assertNotEqual(
                    json.dumps(model.model_dump(mode="json")),
                    json.dumps(raw),
                    "without the flag the field defaults must materialise keys "
                    "the producer never wrote -- if this stops being true the "
                    "flag is no longer load-bearing (audit 53.72)",
                )

    def test_the_auto_tune_routes_bind_their_models_and_the_flag(self):
        """Route identity and the two opposite flag decisions."""
        from fastapi.routing import APIRoute

        from app.api.routes import world_cup_predictions as wc
        from app.models.event import (
            AutoTuneResponse,
            AutoTuneStatusResponse,
            FlexibleResponse,
        )

        def route_for(path: str, method: str) -> APIRoute:
            found = [
                route
                for route in wc.router.routes
                if isinstance(route, APIRoute)
                and route.path == path
                and method in route.methods
            ]
            self.assertEqual(
                len(found), 1, f"expected exactly one {method} route on {path}"
            )
            return found[0]

        post = route_for(
            "/world-cup/predictions/auto-tune/{engine_name}", "POST"
        )
        self.assertIs(
            post.response_model, AutoTuneResponse,
            "POST /auto-tune/{engine_name} no longer binds AutoTuneResponse; it "
            "published no field contract before audit 53.72",
        )
        self.assertIsNot(post.response_model, FlexibleResponse)
        self.assertIs(
            post.response_model_exclude_unset, True,
            "POST /auto-tune/{engine_name} lost response_model_exclude_unset; "
            "its five shapes are disjoint, so without the flag the defaults "
            "serialise null keys it never wrote (audit 53.72)",
        )

        get = route_for(
            "/world-cup/predictions/auto-tune/status/{task_id}", "GET"
        )
        self.assertIs(
            get.response_model, AutoTuneStatusResponse,
            "GET /auto-tune/status/{task_id} no longer binds "
            "AutoTuneStatusResponse (audit 53.72)",
        )
        self.assertIsNot(get.response_model, FlexibleResponse)
        self.assertIs(
            get.response_model_exclude_unset, False,
            "GET /auto-tune/status/{task_id} gained "
            "response_model_exclude_unset. Its only non-error path writes both "
            "keys, so the flag can only ever hide a regression (audit 53.72)",
        )

    def test_the_client_reads_the_auto_tune_contract(self):
        """The client must not re-declare auto-tune shapes, and the console must
        read the keys ``to_dict()`` actually sends.

        The retired ``AutoTuneTask`` declared ``engine`` / ``stage`` / ``message``
        / ``updated_at`` -- none of which the producer has ever sent -- so the
        "阶段" branch and the message line were permanently dead, and
        ``task.progress * 100`` treated an absolute count as a 0..1 fraction.
        """
        client = (
            _REPO_ROOT / "frontend" / "src" / "lib" / "world-cup"
            / "engine-api.ts"
        ).read_bytes().decode("utf-8")
        console = (
            _REPO_ROOT / "frontend" / "src" / "components" / "sports"
            / "world-cup" / "engine-console.tsx"
        ).read_bytes().decode("utf-8")

        for name in ("AutoTuneAccepted", "AutoTuneTask"):
            with self.subTest(copy=name):
                self.assertNotIn(
                    f"export interface {name}", client,
                    f"engine-api.ts re-declared {name}; the generated "
                    "AutoTuneResponse / AutoTuneTask is the contract "
                    "(audit 53.72)",
                )
        self.assertIn("Promise<AutoTuneResponse>", client)
        self.assertIn("Promise<AutoTuneStatusResponse>", client)

        self.assertNotIn(
            "task.stage", console,
            "the console reads `task.stage`, a key OptimizationTask.to_dict() "
            "has never written (audit 53.72)",
        )
        self.assertNotIn(
            "task.message", console,
            "the console reads `task.message`, a key the task view has never "
            "written (audit 53.72)",
        )
        self.assertIn("task.current_match", console)
        self.assertIn("task.logs", console)
        self.assertNotIn(
            '"error", "cancelled"', console,
            "TERMINAL_TASK_STATES invented two values the backend TaskStatus "
            "enum never produces (audit 53.72)",
        )
        self.assertIn(
            'new Set(["completed", "failed"])', console,
            "TERMINAL_TASK_STATES must mirror the TaskStatus enum's terminal "
            "values (audit 53.72)",
        )


class WorldCupBatchEnvelopesContractTests(unittest.TestCase):
    """The three batch routes named in audit 53.74 (batch A, third slice).

    ``POST /batch-predict``, ``POST /batch-switch-engine`` and
    ``POST /batch-optimize`` all published the bare ``FlexibleResponse``
    envelope. They need two different flag decisions:

    * ``/batch-predict`` and ``/batch-optimize`` each have **one** shape that
      writes every key of its literal, so every field is required and neither
      route may set ``response_model_exclude_unset``.
    * ``/batch-switch-engine`` has **two** shapes -- the route's no-match
      literal and, otherwise, the ``batch_predict_matches`` envelope -- and each
      writes all of its own keys, so that route **must** set the flag or the
      no-match shape grows five explicit ``null`` keys nobody wrote.

    The three envelopes share nothing. ``/batch-optimize`` returns
    ``total_processed`` / ``optimizations_generated`` / ``errors`` /
    ``optimizations``; one hand-written ``BatchSummary`` used to type all three
    routes plus the SSE frame and did not even declare ``predictions``.
    """

    def test_the_batch_predict_envelope_is_required_keys_only(self):
        from app.models.event import BatchPredictResponse, FlexibleResponse

        fields = BatchPredictResponse.model_fields
        self.assertEqual(
            list(fields),
            [
                "status",
                "total",
                "succeeded",
                "failed",
                "skipped",
                "elo_odds_count",
                "hybrid_count",
                "integrated_count",
                "gbm_count",
                "predictions",
            ],
            "BatchPredictResponse is no longer the key order "
            "batch_predict_matches builds (audit 53.74)",
        )
        for name, field in fields.items():
            with self.subTest(field=name):
                self.assertTrue(
                    field.is_required(),
                    f"BatchPredictResponse.{name} gained a default; "
                    "batch_predict_matches writes all ten keys on both of its "
                    "return paths, so a default could only hide a regression "
                    "(audit 53.74)",
                )
        self.assertTrue(issubclass(BatchPredictResponse, FlexibleResponse))
        self.assertEqual(
            fields["predictions"].annotation,
            list[dict[str, typing.Any]],
            "BatchPredictResponse.predictions must stay opaque: an element is "
            "whichever of run_prediction_pipeline's seven literal shapes "
            "produced it, and three more returns delegate elsewhere "
            "(audit 53.74)",
        )

        # The producer really does build all ten keys in one literal; if that
        # stops being true the "all required" decision above is wrong.
        pipeline = (
            _BACKEND_DIR / "app" / "services" / "world_cup_prediction_pipeline.py"
        ).read_bytes().decode("utf-8")
        start = pipeline.index("async def batch_predict_matches(")
        body = pipeline[start:pipeline.index("\n    return results", start)]
        for key in fields:
            with self.subTest(producer_key=key):
                self.assertIn(
                    f'"{key}":',
                    body,
                    f"batch_predict_matches no longer writes {key!r} in its "
                    "summary literal (audit 53.74)",
                )
        self.assertIn(
            '"predictions": []',
            body,
            "batch_predict_matches must seed the predictions list; the element "
            "shape is why it stays an opaque list (audit 53.74)",
        )

    def test_the_batch_switch_envelope_keeps_both_shapes_byte_for_byte(self):
        import json

        from app.models.event import BatchSwitchEngineResponse, FlexibleResponse

        fields = BatchSwitchEngineResponse.model_fields
        self.assertEqual(
            list(fields),
            [
                "status",
                "message",
                "total",
                "succeeded",
                "failed",
                "skipped",
                "elo_odds_count",
                "hybrid_count",
                "integrated_count",
                "gbm_count",
                "predictions",
            ],
            "the field order is load-bearing: `message` sits second because "
            "that is where the no-match literal puts it, and the normal path "
            "leaves it unset so the remaining ten keys still serialise in the "
            "producer's order (audit 53.74)",
        )
        for name in ("status", "total", "succeeded", "failed", "skipped"):
            with self.subTest(required=name):
                self.assertTrue(
                    fields[name].is_required(),
                    f"BatchSwitchEngineResponse.{name} gained a default; both "
                    "shapes write it (audit 53.74)",
                )
        for name in (
            "message",
            "elo_odds_count",
            "hybrid_count",
            "integrated_count",
            "gbm_count",
            "predictions",
        ):
            with self.subTest(optional=name):
                self.assertFalse(
                    fields[name].is_required(),
                    f"BatchSwitchEngineResponse.{name} lost its default; only "
                    "one of the two shapes writes it, so the flag must be able "
                    "to drop it (audit 53.74)",
                )
        self.assertTrue(issubclass(BatchSwitchEngineResponse, FlexibleResponse))
        self.assertEqual(
            fields["message"].annotation, str | None,
            "BatchSwitchEngineResponse.message annotation drifted "
            "(audit 53.74)",
        )

        # Two shapes, verbatim from the producers. Each must round-trip through
        # the model with identical bytes under the flag, and must change without
        # it -- that is what makes the flag load-bearing.
        shapes = [
            # 1. the route's own no-match literal
            {
                "status": "ok",
                "message": "没有找到状态为 scheduled 的比赛",
                "total": 0,
                "succeeded": 0,
                "failed": 0,
                "skipped": 0,
            },
            # 2. batch_predict_matches' envelope
            {
                "status": "ok",
                "total": 2,
                "succeeded": 2,
                "failed": 0,
                "skipped": 0,
                "elo_odds_count": 2,
                "hybrid_count": 0,
                "integrated_count": 0,
                "gbm_count": 0,
                "predictions": [{"status": "ok"}],
            },
        ]
        for raw in shapes:
            with self.subTest(shape=sorted(raw)):
                model = BatchSwitchEngineResponse.model_validate(raw)
                self.assertEqual(
                    json.dumps(
                        model.model_dump(mode="json", exclude_unset=True)
                    ),
                    json.dumps(raw),
                    "with the flag each shape must match its producer's dict "
                    "byte for byte, key order included (audit 53.74)",
                )
                self.assertNotEqual(
                    json.dumps(model.model_dump(mode="json")),
                    json.dumps(raw),
                    "without the flag the defaults must materialise keys the "
                    "producer never wrote -- if this stops being true the flag "
                    "is no longer load-bearing (audit 53.74)",
                )

    def test_the_three_batch_routes_bind_their_envelopes_and_flags(self):
        """Route identity and the three flag decisions."""
        from fastapi.routing import APIRoute

        from app.api.routes import world_cup_predictions as wc
        from app.models.event import (
            BatchOptimizeResponse,
            BatchPredictResponse,
            BatchSwitchEngineResponse,
            FlexibleResponse,
        )

        def route_for(path: str, method: str) -> APIRoute:
            found = [
                route
                for route in wc.router.routes
                if isinstance(route, APIRoute)
                and route.path == path
                and method in route.methods
            ]
            self.assertEqual(
                len(found), 1, f"expected exactly one {method} route on {path}"
            )
            return found[0]

        for path, model in (
            ("/world-cup/predictions/batch-predict", BatchPredictResponse),
            ("/world-cup/predictions/batch-optimize", BatchOptimizeResponse),
        ):
            with self.subTest(route=path):
                route = route_for(path, "POST")
                self.assertIs(
                    route.response_model,
                    model,
                    f"POST {path} no longer binds {model.__name__}; it "
                    "published the bare FlexibleResponse before audit 53.74",
                )
                self.assertIsNot(route.response_model, FlexibleResponse)
                self.assertIs(
                    route.response_model_exclude_unset,
                    False,
                    f"POST {path} gained response_model_exclude_unset. It has "
                    "one shape that writes all of its keys, so the flag could "
                    "only ever hide a regression (audit 53.74)",
                )

        switch = route_for(
            "/world-cup/predictions/batch-switch-engine", "POST"
        )
        self.assertIs(
            switch.response_model,
            BatchSwitchEngineResponse,
            "POST /batch-switch-engine no longer binds "
            "BatchSwitchEngineResponse (audit 53.74)",
        )
        self.assertIsNot(switch.response_model, FlexibleResponse)
        self.assertIs(
            switch.response_model_exclude_unset,
            True,
            "POST /batch-switch-engine lost response_model_exclude_unset; its "
            "no-match shape writes six keys, so without the flag the five "
            "defaults serialise null keys it never wrote (audit 53.74)",
        )

    def test_the_client_reads_the_batch_contract(self):
        """One hand-written ``BatchSummary`` served four endpoints; the client
        must read the generated envelopes instead.

        It also had no ``predictions`` field -- the batch envelopes' largest key
        was invisible -- and it was simply wrong for ``/batch-optimize``, whose
        four keys share none of its fields, so that panel rendered
        "共 0 / 成功 0 / 失败 0 / 跳过 0" for every run.
        """
        client = (
            _REPO_ROOT / "frontend" / "src" / "lib" / "world-cup"
            / "engine-api.ts"
        ).read_bytes().decode("utf-8")
        console = (
            _REPO_ROOT / "frontend" / "src" / "components" / "sports"
            / "world-cup" / "engine-console.tsx"
        ).read_bytes().decode("utf-8")

        self.assertNotIn(
            "interface BatchSummary",
            client,
            "engine-api.ts re-declared BatchSummary; one hand-written shape "
            "served four endpoints and declared no `predictions` at all -- the "
            "generated envelopes are the contract (audit 53.74)",
        )
        for name in (
            "BatchPredictResponse",
            "BatchSwitchEngineResponse",
            "BatchOptimizeResponse",
        ):
            with self.subTest(model=name):
                self.assertIn(
                    f"Promise<{name}>",
                    client,
                    f"the client no longer reads {name} from the generated "
                    "contract (audit 53.74)",
                )
                self.assertNotIn(
                    f"export interface {name}",
                    client,
                    f"engine-api.ts re-declared {name} (audit 53.74)",
                )
        # An SSE frame is never a route response_model, so nothing can be
        # generated for it; it is picked from the generated envelope rather
        # than re-declared.
        self.assertIn(
            "BatchSwitchStreamSummary = Pick<",
            client,
            "the SSE complete frame must derive from the generated envelope "
            "(audit 53.74)",
        )

        # /batch-optimize's own four keys, which the retired summary never had.
        for key in (
            "summary.total_processed",
            "summary.optimizations_generated",
        ):
            with self.subTest(console_key=key):
                self.assertIn(
                    key,
                    console,
                    f"the console no longer reads {key} off the batch-optimize "
                    "result (audit 53.74)",
                )
        self.assertIn(
            "engine-optimize-summary",
            console,
            "the batch-optimize result needs its own panel; it cannot share "
            "the batch-prediction summary line (audit 53.74)",
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

    def test_decision_report_reads_the_generated_contract(self):
        """No local copy of the report, and no local overlay copy either.

        Until audit 53.59 this asserted the hand-written ``DecisionReport`` body
        and checked that its three overlay fields pointed at the generated
        names. That body is gone: 53.59 named the report on the backend
        (``build_decision_report`` is its single producer) and the interface here
        became an alias. So the first half now asserts the *absence* of a
        declaration -- the same claim one step further on, since a copy coming
        back is still the regression -- and the second catches the alias being
        repointed at something else.

        ``_overlay_copies`` is kept because it guards a different thing: that
        none of the three quality overlays is re-declared here, which is what
        audit 53.52 introduced it for and is independent of whether the report
        itself is generated. ``_interface_bodies`` collects only ``export
        interface``, so the ``export type`` alias is not mistaken for a
        declaration.
        """
        api_text = _API_TS_PATH.read_bytes().decode("utf-8")
        bodies = _interface_bodies(api_text)

        self.assertNotIn(
            "DecisionReport",
            bodies,
            "api.ts re-declared DecisionReport; generated-types.ts is the source "
            "of truth. Alias the generated name instead (audit 53.59).",
        )
        self.assertEqual(
            _overlay_copies(bodies),
            sorted(ALLOWED_OVERLAY_COPIES_IN_API_TS),
            "api.ts re-declared an overlay; generated-types.ts is the source of "
            "truth. If a local copy is genuinely needed, add its name to "
            "ALLOWED_OVERLAY_COPIES_IN_API_TS in the same commit.",
        )
        self.assertIn(
            "export type DecisionReport = GeneratedDecisionReport;",
            api_text,
            "api.ts no longer aliases the generated decision report",
        )


if __name__ == "__main__":
    unittest.main()
