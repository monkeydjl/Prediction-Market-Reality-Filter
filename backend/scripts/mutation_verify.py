"""Byte-level mutation harness for every guard this audit added.

A green test that stays green when its fix is removed locks nothing. This script
reverts each tracked change one at a time and asserts the matching guard test
turns RED -- then puts it back and asserts the bytes are identical.

It replaces three near-identical per-batch scripts
(``mutation_verify_daily_digest.py``, ``mutation_verify_review_queue_flag.py``,
``mutation_verify_probability_probe.py``). Those all re-implemented the same
backup/apply/revert machinery, and one of them had grown a stronger check than
the other two while the other two had a per-index selector the first lacked.
Merging keeps the union of both: one inventory, one engine, and every set either
of the three had. (The count is deliberately not written down here -- sets have
been added since, and a hardcoded "five sets" went stale twice.)

Usage
-----
    cd backend && python scripts/mutation_verify.py list
    cd backend && python scripts/mutation_verify.py verify                # all sets
    cd backend && python scripts/mutation_verify.py verify probability-probe
    cd backend && python scripts/mutation_verify.py verify whitelist-fixtures --index 30
    cd backend && python scripts/mutation_verify.py verify whitelist-fixtures -i 27,30
    cd backend && python scripts/mutation_verify.py backup review-queue
    cd backend && python scripts/mutation_verify.py apply  review-queue 5
    cd backend && python scripts/mutation_verify.py apply  review-queue 1-3,7
    cd backend && python scripts/mutation_verify.py revert review-queue

``verify`` is the mode to use. It drives pytest itself and asserts a full
four-phase cycle per mutation:

    1. the guard tests PASS on the unmodified tree   (so the selector is real)
    2. they FAIL with the mutation applied, in EVERY named guard file
                                                     (the guard is load-bearing
                                                      in each file it names --
                                                      one return code cannot
                                                      tell "all noticed" from
                                                      "one did")
    3. they PASS again once it is reverted           (the restore restored
                                                      behaviour, not just bytes)
    4. the file's sha256 equals the pre-mutation one (the restore restored bytes)

``--index N[,N...]`` narrows a run to specific mutations, which is what you want
when a target needs re-taking without a twelve-minute pass over the whole set (a
full run was measured at ~12 minutes for ``whitelist-fixtures``). More than one
position may be given, e.g. ``--index 27,30``, and they run in the order typed.
A token of the form ``a-b`` expands to a range. It needs exactly one set key --
there is no sensible answer to ``verify a b --index 3``. Comma-separated rather
than ``nargs='+'`` because the subcommand's ``keys`` are already ``nargs='*'`` --
with a space-separated form, ``verify whitelist-fixtures --index 27 30`` would be
read as two set keys. The selection rules live in ``_select_verify_targets`` so
they can be asserted without running pytest at all, and ``apply`` takes the same
spellings through the same parser and selector.

Phase 1 also catches a selector that matches nothing, which pytest reports as
"no tests ran" and which would otherwise look exactly like a successful phase 2.
The older per-batch scripts only ran phases 1, 2 and 4, and only for the
daily-digest set. ``backup``/``apply``/``revert`` are kept for driving the tests
by hand, which is what you want when a mutation's red needs inspecting rather
than asserting.

Groups
------
A ``group`` marks mutations that rewrite the same bytes in different directions
(``C5``/``C6`` on ``_flag_note``, and ``P7``/``P8`` on the listing query's
``WHERE`` clause). Applying both would fail on the second with "expected 1
occurrence, found 0". ``verify`` applies one at a time so it is unaffected;
``apply <set>`` (all) skips the rest of a group and says so, while naming both
members explicitly is *rejected* instead -- two typed positions are an explicit
request, not a convenience sweep (``_apply_preflight``).

Byte-level on purpose
---------------------
This repo commits LF and checks out CRLF, and ``Path.read_text`` applies
universal-newline translation -- so a read/modify/write round trip rewrites a
whole CRLF file to LF while ``git diff`` hides it (``core.autocrlf`` normalises
before diffing). ``read_bytes``/``write_bytes`` keep the evidence honest, and
the restore is checked by sha256 rather than by eye. Run
``scripts/eol_audit.py`` afterwards for an independent read on line endings.

Needles are declared as raw ``bytes`` for the same reason. They **do not share
one line-ending convention**: some are line-terminated and spell CRLF, others
sit inside a single line. That distinction is no longer load-bearing. A needle's
line breaks are matched against the checkout's own convention (``count_needle``
and ``rewrite_needle``), so a CRLF needle still matches CI's LF checkout and an
LF needle still matches a developer machine -- the inventory stops depending on
which convention the machine that runs it happens to use.

The match is still byte for byte: only the line break may vary, and the count
still has to be exactly one, so a needle that has genuinely gone stale keeps
failing loudly. The replacement inherits the *file's* line endings rather than
the ones spelled in ``new``, so a mutation that deletes a line cannot leave a
file mixed while the sha256 restore check reports everything was put back.

Adding a mutation
-----------------
Append a ``Mutation`` to the set's tuple below. ``verify`` requires the guard
test names to be exact: a bare substring is fine for ``-k``, but a name that
matches nothing fails phase 1 rather than passing quietly.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import pathlib
import shutil
import subprocess
import sys
import tempfile

SCRIPTS = pathlib.Path(__file__).resolve().parent
BACKEND = SCRIPTS.parent
REPO_ROOT = BACKEND.parent


def _resolve_interpreter() -> pathlib.Path:
    """The interpreter the guard subprocesses run under.

    The virtualenv layout depends on how the checkout was provisioned, not only
    on the platform: ``Scripts/python.exe`` on a Windows venv, ``bin/python``
    elsewhere -- and neither is guaranteed to exist at all. CI installs the
    dependencies into the runner's own interpreter and checks the project out
    without a ``.venv``, so pinning the path to one spelling made every guard
    run die with ``interpreter not found`` there.

    Falling back to the interpreter already running this script keeps the guard
    subprocesses inside the same environment the harness itself was started
    from, which is the property that matters; the file's location is only a
    hint. On a developer machine the venv wins, so the subprocess interpreter is
    unchanged.
    """
    candidates = (
        REPO_ROOT / ".venv" / "Scripts" / "python.exe",
        REPO_ROOT / ".venv" / "bin" / "python",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return pathlib.Path(sys.executable)


PY = _resolve_interpreter()
BACKUP_ROOT = pathlib.Path(tempfile.gettempdir()) / "pmrf-mutation-bak"
GUARD_TIMEOUT = 600

# Needles are still written by hand, and this is the spelling most of them use
# for a line break. It is no longer load-bearing: ``count_needle`` and
# ``rewrite_needle`` below try both conventions, precisely because CI checks out
# LF while a developer machine checks out CRLF (see the module docstring).
CRLF = b"\r\n"


def _needle_variants(needle: bytes) -> tuple[bytes, ...]:
    """The needle under each line-ending convention, deduplicated.

    A needle that spans a line break was written against one convention and can
    never match a checkout that uses the other -- which is how seven needles came
    to match nothing on CI while every one of them passed locally. Both spellings
    are tried instead of pinning the inventory to one of them.

    The match is still byte for byte -- only which line break the needle carries
    may vary -- and callers still require the count to be exactly one, so a
    needle that has genuinely gone stale keeps failing loudly.
    """
    if b"\n" not in needle:
        return (needle,)
    lf = needle.replace(b"\r\n", b"\n")
    crlf = lf.replace(b"\n", b"\r\n")
    return (lf,) if lf == crlf else (lf, crlf)


def _eol_of(data: bytes) -> bytes:
    """The line ending ``data`` predominantly uses.

    "Predominantly" rather than "the first one seen": a file can be mixed, and
    the side with more lines is the one a rewrite should stay consistent with.
    """
    crlf = data.count(b"\r\n")
    return b"\r\n" if crlf > data.count(b"\n") - crlf else b"\n"


def _to_eol(data: bytes, eol: bytes) -> bytes:
    """``data`` with every line break spelled ``eol``."""
    return data.replace(b"\r\n", b"\n").replace(b"\n", eol)


def count_needle(content: bytes, needle: bytes) -> int:
    """How many times ``needle`` occurs in ``content``, under either convention.

    Public because the inventory test asserts this same property and must not
    restate the rule: two spellings of one rule is how the rule drifts.
    """
    return sum(content.count(variant) for variant in _needle_variants(needle))


def _matched_variant(content: bytes, needle: bytes) -> bytes | None:
    """The spelling of ``needle`` that ``content`` actually contains, if any."""
    for variant in _needle_variants(needle):
        if variant in content:
            return variant
    return None


def rewrite_needle(content: bytes, old: bytes, new: bytes) -> bytes:
    """Replace ``old`` with ``new`` once, in the convention ``content`` uses.

    ``new``'s own line endings are deliberately ignored. A mutation that deletes
    a line, or that spells an inserted line with the other convention, must not
    turn a CRLF file into a mixed one: that is invisible in a diff
    (``core.autocrlf`` normalises before diffing) and the sha256 restore check
    cannot see it either, because it compares what was written rather than how
    it ended its lines.

    Raises when the needle is absent. Callers preflight the count first, so this
    guards against calling it out of order rather than serving a user-facing
    error path.
    """
    variant = _matched_variant(content, old)
    if variant is None:
        raise ValueError(f"needle not found: {old[:60]!r}")
    eol = _eol_of(variant) if b"\n" in variant else _eol_of(content)
    return content.replace(variant, _to_eol(new, eol), 1)


@dataclasses.dataclass(frozen=True)
class Mutation:
    """One revertible change and the guard that must notice it."""

    label: str
    path: str
    old: bytes
    new: bytes
    guard_file: str | tuple[str, ...]
    guards: tuple[str, ...]
    group: str = ""
    note: str = ""

    def guard_files(self) -> tuple[str, ...]:
        """Every guard file whose tests cover ``old`` -> ``new``.

        Usually exactly one. A mutation aimed at a *shared* definition names
        several, because the behaviour that has to notice the change lives in
        each module that consumes the definition, not only in the one that
        declares it (audit section 42.4). Accepting a bare string keeps every
        existing one-file mutation unchanged -- the normalisation lives here,
        so nothing else has to know which spelling it was handed.
        """
        if isinstance(self.guard_file, str):
            return (self.guard_file,)
        return tuple(self.guard_file)


@dataclasses.dataclass(frozen=True)
class MutationSet:
    key: str
    title: str
    mutations: tuple[Mutation, ...]
    rationale: str = ""


_DIGEST_TESTS = "tests/test_daily_digest_service.py"
_QUEUE_ENDPOINT_TESTS = "tests/test_review_queue_endpoint.py"
_QUEUE_CLI_TESTS = "tests/test_review_queue_cli.py"
_OVERLAY_TESTS = "tests/test_env_overlay_examples.py"
_PROBE_TESTS = "tests/test_report_probability_scale_outliers.py"

_DIGEST_ROUTES = "backend/app/api/routes/events.py"
_MAIN_APP = "backend/app/main.py"
_DIGEST_SERVICE = "backend/app/services/daily_digest_service.py"
_QUEUE_ROUTES = "backend/app/api/routes/review_queue.py"
_QUEUE_CLI = "backend/scripts/review_queue_cli.py"
_PROD_TEMPLATE = "backend/.env.production.example"
_PROBE = "backend/scripts/report_probability_scale_outliers.py"

_PRED_STORE = "backend/app/memory/prediction_store.py"
_TRADES_STORE = "backend/app/memory/simulated_trade_store.py"
_PRED_STORE_TESTS = "tests/test_prediction_store.py"
_TRADES_TESTS = "tests/test_simulated_trade_store.py"

# --- whitelist-fixtures set (§24.2/§24.3/§24.4) -----------------------------
_AVAILABILITY = "backend/app/services/football_live_availability_service.py"
_SCHEDULE = "backend/app/services/football_live_schedule_service.py"
_KALSHI = "backend/app/services/kalshi_event_source.py"
_STATISTICS = "backend/app/services/world_cup_statistics_source.py"
_CLUB_ELO = "backend/app/services/club_elo_service.py"
_MLB_ADAPTER = "backend/app/sports/baseball/mlb_adapter.py"
_QUALITY = "backend/app/services/world_cup_quality_service.py"
_PIPELINE = "backend/app/services/world_cup_prediction_pipeline.py"
_MATCH_SERVICE = "backend/app/services/world_cup_match_service.py"
_ELO_ENGINE = "backend/app/kernel/engines/elo_odds_engine.py"
_SPORTS_FACT = "backend/app/services/sports_fact_service.py"
_LIMITLESS = "backend/app/services/limitless_event_source.py"
_GBM_ENGINE = "backend/app/kernel/engines/gbm_engine.py"
_CHALLENGE_ADAPTER = "backend/app/services/conclusion_challenge_world_cup_adapter.py"
# §45/§47 — every direction constant now lives in the shared vocabulary module.
# §45 merged the six `_STRONG_DIRECTIONS` copies into one definition (their six
# mutations became W31, the first mutation to name several guard files); §47
# moved the remaining seven differently-named constants in beside it. All eight
# of those mutations therefore aim here, and only their guard files differ. The
# three path constants that used to name `prediction_calibration_service`,
# `review_queue_detectors` and `domain_reliability_service` went with the
# definitions they pointed at.
_DIRECTION_VOCABULARY = "backend/app/utils/direction_vocabulary.py"

# §41 — the loopback health probe in restore_stores (audit section 35.5). The
# guard file is the P0 round-2 suite, whose G3 block drives the probe.
_RESTORE_STORES = "backend/scripts/restore_stores.py"
_T_RESTORE_PROBE = "tests/test_p0_fixes_round2.py"

# §42 — the harness capability that lets one mutation name several guard files.
# Both paths below are the harness itself: H1 rewrites _run_guards, and the
# guards are the inventory tests that pin the argv it builds.
_MUTATION_VERIFY = "backend/scripts/mutation_verify.py"
_T_MUTATION_VERIFY = "tests/test_mutation_verify.py"

# §53.44 — the market-snapshot store's ORDER BY tiebreakers. Both reads used to
# sort on `captured_at` alone, which leaves the row order inside a tie
# unspecified; the measured behaviour was the *first*-inserted row winning, i.e.
# a tie silently reported the OLDER capture as the newest. The two mutations in
# the `market-snapshot-order` set are the two directions of that tiebreaker, and
# they need two guards because the two queries feed two different consumers: a
# scalar ("latest") and the chart series.
_MARKET_SNAPSHOT_STORE = "backend/app/kernel/market_snapshot_store.py"
_T_SPORT_MARKET_STORES = "tests/test_sport_market_stores.py"

# §53.47 — the same store's *audit* reader. `audit_summary` assumed every row it
# was handed carried a timestamp, and its sort key returned whatever type it was
# given (`datetime` from a real read, an ISO string from the older fixtures, and
# `""` for a missing timestamp). `kernel_market_snapshots.captured_at` is
# nullable, so a row with no timestamp is legal, and one such row was enough to
# raise TypeError inside `sorted` -- a 500 on both audit routes. The two
# mutations below are the partition and the key's type, and they need separate
# guards because one is asserted on endpoint values and the other on mixing.
_T_ECE_MARKET_AUDIT = "tests/test_ece_and_market_audit.py"

# §53.48 — the series door (GET /snapshots/{match_id}) reads the shared
# `is_timestamped` predicate through `get_price_path`, so its guard lives here.
_T_SPORT_MARKET_ROUTES = "tests/test_sport_market_routes.py"

# §53.49 — the snapshots endpoint's *published contract*, which section 2 calls
# the three-step (named `response_model`, allowlist, regenerate). Both facts are
# invisible to a behavioural test: drop the `response_model` and the payload is
# byte-for-byte the same; drop a model from `_frontend_export` and it is still
# emitted, just as a nested one with an `[k: string]: unknown` tail. So the two
# mutations below each pin one half, and neither can stand in for the other.
_SPORT_MARKET_ROUTES = "backend/app/api/routes/sport_markets.py"
_FRONTEND_EXPORT = "backend/app/models/_frontend_export.py"
_EVENT_MODEL = "backend/app/models/event.py"
_API_TS = "frontend/src/lib/api.ts"
_TYPES_TS = "frontend/src/lib/types.ts"
_T_GENERATE_TYPES = "tests/test_generate_types.py"

_T_AVAILABILITY = "tests/test_football_live_availability_service.py"
_T_SCHEDULE = "tests/test_football_live_schedule_service.py"
_T_KALSHI = "tests/test_kalshi_event_source.py"
_T_STATISTICS = "tests/test_world_cup_statistics_source.py"
_T_CLUB_ELO = "tests/test_club_elo_service.py"
_T_MLB_ADAPTER = "tests/test_mlb_adapter.py"
_T_QUALITY = "tests/test_world_cup_quality_service.py"
_T_ANALYTICS_ROUTES = "tests/test_world_cup_analytics_routes.py"
_T_STAGE = "tests/test_knockout_stage_whitelist_consistency.py"
_T_MATCH_SERVICE = "tests/test_world_cup_match_service.py"
_T_SPORTS_FACT = "tests/test_sports_fact_service.py"
_T_EVENT_SOURCE_UTILS = "tests/test_event_source_utils.py"
_T_GUARDRAIL = "tests/test_guardrail_service.py"
_T_MARKET_QUALITY = "tests/test_market_quality_service.py"
_T_EXECUTION_QUALITY = "tests/test_execution_quality_service.py"
_T_SOURCE_RELIABILITY = "tests/test_source_reliability_service.py"
_T_PREDICTION_CALIBRATION = "tests/test_prediction_calibration_service.py"
_T_REVIEW_QUEUE_DETECTORS = "tests/test_review_queue_detectors.py"
_T_DOMAIN_RELIABILITY = "tests/test_domain_reliability_service.py"
_T_REPLAY_METRICS = "tests/test_replay_metrics.py"

# §34 — the inline `{"YES", "NO"}` sets that were lifted into module-level named
# constants. Their mutations pin the *constant definition* rather than any call
# site that reads it, so one mutation covers every consumer. §47 moved three of
# those definitions (plus four more) into `_DIRECTION_VOCABULARY`, which is why
# only W30 still aims at the module it was extracted from.
_DECISION_QUALITY = "backend/app/services/decision_quality_service.py"
_T_TRADES_STORE = "tests/test_simulated_trade_store.py"
_T_QUALITY_METRICS = "tests/test_quality_metrics.py"
_T_EVENT_INTELLIGENCE = "tests/test_event_intelligence_service.py"
_T_CONCLUSION_CHALLENGE = "tests/test_conclusion_challenge_service.py"
_T_DECISION_QUALITY = "tests/test_decision_quality_service.py"
# §47 — the vocabulary module's own guard file. Its one mutation (W32) pins the
# container type of the single constant that has an *iterating* consumer.
_T_DIRECTION_VOCABULARY = "tests/test_direction_vocabulary.py"

# W12's replacement: a *functionally equivalent* local copy, inserted next to the
# shared import. It keeps behaviour identical on purpose -- the point of the
# mutation is that only the identity guard notices it.
_LOCAL_COPY_SHIM = (
    b"from app.utils.failure_policy import fail_closed_empty_list" + CRLF
    + b"from app.services.event_source_utils import extract_text as _shared_extract_text"
    + CRLF + CRLF + CRLF
    + b"def _extract_text(market, fields):" + CRLF
    + b"    return _shared_extract_text(market, fields)"
)

# The two lines that decide whether _flag_note() reports the flag. C5 and C6 are
# opposite rewrites of this same span, hence the group.
_FLAG_GUARD = b"    if settings.REVIEW_QUEUE_ENABLED:" + CRLF + b"        return None" + CRLF

# The listing query's WHERE clause as one literal, so P7 and P8 rewrite the same
# bytes. The shorter fragment "AND market_probability < 1" appears twice (the
# bucket query has it too), which is why the whole string is used.
_LISTING_FILTER = (
    b'" FROM predictions WHERE market_probability > 0 AND market_probability < 1"'
)

# H1's needle pair: the loop in ``_run_guards`` that walks every guard file.
# Split into two fragments on purpose. H1's target *is* this file, so spelling
# the needle as one literal here -- in the definition or even in this comment --
# would put it in the inventory too: it would then occur twice and
# ``_apply_preflight`` would refuse it. Describing the needle instead of quoting
# it keeps the count at one.
_GUARD_FILES_LOOP = b"for guard_file in mutation." + b"guard_files()"
_GUARD_FILES_LOOP_FIRST = b"for guard_file in mutation." + b"guard_files()[:1]"

# H2's needle pair: the strict half of ``GuardRun.every_file_red`` and the "at
# least one red" reading it must not collapse into. Both halves are split for
# the same self-reference reason as above -- H2's target is this file too.
_EVERY_FILE_RED_STRICT = b"return bool(self.per_file) and not self." + b"green_files"
_EVERY_FILE_RED_LOOSE = b"return bool(self." + b"red_files)"
# eol-audit -- the line-ending audit tool is guarded the same way it guards
# everything else. It is the receipt for "the working tree's line endings are
# intact", so a false red is not harmless: it teaches the next reader to
# distrust a green run. The favicon false positive of 2026-10-01 was exactly
# that. E1 and E2 remove the two halves of the fix; E3 and E4 keep the fix from
# degenerating into "stop reporting damage at all"; E5 guards the bulk HEAD read
# that replaced one ``git show`` per file, where a mis-advanced cursor is silent.
_EOL_AUDIT = "backend/scripts/eol_audit.py"
_T_EOL_AUDIT = "tests/test_eol_audit.py"


SETS: tuple[MutationSet, ...] = (
    MutationSet(
        key="daily-digest",
        title="每日情报摘要（F1/F2/F3/F5/F6）",
        rationale=(
            "F1 date 用真日期而非正则校验的字符串；F2 response_model 发布契约；"
            "F3 阈值处取闭区间；F5 empty 表示「完全没有可摘要的东西」；"
            "F6 没有落在窗口内的事件时不去读 store。"
        ),
        mutations=(
            Mutation(
                label="F1  日期是真日期类型（不是正则校验的字符串）",
                path=_DIGEST_ROUTES,
                old=b"date: _date | None = Query(default=None),",
                new=b'date: str | None = Query(default=None, '
                b'pattern=r"^\\d{4}-\\d{2}-\\d{2}$"),',
                guard_file=_DIGEST_TESTS,
                guards=("test_digest_route_rejects_calendar_invalid_date",),
                note="回到 str + 正则（2 月 30 日这类日历非法值会漏过）",
            ),
            Mutation(
                label="F2  response_model 发布字段契约",
                path=_DIGEST_ROUTES,
                old=b'@router.get("/digest", response_model=DailyDigestResponse)',
                new=b'@router.get("/digest", response_model=FlexibleResponse)',
                guard_file=_DIGEST_TESTS,
                guards=("test_digest_openapi_schema_declares_fields",),
                note="换成无字段的基类 FlexibleResponse",
            ),
            Mutation(
                label="F3  移动标签在阈值处取闭区间",
                path=_DIGEST_SERVICE,
                old=b"if day_net >= MATERIALITY:",
                new=b"if day_net > MATERIALITY:",
                guard_file=_DIGEST_TESTS,
                guards=("test_move_of_exactly_materiality_is_a_directional_mover",),
                note=">= 改成 >（恰好等于 MATERIALITY 不再算移动）",
            ),
            Mutation(
                label="F5  empty 表示「完全没有可摘要的东西」",
                path=_DIGEST_SERVICE,
                old=b'"empty": total_movers == 0 and not new_event_ids and not quiet_event_ids,',
                new=b'"empty": total_movers == 0 and not new_event_ids,',
                guard_file=_DIGEST_TESTS,
                guards=("test_all_quiet_day_is_not_empty",),
                note="去掉 quiet_event_ids 条件（全安静的一天会被误报为空）",
            ),
            Mutation(
                label="F6  没有落在窗口内的事件时不读 store",
                path=_DIGEST_SERVICE,
                old=b"    if stats:",
                new=b"    if True:",
                guard_file=_DIGEST_TESTS,
                guards=("test_store_is_not_read_when_nothing_is_in_window",),
                note="无条件读 store（空窗口也去读）",
            ),
        ),
    ),
    MutationSet(
        key="review-queue",
        title="复核队列 enabled 回显 + overlay 开关块 + CLI 提示（C1–C6）",
        rationale=(
            "C1/C4 让 /review-queue 与 /review-queue/sla 回显 REVIEW_QUEUE_ENABLED，"
            "使「0」只表示一件事；C2/C3 让生产模板点名它留空的开关，但不写成赋值"
            "（overlay override=True，写成 =false 会压掉操作员开的 true）；"
            "C5/C6 让 CLI 在开关关闭时说明 0 的含义。"
        ),
        mutations=(
            Mutation(
                label="C1  /review-queue 回显 REVIEW_QUEUE_ENABLED",
                path=_QUEUE_ROUTES,
                old=b'        "enabled": settings.REVIEW_QUEUE_ENABLED,' + CRLF,
                new=b"",
                guard_file=_QUEUE_ENDPOINT_TESTS,
                guards=(
                    "test_list_reports_whether_the_producer_flag_is_on",
                    "test_list_pending_is_empty_on_fresh_db",
                ),
                note="删掉 enabled 键",
            ),
            Mutation(
                label="C2  生产模板点名 REVIEW_QUEUE_ENABLED",
                path=_PROD_TEMPLATE,
                old=b"# REVIEW_QUEUE_ENABLED=true" + CRLF,
                new=b"",
                guard_file=_OVERLAY_TESTS,
                guards=("test_the_production_overlay_names_the_flags_it_leaves_off",),
                note="删掉那行注释",
            ),
            Mutation(
                label="C3  生产模板不把该开关钉成赋值",
                path=_PROD_TEMPLATE,
                old=b"# WORLD_CUP_CHALLENGE_ENABLED=true" + CRLF,
                new=b"WORLD_CUP_CHALLENGE_ENABLED=false" + CRLF,
                guard_file=_OVERLAY_TESTS,
                guards=("test_the_production_overlay_does_not_pin_those_flags_off",),
                note="注释行改成 =false 赋值",
            ),
            Mutation(
                label="C4  /review-queue/sla 回显 REVIEW_QUEUE_ENABLED",
                path=_QUEUE_ROUTES,
                old=b', "enabled": settings.REVIEW_QUEUE_ENABLED}',
                new=b"}",
                guard_file=_QUEUE_ENDPOINT_TESTS,
                guards=("test_sla_reports_whether_the_producer_flag_is_on",),
                note="删掉 enabled 键",
            ),
            Mutation(
                label="C5  CLI 提示受开关约束",
                path=_QUEUE_CLI,
                old=_FLAG_GUARD,
                new=b"",
                guard_file=_QUEUE_CLI_TESTS,
                guards=("test_sla_says_nothing_extra_when_the_producer_is_on",),
                group="cli-flag-guard",
                note="提示变成无条件打印",
            ),
            Mutation(
                label="C6  CLI 提示可达",
                path=_QUEUE_CLI,
                old=_FLAG_GUARD,
                new=b"    return None" + CRLF,
                guard_file=_QUEUE_CLI_TESTS,
                guards=(
                    "test_sla_names_the_producer_flag_when_it_is_off",
                    "test_list_names_the_producer_flag_when_it_is_empty_and_off",
                ),
                group="cli-flag-guard",
                note="提示永远不打印",
            ),
        ),
    ),
    MutationSet(
        key="probability-probe",
        title="标度错位探针（P1–P15）",
        rationale=(
            "探针是唯一能发现新的 0-1 标度值进入 predictions.market_probability 的东西，"
            "所以它的分桶、过滤边界、修正算术、只读承诺与 --event-id 输出全部逐条锁住。"
        ),
        mutations=(
            Mutation(
                label="P1  分桶查询的 suspect 下界排除 mp = 0",
                path=_PROBE,
                old=b'"WHERE market_probability > 0 "',
                new=b'"WHERE market_probability >= 0 "',
                guard_file=_PROBE_TESTS,
                guards=("test_distribution_buckets_partition_the_rows",),
                note="桶查询下界改成 >=（mp=0 被多算一次）",
            ),
            Mutation(
                label="P2  健康桶有上界（mp > 100 单独成桶）",
                path=_PROBE,
                old=b'"AND market_probability <= 100"',
                new=b'""',
                guard_file=_PROBE_TESTS,
                guards=(
                    "test_out_of_range_high_rows_are_not_lumped_into_the_normal_bucket",
                    "test_distribution_buckets_partition_the_rows",
                ),
                note="去掉上界（105 躲进健康计数）",
            ),
            Mutation(
                label="P3  修正值把市场值换算到 0-100",
                path=_PROBE,
                old=b"corrected = ai - market * 100",
                new=b"corrected = ai - market",
                guard_file=_PROBE_TESTS,
                guards=("test_the_audited_row_is_reported_with_its_corrected_edge",),
                note="去掉 ×100（把存着的 29.97 当成修正值）",
            ),
            Mutation(
                label="P4  连接是只读的",
                path=_PROBE,
                old=b'?mode=ro"',
                new=b'?mode=rw"',
                guard_file=_PROBE_TESTS,
                guards=("test_the_connection_refuses_to_write",),
                note="mode=ro → mode=rw",
            ),
            Mutation(
                label="P5  修正受非数值 ai_probability 保护",
                path=_PROBE,
                old=b"if isinstance(ai, (int, float)):",
                new=b"if True:",
                guard_file=_PROBE_TESTS,
                guards=(
                    "test_a_suspect_with_a_non_numeric_ai_probability_skips_the_correction",
                ),
                note="去掉 isinstance 守卫（TypeError 会冒到输出里）",
            ),
            Mutation(
                label="P6  --event-id 输出截断长值",
                path=_PROBE,
                old=b"if isinstance(value, str) and len(value) > 200:",
                new=b"if False:",
                guard_file=_PROBE_TESTS,
                guards=("test_event_dump_truncates_long_values",),
                note="关掉 200 字符截断",
            ),
            Mutation(
                label="P7  列表查询的 suspect 下界排除 mp = 0",
                path=_PROBE,
                old=_LISTING_FILTER,
                new=b'" FROM predictions WHERE market_probability >= 0 '
                b'AND market_probability < 1"',
                guard_file=_PROBE_TESTS,
                guards=("test_a_zero_market_probability_is_not_a_suspect",),
                group="listing-filter",
                note="列表查询下界改成 >=",
            ),
            Mutation(
                label="P8  列表查询的 suspect 上界排除 mp = 1",
                path=_PROBE,
                old=_LISTING_FILTER,
                new=b'" FROM predictions WHERE market_probability > 0 '
                b'AND market_probability <= 1"',
                guard_file=_PROBE_TESTS,
                guards=("test_a_market_probability_of_exactly_one_is_not_a_suspect",),
                group="listing-filter",
                note="列表查询上界改成 <=",
            ),
            Mutation(
                label="P9  min/max 报告两端",
                path=_PROBE,
                old=b'"SELECT MIN(market_probability), MAX(market_probability) FROM predictions"',
                new=b'"SELECT MIN(market_probability), MIN(market_probability) FROM predictions"',
                guard_file=_PROBE_TESTS,
                guards=("test_min_max_span_every_row_including_the_suspect",),
                note="MAX 改成 MIN",
            ),
            Mutation(
                label="P10 NULL 桶统计未设值",
                path=_PROBE,
                old=b'"WHERE market_probability IS NULL"',
                new=b'"WHERE market_probability IS NOT NULL"',
                guard_file=_PROBE_TESTS,
                guards=("test_the_null_bucket_is_zero_because_the_column_is_not_null",),
                note="改查 IS NOT NULL",
            ),
            Mutation(
                label="P11 空的 suspect 集合会明说",
                path=_PROBE,
                old=b'        print("  none")',
                new=b"        pass",
                guard_file=_PROBE_TESTS,
                guards=("test_an_empty_suspect_set_says_none",),
                note="删掉 none 提示",
            ),
            Mutation(
                label="P12 --event-id 覆盖三张表",
                path=_PROBE,
                old=b'    for table in ("predictions", "simulated_trades", "event_market_links"):',
                new=b'    for table in ("predictions",):',
                guard_file=_PROBE_TESTS,
                guards=(
                    "test_event_dump_covers_the_three_tables",
                    "test_event_dump_marks_a_table_that_has_no_rows",
                ),
                note="只遍历一张表",
            ),
            Mutation(
                label="P13 无行的表会明说 (no row)",
                path=_PROBE,
                old=b'            print("  (no row)")',
                new=b"            pass",
                guard_file=_PROBE_TESTS,
                guards=("test_event_dump_marks_a_table_that_has_no_rows",),
                note="删掉 (no row)",
            ),
            Mutation(
                label="P14 --event-id 输出受开关约束",
                path=_PROBE,
                old=b"        if args.event_id:",
                new=b"        if True:",
                guard_file=_PROBE_TESTS,
                guards=("test_event_dump_is_skipped_without_the_flag",),
                note="无条件输出",
            ),
            Mutation(
                label="P15 缺失 DB 时以具名路径退出",
                path=_PROBE,
                old=b"    if not path.is_file():",
                new=b"    if False:",
                guard_file=_PROBE_TESTS,
                guards=("test_a_missing_db_exits_naming_the_path",),
                note="守卫恒不触发（冒 sqlite3 原始错误）",
            ),
        ),
    ),
    MutationSet(
        key="voided-trade",
        title="作废预测时冻结其模拟交易（V1–V6）",
        rationale=(
            "void_prediction 原本只把 predictions 置 voided，不碰 simulated_trades，"
            "于是每次非真实结算都留一笔永远 open 的模拟交易 —— 它既不进 closed 统计"
            "（没有结算值），也不会被 dangling 普查抓到（事件还在），只是永远挂在"
            "list_open_trades 里冒充持仓。V1 锁住那个调用点；V2/V3 锁住表重建的两个"
            "静默失效点（漏拷 id、CHECK 退回两态）；V4 锁住「终态必须是 voided 而不是"
            "继续 open」。"
            "§51 补 V5/V6：写入侧有终态还不够，**读侧要读对那个终态**。"
            "count/list_voided_trades 是本批新增的读侧（此前 voided 行在任何列表里都"
            "看不见，见 §50.2/§51），它们的状态过滤写错时**不抛错**，只是安静地列出"
            "别的行、或给出别的计数 —— 所以 V5/V6 打的就是那个 token。"
            "⚠️ V5 的守卫用例里 closed 故意放**两条**：若 voided 与 closed 都只有 1 条，"
            "「读对」与「读错」返回同一个数，断言对两种实现都成立 —— 那正是本仓反复"
            "踩到的空头守卫形态（§31/§33/§34）。"
        ),
        mutations=(
            Mutation(
                label="V1  void_prediction 同时冻结该事件的交易",
                path=_PRED_STORE,
                old=b"_maybe_void_trade(event_id)",
                new=b"None  # mutated: the trade stays open",
                guard_file=_PRED_STORE_TESTS,
                guards=("test_void_prediction_also_voids_the_open_simulated_trade",),
                note="去掉回调（交易永远留在 open）",
            ),
            Mutation(
                label="V2  表重建显式拷贝 id",
                path=_TRADES_STORE,
                old=b'col_csv = ", ".join(cols)',
                new=b'col_csv = ", ".join(c for c in cols if c != "id")',
                guard_file=_TRADES_TESTS,
                guards=("test_migrate_widens_the_status_check_and_preserves_row_ids",),
                note="把 id 排除出列清单（AUTOINCREMENT 会按 1..N 重编号）",
            ),
            Mutation(
                label="V3  status 的 CHECK 接受第三个终态",
                path=_TRADES_STORE,
                old=b"CHECK (status IN ('open','closed','voided'))",
                new=b"CHECK (status IN ('open','closed'))",
                guard_file=_TRADES_TESTS,
                guards=(
                    "test_migrate_widens_the_status_check_and_preserves_row_ids",
                    "test_void_trade_moves_the_open_trade_out_of_the_open_list",
                ),
                note="CHECK 退回两态（void_trade 的 UPDATE 触发 IntegrityError）",
            ),
            Mutation(
                label="V4  void_trade 把交易置为 voided 终态",
                path=_TRADES_STORE,
                old=b"exit_reason='voided', exit_time=?, status='voided', updated_at=?",
                new=b"exit_reason='voided', exit_time=?, status='open', updated_at=?",
                guard_file=_TRADES_TESTS,
                guards=(
                    "test_void_trade_moves_the_open_trade_out_of_the_open_list",
                    "test_voided_trade_is_excluded_from_trade_stats",
                ),
                note="只写 exit_reason，status 仍是 open",
            ),
            Mutation(
                label="V5  count_voided_trades 读的终态",
                path=_TRADES_STORE,
                old=b'return _count_trades("voided")',
                new=b'return _count_trades("closed")',
                guard_file=_TRADES_TESTS,
                guards=("test_voided_readers_return_only_voided_rows",),
                note=(
                    "§51：读侧换一个状态 —— /trades/voided 的 total 会变成已平仓数。"
                    "这条守卫要求 voided 与 closed 的条数**不相等**（否则同一个 1 "
                    "既可能是对的也可能是读错了），所以该用例里 closed 有两条"
                ),
            ),
            Mutation(
                label="V6  list_voided_trades 的 WHERE 状态",
                path=_TRADES_STORE,
                old=b"WHERE status='voided'",
                new=b"WHERE status='closed'",
                guard_file=_TRADES_TESTS,
                guards=("test_voided_readers_return_only_voided_rows",),
                note="§51：列出的行换成已平仓（作废列表显示完全错误的交易）",
            ),
        ),
    ),
    MutationSet(
        key="whitelist-fixtures",
        title="白名单 / 枚举常量的「循环 + 钉子」与关系型守卫（W1–W30 + §45 合并的 W31 + §47 的 W32 + §51 的 W33/W34 + §53 的 W35）",
        rationale=(
            "§22.2.3 那批契约型常量的补齐（§二十四）。每条都删掉一个成员、或把别名目标"
            "拼错，断言对应守卫转红。三件事必须写在这里："
            "① **故意不登记的一半**。W1–W8 在 §24.3 里是「循环 + 钉子」成对落的："
            "循环（`for m in CONST`）负责『新增成员会被跑到』，钉子负责『删成员看得见』。"
            "本 harness 只断言 RED，所以只登记钉子那一腿 —— 7 条循环腿实测**保持 GREEN**"
            "（循环里的 CONST 就是被测对象，删成员只是少跑一轮）。那个实测结论留在 §24.5，"
            "不要为了『让它也红』而改循环，那会把盲区藏起来。"
            "② W9–W11 是**关系型守卫**：oracle 是另一个常量（`_STAGE_MAP` / 两个 kernel 引擎副本），"
            "所以不需要钉字面量也承重。"
            "③ W12 是**收敛**那批工作留下的 identity 守卫（§21.5 / §21.7）。它放回的是一份"
            "**功能等价**的本地副本 —— 行为用例全绿，只有 `assertIs` 会红；这正是"
            "『行为测试对重复是盲的』的可执行版本。"
            "④ W15–W19 是其后两批生产修复留下的守卫：W15/W16 来自 §28"
            "（`gbm_engine` 与 challenge adapter 补回规范形 `quarterfinal`/`semifinal`），"
            "W17/W18/W19 来自 §29（方向白名单里的三个 `_STRONG_DIRECTIONS` 副本）。"
            "⚠️ W17/W18 是**一次登记**的：§29.6 实测 `guardrail_service` 与 "
            "`market_quality_service` 两个副本本来就**承重**（删 `NO` 即红），所以当时就能登记。"
            "W19 是**补完用例才登记**的：同一段字节的第三个副本 "
            "`execution_quality_service._STRONG_DIRECTIONS` 当时是**空头守卫**"
            "（删 `NO` 后该文件 12 个用例**全绿**——因为共用 helper `_rec()` 把 "
            "`direction` 默认成 `\"YES\"`，`raw_direction == \"NO\"` 那条路径从未被走到）。"
            "§31 补了 `\"NO\"` 用例（`test_raw_direction_no_is_downgraded_like_yes`）"
            "把该成员变成承重，才回来登记。**这条留痕的意义**：`\"同样的三个副本\"` 原先"
            "「两个承重、一个空头」的差异不是抄错，而是**测试覆盖面**的差异 —— "
            "空头守卫不能用『改循环』糊过去，只能用**补用例**消掉。"
            "⑤ W20–W23 来自 §32 的**方向成员集空头普查**（把 §29.2 表里剩下的 12 处逐一"
            "削 `NO` 跑测试）。结论是**承重与否不能按『名字/语义是否同义』推**："
            "同一批里语义同义的那几处反而大半是空头（`replay/metrics` 与四个内联处），"
            "而异名的三个常量 `_DIRECTIONAL` / `_CALLED_DIRECTIONS` / `_VALID_DIRECTIONS` "
            "**全是承重**（W21/W22/W23），连同名的第五个 `_STRONG_DIRECTIONS` 副本"
            "（`source_reliability`，W20）也是承重。→ 只有变异能定论。"
            "⚠️ **W24 又是『补完用例才登记』的一例**：`replay/metrics._STRONG_DIRECTIONS`"
            "（5 个同名副本里的第 5 个）在 §32 普查里是**空头**（该文件的 "
            "`downgrades_caused` / `conflicts_with_final` 两个用例都用 `YES` 侧）。"
            "§33 补了 `NO` 侧两条用例后才登记 —— 与 W19 同款。"
            "⑥ W25–W29 来自 §34 的**内联空头提取**：把 5 处字面量 `{\"YES\", \"NO\"}` 提成"
            "具名常量（`_REPORTED_DIRECTIONS` / `_STRONG_DISPLAY_DIRECTIONS` / "
            "`_TRADABLE_DIRECTIONS` / `_STRONG_EVENT_DIRECTIONS` / `_STRONG_DIRECTIONS`），"
            "**先补用例让它承重、再登记**——5 处里有 4 处在提取时都是空头。"
            "⚠️ 变异打在**常量定义行**而不是调用行：一个常量可能被 N 处消费"
            "（`_STRONG_EVENT_DIRECTIONS` 被证据门与计算门各读一次），"
            "打定义行只需一条变异，但**守卫必须 N 条**——否则删掉 NO 后"
            "「另一处调用点失效」这件事没有任何用例看得见。"
            "⚠️ 特别注意 W25/W26/W27 三个被提取处的旧字面量所在文件**本来就有 `\"NO\"` 字样**"
            "（`simulated_trade_store` 15 次、`quality_metrics` 8 次）却仍是空头 —— "
            "『测试里有 NO』既不充分也不必要，判据只有变异。"
            "⑦ W30 来自 §36 的**死分支清理**，但它登记的不是被删掉的那段代码"
            "（死分支写回去**行为零变化**，阶段②永远过不了，见 §36.3），"
            "而是**清理之后活下来的那条早退**：`_build_rationale_body` 的"
            "`if consensus_level == \"none\":` 一旦不可达，就落到尾部的保守模板，"
            "而该文件里唯一看着 rationale 的断言只有 `assertNotEqual(..., \"\")` ——"
            "**尾部模板同样非空**，所以「分支从未执行」这件事对旧断言天然盲（§36.4 探针 B）。"
            "§36.6 因此补了一条**精确字符串**断言再登记 —— 与 W19/W24 同款："
            "**空头守卫只能靠补用例消掉**，且这一条同时给出一个警告："
            "**删死代码本身不可登记，别把它和它旁边的空头混为一谈**。"
            "⑦ §45 把 W17/W18/W19/W20/W24/W29 这六条**合并成一条 W31**：六个 `_STRONG_DIRECTIONS` "
            "副本并为 `app/utils/direction_vocabulary.py` 的一处定义，六条变异收成一条、挂 **6 个 "
            "guard_file**、**9 条守卫** —— 本仓第一条多 guard_file 变异（该能力见 set `multi-guard-file`）。"
            "编号沿用「只增不改」：W17–W30 保持历史含义，W31 是新号并在标签里注明它取代了哪六条。"
            "⑧ §47 把剩余七个方向常量也迁进 `direction_vocabulary.py`（只收敛**位置**，语义、"
            "容器、注释一概不变），于是 W21/W22/W23/W25/W26/W27/W28 的 `path` 一并改指该模块 —— "
            "**针一个字节都没改**，因为定义行是逐字搬过去的。若针要跟着改，那说明搬动掺了改动。"
            "⚠️ W25 与 W32 改写的是**同一行**（`_REPORTED_DIRECTIONS` 的定义），故同属一个 "
            "`group`：整组 apply 只留前一条，显式同时点名两条会被 `_apply_preflight` 拒掉。"
            "W32 是 §47 新增的**容器守卫**：把该常量从 `tuple` 改成 `set`。这一条只有 "
            "`direction_vocabulary` 自己的测试看得见 —— `_REPORTED_DIRECTIONS` 是八个常量里"
            "唯一被**迭代**消费的（`for d in _REPORTED_DIRECTIONS:`），而本仓不固定 "
            "`PYTHONHASHSEED`，改成 set 会让 `by_direction` 的 JSON 键序在运行间漂移。⑨ W8 如今**多挂了一个 `guard_file`**（`tests/test_world_cup_analytics_routes.py`）：`/analytics/engine-stats` 原本自带第三份引擎键字面量 `engine_keys`，删掉那份副本、改指 `ENGINE_NAMES` 之后，这条变异才会同时打红两处 —— 第二个 guard 文件本身就是「那条路由确实从名单派生」的可执行证明。它的 oracle 是 `ENGINES` 注册表，不拿 `ENGINE_NAMES` 比 `ENGINE_NAMES`：那样是同义反复，删成员照样绿。"
            "⑩ §51 补 W33/W34：**生产者的分支链**也是这套白名单的输入端。`parse_fixture` 只"
            "产出规范形，所以「消费者认得哪些名字」这件事的上游是它的分支链 —— 而 §50.1 实测"
            "其中两支根本不存在（`round_of_32` / `third_place` 的两种拼写都落到 `unknown`）。"
            "两条变异各废掉一支（改成 `elif False:`），各挂 **两个 guard_file**：生产者自己的"
            "单测钉返回值的**精确形状**，`test_knockout_stage_whitelist_consistency.py` 的 "
            "`ProducerVocabularyTests` 钉「生产者吐出的每个名字消费者都认」这条**关系**。"
            "⚠️ W33 **必须**挂第二个 guard_file 才算诚实：跨模块那条关系对 `third_place` 是"
            "**部分盲**的 —— 「3rd Place Final」含「final」，删掉该分支后它落回 `final`，而 "
            "`final` 本来就在淘汰赛集合里，于是只有 openfootball 拼写（「Match for third "
            "place」）那一腿会红。**精确断言与关系断言不是互相替代，是互相补盲**。"
            "⑪ §53.38 补 W35，与 W8 **同组**：W8 删成员（集合变了），W35 把前两个成员"
            "**对调**（集合一模一样，只有 dict 的插入序变了）。两者改写的是**同一行**字节 —— "
            "一个删、一个换序 —— 所以整组 apply 只留前一条，显式同时点名两条会被 "
            "`_apply_preflight` 拒掉。W35 证明的是「顺序也是契约」，而它此前**没有任何守卫**："
            "`ENGINE_NAMES` 有 4 处 `for engine in ENGINE_NAMES` 建 dict 的消费者"
            "（质量报告的 `by_engine` 与 trends 子表、`/analytics/engine-stats`），"
            "键序跟随插入序 ⇒ 顺序印进发布的 JSON，但全仓对它的比较**清一色是集合式**"
            "（`set(ENGINE_NAMES) == set(ENGINES) | {\"integrated\"}`、`assertIn`、集合推导），"
            "连 `by_engine` 的断言也全是按键索引（`report[\"by_engine\"][\"elo_odds\"]`）—— "
            "对键序天然盲。§53.37.5 的有界普查把这一类（**有序容器 + 被迭代进 dict**）"
            "数到**恰好 2 个**：`DIRECTIONS` 早有顺序钉，`ENGINE_NAMES` 没有；W35 的 guard "
            "就是为此新写的 `test_engine_names_order_is_pinned`。"
        ),
        mutations=(
            Mutation(
                label="W1  _ALLOWED_ROLES 保留 bench",
                path=_AVAILABILITY,
                old=b'_ALLOWED_ROLES = {"star", "starter", "rotation", "bench"}',
                new=b'_ALLOWED_ROLES = {"star", "starter", "rotation"}',
                guard_file=_T_AVAILABILITY,
                guards=("test_the_role_list_is_pinned",),
                note="从校验白名单删掉一个成员（缺它就整份快照作废）",
            ),
            Mutation(
                label="W2  _ALLOWED_STATUSES 保留 suspended",
                path=_SCHEDULE,
                old=b'"cancelled", "suspended",',
                new=b'"cancelled",',
                guard_file=_T_SCHEDULE,
                guards=("test_the_status_list_is_pinned",),
                note="删掉一个合法状态（整份快照会被判为畸形）",
            ),
            Mutation(
                label="W3  _SETTLED_STATUSES 保留 determined",
                path=_KALSHI,
                old=b'_SETTLED_STATUSES = {"settled", "finalized", "closed", "determined"}',
                new=b'_SETTLED_STATUSES = {"settled", "finalized", "closed"}',
                guard_file=_T_KALSHI,
                guards=("test_the_settled_status_list_is_pinned",),
                note="删掉一个已结算状态（已结算市场会被重新当作候选）",
            ),
            Mutation(
                label="W4  _SKIP_PLAYER_STAT_PATHS 保留 substitutes.bench",
                path=_STATISTICS,
                old=b'"substitutes.bench",',
                new=b"",
                guard_file=_T_STATISTICS,
                guards=("test_the_skip_list_is_pinned",),
                note="删掉一条屏蔽路径（簿记字段会被当成球员统计）",
            ),
            Mutation(
                label="W5  _SUFFIXES 保留 ac.",
                path=_CLUB_ELO,
                old=b'_SUFFIXES = ("afc", "fc.", "cf.", "ac.", "fc", "cf", "ac", "sc")',
                new=b'_SUFFIXES = ("afc", "fc.", "cf.", "fc", "cf", "ac", "sc")',
                guard_file=_T_CLUB_ELO,
                guards=("test_the_affix_tables_are_pinned",),
                note="删掉一个后缀（该拼写不再折叠到同一个 Elo key）",
            ),
            Mutation(
                label="W6  _PREFIXES 保留 sc",
                path=_CLUB_ELO,
                old=b'_PREFIXES = ("afc", "fc.", "cf.", "ac.", "fc", "cf", "ac", "sc")',
                new=b'_PREFIXES = ("afc", "fc.", "cf.", "ac.", "fc", "cf", "ac")',
                guard_file=_T_CLUB_ELO,
                guards=("test_the_affix_tables_are_pinned",),
                note="删掉一个前缀（同上）",
            ),
            Mutation(
                label="W7  _MLB_PLAYOFF_TYPES 保留 W",
                path=_MLB_ADAPTER,
                old=b'_MLB_PLAYOFF_TYPES = {"D", "L", "F", "W", "P"}',
                new=b'_MLB_PLAYOFF_TYPES = {"D", "L", "F", "P"}',
                guard_file=_T_MLB_ADAPTER,
                guards=("test_the_playoff_game_types_are_pinned",),
                note="删掉世界大赛（该轮次静默降级为常规赛）",
            ),
            Mutation(
                label="W8  ENGINE_NAMES 保留 gbm",
                path=_QUALITY,
                old=b'ENGINE_NAMES = ("elo_odds", "hybrid", "gbm", "integrated")',
                new=b'ENGINE_NAMES = ("elo_odds", "hybrid", "integrated")',
                guard_file=(_T_QUALITY, _T_ANALYTICS_ROUTES),
                guards=(
                    "test_engine_names_match_the_runnable_registry",
                    "test_engine_stats_serves_one_bucket_per_runnable_engine",
                ),
                group="engine-names-literal",
                note=(
                    "从引擎名单里删掉一个成员：质量报告的 by_engine 与 /analytics/engine-stats "
                    "各少一个键，都不报错。第二个 guard 文件钉的是后一个消费者 —— "
                    "那条路由原本自带第三份字面量，删掉副本之后这条变异才能同时打红两处"
                ),
            ),
            Mutation(
                label="W35 ENGINE_NAMES 前两成员对调（同集合、不同顺序）",
                path=_QUALITY,
                old=b'ENGINE_NAMES = ("elo_odds", "hybrid", "gbm", "integrated")',
                new=b'ENGINE_NAMES = ("hybrid", "elo_odds", "gbm", "integrated")',
                guard_file=_T_QUALITY,
                guards=("test_engine_names_order_is_pinned",),
                group="engine-names-literal",
                note=(
                    "§53.38：成员一个不少，只是把前两个换个次序。质量报告的 by_engine、"
                    "trends 的 by_engine 子表、以及 /analytics/engine-stats，都是 "
                    "`for engine in ENGINE_NAMES` 建 dict —— 键序跟随插入序，所以发布出去的 "
                    "JSON 键序就是它；然而 W8 与其它所有守卫比的都是**集合**，"
                    "这条重排在补 test_engine_names_order_is_pinned 之前没有任何测试看得见。"
                    "与 W8 共用同一段 old 字节（一个删成员、一个改顺序），故同组互斥"
                ),
            ),
            Mutation(
                label="W9  _STAGE_MAP 别名目标拼错 (quarterfinals)",
                path=_PIPELINE,
                old=b'"quarter_final": "quarterfinal",',
                new=b'"quarter_final": "quarterfinals",',
                guard_file=_T_STAGE,
                guards=("test_stage_map_values_partition_into_knockout_and_group_stages",),
                note="别名指向一个既非淘汰赛也非小组赛的规范形",
            ),
            Mutation(
                label="W10 _KNOCKOUT_STAGES 增一个死成员 (round_of_64)",
                path=_PIPELINE,
                old=b'    "third_place",\n}',
                new=b'    "third_place", "round_of_64",\n}',
                guard_file=_T_STAGE,
                guards=("test_pipeline_knockout_names_are_producible_by_the_stage_map",),
                note=(
                    "normalizer 永远产生不出来的名字 → is_knockout 恒 False。"
                    "§51 把死成员从 round_of_32 换成 round_of_64：前者在这一批里"
                    "已经变成 _STAGE_MAP 可达的规范形，放回去就不再是死成员，"
                    "于是「不可达成员」这条守卫会失去它唯一的触发器"
                ),
            ),
            Mutation(
                label="W11 elo_odds_engine._KNOCKOUT_STAGES 删掉 final",
                path=_ELO_ENGINE,
                old=b'"semifinal", "semi_final", "final",',
                new=b'"semifinal", "semi_final",',
                guard_file=_T_STAGE,
                guards=("test_kernel_engines_recognise_every_canonical_knockout_stage",),
                note="引擎副本少一个规范形（该轮次回落到『允许平局』的安全默认）",
            ),
            Mutation(
                label="W12 共享助手放回功能等价的本地副本",
                path=_LIMITLESS,
                old=b"from app.utils.failure_policy import fail_closed_empty_list",
                new=_LOCAL_COPY_SHIM,
                guard_file=_T_EVENT_SOURCE_UTILS,
                guards=("test_text_number_and_probability_helpers_are_the_shared_objects",),
                note="行为等价，只有 identity 守卫能红（行为测试对重复是盲的）",
            ),
            Mutation(
                label="W13 _KNOWN_KINDS 成员违反代码自身的归一化",
                path=_SPORTS_FACT,
                old=b'    "match_state",',
                new=b'    "Match_State",',
                guard_file=_T_SPORTS_FACT,
                guards=(
                    "test_every_known_kind_passes_validation",
                    "test_the_whitelist_matches_the_documented_kinds",
                ),
                note="代码先 .lower() 再查名单 → 混合大小写的成员永远匹配不上",
            ),
            Mutation(
                label="W14 _KNOWN_KINDS 删掉 availability",
                path=_SPORTS_FACT,
                old=b'    "availability",',
                new=b"",
                guard_file=_T_SPORTS_FACT,
                guards=("test_the_whitelist_matches_the_documented_kinds",),
                note="删掉一个成员（该 kind 会被静默拒绝为 unsupported_kind）",
            ),
            Mutation(
                label="W15 gbm_engine._KNOCKOUT_STAGES 删掉规范形 quarterfinal",
                path=_GBM_ENGINE,
                old=b'"quarterfinal",',
                new=b'"quarterfinals",',
                guard_file=_T_STAGE,
                guards=("test_kernel_engines_recognise_every_canonical_knockout_stage",),
                note="§28 修的就是这条：引擎副本缺规范形 → QF 回落到「允许平局」",
            ),
            Mutation(
                label="W16 challenge adapter._HIGH_RISK_STAGES 删掉规范形 semifinal",
                path=_CHALLENGE_ADAPTER,
                old=b'"semifinal",',
                new=b'"semifinals",',
                guard_file=_T_STAGE,
                guards=(
                    "test_challenge_adapter_rates_every_canonical_knockout_stage_high_risk",
                ),
                note="§28 新增的关系型守卫（challenge ⊇ pipeline）失效",
            ),
            Mutation(
                label="W21 direction_vocabulary._DIRECTIONAL 删掉 NO",
                path=_DIRECTION_VOCABULARY,
                old=b'_DIRECTIONAL = ("YES", "NO")',
                new=b'_DIRECTIONAL = ("YES",)',
                guard_file=_T_PREDICTION_CALIBRATION,
                guards=(
                    "test_case_insensitive",
                    "test_no_recommendation_no_outcome_correct",
                    "test_full_resolution_no_correct",
                ),
                note=(
                    "§32：NO 不再算可检验方向 → direction_correct 直接返回 None；"
                    "§47 定义迁入共享模块，针一个字节未改"
                ),
            ),
            Mutation(
                label="W22 direction_vocabulary._CALLED_DIRECTIONS 删掉 NO",
                path=_DIRECTION_VOCABULARY,
                old=b'_CALLED_DIRECTIONS = frozenset({"YES", "NO"})',
                new=b'_CALLED_DIRECTIONS = frozenset({"YES"})',
                guard_file=_T_REVIEW_QUEUE_DETECTORS,
                guards=(
                    "test_high_value_downgraded_fires_for_a_no_call_too",
                    "test_fires_when_a_confident_no_call_resolves_yes",
                    "test_a_partial_resolution_above_zero_counts_as_yes",
                ),
                note=(
                    "§32：NO 不再算「已下注的调用」→ NO 调用不再触发任何 review 触发器；"
                    "§47 定义迁入共享模块，针一个字节未改"
                ),
            ),
            Mutation(
                label="W23 direction_vocabulary._VALID_DIRECTIONS 删掉 NO",
                path=_DIRECTION_VOCABULARY,
                old=b'_VALID_DIRECTIONS = {"YES", "NO"}',
                new=b'_VALID_DIRECTIONS = {"YES"}',
                guard_file=_T_DOMAIN_RELIABILITY,
                guards=(
                    "test_no_direction_correct_support",
                    "test_no_direction_wrong_support",
                ),
                note=(
                    "§32：NO 不再算合法方向 → 该记录的来源归因候选为空；"
                    "§47 定义迁入共享模块，针一个字节未改"
                ),
            ),
            Mutation(
                label="W25 direction_vocabulary._REPORTED_DIRECTIONS 删掉 NO",
                path=_DIRECTION_VOCABULARY,
                old=b'_REPORTED_DIRECTIONS = ("YES", "NO")',
                new=b'_REPORTED_DIRECTIONS = ("YES",)',
                guard_file=_T_TRADES_STORE,
                guards=("test_by_direction_reports_both_strong_directions",),
                group="reported-directions",
                note=(
                    "§34：by_direction 不再统计 NO 侧 —— 该文件原有断言只有『作废交易不出现」"
                    "这一负向腿，删 NO 后照样成立。§47 定义迁入共享模块；与 W32 改写同一行，"
                    "同组互斥"
                ),
            ),
            Mutation(
                label="W26 direction_vocabulary._STRONG_DISPLAY_DIRECTIONS 删掉 NO",
                path=_DIRECTION_VOCABULARY,
                old=b'_STRONG_DISPLAY_DIRECTIONS = ("YES", "NO")',
                new=b'_STRONG_DISPLAY_DIRECTIONS = ("YES",)',
                guard_file=_T_QUALITY_METRICS,
                guards=("test_anomalies_flags_wide_spread_not_downgraded",),
                note=(
                    "§34：宽价差旗标的异常列表漏掉 NO 侧 —— 旧断言用 WAIT 记录做负控，"
                    "对分支是否存在天然盲；§47 定义迁入共享模块，针一个字节未改"
                ),
            ),
            Mutation(
                label="W27 direction_vocabulary._TRADABLE_DIRECTIONS 删掉 NO",
                path=_DIRECTION_VOCABULARY,
                old=b'_TRADABLE_DIRECTIONS = ("YES", "NO")',
                new=b'_TRADABLE_DIRECTIONS = ("YES",)',
                guard_file=_T_EVENT_INTELLIGENCE,
                guards=("test_persist_events_opens_the_trade_as_no_for_a_no_recommendation",),
                note=(
                    "§34：NO 推荐被折回 YES 开仓 —— 该回退只在 entry_edge == 0 时可观测"
                    "（后文会按 edge 符号覆写方向）；§47 定义迁入共享模块，针一个字节未改"
                ),
            ),
            Mutation(
                label="W28 direction_vocabulary._STRONG_EVENT_DIRECTIONS 删掉 NO",
                path=_DIRECTION_VOCABULARY,
                old=b'_STRONG_EVENT_DIRECTIONS = ("YES", "NO")',
                new=b'_STRONG_EVENT_DIRECTIONS = ("YES",)',
                guard_file=_T_CONCLUSION_CHALLENGE,
                guards=(
                    "test_strong_no_conclusion_without_support_is_insufficient_evidence",
                    "test_small_probability_change_counts_for_a_no_conclusion",
                ),
                note=(
                    "§34：NO 不再算强方向 —— 证据门与计算门各读一次该常量，故守卫必须两条，"
                    "缺一条即半空头；§47 定义迁入共享模块，针一个字节未改"
                ),
            ),
            Mutation(
                label="W30 decision_quality._build_rationale_body 的 none 早退改成不可达",
                path=_DECISION_QUALITY,
                old=b'    if consensus_level == "none":\r\n        return',
                new=b'    if consensus_level == "NEVER":\r\n        return',
                guard_file=_T_DECISION_QUALITY,
                guards=("test_missing_both_inputs_rationale_names_the_missing_breakdown",),
                note="§36：早退不可达后落到尾部的保守模板，而旧断言只查非空 —— 尾部模板同样非空，故对「这条分支从未执行」天然盲",
            ),
            Mutation(
                label=(
                    "W31 共享 direction_vocabulary.STRONG_DIRECTIONS 删掉 NO"
                    "（合并 W17/W18/W19/W20/W24/W29）"
                ),
                path=_DIRECTION_VOCABULARY,
                old=b'STRONG_DIRECTIONS = ("YES", "NO")',
                new=b'STRONG_DIRECTIONS = ("YES",)',
                guard_file=(
                    _T_GUARDRAIL,
                    _T_MARKET_QUALITY,
                    _T_EXECUTION_QUALITY,
                    _T_SOURCE_RELIABILITY,
                    _T_REPLAY_METRICS,
                    _T_DECISION_QUALITY,
                ),
                guards=(
                    "test_fires_for_no_when_llm_degraded",
                    "test_two_rules_fire_preserves_existing_reason",
                    "test_downgrade_no_to_wait_when_score_low",
                    "test_wide_spread_downgrades_no_direction",
                    "test_raw_direction_no_is_downgraded_like_yes",
                    "test_downgraded_flag_true_when_suggested_differs",
                    "test_downgrades_caused_counted_for_a_no_base",
                    "test_conflict_case_collected_for_a_no_phase",
                    "test_rule4_empty_breakdown_downgrades_no_to_wait",
                ),
                note=(
                    "§45 合并组①：一处定义、六个文件消费 —— 9 条守卫跨 6 个文件"
                    "（本仓第一条多 guard_file 变异）；缺任何一个消费点都会少一条腿"
                ),
            ),
            Mutation(
                label="W32 direction_vocabulary._REPORTED_DIRECTIONS 从 tuple 改成 set",
                path=_DIRECTION_VOCABULARY,
                old=b'_REPORTED_DIRECTIONS = ("YES", "NO")',
                new=b'_REPORTED_DIRECTIONS = {"YES", "NO"}',
                guard_file=_T_DIRECTION_VOCABULARY,
                guards=("test_reported_directions_stays_an_ordered_sequence",),
                group="reported-directions",
                note=(
                    "§47：唯一被迭代消费的方向常量 —— 改成 set 后 by_direction 的键序随 "
                    "PYTHONHASHSEED 漂移（本仓不固定它）。守卫只存在于本模块自己的测试里，"
                    "所以这条变异同时证明那个新测试文件确实被跑到"
                ),
            ),
            Mutation(
                label="W33 parse_fixture 的 third_place 分支",
                path=_MATCH_SERVICE,
                old=b'    elif "third" in round_info or "3rd" in round_info:',
                new=b"    elif False:  # mutated: the third-place branch never fires",
                guard_file=(_T_MATCH_SERVICE, _T_STAGE),
                guards=(
                    "test_third_place_mapping_from_api_football_spelling",
                    "test_the_two_new_rounds_are_classified_knockout",
                ),
                note=(
                    "§51：废掉生产者认第三名的那一支 —— 「3rd Place Final」含「final」，"
                    "于是被读成决赛本身（§50.1）。跨模块那个 guard 对这一支只是**部分**可见，"
                    "所以必须并列单测那条精确断言"
                ),
            ),
            Mutation(
                label="W34 parse_fixture 的 round_of_32 分支",
                path=_MATCH_SERVICE,
                old=b'    elif "32" in round_info:',
                new=b"    elif False:  # mutated: the round-of-32 branch never fires",
                guard_file=(_T_MATCH_SERVICE, _T_STAGE),
                guards=(
                    "test_round_of_32_mapping",
                    "test_the_two_new_rounds_are_classified_knockout",
                ),
                note=(
                    "§51：2026 赛制的 32 强回落到 unknown，而每个消费者都把 unknown 读作"
                    "非淘汰赛（允许平局）—— 与 §28 修 QF/SF 时同一条失效路径"
                ),
            ),
        ),
    ),
    MutationSet(
        key="restore-loopback-probe",
        title="恢复前的存活探测是否绕过环境代理（L1–L4）",
        rationale=(
            "§35 的环境归因：本机设了 HTTP_PROXY、没设 NO_PROXY，而 urllib 只看环境变量、"
            "不看 WinINET 的 ProxyOverride → 探测请求根本没到 localhost:8000，代理替一个无人"
            "监听的端口回了 502，而 502 也算「有人在听」→ 警告恒亮。修法是 loopback 目标显式"
            "用空 ProxyHandler。四条变异各锁一半：L1 锁「只有 loopback 才绕过」这一步的方向；"
            "L2 锁「localhost 这个名字算 loopback」；L3 锁「127.x / ::1 也算 loopback」；"
            "L4 锁「绕过用的 handler 确实是空的」——ProxyHandler() 无参会读回环境代理，等于没修。"
            "四者缺一，修法都能在某一半上失效而整套守卫仍绿。"
        ),
        mutations=(
            Mutation(
                label="L1 loopback 目标改用默认 opener（绕过方向反转）",
                path=_RESTORE_STORES,
                old=b"    if not _is_loopback_url(url):\r\n        return urllib.request.urlopen(request, timeout=timeout)",
                new=b"    if _is_loopback_url(url):\r\n        return urllib.request.urlopen(request, timeout=timeout)",
                guard_file=_T_RESTORE_PROBE,
                guards=(
                    "test_a_loopback_probe_builds_an_opener_with_no_proxy",
                    "test_a_remote_probe_keeps_the_default_opener",
                    "test_a_proxy_answering_everything_cannot_make_a_dead_port_look_alive",
                ),
                note="§41：方向反了 —— loopback 走环境代理（正是 §35 的病灶），远端反而被剥掉代理",
            ),
            Mutation(
                label="L2 _is_loopback_url 的 localhost 名字分支返回 False",
                path=_RESTORE_STORES,
                old=b'    if host == "localhost":\r\n        return True',
                new=b'    if host == "localhost":\r\n        return False',
                guard_file=_T_RESTORE_PROBE,
                guards=(
                    "test_loopback_urls_are_recognised",
                    "test_a_proxy_answering_everything_cannot_make_a_dead_port_look_alive",
                ),
                note="§41：名字分支失效 —— 默认 URL 是 http://localhost:…，等于修法在本机默认配置下没生效",
            ),
            Mutation(
                label="L3 _is_loopback_url 的 IP 字面量分支返回 False",
                path=_RESTORE_STORES,
                old=b"        return ipaddress.ip_address(host).is_loopback",
                new=b"        return False",
                guard_file=_T_RESTORE_PROBE,
                guards=(
                    "test_loopback_urls_are_recognised",
                    "test_a_proxy_answering_everything_cannot_make_a_dead_port_look_alive",
                ),
                note="§41：只认名字不认地址 —— 127.0.0.1 / ::1 会重新落到代理后面",
            ),
            Mutation(
                label="L4 绕过用的 ProxyHandler 退回读环境变量（无参构造）",
                path=_RESTORE_STORES,
                old=b"    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))",
                new=b"    opener = urllib.request.build_opener(urllib.request.ProxyHandler())",
                guard_file=_T_RESTORE_PROBE,
                guards=("test_a_loopback_probe_builds_an_opener_with_no_proxy",),
                note="§41：ProxyHandler() 无参会 getproxies() —— 分支对了、handler 不空，等于没修",
            ),
        ),
    ),
    MutationSet(
        key="multi-guard-file",
        title="harness 新能力：一条变异可挂多个 guard_file（H1–H2）",
        rationale=(
            "§42.4/§42.6 方案 B 的前置：13 条方向常量变异是「一个文件一条」，因为 "
            "Mutation.guard_file 原本是单值。一旦针指向的是**共享定义**，那条变异就必须同时"
            "锚定**每一个消费它的文件**的用例，否则合并常量会以「看起来只是少几条变异」的"
            "方式真丢锚定。本 set 用两条变异把这个能力的两半分别钉住："
            "① **H1 锁「每个文件都跑到」**—— 把 _run_guards 从「把全部 guard_file 都交给 "
            "pytest」改成「只跑第一个」；这正是该能力的唯一消费点，只跑第一个后其余文件的"
            "守护全部静默失效。"
            "② **H2 锁「每个文件都红」**—— 把阶段②的判据从「每个 guard_file 各至少一红」"
            "退化成「≥1 红」（§45.5 记下的缺口，§46 补上）。这一条只在多文件变异上分得开："
            "单文件变异两种读法等价，而 W31 挂了 6 个文件，退化成「≥1 红」后某个文件的空头"
            "守卫会被兄弟文件的红遮住。"
            "生产消费点已由 §45 的 W31 落地（6 个 guard_file / 9 条守卫）——"
            "§44 当时写的「尚无生产消费点、且由一条测试钉住」已随之作废。"
        ),
        mutations=(
            Mutation(
                label="H1 _run_guards 只把第一个 guard_file 交给 pytest",
                path=_MUTATION_VERIFY,
                old=_GUARD_FILES_LOOP,
                new=_GUARD_FILES_LOOP_FIRST,
                guard_file=_T_MUTATION_VERIFY,
                guards=("test_run_guards_passes_every_named_file_to_pytest",),
                note=(
                    "§42 方案 B 前置：多 guard_file 的消费点 —— 只取第一个会让跨文件守护形同虚设。"
                    "§46 把 _run_guards 从「一次调用传全部文件」改成「每文件一次调用」，"
                    "故针随之由 argv 的展开改为那次循环"
                ),
            ),
            Mutation(
                label="H2 every_file_red 退化成「只要有一条红」（≥1 而非 N/N）",
                path=_MUTATION_VERIFY,
                old=_EVERY_FILE_RED_STRICT,
                new=_EVERY_FILE_RED_LOOSE,
                guard_file=_T_MUTATION_VERIFY,
                guards=("test_every_file_red_requires_every_file_not_just_one",),
                note=(
                    "§46：阶段②的判据退化 —— W31 的 6 个文件里只要 1 个红就当作全部红，"
                    "某个文件的空头守卫会被兄弟文件遮住"
                ),
            ),
        ),
    ),
    MutationSet(
        key="eol-audit",
        title="行尾审计工具（E1/E2/E3/E4/E5）",
        rationale=(
            "工具本身就是「工作树行尾未受损」的收据，所以它的假红不是无害的 ——"
            "会让下一个读者不再信任那个绿色。E1/E2 各去掉修复的一半，"
            "E3/E4 则保证这个修复不等于「干脆不报损伤」；"
            "E5 守卫换掉逐文件 git show 之后新写的批量读 —— 那里游标错位是静默的。"
        ),
        mutations=(
            Mutation(
                label="E1  去掉 head_lf == 0，把「HEAD 本就混合」说成「曾经纯 CRLF」",
                path=_EOL_AUDIT,
                old=b"    if head_crlf > 0 and head_lf == 0 and tree_lf > 0:",
                new=b"    if head_crlf > 0 and tree_lf > 0:",
                guard_file=_T_EOL_AUDIT,
                guards=(
                    "test_a_head_that_was_already_mixed_is_not_called_pure_crlf",
                ),
                note=(
                    "判词与它自己打印的画像矛盾：HEAD(crlf=1,lf=29) 不是 pure CRLF。"
                    "2026-10-01 的上线审查里 favicon.ico 收到的正是这句话"
                ),
            ),
            Mutation(
                label="E2  关掉二进制跳过，二进制文件被当文本画像",
                path=_EOL_AUDIT,
                old=b"        if rel in binary:",
                new=b"        if False and rel in binary:",
                guard_file=_T_EOL_AUDIT,
                guards=("test_a_binary_file_is_skipped_and_the_walk_stays_green",),
                note=(
                    "ICO/PNG/WOFF/PDF 与 backend/data/gbm_*.txt 里的 CR/LF 与行尾无关；"
                    "当成文本读就会凭空造出 mixed 损伤，--all 于是永远红"
                ),
            ),
            Mutation(
                label="E3  mixed 判据的 and 改成 or，正常 CRLF 检出被判成损伤",
                path=_EOL_AUDIT,
                old=b"    if tree_crlf > 0 and tree_lf > 0:",
                new=b"    if tree_crlf > 0 or tree_lf > 0:",
                guard_file=_T_EOL_AUDIT,
                guards=("test_a_crlf_checkout_is_not_damage",),
                note=(
                    "「HEAD 存 LF、工作树 CRLF」是本仓的正常状态；改成 or 会把整棵树报成损伤"
                ),
            ),
            Mutation(
                label="E4  binary_paths() 返回空集，判据退化成「没有二进制」",
                path=_EOL_AUDIT,
                old=(
                    b"    return frozenset(path for token, path in tracked_eol() "
                    b"if token == BINARY_TOKEN)"
                ),
                new=b"    return frozenset()",
                guard_file=_T_EOL_AUDIT,
                guards=(
                    "test_the_favicon_is_a_binary_path",
                    "test_the_binary_set_is_the_index_text_token",
                ),
                note=(
                    "E2 关掉消费端，E4 掏空供应端 —— 两个方向都要有守卫，"
                    "否则「集合恒为空」能通过其余全部断言"
                ),
            ),
            Mutation(
                label="E5  批量读 HEAD 时游标少前进 1 字节，其后的文件全部错位",
                path=_EOL_AUDIT,
                old=b"                cursor += size + 1",
                new=b"                cursor += size",
                guard_file=_T_EOL_AUDIT,
                guards=("test_the_bulk_read_agrees_with_git_show",),
                note=(
                    "git 在 blob 之后补一个换行；少前进 1 字节，下一个 header 就落在空行上，"
                    "解析不出来便把这个文件判成 NEW —— 不抛错、不报警，"
                    "只有与 git show 逐字节比对才能抓住"
                ),
            ),
        ),
    ),
    MutationSet(
        key="market-snapshot-order",
        title="市场快照两条读法的 tiebreaker（S1/S2）",
        rationale=(
            "`MarketSnapshotStore` 的两处 ORDER BY 过去只看 captured_at；并列时行序无定义，"
            "实测是「先插入者胜」—— 也就是把更旧的一次采样当成最新（§53.39 那条偶发红的形状，"
            "由 §53.43 用正控复现）。两条变异各打掉一个方向的 tiebreaker，而且必须是两条："
            "两个读法喂的是两个不同消费者（标量 latest / 折线序列），没有一条守卫能同时盖住。"
            "故意不把两者并成一条「两个 ORDER BY 都改」的变异 —— 那样一条守卫变红时"
            "分不清是哪一处坏了。"
        ),
        mutations=(
            Mutation(
                label="S1  get_latest_snapshot 的 id 次序反向：并列时报最旧那行",
                path=_MARKET_SNAPSHOT_STORE,
                old=b"                    KernelMarketSnapshot.id.desc(),",
                new=b"                    KernelMarketSnapshot.id.asc(),",
                guard_file=_T_SPORT_MARKET_STORES,
                guards=("test_get_latest_snapshot_tie_returns_the_later_row",),
                note=(
                    "并列时行序无定义。改回 asc 之后 tie 返回首插行，"
                    "`latest` 的含义从「最后写入」变成「最早写入」——"
                    "这正是 §53.39 那条偶发红的形状（断言 `assert 0.6 == 0.65`）。"
                    "标量断言之外，这个值还会被 sport_markets 路由 / edge_detector / "
                    "market_liquidity 三个消费者继续用下去。"
                ),
            ),
            Mutation(
                label="S2  get_snapshots 的 id 次序反向：并列段在折线上倒序",
                path=_MARKET_SNAPSHOT_STORE,
                old=b"                    KernelMarketSnapshot.id.asc(),",
                new=b"                    KernelMarketSnapshot.id.desc(),",
                guard_file=_T_SPORT_MARKET_STORES,
                guards=("test_get_snapshots_tie_keeps_insertion_order",),
                note=(
                    "同一段并列数据 asc 下是插入序、desc 下是倒序。这条与 S1 打的是"
                    "**不同**的字节段（另一处 ORDER BY），所以互为反向改写也不构成同组冲突。"
                    "折线图对倒序表现为可视假象，只有断言行序的守卫能看见。"
                ),
            ),
        ),
    ),
    MutationSet(
        key="market-audit-timestamps",
        title="市场快照：无时间戳行不得成为端点、也不得进序列（A1–A4）",
        rationale=(
            "「没有时间戳的快照不是价格路径上的点」这条口径有两个消费者，各有各的失效方式。"
            "A1 打掉 `audit_summary` 的分区：删掉谓词后 NULL 行落回时间轴并抢走 first/last 之一"
            "（§53.47 用一行**合法** NULL 在未变异代码上先证到的形状）。A2 打掉排序键的类型一致："
            "键随输入变类型时 `sorted` 抛 TypeError，而两个 audit 路由的 `audit_summary(...)` 调用点"
            "（sport_markets.py:151 / :176）外面没有 try ⇒ 500 不是降级。"
            "（这两个行号 §53.48 记的是 140 / 165：该节给本文件加了 docstring 与序列门调用后整体下移，"
            "§53.49 又加了一行 import ⇒ 现值 151 / 176。）"
            "A3 打掉序列门的过滤：`get_price_path` 照发之后，"
            "`GET /snapshots/{match_id}` 又把无法定位的行当成**最早**的点画给图表（§53.48 统一的正是这一处）。"
            "A4 直接塌掉那个**共享谓词** `is_timestamped`——它的守卫**跨两个文件**，"
            "正是「一处谓词、两个门」的证据。四条必须分开：一条断言审计端点取值、一条断言两种时间类型混排、"
            "一条断言序列门的点位与计数、一条证明谓词的确是两个门共用的那一份。"
        ),
        mutations=(
            Mutation(
                label="A1  无时间戳行被放回时间轴：NULL 抢走 first/last 之一",
                path=_MARKET_SNAPSHOT_STORE,
                old=b"            (s, p) for s, p in priced if is_timestamped(s)",
                new=b"            (s, p) for s, p in priced",
                guard_file=_T_ECE_MARKET_AUDIT,
                guards=(
                    "test_audit_summary_untimestamped_row_does_not_become_an_endpoint",
                    "test_audit_summary_all_untimestamped_rows_are_not_available",
                ),
                note=(
                    "去掉谓词后 `timestamped` 就是 `priced`，`untimestamped_count` 恒为 0。"
                    "NULL 行不抛错（键是 `str(None) == 'None'`），但会按字符串参与排序并把"
                    "first/last 之一抢走 —— 「静默读错」而不是「响亮失败」，所以只有断言端点值的"
                    "守卫能看见。"
                ),
            ),
            Mutation(
                label="A2  排序键退回「有什么返什么」：datetime 与 str 混比",
                path=_MARKET_SNAPSHOT_STORE,
                old=b"            return t.isoformat() if isinstance(t, datetime) else str(t)",
                new=b"            return t",
                guard_file=_T_ECE_MARKET_AUDIT,
                guards=(
                    "test_audit_summary_mixed_timestamp_types_still_order_chronologically",
                ),
                note=(
                    "这近乎原实现（`return t or \"\"`）。键的类型随输入而定："
                    "同一组里既有 `datetime`（真实读法）又有 ISO 字符串（既有夹具）时，"
                    "`sorted` 抛 `TypeError: '<' not supported between instances of "
                    "'datetime.datetime' and 'str'` ⇒ 两个 audit 路由 500。"
                    "只有「一个序列里两种类型并存」那条守卫能看见它 —— "
                    "全 `datetime` 或全字符串都不触发，这也正是它此前一直没被发现的原因。"
                ),
            ),
            Mutation(
                label="A3  序列门不过滤：无时间戳行又被当成最早的点发给图表",
                path=_MARKET_SNAPSHOT_STORE,
                old=b"        points = [row for row in rows if is_timestamped(row)]",
                new=b"        points = list(rows)",
                guard_file=_T_SPORT_MARKET_ROUTES,
                guards=(
                    "test_snapshots_timeseries_drops_and_counts_untimestamped_rows",
                ),
                note=(
                    "`get_price_path` 是 `GET /snapshots/{match_id}` 唯一的取数口。照发之后，"
                    "SQLite 仍把 NULL 排在最前 ⇒ 图表把一行**无法定位**的价格画成最早的点，"
                    "而且 `untimestamped_count` 同时变成 0（连声明也没了）。"
                    "守卫断言的是确切点位列表与计数，所以「行数变了但没报」也会被抓到。"
                ),
            ),
            Mutation(
                label="A4  共享谓词塌成恒真：两个门同时失去这条口径",
                path=_MARKET_SNAPSHOT_STORE,
                old=b"    return _ts(row) is not None",
                new=b"    return True",
                guard_file=(_T_ECE_MARKET_AUDIT, _T_SPORT_MARKET_ROUTES),
                guards=(
                    "test_audit_summary_untimestamped_row_does_not_become_an_endpoint",
                    "test_snapshots_timeseries_drops_and_counts_untimestamped_rows",
                ),
                note=(
                    "`is_timestamped` 是审计门与序列门**共用**的那一份判据（§53.48 的存在理由）。"
                    "恒真之后审计的端点被 NULL 抢走、序列门把 NULL 发给图表 —— 因此这条的守卫"
                    "**跨两个文件**，两个都必须红：这正是「一处谓词、两个门」的可验证表述。"
                    "注意模块里**没有第二份**「有没有时间戳」的判断（`_ts` 也只剩这一处消费者），"
                    "所以塌掉它没有别的地方能挡住。"
                ),
            ),
        ),
    ),
    MutationSet(
        key="market-snapshot-contract",
        title="快照端点：合约要么被发布、要么被渲染成精确 interface（C1–C2）",
        rationale=(
            "§2 的三件套（具名 `response_model` → 导出白名单 → 重生成 TS）在**行为上是隐形的**。"
            "C1 删掉路由的 `response_model=MarketSnapshotsResponse`：返回的 dict 一字节没变，"
            "本文件其余断言照绿，而 OpenAPI 丢字段契约、生成的 TS 也不再描述这个 payload —— "
            "兄弟守卫 `test_operational_readiness.test_event_routes_declare_response_models` "
            "只走 events router，作用域覆盖不到 sport-markets。"
            "C2 把 `MarketSnapshotSeries` 从 `_frontend_export` 的**导入**里挪走（`__all__` 只是文档，"
            "生成器用 `inspect.getmembers` 收集**命名空间**）：它仍会作为嵌套 interface 出现，"
            "但不再是 root ⇒ 不再被临时置 `extra='forbid'` ⇒ `json2ts` 补回 `[k: string]: unknown`，"
            "生成类型比它替换掉的手写 interface **更松**，而 `tsc` 照样绿。"
            "两条各钉一半，且必须分开：一条落在路由对象上，一条落在导出模块上。"
        ),
        mutations=(
            Mutation(
                label="C1  路由丢掉 response_model：合约不再发布",
                path=_SPORT_MARKET_ROUTES,
                old=b'@router.get("/snapshots/{match_id}", response_model=MarketSnapshotsResponse)',
                new=b'@router.get("/snapshots/{match_id}")',
                guard_file=_T_SPORT_MARKET_ROUTES,
                guards=("test_snapshots_route_publishes_its_response_model",),
                note=(
                    "删掉 `response_model=` 后返回体不变 ⇒ 该文件其余断言全绿，只有这条**静态**"
                    "断言会红 —— 它断言的正是「装配本身」，不是 payload。"
                    "（同族的 events 守卫在 test_operational_readiness，作用域不含 sport-markets。）"
                ),
            ),
            Mutation(
                label="C2  导出模块不再保留该名字：interface 从闭合变成宽松",
                path=_FRONTEND_EXPORT,
                old=b"    MarketSnapshotSeries,",
                new=b"    MarketSnapshotSeries as _MarketSnapshotSeriesUnused,",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_market_snapshot_models_are_export_roots",),
                note=(
                    "**导入改成别名**，不是把名字换掉：`old` → `new` 里源名字仍在，"
                    "所以 `from app.models.event import ...` 照常成立，`__all__` 里的 "
                    "`\"MarketSnapshotSeries\",` 也仍在（它只是个字符串），"
                    "**只有**命名空间里少了 `MarketSnapshotSeries` 这个名字。"
                    "守卫断言它是 `_frontend_export` 的属性（= root），而那正是 interface 闭合的成因。"
                    "🔴 **这条写法是 §53.53 更正过的，原文是 `new=b\"    _MarketSnapshotSeriesUnused,\"`（把名字换掉）。"
                    "换成别名之前，它虽然也能让守卫变红，但红的是**另一件事**：测试体里那行 "
                    "`from app.models import _frontend_export` 直接抛 "
                    "`ImportError: cannot import name '_MarketSnapshotSeriesUnused'`。"
                    "实测两种写法都红，但只有别名那种红在**可达性**上 —— "
                    "前者对「模块能导入但名字没了」这种真实回归**永远不会红**，"
                    "而那才是守卫要拦的东西。同族的 D1 一并改了。"
                ),
            ),
        ),
    ),
    MutationSet(
        key="frontend-type-closure",
        title="前端类型闭合：可达的普通模型必须登记为根，开放集是拍过板的，且 api.ts 不自己声明副本（D1–D6、J1–J95）",
        rationale=(
            "§2 的「登记为根」规则（§53.49 立、§53.50 推广）。`pydantic2ts` 用 "
            "`inspect.getmembers(module)` 收集**模块命名空间里**的模型当根；只有根会被临时置 "
            "`extra='forbid'`，从而拿到 `additionalProperties: false` 并渲染成**闭合** interface。"
            "只被 `$ref` 引用到的模型没有这个键 ⇒ `json2ts` 补 `[k: string]: unknown` ⇒ "
            "比后端契约**更松**（普通 `BaseModel` 是 `extra='ignore'`，闭合才是忠实形状）——"
            "而 `tsc` 照样绿，所以唯一的拦截点是**静态**守卫。"
            "D1 把 `EvidenceProfile` 从 `_frontend_export` 的**导入**里挪走：它仍会作为嵌套 interface "
            "出现，但不再是 root；守卫问的是**模型互相引用**（生产者驱动），所以在**重新生成之前**就变红 —— "
            "与 C2 互补：C2 钉 MarketSnapshot* 这一族的具体成员，D1 钉「可达即须登记」这条**规则**本身。"
            "排除 `ConfigDict(extra='allow')` 一族曾经是刻意的（开放是**故意**的，塌成闭合才是失真）；"
            "§53.51 逐条读过生产者与所有 try/except 回退块之后，这条排除只剩两个理由成立："
            "`EventRecord`（承 `normalize_event_record()` 的 schema 演进）与 `EventSource`"
            "（9 个 adapter 真会往里加 `question` / `status` / `close_time` / `bid_ask` / `chain` 等键）；"
            "另外七个 overlay 已收成闭合 ⇒ 排除集就是 `test_generate_types.DELIBERATELY_OPEN`。"
            "D2/D3 钉这条**两向**规则，因为它们共用的守卫断言的是**集合相等**：D2 把已闭合的"
            "重新开放（集合多一员），D3 把必须开放的收掉（集合少一员）。只禁一个方向的守卫"
            "会放过另一个 —— 而「收得太狠」恰恰是这次改动的失败模式，不是理论风险。"
            "§53.52 把同一条「生成物是唯一真源」的规则推到了**消费侧**：`api.ts` 是手写模块，"
            "没有生成器写它，所以它自己声明一份 overlay 副本时**编译、eslint、tsc 全绿** —— "
            "报文经 `api.ts` 的 `api<T>` 助手（`const data = await res.json() as T`）到达，"
            "那个 cast 对形状什么都不主张。D4/D5/D6 原本各踩守卫的三个**互不重叠**的判据：D4 踩「名字以 `Overlay` 结尾」、"
            "D5 踩「字段必须指生成名」、D6 踩「体内任意位置出现索引签名」。"
            "为什么必须是三条：只补一条就分不清守卫哪半是承重的 —— "
            "「副本没回来」与「引用仍读生成类型」是两件事，§53.52 同时做了这两件，"
            "一个绿结果会把它们混为一谈。D6 特意把索引签名放在**非末行**："
            "首版探测器只看最后一个非空行 ⇒ 会漏掉它，改成「体内任意位置」后这条才红。"
            "🔴 **§53.59 节点② 把这三条的靶子全部换过 —— 换的是判据，不只是地址**"
            "（§4.3「针的语义会随生产代码变化」，本轮一次性兑现三条）："
            "① **D5 原来那条判据没有了**。它打的是**手写 `DecisionReport` 体内**的一个 overlay 字段，"
            "守卫当时断言「字段必须指生成名」；节点② 把那份手写体退役成别名之后，"
            "那个字段不复存在，而且**没有任何守卫还在断言字段指向** ⇒ "
            "把这条重锚到别的 interface 字段上也不会变红，那就不再是一条变异（§4.2 阶段②「变异后必红」）。"
            "所以 D5 改打**同族里唯一还没人覆盖**的那条判据："
            "`test_decision_report_reads_the_generated_contract` 的**第三个**断言 —— "
            "别名必须**逐字**是 `GeneratedDecisionReport`（现有 148 条里没有任何一条碰过它，已 grep 确认）。"
            "覆盖的仍然是「契约来源必须是生成类型」，只是从**字段那层**上移到**别名那层**："
            "节点② 之后 `DecisionReport` 在 `api.ts` 只剩别名这一处声明。"
            "② **D4 原来的分工声明按字面从来不成立**：它的 `new` 体内**同时**写着 `[key: string]: unknown;` ⇒ "
            "名字分支与索引分支会**同时**踩，于是「D4 证明名字分支承重」这句话"
            "在索引分支坏掉时**照样为真**。去掉那一行之后 D4 只踩名字分支，"
            "与只踩索引分支的 D6 才真的互不重叠。"
            "③ D6 与 J13 只是换宿主/换地址（`DecisionReport` 体内 → `CategoryCoverage` 体内；"
            "被退役的手写体 → `EventMoversResponse` 的别名行），判据未变。"
            "三条新锚都是「对全部 api.ts 针逐个算命中窗口」选出来的：实测各命中 1 次、两两字节区间不相交；"
            "三条的 `guards` 也同步换成改名后的 `test_decision_report_reads_the_generated_contract`。"
            "§53.53 把同一条规则推进到 `GET /events/{id}/history`：`event.py` 发布了三个具名模型"
            "（`EventAuditSnapshot` / `TrendSummary` / `EdgeTrajectorySummary`），"
            "前端三个手写类型（`types.ts` 的 `HistorySnapshot` / `Trend`、`api.ts` 的 `EdgeTrajectory`）"
            "改成读生成名。J1–J4 各踩**一条互不重叠**的判据，且分布在**三个文件**上："
            "J1 踩「`api.ts` 不得再声明副本」、J2 踩「`/history` 调用点必须读已发布信封」"
            "（守卫的**正向**那半）、J3 踩同一负向判据的**另一个文件** `types.ts`、"
            "J4 踩「信封字段必须指名模型」这条**模型身份**判据。"
            "为什么 J1 不能靠 D4–D6 替代：那三条的守卫判据是「名字以 `Overlay` 结尾」或「体内有索引签名」，"
            "而一份去掉三个字段的 `EdgeTrajectory` 副本两条都不占 ⇒ 老守卫**天然盲** —— "
            "这正是新判据必须**按名**判的理由。"
            "为什么 J3 与 J1 要分开：同一条判据作用在**两个文件**上，只扫 `api.ts` 的守卫会放过 `types.ts`，"
            "而 `types.ts` 恰恰是写着「DO NOT hand-write fields that exist in the Pydantic models」"
            "却**没有**守卫的那个文件（规则在注释里 ≈ 空头）。"
            "🔴 **有意不登记的一条变异**：不再为「把 `EventAuditSnapshot` 从 `_frontend_export` 的 import 里挪走」"
            "补一条 —— 它踩的是 D1 已经证明承重的那**同一条**判据（`test_every_reachable_plain_model_is_registered`），"
            "同一个规则换个成员再加一条只会让计数虚胖，不会多发现一个盲区。"
            "⚠️ **这条推理的前提本轮被改过**：§53.53 发现 D1 原文（把名字换掉）虽然变红，但红在 "
            "`ImportError` 上、**不在**可达性判据上（≈空判据）⇒ 已把 D1 改成**导入别名**写法并更正 note。"
            "现在「D1 证明该判据承重」这句话才成立于它所声称的那条判据；"
            "同族的 C2（`market-snapshot-contract`）同批更正。复述本段时必须带上这条前提。"
            "⚠️ 但它的**结论有前提**：若哪天有人把那条守卫的判据从「可达即须登记」改成只认具体名单，"
            "这条推理就过期了，届时要补。"
            "🔴 另有一条**确实缺失**的判据，登记备查、本轮不做：`EventHistoryResponse` 五个字段从带默认值"
            "收紧成 required（生成的 TS 里由 `?:` 变必填）**没有**独立变异 —— "
            "它只在 `test_closed_overlay_interfaces_have_no_tail` 间接体现在「三个新模型必须闭合」上；"
            "要单独钉住可选性，得新写一条读生成文件的正向断言（`event_id: string;` 而非 `event_id?: string;`），"
            "而那条需要先重新生成，超出本 set 的静态判据口径。"
            "⚠️ **这条缺口笔记的前提已被 §53.54 部分改写**：可选性确实能钉，但**不必**读生成文件 —— "
            "`model_fields[...].is_required()` 在**模型层**就能读到「有没有默认值」，"
            "而生成文本里 `?:` 的有无正是它的函数。所以 §53.54 的新守卫改用 `is_required()`，"
            "仍是静态判据、不需要 json2ts。"
            "§53.54 把同一条规则推进到 `GET /events/edges/fresh`：`event.py` 发布了 `EdgeSeriesPoint` / "
            "`FreshEdgeItem`（`edges` 由 `list[dict[str, Any]]` 改成 `list[FreshEdgeItem]`），"
            "信封的恒写字段收紧成 required，前端 `api.ts` 两个调用点改读 `api<FreshEdgesResponse>`、"
            "手写 `FreshEdge` / `EdgePoint` 退役成别名，`decisions/page.tsx` 的 `as FreshEdge[]` 强转一并清掉。"
            "J5–J12 各踩**一条互不重叠**的判据，**分布在三个文件**上："
            "J5 踩「`api.ts` 不得再声明副本」（**别名刻意留着**，所以红的只可能是这半）、"
            "J6 踩同一条判据在 `types.ts` 上的那一份（§4.2：同判据两个文件要两条）、"
            "J7 踩「别名必须指向生成名」这半（**不插 interface** —— 没有它就无法区分"
            "「守卫拦住了副本」与「守卫恰好在别名那半红了」，即 J5/J6 会让这半变成空头）、"
            "J8 / J9 各踩「调用点必须读已发布信封」这半，且**两个调用点各一条**"
            "（§4.2：一成员被 N 处消费却只补 1 处，另一处的绕过永远不红）、"
            "J10 踩「`edges` 必须指名元素模型」、J11 踩「元素自己的 `edge` 必须指名 §53.53 的模型」、"
            "J12 踩「条件写入的键必须保留默认值」—— 这正是上面那条缺口笔记的方向相反那一半。"
            "为什么 J8 之后还要 J9：`api.ts` 里 `    api<FreshEdgesResponse>(` 这个**前缀**两处都占（实测 2 次），"
            "所以 J8 必须用整行针、J9 必须用跨行针；两条不能合成一条。"
            "为什么 J5 要留别名：`assertIn`（别名）与 `assertNotIn`（副本）落在一个测试里，"
            "一改别名就同时红，红了就分不清判据哪半承重 —— 于是 J5 留别名、J7 不插副本，"
            "两条各自把一半**隔离**出来。"
            "🔴 **有意不登记的一条**：恒写字段（`count` / `total` / `limit` / `offset` / `classification` / "
            "`classification_totals`）加回默认值不补变异 —— 它与 J12 走**同一个** "
            "`is_required()` 断言形状，再加一条只让计数虚胖。"
            "⚠️ 前提：这两组仍共用同一个断言形状；若被拆成两组独立断言，另一侧就要补。"
            "⚠️ 另一条前提：`FreshEdgeItem.series` 的同一性判据（`list[EdgeSeriesPoint] | None`）"
            "本轮也**没有**独立变异，理由同 J11（同一个 `assertEqual(annotation, …)` 形状）；"
            "若哪天该断言被拆开，要补一条把 `series` 退回 `list[dict[str, Any]]` 的变异。"
            "§53.55 把同一条规则推进到 `GET /events/movers`：`event.py` 发布了 `EventMoverItem`"
            "（`movers` 由 `list[dict[str, Any]]` 改成 `list[EventMoverItem]`），信封的 `count` 收紧成 required，"
            "前端 `api.ts` 的唯一调用点改读 `api<EventMoversResponse>`、手写 `Mover` 退役成别名，"
            "并把 `app-nav.test.tsx` 里只有 3 个字段的 `trend` 夹具补成 14 个（无类型 mock ⇒ 永远不会红）。"
            "J13–J21 各踩**一条互不重叠**的判据，**分布在四个文件**上："
            "J13 踩「`api.ts` 不得再声明副本」（**别名刻意留着**）、"
            "J14 踩同一判据在 `types.ts` 上的那一份（§4.2：同判据两个文件要两条）、"
            "J15 踩「别名必须指向生成名」这半（**不插 interface**，与 J13 一起把两半隔离）、"
            "J16 踩「调用点必须读已发布信封」、J17 踩「`movers` 必须指名元素模型」、"
            "J18 踩「元素自己的 `trend` 必须指名 `TrendSummary`」、"
            "J19 踩「条件写入的键必须保留默认值」（`assertFalse(is_required())` 那半）、"
            "J20 踩方向相反的「恒写字段必须 required」（`assertTrue(is_required())` 那半）、"
            "J21 踩**路由装饰器上的 `response_model_exclude_unset`** —— 这是本节唯一一个"
            "「不落在生成类型上」的判据，也是本节新增的守卫。"
            "🔴 **J20 与 §53.54「有意不登记恒写字段加回默认值」并不矛盾，两者的前提不同**："
            "§53.54 的取舍是针对**它自己那个**守卫（两组共用同一个 `is_required()` 断言形状，"
            "再加一条只让计数虚胖）；而本节更重要的实测事实是 —— "
            "**本 set 里此前没有任何一条变异性红过 `assertTrue(is_required())` 这一半**"
            "（J12 红的是 `assertFalse` 那半），所以 J20 是**首次覆盖**，不是重复。"
            "⚠️ 由此**新发现一条同族缺口、本轮只登记不修**：§53.54 那个守卫的 `envelope` 循环"
            "（`count` / `total` / `limit` / `offset` / `classification` / `classification_totals`）"
            "的 `assertTrue` 那半**同样从未被任何变异性红过**；要补就再加一条"
            "「`FreshEdgesResponse.count` 加回默认值」的变异。"
            "§53.56 把同一条规则推进到 `GET /events/{id}/similar`：`event.py` 发布 `SimilarEventItem`"
            "（`similar` 由 `list[dict[str, Any]]` 改成 `list[SimilarEventItem]`），信封三键收紧成 required，"
            "前端 `api.ts` 的唯一调用点改读 `api<SimilarEventsResponse>`、手写 `SimilarEvent` 退役成别名。"
            "🔴 **与 §53.55 相对的一条**：这条端点**不需要** `response_model_exclude_unset`，因为 "
            "`find_similar` 对每条 item **无条件写全部 8 个键**（可空的那四个写 `None`，不是省掉）"
            "⇒ **任何默认值都是错的**，J27 钉的就是这个前提（而且它顺带证明「无默认值」是「不需要那面旗子」的原因）。"
            "🔴 **J28 是方向相反的另一类错误**：把 `direction: str | None` 收成 3 值 `Literal` —— "
            "看着像「更严」，实则比 `Probability.direction: str` 这份被镜像的声明**更窄**，"
            "会把将来的第四个方向值变成 422（22 205 条观测到的词表恰是 3 值，但那是观测不是保证）。"
            "🔴 **两条新针的锚点刻意避开既有针占用的字节**（J22 用 `PendingLink` 而非 J13 的 `DecisionReport`；"
            "J23 用一行无关导出而非 J24 的别名行；J29 从 docstring 尾句向上取上界，在 J26 的针**之前**结束）—— "
            "对指针/针重叠的规避沿用 §53.55 的做法：**换上下文方向，不硬塞 `group`**。"
            "§53.58 把同一条规则推进到 `GET /events/predictions/recent`，元素是本 set 目前**最宽**的一个："
            "`event.py` 发布 `RecentPredictionItem`（35 键 = store 的 33 列 + 两个 title），"
            "`predictions` 由 `list[dict[str, Any]]` 改成 `list[RecentPredictionItem]`，信封五键收紧成 required，"
            "`api.ts` 的唯一调用点改读 `api<RecentPredictionsResponse>`，手写 `PredictionRecord`（18 键）退役成别名。"
            "🔴 **这一节带出一个与前面各节方向相反的新风险**：元素是**闭合**的（普通 `BaseModel` 默认 "
            "`extra='ignore'`），所以这一次「声明得比生产者少」不再是「类型偏松」，而是 "
            "**pydantic 静默把多余的键从响应里删掉、直接改线上字节**。J43 是本 set 第一条朝这个方向的变异 —— "
            "此前所有变异最多让类型变松，没有一条会改变返回的 JSON 内容。"
            "J40–J46 各踩**一条互不重叠**的判据：J40 踩「`api.ts` 不得再声明元素副本」（**别名刻意留着**）、"
            "J41 踩同一条负向判据的**第三个名字**（信封副本；§4.2：只变异第一个只能证明循环存在，"
            "不能证明它走到了后面 —— 与 §53.57 的 J30/J31 同款先例）、"
            "J42 踩「调用点必须读已发布信封」（且用**不含路径**的针，与 §53.56 J25 的同行针互补）、"
            "J43 踩「`predictions` 必须指名元素模型」（静默）、"
            "J44 踩方向相反的「恒写信封键必须 required」、"
            "J45 踩「可空列必须保留 `| None`」（`float | None` 收成 `float`：拿观测当保证，比 schema **更紧**）、"
            "J46 踩「取值忠实性」：`direction_correct` 由 store 原样吐出 SQLite 的 0/1/NULL，"
            "声明成 `bool` 会把 1/0 改写成 JSON 布尔 —— 与 §53.57 的 J37 **方向相反**（那节是 `bool` 漂成 `int`），"
            "两条合起来说明「谁转换、谁不转换」在两个端点上各有一个答案，**不能按名字推**。"
            "🔴 **有意不登记的一条**：`assertNotIn` 那个循环的**第二个**名字（生成名 `RecentPredictionItem` 本身）"
            "不补变异 —— 它与 J41 走**同一个** `assertNotIn(name, api_bodies)` 断言形状、同一条判据；"
            "J40 证明该判据承重、J41 证明它走到了第一个名字之后，再加一条只让计数虚胖"
            "（§53.54「恒写字段加回默认值不补变异」的同款理由）。"
            "⚠️ 前提：这三个名字仍共用同一个断言形状；若守卫改成按名分立的断言，第二、三个名字要各补一条。"
            "🔴 **另一条有意不登记的**：`RecentPredictionItem` 其余 33 个字段的注解不逐条补变异 —— "
            "它们与 J45/J46 走同一个 `assertEqual(annotation, want)` 循环，"
            "覆盖的是**两类的代表**（可空 `float` 的宽窄、可空 `int` 的取值忠实性），而不是 35 条同形变异。"
            "⚠️ 前提：若那张 35 键注解表哪天从表驱动拆成 35 条独立断言，这份覆盖就退化成只覆盖两个成员，届时要补。"
            "🔴 **J44 的锚点为什么从 docstring 尾句起算**：本文件另有 `FreshEdgesResponse` 含"
            "「docstring 结束 + 四个恒写信封键」这段字节（短针实测 2 次），而本节还要**避让 J43 的针**"
            "（元素那一行紧随其后）⇒ 从 docstring 尾句向上取上界、止于 `offset: int`、**不含**元素那一行。"
            "代价：那句 docstring 措辞一改这条针就断，届时应由 `count_needle != 1` 立刻报出来。"
            "§53.60 补 J63、收掉 §53.59 留下的**同族最后一个缺口**：`GET /events/{event_id}/decision`"
            "返回的就是 `build_decision_report` 的 dict **本身**（没有信封可命名），它的 "
            "`response_model` 一直是裸 `FlexibleResponse` ⇒ OpenAPI 只发 "
            "`#/components/schemas/FlexibleResponse`，报文带着**未声明形状**到达浏览器 —— "
            "正是 53.59 在姊妹端点 `/decisions/open` 上终结掉的那个状态。"
            "J63 的守卫判据是**路由对象身份**（`route.response_model is DecisionReport`），"
            "不是源码文本：裸 `FlexibleResponse` 是 `extra='allow'`、零字段，退回去之后"
            "**仍然校验通过、仍然 200、`tsc` 仍然全绿**，唯一变化是已发布的 schema 变回不透明体 ⇒ "
            "具名契约的拦截点只能是运行时事实。"
            "🔴 **两个前提必须跟着 J63 走**："
            "① **具名不是零字节差**（探针 59 用例、`flexible`/`committed` 同树产出）："
            "每个域内响应的 `$.probability` 子对象键序从 `estimated,baseline` 变为 `baseline,estimated` —— "
            "等长、canonical 相同 ⇒ **纯键序**；老板**拍板接受**（两端口径由此统一到模型序；"
            "备选的「改 `DecisionProbability` 字段序」会把 `/decisions/open` 的键序翻回去，未取）。"
            "复述「具名零字节差」之前必须带上这条前提 —— 它**不成立**。"
            "② **`DecisionReport` 会重塑 pass-through overlay**（§53.59 引入、已活在 "
            "`/decisions/open` 上，本端点只是继承）：四个 overlay 模型**全部字段带默认值**，"
            "具名会把记录里缺失的键实体化成默认值、并丢弃模型未声明的键。"
            "实测**当前不可达**：全库 257 条记录里 48 条 `market_quality` 是旧 schema、"
            "48 条 `llm_telemetry` 部分缺失、5 条 `source_reliability` 缺 `error`，"
            "**全都没有 committed prediction**；171 个有 prediction 的事件里 overlay "
            "**要么缺席要么与模型字段集完全一致**（0 条被重塑）⇒ 记录备查、本轮不另立变异；"
            "哪天一条旧形记录获得了 prediction，就要拍板。",
            "§53.61 补 J64–J71、把 **`/events/predictions/calibration`** 与 "
            "**`/events/predictions/calibration/buckets`** 具名为 "
            "`PredictionCalibrationResponse` / `CalibrationBucketSummaryResponse`"
            "（元素 `CalibrationCategory` / `CalibrationSegment` / "
            "`CalibrationBucketCell`）。与 §53.60 相反，本轮**具名就是零字节差**："
            "探针（`.workbuddy/audit-2026-10-03/probe_5361.py`，10 用例同树两态）"
            "域内 7/7 字节级一致 —— 前提是**模型字段序按生产者 dict 序声明**"
            "（`calibration_summary` 两条路径键集相同且逐键写全、"
            "`calibration_bucket_summary` 同理，无 `exclude_unset` 需求）；"
            "三个 CONTROL（`n=None` / `segment_min_samples=None` / "
            "category `brier_score=None`）全部 200→500，证明校验真实生效。"
            "前端三个手写副本退役成别名，其中 `PredictionCalibration` 有四处"
            "**比生产者松**（`segment_min_samples?` / `segments?` 等可选）—— "
            "收口时 `history/page.tsx` 的 `EMPTY_PRED` 占位对象 "
            "`segment_min_samples: null` 被 `tsc` 抓出（生产者恒写 settings int），"
            "改 0 并注释；`page.test.tsx` 夹具同形修正（tsc 不查测试文件、"
            "vitest 只转译 ⇒ 夹具只能人工跟）。",
            "§53.62 补 J72–J79、把 **`/loop/status`** 具名为 `LoopStatusResponse`"
            "（嵌套 `LoopSchedulerState` / `LoopStatusStorage` / `LoopStatusCounts` / "
            "`ReviewQueueSummary` / `ReviewQueueSeverityBucket` / `LoopRunView`，"
            "`calibration` **复用** 53.61 的模型）。本轮新事实：**一个 URL 两种形状**"
            "（run details 在 write key 之后）⇒ `LoopRunView` 按行序声明全 8 字段、"
            "其中 3 个 detail-only 字段带默认值、路由**同改动补 "
            "`response_model_exclude_unset=True`**（§53.55 第 4 检查点的变体："
            "这次旗子不是「不需要」而是「必须有」——没有它 subset 路径会长出 3 个"
            "生产者从未写过的 null 键）。探针（`probe_5362.py`，9 用例同树两态）"
            "域内 **6/6 字节级一致**（含 315KB 的 details 路径与 int age 保形），"
            "CONTROL 3/3 200→500。消费侧：前端手写副本缺 `review_queue` 整块、"
            "`LoopRun` 带两个生产者从不写的键（`job`/`duration_seconds`）——"
            "仪表盘 `resultEntries` 的 `?? details` 回退因此是**死代码**，"
            "它掩盖了真键 `result`；收窄后 `tsc` 抓出全部四处，"
            "顺带修了「结果面板永不显示」这个被手写副本掩盖的预存在 UI 缺陷。"
            "🔴 **消费面判据教训：按「类型名」grep 消费者，别按 API 方法名**——"
            "组件经 `LoopStatus[\"runs\"]` 索引访问消费，方法名 grep 报零是假象。",
        ),
        mutations=(
            Mutation(
                label="D1  导出模块不再保留该名字：闭合的 interface 退回嵌套（变松）",
                path=_FRONTEND_EXPORT,
                old=b"    EvidenceProfile,",
                new=b"    EvidenceProfile as _EvidenceProfileUnused,",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_every_reachable_plain_model_is_registered",),
                note=(
                    "**导入改成别名**，不是把名字换掉：源名字仍在 ⇒ `from app.models.event import …` "
                    "照常成立，`__all__` 里也仍有 `\"EvidenceProfile\",`（那只是个字符串），"
                    "**只有**命名空间里少了这个名字。守卫遍历**命名空间**里的根（= 生成器真正收集的那一组），"
                    "沿字段注解递归展开 `Optional` / `list` 等，再要求每个 `extra != 'allow'` 的可达模型"
                    "都在命名空间里；`EvidenceProfile` 是 `EventRecord.evidence` 的类型 ⇒ 立刻成为 missing。"
                    "针 `    EvidenceProfile,` 在本文件恰好出现 1 次（`read_bytes().count()` 实测）。"
                    "🔴 **这条写法是 §53.53 更正过的**，原文是 `new=b\"    _EvidenceProfileUnused,\"`（把名字换掉），"
                    "其 `note` 当时声称「模块照常导入，只是命名空间里少了这个名字」—— **实测不成立**："
                    "测试体里那行 `from app.models import _frontend_export` 会直接抛 "
                    "`ImportError: cannot import name '_EvidenceProfileUnused' from 'app.models.event'`。"
                    "两种写法都让守卫变红，但**红的不是同一件事**：原文红在「模块导不进来」，"
                    "任何语法/导入错误都能让它红（≈空判据）；别名那种红在**可达性**上，"
                    "实测失败文本是 `AssertionError: Lists differ: ['EvidenceProfile'] != []`。"
                    "⇒ **一条变异「能变红」不等于「红在它声称的那条判据上」**。"
                    "同族的 C2（`market-snapshot-contract`）一并改了。"
                ),
            ),
            Mutation(
                label="D2  已闭合的 overlay 又设回 extra='allow'：interface 重新长出尾巴",
                path=_EVENT_MODEL,
                old=b'    kind: str = "news"          # official | news',
                new=(
                    b'    model_config = ConfigDict(extra="allow")\n\n'
                    b'    kind: str = "news"          # official | news'
                ),
                guard_file=_T_GENERATE_TYPES,
                guards=("test_only_the_documented_models_stay_open",),
                note=(
                    "在 `EvidenceItem` 的第一个字段前插回 `model_config = ConfigDict(extra=\"allow\")`，"
                    "把它还原成 §53.51 之前的开放态。生成器对显式 allow 的模型**跳过** "
                    "`extra='forbid'` 覆盖（`_schema_generation_overrides`）⇒ `json2ts` 补回 "
                    "`[k: string]: unknown`，而 `tsc` 照样绿 —— 所以拦截点只能是静态守卫。"
                    "守卫问的是可达模型的 `model_config`，因此**不必重新生成**就变红。"
                    "针取单行字段行（`read_bytes().count()` 实测 1 次；"
                    "`    model_config = ConfigDict(extra=\"allow\")` 这个字面量在本文件出现 3 次，"
                    "单行针不能用）。`new` 里的换行交给 `rewrite_needle`，它按**文件**行尾重拼，"
                    "所以不会把 CRLF 文件写成混用。"
                ),
            ),
            Mutation(
                label="D3  必须开放的 EventSource 被收成闭合：契约藏起 API 真发的键",
                path=_EVENT_MODEL,
                old=(
                    b'    fields beyond the declared ones below (via ``extra="allow"``)."""\n\n'
                    b'    model_config = ConfigDict(extra="allow")\n\n'
                    b'    type: str = "unknown"'
                ),
                new=(
                    b'    fields beyond the declared ones below (via ``extra="allow"``)."""\n\n'
                    b'    type: str = "unknown"'
                ),
                guard_file=_T_GENERATE_TYPES,
                guards=("test_only_the_documented_models_stay_open",),
                note=(
                    "删掉 `EventSource` 的 allow 声明。与 D2 方向相反、共用同一守卫：D2 是「该闭的又开了」，"
                    "D3 是「该开的被闭了」。针必须跨行 —— "
                    "`    model_config = ConfigDict(extra=\"allow\")` 这个字面量在本文件出现 3 次，"
                    "靠它前面那句 docstring 尾行才唯一（`count_needle` 实测 2 次拼写各 1 次）。"
                    "本轮「真正可能发生」的方向：§53.51 一口气收了七个，很容易顺手把 `EventSource` "
                    "也扫进去 —— 那会让前端类型看不见 adapter 真发的键（`question` 等），"
                    "而 `tsc` 依旧绿。跨行针由 `_needle_variants` 在 LF / CRLF 两种写法下各试一次，"
                    "与检出约定无关。"
                ),
            ),
            Mutation(
                label="D4  手写 overlay 副本长回来：api.ts 又自己声明一份",
                path=_API_TS,
                old=(
                    b"// Re-export generated response types (replaces hand-written inline versions)."
                ),
                new=(
                    b"export interface SourceReliabilityOverlay {\n"
                    b"  downgrade_reason?: string | null;\n"
                    b"}\n\n"
                    b"// Re-export generated response types (replaces hand-written inline versions)."
                ),
                guard_file=_T_GENERATE_TYPES,
                guards=("test_decision_report_reads_the_generated_contract",),
                note=(
                    "把 §53.52 退役掉的那种手写副本还原回去（插在 `DecisionReport` 别名**下方**，"
                    "所以 TS 语法仍然合法；守卫是文本判据，不看语法）。"
                    "`api.ts` 没有任何生成器写它 ⇒ 这种回归**编译、lint、类型检查全绿**："
                    "报文经 `res.json() as T` 到达，那个 cast 对形状什么都不主张。"
                    "🔴 **§53.59 节点② 换了针，而且必须换**：原针 `export interface DecisionReport {` "
                    "锚在节点② 退役的那份手写体上 ⇒ 实测命中 **0 次**"
                    "（`test_every_needle_occurs_exactly_once_in_its_file` 报出）。"
                    "新锚是紧邻别名下方的那句注释，实测 1 次；"
                    "对全部 api.ts 针逐个算命中窗口后比对，与其余各条**互不重叠**"
                    "（整集 `apply` 要求字节区间不相交）。"
                    "🔴 **同一批还收窄了 `new` 的体内内容**：原文这份副本**同时**写着 "
                    "`[key: string]: unknown;` ⇒ 它其实**名字分支与索引分支会同时踩**，"
                    "于是「D4 证明名字分支承重」这句话按字面**从来就不成立**"
                    "（索引分支坏掉它照样红）。去掉那行后 D4 只踩名字分支、D6 只踩索引分支，"
                    "两条才真的互不重叠 —— 也就是本 set rationale 一直声称的那个分工。"
                    "针单行 ⇒ 与检出约定无关；`new` 里写 `\\n`，由 `rewrite_needle` 按**文件**行尾重拼。"
                    "另外两条判据（`assertNotIn(\"DecisionReport\", bodies)`、别名逐字那半）仍绿 ⇒ 归因可辨。"
                ),
            ),
            Mutation(
                label="D5  DecisionReport 别名被改指别处：契约来源不再是生成的报告",
                path=_API_TS,
                old=b"export type DecisionReport = GeneratedDecisionReport;",
                new=b"export type DecisionReport = GeneratedDecisionTimelineResponse;",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_decision_report_reads_the_generated_contract",),
                note=(
                    "🔴 **§53.59 节点② 换了这条变异的靶子，因为原来那个判据本身没了。**"
                    "原文打的是 `  source_reliability?: SourceReliability | null;` —— "
                    "一个**手写 `DecisionReport` 体内**的字段，"
                    "守卫当时断言「三个 overlay 字段必须指生成名」。"
                    "节点② 把那份手写体退役成别名之后：① 该字段已不存在（针实测 0 次）；"
                    "② 更重要的是**没有任何守卫还在断言字段指向** ⇒ "
                    "就算把它重锚到别的 interface 的字段上，改指 `Record<string, unknown>` "
                    "也**不会变红**，那就不再是一条变异（§4.2 阶段②「变异后必红」不成立）。"
                    "所以改打**同族里唯一还没人覆盖**的那条判据："
                    "`test_decision_report_reads_the_generated_contract` 的第三个断言 —— "
                    "别名必须**逐字**是 `GeneratedDecisionReport`。"
                    "它覆盖的仍是同一件事（**契约来源必须是生成类型**），只是从「字段那层」上移到「别名那层」："
                    "节点② 之后 `DecisionReport` 只剩下别名这一处声明。"
                    "改指 `GeneratedDecisionTimelineResponse` 时**挑一个真实存在且已被 import 的生成名**"
                    "（同 J57 的取舍）：改指不存在的名字等于废掉整行，那是在测「行还在不在」而不是「它指着谁」。"
                    "副本没回来、名字不以 `Overlay` 结尾、体内也无索引签名 ⇒ "
                    "`_overlay_copies` 与 `assertNotIn` 两半仍绿，红的只可能是别名那半。"
                    "针单行、实测 1 次；对全部 api.ts 针逐个算窗口后比对，与其余各条互不重叠。"
                ),
            ),
            Mutation(
                label="D6  索引签名插在体内非末行：尾巴不在末尾也照样变松",
                path=_API_TS,
                old=b"  predicted_mean: number | null;",
                new=(
                    b"  [key: string]: unknown;\n"
                    b"  predicted_mean: number | null;"
                ),
                guard_file=_T_GENERATE_TYPES,
                guards=("test_decision_report_reads_the_generated_contract",),
                note=(
                    "把索引签名塞进一个**仍存在的**手写 interface 的**中段**（不是末行）。"
                    "刻意的：首版探测器只看「体内最后一个非空行」 ⇒ 这条**不会**变红；"
                    "改成「体内任意位置」后它才红，所以这条同时是那个设计选择的证据。"
                    "🔴 **§53.59 节点② 换过宿主**：原针 `  final_downgrade_reason?: string | null;` "
                    "在被退役的手写 `DecisionReport` 体内 ⇒ 与 D4 同批实测命中 **0 次**。"
                    "改锚 `CategoryCoverage`（节点② 未触碰的手写 interface）的最后一个字段**之前**，"
                    "「中段」这个设计点因此原样保留。实测该针在 `api.ts` 恰好 1 次，"
                    "且对全部 api.ts 针逐个算窗口后与其余各条互不重叠。"
                    "读数（本轮重测，不再是引用旧数）：`api.ts` **36** 个 interface、"
                    "**36** 个体内**带任一拼写的 0 个** ⇒ 「任意位置」在本文件是等价且更强的判据。"
                    "⚠️ 而同一次重测还改掉了原文的另一个引用：**整文件**搜 `[key: string]: unknown` 现在是 **0 次**"
                    "（退役前它出现在 `DecisionReport` 头顶那段注释里）；"
                    "唯一剩下的散文命中是第 331 行 `FreshEdge` 注释里的 `[k: string]: unknown` —— "
                    "**另一个拼写、也不在任何 interface 体内**。"
                    "注释也是文件字节：`_overlay_copies` 因此**只扫 interface 体内**、不整文件 grep，"
                    "否则会踩到自己的文档（同 §4.3「自指变异」那一族）—— 换了例子，这条理由不变。"
                    "与 `generated-types.ts` 相反：那里 `dict[str, Any]` 字段的 inline 索引签名是合法的，"
                    "不能用同一判据（§53.51 的缩进盲搜教训）。"
                    "名字不以 `Overlay` 结尾 ⇒ 只有索引分支会红（名字分支由 D4 单独踩），两条互不重叠。"
                    "🔴 **§53.63 节点② 再换宿主**：原针 `  unknown_rate: number | null;` 在 §53.63 退役为别名的手写 `CategoryCoverage` 体内 ⇒ 与 §53.62 J30 同型（地址合法地消失），整集实测命中 **0 次**。"
                    "改针 `CalibrationDeviationRow`（quality 族手写 interface，仍存在）的第 3 个字段 `predicted_mean` **之前**，「中段」设计点原样保留；"
                    "实测新针在 `api.ts` 恰好 1 次（已字节复核）。"
                ),
            ),
            Mutation(
                label="J1  api.ts 又自己声明 EdgeTrajectory：消费侧副本长回来",
                path=_API_TS,
                old=b"export type EdgeTrajectory = GeneratedEdgeTrajectorySummary;",
                new=(
                    b"export interface EdgeTrajectory {\n"
                    b"  observations: number;\n"
                    b"  latest_edge: number | null;\n"
                    b"  classification: string;\n"
                    b"}"
                ),
                guard_file=_T_GENERATE_TYPES,
                guards=("test_history_endpoint_reads_the_generated_envelope",),
                note=(
                    "把 §53.53 退役掉的那份手写 `EdgeTrajectory` 还原（去掉 `first_seen` / `last_seen` / "
                    "`span_hours` 三个字段，复刻它当时**比生产者更窄**的样子）。"
                    "这条**故意绕开** `ALLOWED_OVERLAY_COPIES_IN_API_TS`：那个判据是「名字以 `Overlay` 结尾」"
                    "或「体内有索引签名」，而这份副本两条都不占 ⇒ 老守卫对它**天然盲**。"
                    "这正是新判据必须**按名字**判的原因，也是 J1 存在的理由：它证明新那半承重，"
                    "而不是「反正 D4–D6 也绿了」。`api.ts` 无生成器写它 ⇒ 编译、eslint、tsc 全绿。"
                    "针单行、在 `api.ts` 恰好 1 次（实测；LF/CRLF 两种拼法各 1 次）。"
                    "`new` 里的换行交给 `rewrite_needle` 按**文件**行尾重拼，不会把 CRLF 文件写混。"
                ),
            ),
            Mutation(
                label="J2  /history 调用点绕过已发布信封：generic 换成内联形状",
                path=_API_TS,
                old=b"    api<EventHistoryResponse>(`/events/${encodeURIComponent(id)}/history`),",
                new=b"    api<{ history: unknown[] }>(`/events/${encodeURIComponent(id)}/history`),",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_history_endpoint_reads_the_generated_envelope",),
                note=(
                    "只改 generic，路径一字不动 —— 复刻 §53.53 之前的状态：契约**发布了**，"
                    "但唯一的调用点用内联形状把它绕过去，于是 `EventHistoryResponse` 的 re-export "
                    "**零消费者**。这条让守卫的**正向**那半变红（别名三行仍在、副本也没有 ⇒ 只有调用点判据会红），"
                    "与 J1 / J3 踩的**负向**那半分开。选 `unknown[]` 而不是还原原内联形状："
                    "后者要重新 import `HistorySnapshot` / `Trend`，会顺带引入第二个缺陷，"
                    "让「红了」分不清是踩了哪一条。`tsc` 对 `api<…>` 是盲目 cast，这条改动静默。"
                    "针单行、恰好 1 次（实测，两种行尾各 1 次）。"
                ),
            ),
            Mutation(
                label="J3  types.ts 又自己声明 Trend：别名退回手写接口",
                path=_TYPES_TS,
                old=b"export type Trend = TrendSummary;",
                new=(
                    b"export interface Trend {\n"
                    b"  observations?: number;\n"
                    b"  net_change?: number;\n"
                    b"}"
                ),
                guard_file=_T_GENERATE_TYPES,
                guards=("test_history_endpoint_reads_the_generated_envelope",),
                note=(
                    "在**另一个文件**里还原同一类副本。与 J1 刻意分开：两者踩的是同一条判据的**两个不同文件**，"
                    "只扫 `api.ts` 的守卫会放过这条 —— 而 `types.ts` 恰恰是本仓自己写着"
                    "「DO NOT hand-write fields that exist in the Pydantic models」的那个文件，"
                    "规则写在注释里，当时**没有**守卫。`types.ts` 有 `export type * from \"./generated-types\"`，"
                    "所以这份手写 `Trend` 是**重复导出**，`tsc` 会红 —— 但这条变异只在守卫阶段跑，"
                    "且守卫是文本判据、不看语法。"
                    "⚠️ 另一条独立理由：`adapt.ts:13` 有**第三个** `Trend`（`\"up\" | \"down\" | \"flat\"`），"
                    "所以生成名才必须叫 `TrendSummary` —— 名字撞车是**先决条件**，不是偏好。"
                    "针单行、在 `types.ts` 恰好 1 次（实测）。"
                ),
            ),
            Mutation(
                label="J4  EventHistoryResponse.trend 退回裸 dict：具名契约消失",
                path=_EVENT_MODEL,
                old=(
                    b"    count: int\n"
                    b"    trend: TrendSummary"
                ),
                new=(
                    b"    count: int\n"
                    b"    trend: dict[str, Any]"
                ),
                guard_file=_T_GENERATE_TYPES,
                guards=("test_event_history_response_points_at_the_published_models",),
                note=(
                    "把 `trend` 退回 §53.53 之前的 `dict[str, Any]`。这条是**唯一**能踩到"
                    "「模型身份」判据的变异，因为它是唯一的**静默**回归：渲染层照样合法、"
                    "`FlexibleResponse` 照样校验、返回字节一模一样（`dict[str, Any]` 与已校验模型都吐出同一份 map），"
                    "而 `generated-types.ts` 只是把 `trend: TrendSummary` 换回 inline `{ [k: string]: unknown }` "
                    "—— `tsc` 依旧全绿。"
                    "所以判据必须是 `model_fields[...].annotation is X` 的**同一性**，不是 `issubclass`、"
                    "更不是读生成文件（读文件需要先重新生成，而变异跑的是**未重新生成**的树）。"
                    "🔴 **针由单行改成跨行（§53.55 改）**：原文是单行 `    trend: TrendSummary`，"
                    "当时在本文件唯一；§53.55 新增 `EventMoverItem.trend: TrendSummary` 之后该单行变成 **2 次**"
                    "（`EventHistoryResponse.trend` 与 `EventMoverItem.trend`），"
                    "`test_every_needle_occurs_exactly_once_in_its_file` 立刻报错 —— "
                    "**针的语义会随生产代码变化**，所以带上上一行 `    count: int`（`count: int` 单行在本文件有 11 次，"
                    "但「`count: int` + `trend: TrendSummary`」这一对只有 `EventHistoryResponse` 命中）。"
                    "同批新增的 J18 用的是另一侧上下文（上一行 `    event_title: str`），两条互不重叠。"
                ),
            ),
            Mutation(
                label="J5  api.ts 又自己声明 EdgePoint 型：消费侧副本长回来，别名仍在",
                path=_API_TS,
                old=b"export type EdgePoint = GeneratedEdgeSeriesPoint;",
                new=(
                    b"export interface EdgePoint {\n"
                    b"  timestamp?: string | null;\n"
                    b"  estimated: number;\n"
                    b"}\n\n"
                    b"export type EdgePoint = GeneratedEdgeSeriesPoint;"
                ),
                guard_file=_T_GENERATE_TYPES,
                guards=("test_fresh_edges_callsites_read_the_generated_envelope",),
                note=(
                    "把 §53.54 退役掉的手写 `EdgePoint` 还原（复刻它当时 `timestamp?:` 可选的样子），"
                    "**但把三行别名原样留在下面**。这一点是刻意的：J7 已经单独证明「别名必须在」那半会红，"
                    "所以这条只要别名还在，红的就**只可能是**「`api.ts` 不得再声明副本」那半 —— "
                    "两条变异各自**隔离**一条判据，合起来才说明守卫两个方向都承重。"
                    "若这里顺手把别名删掉（像 J1 那样），「红了」就分不清是哪半踩的。"
                    "TS 会因为重复标识符报错，但变异只在守卫阶段跑，而守卫是**文本**判据、不看语法。"
                    "针单行、在 `api.ts` 恰好 1 次（`read_bytes()` 实测）。"
                ),
            ),
            Mutation(
                label="J6  types.ts 又自己声明 FreshEdgeItem：同一条负向判据的另一个文件",
                path=_TYPES_TS,
                old=b"export type * from \"./generated-types\";",
                new=(
                    b"export interface FreshEdgeItem {\n"
                    b"  event_id: string;\n"
                    b"}\n\n"
                    b"export type * from \"./generated-types\";"
                ),
                guard_file=_T_GENERATE_TYPES,
                guards=("test_fresh_edges_callsites_read_the_generated_envelope",),
                note=(
                    "这条与 J5 **同判据、不同文件**，故意分开 —— §53.53 已经踩过一次这个坑："
                    "只扫 `api.ts` 的守卫会放过 `types.ts`。`types.ts` 恰恰是本仓自己写着"
                    "「DO NOT hand-write fields that exist in the Pydantic models」的那个文件。"
                    "选 `FreshEdgeItem`（而不是 `EdgePoint`）是因为 `types.ts` 里本来就**没有** "
                    "`EdgePoint` 这个名字 —— 新名字只可能来自一次真的复制。"
                    "`types.ts` 有 `export type * from \"./generated-types\"` ⇒ 这也是重复导出、`tsc` 会红，"
                    "但同样只在守卫阶段跑，文本判据不看语法。"
                    "🔴 **§53.59 换过针，理由是整集 `apply` 而不是判据**：原针 "
                    "`export type Trend = TrendSummary;` 与 **J3 字节完全相同**，"
                    "而 J3 排在前面、它的 `new` 把那一行**换成接口**（别名行随之消失）⇒ "
                    "整集 `apply` 到 J6 时针已经找不到（对全部 12 套做「按选中顺序在内存副本上重放」的模拟，"
                    "实测只有 J6 与 J50 落不下去）。"
                    "两条变异**语义上并不互斥**（J3 改的是 `Trend`，J6 只是另插一个 `FreshEdgeItem`）"
                    "⇒ 正确修法是**换锚**、不是宣告 `group`（§4.3 的 `group` 是给「同一段字节的两条**相反**改写」用的）。"
                    "新锚 `export type * from \"./generated-types\";` 在 `types.ts` 恰好 1 次、"
                    "且与本节其余 types.ts 针的字节区间不相交；位置也贴题 —— 副本就写在「re-export 全部生成类型」那句旁边。"
                ),
            ),
            Mutation(
                label="J7  别名被改指内联形状：副本没回来，但契约来源已经不是生成类型",
                path=_API_TS,
                old=b"export type FreshEdge = GeneratedFreshEdgeItem;",
                new=b"export type FreshEdge = { event_id: string };",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_fresh_edges_callsites_read_the_generated_envelope",),
                note=(
                    "🔴 这条的存在理由是**防空头**：J5 / J6 一改，守卫的**负向**那半和**别名**那半会**同时**红，"
                    "于是「别名必须在」这三行可能从来没被真正执行过 —— 换个说法，"
                    "没有 J7 就无法区分「守卫拦住了副本」与「守卫只是恰好在别名那半红了」。"
                    "这里**不插入任何 interface**（负向那半照常通过），只把别名指向一个内联对象字面量："
                    "契约来源从生成类型变回手写，而这正是 §53.52/§53.53 要禁的那件事。"
                    "`tsc` 对 `export type X = {...}` 完全合法 ⇒ 编译、lint、类型检查全绿，"
                    "拦截点只有这条静态判据。针单行、在 `api.ts` 恰好 1 次（实测）。"
                ),
            ),
            Mutation(
                label="J8  freshEdges 调用点绕过已发布信封：generic 退回内联形状（第 1 处）",
                path=_API_TS,
                old=b"    api<FreshEdgesResponse>(`/events/edges/fresh?limit=${limit}`),",
                new=b"    api<{ edges: unknown[] }>(`/events/edges/fresh?limit=${limit}`),",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_fresh_edges_callsites_read_the_generated_envelope",),
                note=(
                    "只改 generic，**路径一字不动** —— 与 J2 同型。契约仍然发布着，"
                    "但调用点用内联形状把它绕过去，于是 `FreshEdgesResponse` 的 re-export 又变成零消费者。"
                    "选 `unknown[]` 而不是还原当时那个内联形状：后者要重新 import `FreshEdge`，"
                    "会顺带把「副本回来了」一起引进来，红了就分不清是踩了哪条判据。"
                    "针是**整行**（含路径），因此天然唯一 —— 若只取 `    api<FreshEdgesResponse>(` 这个前缀，"
                    "它会同时出现在两个调用点上（实测 2 次），针必须是整行或跨行。"
                ),
            ),
            Mutation(
                label="J9  edgeMonitor 调用点绕过已发布信封：generic 退回内联形状（第 2 处）",
                path=_API_TS,
                old=(
                    b"    api<FreshEdgesResponse>(\n"
                    b"      `/events/edges/fresh?limit=${limit}&offset=${offset}"
                    b"&classification=${classification}&include_series=true`,\n"
                    b"    ),"
                ),
                new=(
                    b"    api<{ edges: unknown[] }>(\n"
                    b"      `/events/edges/fresh?limit=${limit}&offset=${offset}"
                    b"&classification=${classification}&include_series=true`,\n"
                    b"    ),"
                ),
                guard_file=_T_GENERATE_TYPES,
                guards=("test_fresh_edges_callsites_read_the_generated_envelope",),
                note=(
                    "同一个端点族的**第二个**调用点。与 J8 分开是 §4.2 的规矩："
                    "「一成员被 N 处消费却只补 1 处」—— 两处调用点是两条独立的回归路径，"
                    "只补一处，另一处的绕过永远不会红。"
                    "这条必须用**跨行**针：`    api<FreshEdgesResponse>(` 这个前缀两处都占（实测 2 次），"
                    "带上第二行的路径片段后才唯一。跨行针在 `core.autocrlf` 仓里由 `count_needle` / "
                    "`_needle_variants` 在 LF 与 CRLF 两种拼法下各试一次，"
                    "`test_mutation_verify.py` 会按**文件自己的字节**重铸两种约定来验，所以与检出无关。"
                    "⚠️ 顺带一条余量说明：守卫用「从调用点行往回找最近的 `api<`」而不是固定窗口，"
                    "就是因为这个调用点下方不远处是 `resetData` 的内联 `api<{ ... }>` —— "
                    "任何够宽的固定窗口都会匹配到那个邻居。"
                ),
            ),
            Mutation(
                label="J10 FreshEdgesResponse.edges 退回裸 dict：元素契约消失（静默）",
                path=_EVENT_MODEL,
                old=b"    edges: list[FreshEdgeItem]",
                new=b"    edges: list[dict[str, Any]]",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_fresh_edges_response_points_at_the_published_element",),
                note=(
                    "把 `edges` 退回 §53.54 之前的 `list[dict[str, Any]]`。与 J4 同族（模型身份判据）"
                    "但**不同字段**：J4 覆盖 `EventHistoryResponse.trend`，这条覆盖本节点自己的标题字段。"
                    "回归是**完全静默**的：`FlexibleResponse` 照样校验、返回字节一模一样，"
                    "`generated-types.ts` 只是把元素换回 inline `{ [k: string]: unknown }`，`tsc` 依旧全绿 —— "
                    "而那正是前端当初手写 `FreshEdge` 的原因。"
                    "所以判据只能是 `model_fields['edges'].annotation == list[FreshEdgeItem]` 的**同一性**，"
                    "不是 `issubclass`、更不是读生成文件（读文件要先重新生成，而变异跑的是未重新生成的判据）。"
                    "针单行、在 `event.py` 恰好 1 次（实测）。"
                ),
            ),
            Mutation(
                label="J11 FreshEdgeItem.edge 退回裸 dict：嵌套的具名契约消失（静默）",
                path=_EVENT_MODEL,
                old=b"    event_title_zh: str | None = None\n    edge: EdgeTrajectorySummary",
                new=b"    event_title_zh: str | None = None\n    edge: dict[str, Any]",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_fresh_edges_response_points_at_the_published_element",),
                note=(
                    "把元素的 `edge` 退回裸 dict。这条踩的是**嵌套**那半的同一性判据 —— "
                    "`EdgeTrajectorySummary` 是 §53.53 已经命名的模型，本节点只是**引用**它，"
                    "所以它被换掉时 `FreshEdgeItem` 自己仍然是闭合的、`tsc` 仍然全绿（同上，静默）。"
                    "必须用跨行针：`    edge: EdgeTrajectorySummary` 在 `event.py` 出现 **2 次**"
                    "（另一个是 `EventHistoryResponse.edge`），实测 2 次 ⇒ 单行针不合法；"
                    "带上前面那行 `event_title_zh` 才唯一。"
                    "为什么不改用 `EventHistoryResponse.edge` 那处：那已经由 J4 的同族判据覆盖，"
                    "而这里要证明的是**新模型自己的**字段也在判据范围内。"
                ),
            ),
            Mutation(
                label="J12 FreshEdgeItem.event_title_zh 丢掉默认值：条件写入的键变成必填",
                path=_EVENT_MODEL,
                old=(
                    b"    event_title: str\n"
                    b"    event_title_zh: str | None = None"
                ),
                new=(
                    b"    event_title: str\n"
                    b"    event_title_zh: str | None"
                ),
                guard_file=_T_GENERATE_TYPES,
                guards=("test_fresh_edges_response_points_at_the_published_element",),
                note=(
                    "🔴 这条踩的是**可选性**判据里「条件写入的键必须**保留**默认值」那半 —— "
                    "§53.53 明确登记过的一条缺口：当时 `EventHistoryResponse` 由可选收紧成必填，"
                    "**没有任何独立变异**能证明「可选性」这件事被守卫看见了。这里补上方向相反的那一半。"
                    "它不是理论风险：路由只在商店里有中文标题时才写 `event_title_zh`"
                    "（`routes/events.py` 的 `if title_zh:`），`response_model_exclude_unset=True` 保证缺席即不出现键；"
                    "一旦这个字段变成必填，**没有中文标题的事件**（五份语料里三份的常态）会直接 500。"
                    "同时它也是「五字段恒写 / 两字段条件写」双向断言里唯一能被踩的一侧："
                    "另一侧（`count` 等恒写字段加回默认值）走的是**同一个** `is_required()` 断言实例，"
                    "再加一条只会让计数虚胖、不会多发现一个盲区，故**有意不登记**。"
                    "⚠️ 该「不登记」的前提是：这两组仍然共用 `assertTrue/assertFalse(is_required())` 这一个断言形状；"
                    "若哪天被拆成两组独立断言，另一侧就要补。"
                    "🔴 **针由单行改成跨行（§53.55 改）**：原文是单行 `    event_title_zh: str | None = None`，"
                    "当时在本文件唯一；§53.55 新增 `EventMoverItem.event_title_zh: str | None = None` 之后变成 **2 次**，"
                    "`test_every_needle_occurs_exactly_once_in_its_file` 立刻报错 —— **针的语义会随生产代码变化**。"
                    "带上上一行 `    event_title: str` 后实测唯一（`EventMoverItem` 的同名字段上一行是 `    trend: TrendSummary`，"
                    "不会命中）。同批新增的 J19 故意**不碰这两行**，改从**下方**取上下文"
                    "（`event_title_zh` 之后是空行与 `class EventMoversResponse`），所以两条针互不重叠、可同时 apply。"
                ),
            ),
            Mutation(
                label="J13 api.ts 又自己声明 Mover 型：消费侧副本长回来，别名仍在",
                path=_API_TS,
                old=b"export type EventMoversResponse = GeneratedEventMoversResponse;",
                new=(
                    b"export interface Mover {\n"
                    b"  event_id: string;\n"
                    b"  event_title?: string;\n"
                    b"}\n\n"
                    b"export type EventMoversResponse = GeneratedEventMoversResponse;"
                ),
                guard_file=_T_GENERATE_TYPES,
                guards=("test_event_movers_callsite_reads_the_generated_envelope",),
                note=(
                    "把 §53.55 退役掉的那种手写副本还原回去（插在 `EventMoversResponse` 别名**上方** —— "
                    "副本就长回它所属的那个信封旁边；TS 语法仍然合法）。**刻意的两处留白**，与 §53.54 的 J5 同款："
                    "① 副本体内**不放**索引签名、名字也**不以 `Overlay` 结尾** ⇒ "
                    "`ALLOWED_OVERLAY_COPIES_IN_API_TS` 那条**老**判据仍然绿 —— "
                    "这正是「§53.52 的判据对这份副本天然盲」的实证，也是本节必须**按名**判的理由；"
                    "② **别名刻意留着** ⇒ `assertIn`（别名）那半照旧通过，红只可能红在 `assertNotIn`（副本）那半。"
                    "没有 ② 就无法区分「守卫拦住副本」与「守卫恰好在别名那半红了」。"
                    "🔴 **§53.59 节点② 换过针**：原针 `export interface DecisionReport {` 锚在节点② 退役的手写报告体上 ⇒ "
                    "与 D4/D5/D6 同批实测命中 **0 次**。"
                    "换成 `EventMoversResponse` 的别名行后实测 1 次，"
                    "且对全部 api.ts 针逐个算窗口后比对，与其余各条互不重叠（尤其不撞 J16 的**调用点**那行）。"
                    "单行 ⇒ 与检出约定无关。"
                ),
            ),
            Mutation(
                label="J14 types.ts 又自己声明 EventMoverItem：同一条负向判据的另一个文件",
                path=_TYPES_TS,
                old=b"export type Mover = EventMoverItem;",
                new=(
                    b"export interface EventMoverItem {\n"
                    b"  event_id: string;\n"
                    b"  event_title?: string;\n"
                    b"}\n\n"
                    b"export type Mover = EventMoverItem;"
                ),
                guard_file=_T_GENERATE_TYPES,
                guards=("test_event_movers_callsite_reads_the_generated_envelope",),
                note=(
                    "同一条判据作用在**第二个文件**上（§4.2：一成员被 N 处消费就要 N 条；"
                    "§53.53 的 J1/J3 先例）。`types.ts` 是那个**自己写着**「DO NOT hand-write fields "
                    "that exist in the Pydantic models」却长期没有守卫的文件 —— 规则在注释里 ≈ 空头。"
                    "这条与 J13 必须分开：只扫 `api.ts` 的守卫会放过 `types.ts`。"
                    "副本故意不带索引签名（理由同 J13），且插在别名声明之前 ⇒ TS 语法合法。"
                    "针单行、恰好 1 次（实测）。"
                ),
            ),
            Mutation(
                label="J15 别名被改指内联形状：副本没回来，但契约来源已经不是生成类型",
                path=_TYPES_TS,
                old=b"export type Mover = EventMoverItem;",
                new=(
                    b"export type Mover = {\n"
                    b"  event_id: string;\n"
                    b"  event_title?: string;\n"
                    b"  event_title_zh?: string;\n"
                    b"  trend?: TrendSummary;\n"
                    b"};"
                ),
                guard_file=_T_GENERATE_TYPES,
                guards=("test_event_movers_callsite_reads_the_generated_envelope",),
                note=(
                    "**不插 interface**，只把别名指回手写形状 ⇒ 只可能红在 `assertIn`（别名）那半。"
                    "与 J13 合起来才把同一个测试的两半**隔离**出来（§53.54 的 J5/J7 同款设计）。"
                    "它也是**更隐蔽**的那一种回归：副本没「长回来」（名字仍是 `Mover` 且是 `export type`），"
                    "`_interface_bodies` 也收集不到它 —— 只有把别名钉成**精确字符串**才看得见。"
                    "针单行、恰好 1 次（实测）。"
                ),
            ),
            Mutation(
                label="J16 movers 调用点绕过已发布信封：generic 退回内联形状",
                path=_API_TS,
                old=b"    api<EventMoversResponse>(`/events/movers?limit=${limit}`),",
                new=b"    api<{ movers: unknown[] }>(`/events/movers?limit=${limit}`),",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_event_movers_callsite_reads_the_generated_envelope",),
                note=(
                    "`/events/movers` 在 `api.ts` 里**只有一处**调用点（实测路径行 1 次），"
                    "所以不出现 §53.54 那种「两处调用点各要一条」的余量。"
                    "用 `unknown[]` 而不是还原成旧的 `movers: Mover[]`：后者会让 `Mover` 重新变成"
                    "被使用的名字，从而**顺带**改变别处的判据，红的归因就不可辨了。"
                    "针是整行（含路径），恰好 1 次（实测）。"
                    "守卫用「从调用点行往回找最近的 `api<`」定位 generic，不看固定窗口。"
                ),
            ),
            Mutation(
                label="J17 EventMoversResponse.movers 退回裸 dict：元素契约消失（静默）",
                path=_EVENT_MODEL,
                old=b"    movers: list[EventMoverItem]",
                new=b"    movers: list[dict[str, Any]]",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_event_movers_response_points_at_the_published_element",),
                note=(
                    "**静默**回归的典型：`EventMoversResponse` 是 `FlexibleResponse`，"
                    "退回 `list[dict[str, Any]]` 后**仍然校验通过、仍然返回同样的字节、`tsc` 仍然全绿**，"
                    "生成出来的元素类型只是悄悄回到 `{ [k: string]: unknown }` —— "
                    "也就是前端当初不得不手写 `Mover` 的那个形状。"
                    "所以拦截点只能是**模型身份**判据（`annotation` 相等），且必须取得全限定类对象"
                    "（用 `issubclass` 会被 `dict` 与模型都绕过）。"
                    "针单行、恰好 1 次（实测：本文件里 `movers: list[EventMoverItem]` 只出现 1 次）。"
                ),
            ),
            Mutation(
                label="J18 EventMoverItem.trend 退回裸 dict：嵌套的具名契约消失（静默）",
                path=_EVENT_MODEL,
                old=(
                    b"    event_title: str\n"
                    b"    trend: TrendSummary"
                ),
                new=(
                    b"    event_title: str\n"
                    b"    trend: dict[str, Any]"
                ),
                guard_file=_T_GENERATE_TYPES,
                guards=("test_event_movers_response_points_at_the_published_element",),
                note=(
                    "元素里**已经发布过**的那一半（`TrendSummary`，§53.53 立的具名契约）被退回裸 dict。"
                    "与 J17 是**两条**不同的身份判据：J17 钉「`movers` 指向元素模型」，J18 钉"
                    "「元素自己的 `trend` 指向趋势模型」—— §53.54 的 J10/J11 是同一对关系。"
                    "跨行针是**必须**的：单行针 `    trend: TrendSummary` 在本文件出现 **2 次**"
                    "（`EventHistoryResponse.trend` 与 `EventMoverItem.trend`），"
                    "靠它上面那行 `    event_title: str` 才唯一 —— 而 `EventHistoryResponse` 的上一行是"
                    "`    count: int`，所以两处不会同时命中。带上文后实测 1 次（按 CRLF 检出）；"
                    "跨行针在 `core.autocrlf` 仓里由 `count_needle` / `_needle_variants` 在 LF 与 CRLF 两种拼法下各试一次。"
                ),
            ),
            Mutation(
                label="J19 EventMoverItem.event_title_zh 丢掉默认值：条件写入的键变成必填",
                path=_EVENT_MODEL,
                old=(
                    b"    event_title_zh: str | None = None\n"
                    b"\n"
                    b"\n"
                    b"class EventMoversResponse(FlexibleResponse):"
                ),
                new=(
                    b"    event_title_zh: str | None\n"
                    b"\n"
                    b"\n"
                    b"class EventMoversResponse(FlexibleResponse):"
                ),
                guard_file=_T_GENERATE_TYPES,
                guards=("test_event_movers_response_points_at_the_published_element",),
                note=(
                    "踩**可选性**判据里「条件写入的键必须保留默认值」那半（`assertFalse(is_required())`）。"
                    "路由只在商店里有中文标题时才写这个键（`if title_zh:`），"
                    "一旦变成必填，**没有中文标题的事件**会直接 500 —— 而这不是理论风险："
                    "普查实测这个分支**今天不可达**（语料侧 332/332、活库侧 257/257 都有中文标题），"
                    "正因如此它**只能靠静态判据守**，没有任何端到端偶发测试会碰它。"
                    "跨行针是**必须**的，且刻意**从下方取上下文**：单行针 `    event_title_zh: str | None = None` "
                    "在本文件出现 **2 次**（`FreshEdgeItem` 与 `EventMoverItem`）；"
                    "若改从**上方**那行 `    trend: TrendSummary` 消歧，跨行针就会与 J18 的针**压在同一行字节上**，"
                    "整集 apply 时二者无法同时落位。改用下方（空行 + `class EventMoversResponse(FlexibleResponse):`）"
                    "之后与 J18 完全不重叠，两条可以同时 apply。实测 1 次（按 CRLF 检出）。"
                ),
            ),
            Mutation(
                label="J20 EventMoversResponse.count 加回默认值：已发布信封被静默放宽",
                path=_EVENT_MODEL,
                old=(
                    b"    (section 53.53); this names the remaining four-field wrapper.\n"
                    b'    """\n'
                    b"\n"
                    b"    count: int"
                ),
                new=(
                    b"    (section 53.53); this names the remaining four-field wrapper.\n"
                    b'    """\n'
                    b"\n"
                    b"    count: int = 0"
                ),
                guard_file=_T_GENERATE_TYPES,
                guards=("test_event_movers_response_points_at_the_published_element",),
                note=(
                    "🔴 **本 set 里第一条性红 `assertTrue(is_required())` 这一半的变异**"
                    "（J12 红的是方向相反的 `assertFalse` 那半）。"
                    "值得独立登记的理由不是「另一个字段」而是**另一个方向**："
                    "在 J12 之后，`assertTrue(is_required())` 这个断言**从未被任何变异性红过** —— "
                    "若它哪天退化成恒真，整套绿都不会变。"
                    "它踩的那半也是**静默**的：加回默认值之后生成文本仍然「合法」，只是"
                    "`count: number` 悄悄变回 `count?: number`，比端点实际行为更松，"
                    "而 `tsc`、eslint、全部前端测试都不会有任何反应（两个消费者都不读 `count`）。"
                    "跨行针**必须**：单行针 `    count: int` 在本文件出现 **11 次**"
                    "（`EventHistoryResponse` 等一大批信封都有同名字段）；"
                    "但**不能**用下一行 `    movers: list[EventMoverItem]` 消歧 —— 那会与 J17 的针压在同一行字节上，"
                    "整集 apply 时两条无法同时落位。改从**上方**取上下文（docstring 尾行 + 空行）后与 J17 完全不重叠。"
                    "⚠️ 更短的形态 `    \"\"\"` + 空行 + `    count: int` 仍有 **2 次**"
                    "（另一处信封同样以 docstring 收尾），所以针必须带上 docstring 的最后一行文字；"
                    "代价是那句措辞一改这条针就会断 —— 届时应由 `count_needle != 1` 立刻报出来，而不是静默失效。"
                ),
            ),
            Mutation(
                label="J21 /movers 丢掉 response_model_exclude_unset：缺席的键变成显式 null",
                path=_DIGEST_ROUTES,
                old=(
                    b"    response_model=EventMoversResponse,\n"
                    b"    response_model_exclude_unset=True,"
                ),
                new=b"    response_model=EventMoversResponse,",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_movers_route_keeps_the_exclude_unset_flag",),
                note=(
                    "本节**唯一**一条不落在生成类型上的判据，也是本节新增的守卫。"
                    "为什么需要它：元素原本是裸 dict ⇒ pydantic 不碰它 ⇒ 没有中文标题的 mover"
                    "**整个键缺席**；具名成 `list[EventMoverItem]` 之后它要过模型，"
                    "而这条路由**没有**（姊妹路由 `/events/edges/fresh` **有**）"
                    "`response_model_exclude_unset` ⇒ 键会变成**显式 `null`**。"
                    "实测见 `probe_movers_exclude_unset.py`：三种形状逐条量过，"
                    "控制组证明 `count` / `movers` 两种模式下都在 ⇒ 加这个标志**不会**丢字段。"
                    "为什么别的判据都看不见它：模型层那半有没有这个标志都通过；"
                    "`tsc` 不查测试文件；唯一的前端消费者是 "
                    "`m.event_title_zh || m.event_title || m.event_id`，`null` 与「缺席」折叠结果相同。"
                    "⇒ 这条回归**只会在线上报文里露出来**，是本节最需要静态守卫的一条。"
                    "`path` 复用 `_DIGEST_ROUTES`（它就是 `backend/app/api/routes/events.py`，同一个文件；"
                    "该常量名源自更早的 §53.38/§53.39 摘要端点，此处不另立重复字面量）。"
                    "跨行针**必须**：单行针 `response_model_exclude_unset=True` 在本文件出现 **3 次**"
                    "（`/edges/fresh`、`/movers` 以及另一处），带上上一行 `EventMoversResponse` 后实测 1 次。"
                ),
            ),
            Mutation(
                label="J22 api.ts 又自己声明 SimilarEvent 型：消费侧副本长回来，别名仍在",
                path=_API_TS,
                old=b"export interface AutoResolveMatch {",
                new=(
                    b"export interface SimilarEvent {\n"
                    b"  event_id: string;\n"
                    b"  event_title?: string;\n"
                    b"}\n\n"
                    b"export interface AutoResolveMatch {"
                ),
                guard_file=_T_GENERATE_TYPES,
                guards=("test_similar_callsite_reads_the_generated_envelope",),
                note=(
                    "§53.56 退役的那种手写副本被还原（放在 `PendingLink` **之前**，TS 语法合法）。"
                    "与 J13 同款的两处刻意留白：副本体内不带索引签名、名字不以 `Overlay` 结尾 ⇒ "
                    "`ALLOWED_OVERLAY_COPIES_IN_API_TS` 那条**老**判据仍然绿（本节的守卫必须**按名**判）；"
                    "**别名刻意留着** ⇒ 只可能红在 `assertNotIn` 那半，归因可辨。"
                    "🔴 **锚点与 J13 刻意不同**：J13 用 `export interface DecisionReport {`，"
                    "这里用 `export interface AutoResolveMatch {` —— 两条若共用同一行字节，"
                    "整集 `apply` 时无法同时落位（§53.55 的 J19/J20 教训）。"
                    "🔴 **这条针 §53.57 换过一次**：原针是被退役的 `export interface PendingLink {`，"
                    "本节把那行删掉了 ⇒ 旧针命中 0 次（`count_needle != 1` 正是该报的），"
                    "换到紧随其后的 `AutoResolveMatch`，与任何其它针都不相交。"
                    "针单行、`api.ts` 里恰好 1 次（实测）。"
                ),
            ),
            Mutation(
                label="J23 types.ts 又自己声明 SimilarEventItem：同一条负向判据的另一个文件",
                path=_TYPES_TS,
                old=b"export type Semantics = BackendEventSemantics;",
                new=(
                    b"export interface SimilarEventItem {\n"
                    b"  event_id: string;\n"
                    b"  event_title?: string;\n"
                    b"}\n\n"
                    b"export type Semantics = BackendEventSemantics;"
                ),
                guard_file=_T_GENERATE_TYPES,
                guards=("test_similar_callsite_reads_the_generated_envelope",),
                note=(
                    "同一条判据作用在**第二个文件**上（§4.2：一成员被 N 处消费就要 N 条）。"
                    "只扫 `api.ts` 的守卫会放过 `types.ts`。"
                    "🔴 **锚点刻意不选别名那一行**：J24 的针就是 "
                    "`export type SimilarEvent = SimilarEventItem;`，两条若共用同一段字节就落位冲突；"
                    "改用同文件里一个与被测判据无关的、恰好 1 次的导出行做插入锚点（实测）。"
                    "副本不带索引签名（理由同 J22）⇒ TS 语法合法。"
                ),
            ),
            Mutation(
                label="J24 别名被改指内联形状：副本没回来，但契约来源已经不是生成类型",
                path=_TYPES_TS,
                old=b"export type SimilarEvent = SimilarEventItem;",
                new=(
                    b"export type SimilarEvent = {\n"
                    b"  event_id: string;\n"
                    b"  event_title: string;\n"
                    b"  event_title_zh?: string;\n"
                    b"  similarity?: number;\n"
                    b"};"
                ),
                guard_file=_T_GENERATE_TYPES,
                guards=("test_similar_callsite_reads_the_generated_envelope",),
                note=(
                    "**不插 interface**，只把别名指回手写形状 ⇒ 只可能红在 `assertIn`（别名）那半。"
                    "与 J22 合起来才把同一个测试的两半**隔离**出来（§53.54 的 J5/J7、§53.55 的 J13/J15 同款）。"
                    "它也是**更隐蔽**的那一种回归：名字仍是 `SimilarEvent` 且仍是 `export type`，"
                    "`_interface_bodies` 收集不到它 —— 只有把别名钉成**精确字符串**才看得见。"
                    "替换串沿用**旧手写形状**（6 字段、全可选）：这正是被退役的那份副本，"
                    "所以这条变异同时证明「漂移的那两个字段（`direction`/`last_updated`）不会再悄悄回到别名里」。"
                    "针单行、恰好 1 次（实测）。"
                ),
            ),
            Mutation(
                label="J25 similar 调用点绕过已发布信封：generic 退回内联形状",
                path=_API_TS,
                old=b"    api<SimilarEventsResponse>(",
                new=b"    api<{ similar: unknown[] }>(",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_similar_callsite_reads_the_generated_envelope",),
                note=(
                    "`/similar` 在 `api.ts` 里**只有一处**调用点（实测），所以不出现 §53.54 那种"
                    "「两处调用点各要一条」的余量。用 `unknown[]` 而不是还原成旧的 `SimilarEvent[]`："
                    "后者会让 `SimilarEvent` 重新变成被使用的名字，**顺带**改变别处的判据，红的归因就不可辨了。"
                    "针是 generic 那一行（不含路径），恰好 1 次（实测）—— 守卫用「从调用点行往回找最近的 "
                    "`api<`」定位，所以针不必包含路径本身。"
                ),
            ),
            Mutation(
                label="J26 SimilarEventsResponse.similar 退回裸 dict：元素契约消失（静默）",
                path=_EVENT_MODEL,
                old=b"    similar: list[SimilarEventItem]",
                new=b"    similar: list[dict[str, Any]]",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_similar_response_points_at_the_published_element",),
                note=(
                    "**静默**回归的典型（同 §53.55 的 J17）：`SimilarEventsResponse` 是 `FlexibleResponse`，"
                    "退回 `list[dict[str, Any]]` 后**仍然校验通过、仍然返回同样的字节、`tsc` 仍然全绿**，"
                    "生成出来的元素类型只是悄悄回到 `{ [k: string]: unknown }` —— "
                    "也就是前端当初不得不手写 `SimilarEvent` 的那个形状。"
                    "拦截点只能是**模型身份**判据。针单行、恰好 1 次（实测）。"
                ),
            ),
            Mutation(
                label="J27 SimilarEventItem.estimated_probability 加回默认值：把「必有键」放宽成可选",
                path=_EVENT_MODEL,
                old=b"    estimated_probability: float | None",
                new=b"    estimated_probability: float | None = None",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_similar_element_requires_all_eight_keys",),
                note=(
                    "本节**与 §53.55 相对**的那一条。`find_similar` 对每条 item **无条件写**这 8 个键"
                    "（可空的那四个写 `None`，不是省掉），所以**任何**默认值都是错的："
                    "它会把生成类型从 `number | null` 静默放宽成 `number | null | undefined`，"
                    "并重新引入这条端点**本来没有**的「缺席 vs null」歧义。"
                    "这条也是「新模型**不需要** `response_model_exclude_unset`」这个结论的守卫 —— "
                    "因为它钉住的正是那个结论的前提（无默认值 ⇒ 无 `unset` 可言）。"
                    "针单行、恰好 1 次（实测：`estimated_probability` 只在本节新增元素里出现）。"
                ),
            ),
            Mutation(
                label="J28 SimilarEventItem.direction 窄化成 Literal：比生产者的声明更紧",
                path=_EVENT_MODEL,
                old=(
                    b"    direction: str | None\n"
                    b"    last_updated: str | None"
                ),
                new=(
                    b'    direction: Literal["rising", "falling", "stable"] | None\n'
                    b"    last_updated: str | None"
                ),
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_similar_element_requires_all_eight_keys",),
                note=(
                    "**方向相反的另一类错误**：把 `str | None` 收成 3 值 `Literal`。"
                    "它看着像「更严的契约」，但记录自己的 `Probability.direction` 声明就是 `str`，"
                    "所以这比**它镜像的那份契约更窄**，会把将来第四个方向值变成 422。"
                    "普查观测到的词表确实恰是 `{rising, stable, falling}`（22 205 条）—— "
                    "但那是**观测**，不是保证（§2：「没观测到」≠「不可能」）。"
                    "拦它的判据是 `typing.get_args(annotation) == (str, type(None))`。"
                    "🔴 **跨行针是必须的**：单行针 `    direction: str | None` 在本文件出现 **2 次**"
                    "（`EventAuditSnapshot.direction` 与本节新增的元素），"
                    "靠下面那行 `    last_updated: str | None`（实测全文件唯一）才唯一；"
                    "取**下方**上下文也让它与 J27/J29 的针互不相邻。"
                ),
            ),
            Mutation(
                label="J29 SimilarEventsResponse.count 加回默认值：已发布信封被静默放宽",
                path=_EVENT_MODEL,
                old=(
                    b'spelled the envelope inline.\n    """\n\n    event_id: str\n    count: int'
                ),
                new=(
                    b'spelled the envelope inline.\n    """\n\n    event_id: str\n    count: int = 0'
                ),
                guard_file=_T_GENERATE_TYPES,
                guards=("test_similar_response_points_at_the_published_element",),
                note=(
                    "信封那半（J27 是元素那半；两条各自钉住一个测试里的 `is_required()` 断言，"
                    "§4.2：一成员被 N 处消费就要 N 条）。加回默认值会**静默地**把已发布信封"
                    "重新放宽成 `count?:` —— 生成文本本身仍然「合法」，只有 `is_required()` 看得见。"
                    "🔴 **针刻意从上方取上下文**：`    event_id: str\\n    count: int` 在本文件出现 **3 次**，"
                    "而补上第三行 `    similar: list[SimilarEventItem]` 会与 J26 的针**压在同一行字节上**"
                    "（整集 `apply` 落位冲突）。改用 docstring 尾句（全文件唯一）作上界，"
                    "针在 `similar:` 行**之前**结束 ⇒ 与 J26 完全不重叠。"
                    "跨行针在 `core.autocrlf` 仓里由 `count_needle` / `_needle_variants` 在 LF 与 CRLF 两种拼法下各试一次。"
                    "代价：那句 docstring 措辞一改这条针就断 —— 届时应由 `count_needle != 1` 立刻报出来。"
                ),
            ),
            Mutation(
                label="J30 api.ts 又自己声明 PendingLink：被退役的队列元素副本长回来",
                path=_API_TS,
                old=b"export type LoopRun = GeneratedLoopRunView;",
                new=(
                    b"export interface PendingLink {\n"
                    b"  event_id: string;\n"
                    b"  contract_id?: string;\n"
                    b"}\n\n"
                    b"export type LoopRun = GeneratedLoopRunView;"
                ),
                guard_file=_T_GENERATE_TYPES,
                guards=("test_pending_callsites_read_the_generated_contract",),
                note=(
                    "§53.57 退役的那份手写副本被还原（插在 `LoopRun` **之前**，TS 语法合法）。"
                    "**别名刻意留着** ⇒ 只可能红在 `assertNotIn` 那半，归因可辨（§53.54 的 J5/J7、"
                    "§53.55 的 J13/J15、§53.56 的 J22/J24 同款）。"
                    "副本体内不带索引签名、名字不以 `Overlay` 结尾 ⇒ "
                    "`ALLOWED_OVERLAY_COPIES_IN_API_TS` 那条**老**判据仍然绿（本节的守卫必须**按名**判）。"
                    "🔴 **2026-10-10 §53.62 换锚**：原锚 `export interface LoopRun {` 随手写副本"
                    "一起退役（实测 0x，`test_mutation_verify` 三条清单测试当场拦下）⇒ "
                    "判据不变（PendingLink 副本不得回来）、只换锚到**别名行** "
                    "`export type LoopRun = GeneratedLoopRunView;` —— 别名由本节的守卫"
                    "（test_the_loop_status_callsite...）钉住，不会再静默消失。"
                    "🔴 **锚点必须与 J22 不同**：J22 用 `export interface AutoResolveMatch {`、"
                    "J31 用 `PendingLinksResponse` 的别名行 —— 三条若共用同一行字节，"
                    "整集 `apply` 时无法同时落位（§53.55 的 J19/J20 教训）。"
                    "针单行、`api.ts` 里恰好 1 次（实测）。"
                ),
            ),
            Mutation(
                label="J31 api.ts 又自己声明 EventMarketLink：兄弟端点那份 10 字段副本",
                path=_API_TS,
                old=b"export type PendingLinksResponse = GeneratedPendingLinksResponse;",
                new=(
                    b"export interface EventMarketLink {\n"
                    b"  id: string;\n"
                    b"  event_id: string;\n"
                    b"}\n\n"
                    b"export type PendingLinksResponse = GeneratedPendingLinksResponse;"
                ),
                guard_file=_T_GENERATE_TYPES,
                guards=("test_pending_callsites_read_the_generated_contract",),
                note=(
                    "同一条负向判据作用在**第二个名字**上（§4.2：那条循环覆盖三个名字，"
                    "只变异第一个只能证明循环存在、不能证明它走到了后面）。"
                    "`EventMarketLink` 是本节新增的兄弟端点响应模型，前端**从来没有**手写过它的副本 —— "
                    "所以这条变异问的是「将来会不会有人手写一个」。"
                    "插在 `PendingLinksResponse` 别名行之前：与 J32 的针（`PendingLink` 那一行）相邻但**不相交**，"
                    "与 J30 的锚点也不同。针单行、恰好 1 次（实测）。"
                ),
            ),
            Mutation(
                label="J32 别名被改指内联形状：副本没回来，但契约来源已经不是生成类型",
                path=_API_TS,
                old=b"export type PendingLink = GeneratedPendingLinkItem;",
                new=(
                    b"export type PendingLink = {\n"
                    b"  event_id: string;\n"
                    b"  contract_id?: string;\n"
                    b"};"
                ),
                guard_file=_T_GENERATE_TYPES,
                guards=("test_pending_callsites_read_the_generated_contract",),
                note=(
                    "**不插 interface**，只把别名指回内联形状 ⇒ 只可能红在 `assertIn`（别名）那半。"
                    "与 J30 合起来把同一个测试的两半**隔离**出来（§53.54 的 J5/J7 同款）。"
                    "它也是**更隐蔽**的那一种：名字仍是 `PendingLink`、仍是 `export type`，"
                    "`_interface_bodies` 收集不到它 —— 只有把别名钉成**精确字符串**才看得见。"
                    "替换串沿用**旧手写形状**（`contract_id?` 可选）：那正是被退役的那份副本，"
                    "所以这条同时证明「声明的键数与生产者不符」不会悄悄回到别名里。"
                    "针单行、恰好 1 次（实测）。"
                ),
            ),
            Mutation(
                label="J33 pendingLinks 调用点绕过已发布信封：generic 退回内联形状",
                path=_API_TS,
                old=b'api<PendingLinksResponse>("/events/links/pending")',
                new=b'api<{ pending: unknown[] }>("/events/links/pending")',
                guard_file=_T_GENERATE_TYPES,
                guards=("test_pending_callsites_read_the_generated_contract",),
                note=(
                    "用 `unknown[]` 而不是还原成旧的 `PendingLink[]`：后者会让 `PendingLink` 重新变成"
                    "被使用的名字，**顺带**改变别处的判据，红的归因就不可辨了（§53.56 的 J25 同款）。"
                    "针含路径本身（这一处调用点很短，generic 与路径同行），"
                    "守卫「从路径行往回找最近的 `api<`」在这条上就是同行定位。"
                    "路径 `/events/links/pending` 在 `api.ts` 里恰好 1 次（实测）—— "
                    "**这也是本轮把别名上方注释里那句字面路径删掉的原因**：注释也是文件字节，"
                    "留着它会让这条守卫的路径计数变成 2（§53.56 踩过同一个坑）。"
                ),
            ),
            Mutation(
                label="J34 verifyLink 退回读队列元素：兄弟端点的 10 键被当成 14 键",
                path=_API_TS,
                old=b"    api<EventMarketLink>(`",
                new=b"    api<PendingLink>(`",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_pending_callsites_read_the_generated_contract",),
                note=(
                    "**本节特有的一条**：`/link/verify` 返回的是**行**（10 键），不是队列元素（14 键），"
                    "而 §53.57 之前这两个端点**共用同一个手写副本** —— 所以「退回共用」正是原来的缺陷。"
                    "还原成 `api<PendingLink>` 后 TS 仍然合法（`PendingLink` 是别名，仍在导出），"
                    "`tsc` 也不会报错（`res.json() as T` 是断言）—— "
                    "只有把两个端点的 generic **分别**钉住才看得见。"
                    "针不含路径（守卫从含 `/link/verify\\`` 的那一行往回找最近的 `api<`），"
                    "恰好 1 次（实测）。"
                ),
            ),
            Mutation(
                label="J35 PendingLinksResponse.pending 退回裸 dict：元素契约消失（静默）",
                path=_EVENT_MODEL,
                old=b"    pending: list[PendingLinkItem]",
                new=b"    pending: list[dict[str, Any]]",
                group="pending-envelope",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_pending_response_points_at_the_published_element",),
                note=(
                    "**静默**回归的典型（同 §53.55 的 J17、§53.56 的 J26）：`PendingLinksResponse` 是 "
                    "`FlexibleResponse`，退回 `list[dict[str, Any]]` 后**仍然校验通过、仍然返回同样的字节、"
                    "`tsc` 仍然全绿**，生成出来的元素类型只是悄悄回到 `{ [k: string]: unknown }` —— "
                    "也就是前端当初不得不手写 `PendingLink` 的那个形状。拦截点只能是**模型身份**判据。"
                    "🔴 **与 J36 同 `group`**：两条改的是**同一行字节**（本节的信封只有一个字段），"
                    "整集 `apply` 只能留第一条，显式同时点名两条会被 `_apply_preflight` 拒掉 —— "
                    "这正是 `group` 存在的理由（§4.3：同一段字节的两条改写必须同组）。"
                ),
            ),
            Mutation(
                label="J36 PendingLinksResponse.pending 加回默认值：已发布信封被静默放宽",
                path=_EVENT_MODEL,
                old=b"    pending: list[PendingLinkItem]",
                new=b"    pending: list[PendingLinkItem] = Field(default_factory=list)",
                group="pending-envelope",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_pending_response_points_at_the_published_element",),
                note=(
                    "信封那半（J35 是元素身份那半；两条各自钉住同一个测试里的一个断言，"
                    "§4.2：一成员被 N 处消费就要 N 条）。加回默认值会**静默地**把已发布信封"
                    "重新放宽成 `pending?:` —— 生成文本本身仍然「合法」，只有 `is_required()` 看得见。"
                    "与 J35 同 `group` 的理由见 J35。"
                ),
            ),
            Mutation(
                label="J37 PendingLinkItem.verified 从 bool 漂成 int：与行契约不再同形",
                path=_EVENT_MODEL,
                old=(
                    b"    link_confidence: float\n"
                    b"    verified: bool\n"
                    b"    linked_at: str\n"
                    b"    event_title: str"
                ),
                new=(
                    b"    link_confidence: float\n"
                    b"    verified: int\n"
                    b"    linked_at: str\n"
                    b"    event_title: str"
                ),
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_element_is_the_row_plus_four",),
                note=(
                    "**本节唯一一条只打「重叠不变量」的变异**：它改的是元素里那十个**共享**字段之一，"
                    "而元素那半（`test_the_element_requires_all_fourteen_keys`）**只查那四个 enrichment 字段的注解**，"
                    "所以这条只可能红在重叠循环上 —— 这正是那个循环承重的证明（§4.2：承重不能靠名字推）。"
                    "`verified` 真实类型是 `bool`：store 的 `_row_to_dict` 把 SQLite 的 0/1 转成 JSON 布尔；"
                    "漂成 `int` 看着无害（1/0 仍能通过），生成的 TS 却会变成 `number`。"
                    "🔴 **单行针 `    verified: bool` 在本文件出现 3 次** ⇒ 必须跨行；"
                    "下面那行 `    event_title: str` 只在 `PendingLinkItem` 里紧跟它（`EventMarketLink` 里后面是空行），"
                    "实测全文件唯一（§53.55 的 J28 同款做法）。"
                ),
            ),
            Mutation(
                label="J38 PendingLinkItem.event_summary 加回默认值：四个 enrichment 之一被放宽",
                path=_EVENT_MODEL,
                old=(
                    b"    event_title_zh: str\n"
                    b"    event_summary: str\n"
                    b"    event_resolution_criteria: str"
                ),
                new=(
                    b"    event_title_zh: str\n"
                    b'    event_summary: str = ""\n'
                    b"    event_resolution_criteria: str"
                ),
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_element_requires_all_fourteen_keys",),
                note=(
                    "打的是**元素那半**（J37/J39 是共享字段与行那一半）。"
                    "`event_summary` 是四个 enrichment 之一：路由用 `record.get(..., \"\")` 写它，"
                    "所以**永远有键**，给默认值就是错的 —— 它会把 `string` 静默放宽成 "
                    "`string | undefined`，而前端 `link.event_summary && …` 那种读法对两者都成立，"
                    "线上看不出来（空串与缺席在这里被折叠成同一个渲染结果）。"
                    "🔴 单行针 `    event_summary: str` 出现 2 次 ⇒ 跨行带上前后两行（三行组合实测唯一）。"
                ),
            ),
            Mutation(
                label="J39 EventMarketLink.linked_at 加回默认值：行契约被静默放宽",
                path=_EVENT_MODEL,
                old=(
                    b"    verified: bool\n"
                    b"    linked_at: str\n"
                    b"\n"
                    b"\n"
                    b"class PendingLinkItem"
                ),
                new=(
                    b"    verified: bool\n"
                    b'    linked_at: str = ""\n'
                    b"\n"
                    b"\n"
                    b"class PendingLinkItem"
                ),
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_element_is_the_row_plus_four",),
                note=(
                    "打的是**行那一半**（J37 是重叠那一半、J38 是元素那一半）。"
                    "`EventMarketLink` 的每一列都是 `NOT NULL` 且 `_row_to_dict` 全写 ⇒ 一个默认值都不该有。"
                    "拦它的判据是行自己的 `is_required()` 循环。"
                    "🔴 **针止于类尾**：本文件**通篇不写闭合大括号**（`grep -c '^}'` = 0 —— "
                    "类体靠 dedent 结束，这是本文件的既有风格，不是缺陷），"
                    "所以拿不到 `}` 当唯一后缀；改用「两行字段 + 两个空行 + 下一行 class 头」，"
                    "跨行组合实测唯一（单行 `    linked_at: str` 出现 3 次）。"
                ),
            ),
            Mutation(
                label="J40 api.ts 又自己声明 PredictionRecord：被退役的 18 键元素副本长回来",
                path=_API_TS,
                old=b"export interface EventListFilters {",
                new=(
                    b"export interface PredictionRecord {\n"
                    b"  id: string;\n"
                    b"  event_id: string;\n"
                    b"  event_title?: string;\n"
                    b"  ai_probability: number;\n"
                    b"  market_probability: number;\n"
                    b"  raw_edge: number;\n"
                    b"}\n\n"
                    b"export interface EventListFilters {"
                ),
                guard_file=_T_GENERATE_TYPES,
                guards=("test_recent_callsite_reads_the_generated_contract",),
                note=(
                    "§53.58 退役的那份手写副本被还原（不必写满 18 个键 —— 守卫判的是**名字**；"
                    "少写几行也让这条的替换串与 J30/J32 明显不同）。"
                    "**别名刻意留着** ⇒ 只可能红在 `assertNotIn` 那半，归因可辨"
                    "（§53.54 的 J5/J7、§53.55 的 J13/J15、§53.56 的 J22/J24、§53.57 的 J30/J32 同款）。"
                    "副本体内不带索引签名、名字不以 `Overlay` 结尾 ⇒ "
                    "`ALLOWED_OVERLAY_COPIES_IN_API_TS` 那条**老**判据仍然绿（本节的守卫必须**按名**判）；"
                    "`_interface_bodies` 只收 `export interface` ⇒ 这一次它收得到（别名不占这个字面量）。"
                    "🔴 **锚点必须与 J22、J30 不同**：三条若共用同一行字节，整集 `apply` 时无法同时落位"
                    "（§53.55 的 J19/J20 教训）。针是单行，`api.ts` 里恰好 1 次（实测）。"
                ),
            ),
            Mutation(
                label="J41 api.ts 又自己声明 RecentPredictionsResponse：信封副本长回来",
                path=_API_TS,
                old=b"export type RecentPredictionsResponse = GeneratedRecentPredictionsResponse;",
                new=(
                    b"export interface RecentPredictionsResponse {\n"
                    b"  count?: number;\n"
                    b"  total?: number;\n"
                    b"  limit?: number;\n"
                    b"  offset?: number;\n"
                    b"  predictions: unknown[];\n"
                    b"}\n\n"
                    b"export type RecentPredictionsResponse = GeneratedRecentPredictionsResponse;"
                ),
                guard_file=_T_GENERATE_TYPES,
                guards=("test_recent_callsite_reads_the_generated_contract",),
                note=(
                    "打的是同一条负向判据的**第三个名字**（J40 是第一个）。"
                    "§4.2 的理由：只变异第一个只能证明那个循环**存在**，不能证明它**走到了后面** —— "
                    "§53.57 的 J30/J31 就是这个理由的先例。"
                    "插在别名行**之前**：与 J42 的针（调用点那行）不相交，与 J40/J22/J30 的锚点也不同。"
                    "`predictions: unknown[]` 而不是 `PredictionRecord[]`：后者会让被退役的名字重新变成"
                    "**被使用**的名字，顺带改变别处的判据（§53.56 的 J25、§53.57 的 J33 同款）。"
                    "针单行、`api.ts` 里恰好 1 次（实测）。"
                ),
            ),
            Mutation(
                label="J42 recentPredictions 调用点绕过已发布信封：generic 退回内联形状",
                path=_API_TS,
                old=b"    api<RecentPredictionsResponse>(",
                new=b"    api<{ predictions: unknown[] }>(",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_recent_callsite_reads_the_generated_contract",),
                note=(
                    "针是 **generic 那一行、不含路径**：守卫从含路径的那一行往回找最近的 `api<`，"
                    "两行相邻 —— 所以这条钉的是「往回找」这半，与 §53.56 的 J25（generic 与路径同行）互补。"
                    "旧形状是把信封**内联**写进 generic（`{ predictions: PredictionRecord[]; count?: … }`），"
                    "这里退回内联但只留一个字段：仍然是「不经由已发布信封」，而 TS 依然合法 —— "
                    "`res.json() as T` 是断言，`tsc` 对形状什么都不主张。"
                ),
            ),
            Mutation(
                label="J43 RecentPredictionsResponse.predictions 退回裸 dict：元素契约消失（静默）",
                path=_EVENT_MODEL,
                old=b"    predictions: list[RecentPredictionItem]",
                new=b"    predictions: list[dict[str, Any]]",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_recent_response_points_at_the_published_element",),
                note=(
                    "**静默**回归的典型（同 §53.55 的 J17、§53.56 的 J26、§53.57 的 J35）："
                    "`RecentPredictionsResponse` 是 `FlexibleResponse`，退回 `list[dict[str, Any]]` 后"
                    "**仍然校验通过、仍然返回同样的字节、`tsc` 仍然全绿**，生成出来的元素只是悄悄回到 "
                    "`{ [k: string]: unknown }` —— 也就是前端当初不得不手写 18 键副本的那个形状。"
                    "拦截点只能是**模型身份**判据（`assertEqual(annotation, list[Element])`）。"
                ),
            ),
            Mutation(
                label="J44 RecentPredictionsResponse.count 加回默认值：已发布信封被静默放宽",
                path=_EVENT_MODEL,
                old=(
                    b"typed fields instead of a hand-written subset of 18.\n"
                    b'    """\n'
                    b"\n"
                    b"    count: int\n"
                    b"    total: int\n"
                    b"    limit: int\n"
                    b"    offset: int"
                ),
                new=(
                    b"typed fields instead of a hand-written subset of 18.\n"
                    b'    """\n'
                    b"\n"
                    b"    count: int = 0\n"
                    b"    total: int\n"
                    b"    limit: int\n"
                    b"    offset: int"
                ),
                guard_file=_T_GENERATE_TYPES,
                guards=("test_recent_response_points_at_the_published_element",),
                note=(
                    "打的是**信封那半**（J45/J46 是元素那半）。加回默认值会**静默地**把已发布信封重新放宽成 "
                    "`count?:` —— 生成文本本身仍「合法」，只有 `is_required()` 看得见。"
                    "🔴 **锚点为什么不从 `count` 那一行直接起算**：本文件另有 `FreshEdgesResponse` 含"
                    "「docstring 结束 + 四个恒写信封键」这段字节（实测那个短针出现 2 次），"
                    "而且本节还要**避让 J43 的针**（元素那一行紧随其后）⇒ "
                    "从 docstring 尾句向上取上界，止于 `offset: int`、**不含**元素那一行"
                    "（§53.56 的 J29 同款做法）。代价：那句 docstring 措辞一改这条针就断，"
                    "届时应由 `count_needle != 1` 立刻报出来。跨行组合实测唯一。"
                ),
            ),
            Mutation(
                label="J45 RecentPredictionItem.trust 丢掉 | None：可空列被声明成必填（比 schema 更紧）",
                path=_EVENT_MODEL,
                old=(
                    b"    raw_edge: float\n"
                    b"    trust: float | None\n"
                    b"    adjusted_edge: float | None"
                ),
                new=(
                    b"    raw_edge: float\n"
                    b"    trust: float\n"
                    b"    adjusted_edge: float | None"
                ),
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_element_requires_all_thirty_five_keys",),
                note=(
                    "**本节特有的一条方向**：`trust` 的列可空，只是 171 行实测**从未**出现过 null —— "
                    "把 `| None` 收掉看着像「更严」，实则是拿**观测**当**保证**："
                    "声明得比 schema 紧，第一次写入空值就会 422。"
                    "它与 J43 恰好互为反面（J43 把元素变松、这条把它变紧），"
                    "而两者由**同一个** `assertEqual(annotation, want)` 循环拦住 —— "
                    "这正是那张 35 键注解表存在的理由（只查名字的守卫对两个方向都盲）。"
                    "🔴 单行针 `    trust: float | None` 出现 2 次（另一处在 `MarketQuality` 一族）⇒ 必须跨行；"
                    "带上前后两行 `raw_edge` / `adjusted_edge` 后实测唯一。"
                ),
            ),
            Mutation(
                label="J46 RecentPredictionItem.direction_correct 从 int|None 漂成 bool：改写线上取值",
                path=_EVENT_MODEL,
                old=b"    direction_correct: int | None",
                new=b"    direction_correct: bool",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_element_requires_all_thirty_five_keys",),
                note=(
                    "**取值忠实性**，且与 §53.57 的 J37 **方向相反**：那一节 `EventMarketLink.verified` 是 "
                    "`bool` 漂成 `int`（store 的 `_row_to_dict` 确实转换过，所以 `bool` 才对），"
                    "这一节 `list_recent` 走 `[dict(row) …]`、**不转换** ⇒ store 原样吐 SQLite 的 0/1/NULL，"
                    "声明成 `bool` 会把 1/0 改写成 JSON 布尔 —— 是**改线上字节**，不是「更严」。"
                    "两条合起来说明「谁转换、谁不转换」在两个端点上有两个答案，**不能按名字推**："
                    "同一个词表、同一个 store，读法不同则忠实类型不同。"
                    "针单行、本文件恰好 1 次（实测）—— `direction_correct` 只在这里出现。"
                ),
            ),
            Mutation(
                label="J47 api.ts 又自己声明 EventDiscoveryResponse：被退役的信封副本长回来",
                path=_API_TS,
                old=b"export type TradeStats = GeneratedTradeStats;",
                new=(
                    b"export interface EventDiscoveryResponse {\n"
                    b"  count?: number;\n"
                    b"  events?: unknown[];\n"
                    b"}\n\n"
                    b"export type TradeStats = GeneratedTradeStats;"
                ),
                guard_file=_T_GENERATE_TYPES,
                guards=("test_discovery_callsite_reads_the_generated_contract",),
                note=(
                    "打的是「`api.ts` 不得再声明信封副本」这条负向判据的**第一个名字**。"
                    "**别名刻意留着** ⇒ 只可能红在 `assertNotIn(name, api_bodies)` 那半，归因可辨"
                    "（§53.54 的 J5/J7、§53.57 的 J30/J32、§53.58 的 J40/J41 同款）。"
                    "副本体内不带索引签名、名字也不以 `Overlay` 结尾 ⇒ "
                    "`ALLOWED_OVERLAY_COPIES_IN_API_TS` 那条**老**判据仍然绿 —— "
                    "本节的守卫必须**按名**判，不能靠 overlay 检测器。"
                    "🔴 **锚点必须与 J40/J41 不同**：三条若共用同一行字节，整集 `apply` 时无法同时落位"
                    "（§53.55 的 J19/J20 教训）。针单行、`api.ts` 里恰好 1 次（实测）。"
                ),
            ),
            Mutation(
                label="J48 api.ts 又自己声明 EventRecord：元素副本换个身份长回来",
                path=_API_TS,
                old=b"// The `/events/discover` envelope used to be hand-written *inline* at the call",
                new=(
                    b"export interface EventRecord {\n"
                    b"  event_id?: string;\n"
                    b"}\n\n"
                    b"// The `/events/discover` envelope used to be hand-written *inline* at the call"
                ),
                guard_file=_T_GENERATE_TYPES,
                guards=("test_discovery_callsite_reads_the_generated_contract",),
                note=(
                    "同一条负向判据的**第二个名字**（J47 是第一个）。§4.2 的理由：只变异第一个"
                    "只能证明那个循环**存在**，不能证明它**走到了后面** —— "
                    "§53.57 的 J30/J31、§53.58 的 J40/J41 是同款先例。"
                    "这里两个名字是**两种不同的回归**（元素副本 vs 信封副本），不是同一条的两个 spelling "
                    "⇒ 两条都值得登记；§53.58 拒绝补的**第三个**名字走的是同一个断言形状、已被前两条覆盖，"
                    "与此不同。"
                    "🔴 **锚点就是本节点新增的那句注释**（整个 `// The …` 行，`api.ts` 里恰好 1 次，实测）："
                    "用它当锚点可同时保证与 J47/J49/J50/J51 的针互不相交。"
                    "注意 `EventRecord` 本来就**被导入**（`from \"./types\"`），所以这里新增的是一个**声明**，"
                    "而 `_interface_bodies` 只收 `export interface` ⇒ 收得到。"
                ),
            ),
            Mutation(
                label="J49 discover 调用点绕过已发布信封：generic 退回内联形状",
                path=_API_TS,
                old=b"    api<EventDiscoveryResponse>(",
                new=b"    api<{ events: unknown[] }>(",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_discovery_callsite_reads_the_generated_contract",),
                note=(
                    "针是 **generic 那一行、不含路径**：守卫从含路径的那一行往回找最近的 `api<`，"
                    "两行相邻 —— 所以这条钉的是「往回找」这半，与 §53.56 的 J25（generic 与路径同行）互补，"
                    "与 §53.58 的 J42 同款。旧形状是把信封**内联**写进 generic，"
                    "这里退回内联但只留一个字段：仍然是「不经由已发布信封」，而 TS 依然合法 —— "
                    "`res.json() as T` 是断言，`tsc` 对形状什么都不主张。"
                    "🔴 用 `unknown[]` 而不是 `EventRecord[]`：后者会让被扫的那段字面量重新出现，"
                    "顺带把「内联形状不得回来」那条判据也踩红，归因就不清了"
                    "（§53.56 的 J25、§53.57 的 J33 同款顾虑）。"
                ),
            ),
            Mutation(
                label="J50 被退役的内联信封形状以类型别名回来：判据按字面量扫全文件",
                path=_API_TS,
                old=b"export type FreshEdgesResponse = GeneratedFreshEdgesResponse;",
                new=(
                    b"export type DiscoveryInlineShape = "
                    b"{ events: EventRecord[]; source?: string; count?: number };\n\n"
                    b"export type FreshEdgesResponse = GeneratedFreshEdgesResponse;"
                ),
                guard_file=_T_GENERATE_TYPES,
                guards=("test_discovery_callsite_reads_the_generated_contract",),
                note=(
                    "打的是**第三条互不重叠的判据**：守卫除了「别名必须在」和「调用点必须读别名」，"
                    "还按**字面量**扫全文件（`assertNotIn(inline_spelling, api_text)`）—— "
                    "因为别名在、调用点也对，手写形状仍可能换个类型名、换个位置溜回来。"
                    "这里不动别名、不动调用点 ⇒ 只可能红在那一条，归因可辨。"
                    "🔴 **本文件自己差点把这条判据写死**：那句注释的初稿**逐字引用**了这段形状，"
                    "而注释也是文件字节 ⇒ 守卫会从写下那一刻起就恒红。"
                    "改法是注释只**描述**不**复现**"
                    "（§4.3「自指变异不能以完整字面量 + 注释引用同现一处」）。"
                    "🔴 **§53.59 换过针，理由同样是整集 `apply` 而不是判据**：原针 "
                    "`export type EdgeTrajectory = GeneratedEdgeTrajectorySummary;` 与 **J1 字节完全相同**，"
                    "而 J1 排在前面、它的 `new` 把那一行**换成接口**（别名行消失）⇒ "
                    "整集 `apply` 到本条时针已经找不到（§53.59 对全部 12 套重放模拟，实测只有 J6 与本条落不下去）。"
                    "两条语义上并不互斥（J1 改 `EdgeTrajectory`，本条只是另插一个类型别名）⇒ 换锚而非宣告 `group`。"
                    "新锚 `export type FreshEdgesResponse = GeneratedFreshEdgesResponse;` 实测 1 次、"
                    "与 J1 的窗口相邻但不重叠（实测 12220 > 12216）。"
                    "而且位置一换**顺带强化了这条变异要证的事**：守卫扫的是**全文件字面量**，"
                    "所以形状从哪个别名旁边溜回来都该被抓 —— 现在它离被退役的那处**更远**，判据仍然抓得住。"
                ),
            ),
            Mutation(
                label="J51 别名被改指别处：契约来源不再是生成的信封（副本没回来）",
                path=_API_TS,
                old=b"export type EventDiscoveryResponse = GeneratedEventDiscoveryResponse;",
                new=b"export type EventDiscoveryResponse = GeneratedRecentPredictionsResponse;",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_discovery_callsite_reads_the_generated_contract",),
                note=(
                    "§53.54 的 J15、§53.55 的 J19 同族：**副本没回来，但契约来源已经换了**。"
                    "这里不动调用点（仍是 `api<EventDiscoveryResponse>(`）⇒ 调用点那半仍绿，"
                    "只红在「别名必须逐字是那一条」上。"
                    "🔴 目标故意挑一个**已存在**的生成名：若改指一个不存在的名字，"
                    "编辑本身就废掉了整行，等于在测「这行还在不在」，而不是在测「它指着谁」。"
                    "针单行、`api.ts` 里恰好 1 次（实测）。"
                ),
            ),
            Mutation(
                label="J52 EventDiscoveryResponse.events 退回裸 dict：元素契约消失（静默）",
                path=_EVENT_MODEL,
                old=b"    events: list[EventRecord]",
                new=b"    events: list[dict[str, Any]]",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_discovery_response_points_at_the_published_element",),
                note=(
                    "**静默**回归的典型（同 §53.55 的 J17、§53.57 的 J35、§53.58 的 J43）："
                    "`EventDiscoveryResponse` 是 `FlexibleResponse`，退回 `list[dict[str, Any]]` 后"
                    "**仍然校验通过、仍然返回同样的字节、`tsc` 仍然全绿**，生成出来的元素只是悄悄回到 "
                    "`{ [k: string]: unknown }[]` —— 也就是前端当初不得不手写那个对象类型的原因。"
                    "拦截点只能是**模型身份**判据（`assertEqual(annotation, list[EventRecord])`）。"
                    "🔴 针**止于行尾、不含换行** ⇒ 与 J53 的信封针（以 `count: int` + CRLF 结尾）"
                    "字节区间不相交，整集 `apply` 时两条都能落位（实测：信封针终点 == 本针起点，重叠为 False）。"
                ),
            ),
            Mutation(
                label="J53 EventDiscoveryResponse.count 加回默认值：已发布信封被静默放宽",
                path=_EVENT_MODEL,
                old=(
                    b"    platform: str\r\n"
                    b"    source: str\r\n"
                    b"    count: int\r\n"
                ),
                new=(
                    b"    platform: str\r\n"
                    b"    source: str\r\n"
                    b"    count: int = 0\r\n"
                ),
                guard_file=_T_GENERATE_TYPES,
                guards=("test_discovery_response_points_at_the_published_element",),
                note=(
                    "打的是**信封那半**（J52 是元素那半）。加回默认值会**静默地**把已发布信封重新放宽成 "
                    "`count?:` —— 生成文本本身仍「合法」，只有 `is_required()` 看得见。"
                    "🔴 **锚点必须带 CRLF**：本文件是纯 CRLF（实测 crlf=1387 / bare_lf=0），"
                    "同一组合用裸 `\\n` 写命中 **0 次**（实测对照）；"
                    "而单行针 `    count: int` 在 `event.py` 里出现 **11 次**，不可用。"
                    "带上 `platform` / `source` 两行后实测唯一。"
                    "🔴 针**不含元素那一行**，否则与 J52 的针重叠、整集 `apply` 无法同时落位（实测不重叠）。"
                ),
            ),
            Mutation(
                label="J54 EventRecord.intelligence_report 加回默认值：元素边界被静默放宽",
                path=_EVENT_MODEL,
                old=b"    intelligence_report: IntelligenceReport",
                new=b"    intelligence_report: IntelligenceReport | None = None",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_element_boundary_is_the_eleven_required_keys",),
                note=(
                    "本节点**新增**的那条边界：具名之后元素从「什么都不校验」变成"
                    "「按 `EventRecord` 的必填键校验」⇒ 必填集**就是** 200 与 500 的分界。"
                    "给其中一个键加回默认值，等于把边界悄悄挪走，而**没有别的东西会变红**"
                    "（生成出来的 TS 照样合法、`tsc` 照样绿、响应字节在旧数据上照样一样）。"
                    "所以拦它的只能是那条 `assertEqual(required_set, {…11 个})`。"
                    "🔴 另外十个必填键**不逐条补变异**（同 §53.58 的取舍）：它们与这条共用**同一个**"
                    "集合相等断言，覆盖的是这一类的一个代表，而不是十一条同形变异。"
                    "⚠️ 前提是该断言仍是集合相等；若哪天拆成十一条独立断言，这条的覆盖就退化成只覆盖一个成员，届时要补。"
                    "针单行、`event.py` 里恰好 1 次（实测）。"
                ),
            ),
            Mutation(
                label="J55 api.ts 又自己声明 DecisionReport：被退役的 17 字段报告副本长回来",
                path=_API_TS,
                old=b"// The `/events/decisions/open` envelope was hand-written *inline* at its call",
                new=(
                    b"export interface DecisionReport {\n"
                    b"  event_id?: string;\n"
                    b"  risk?: { flags?: string[] };\n"
                    b"}\n\n"
                    b"// The `/events/decisions/open` envelope was hand-written *inline* at its call"
                ),
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_report_callsite_reads_the_generated_contract",),
                note=(
                    "§53.59 节点② 退役的那份手写副本被还原（不必写满 17 个键 —— 守卫判的是**名字**）。"
                    "**别名刻意留着** ⇒ 只可能红在 `assertNotIn(name, api_bodies)` 那半，归因可辨。"
                    "注意这条与 §53.52 立的 `_overlay_copies` 那条**老**判据是两回事："
                    "本副本名字不以 `Overlay` 结尾、体内也不带索引签名 ⇒ 老判据仍绿，"
                    "而它恰恰是 `test_decision_report_reads_the_generated_contract` **改写过**的那半 —— "
                    "§53.52 当初断言的「`DecisionReport` 是手写 interface」在节点② 已经不成立。"
                    "🔴 锚点必须与 J47/J48 不同（三条若同行字节，整集 `apply` 无法同时落位）；"
                    "用节点② 新增的那句注释，实测 `api.ts` 里恰好 1 次。"
                ),
            ),
            Mutation(
                label="J56 openDecisions 调用点绕过已发布信封：generic 退回内联形状",
                path=_API_TS,
                old=b"    api<OpenDecisionsResponse>(",
                new=b"    api<{ decisions: unknown[] }>(",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_report_callsite_reads_the_generated_contract",),
                note=(
                    "与 J49 同款：针是 **generic 那一行、不含路径**，守卫从含路径那行往回找最近的 `api<`。"
                    "旧形状是把信封**内联**写进 generic（五个非元素键全可选 + `DecisionReport[]`），"
                    "这里退回内联但只留一个字段：仍是「不经由已发布信封」，而 TS 依然合法 —— "
                    "`res.json() as T` 是断言。"
                ),
            ),
            Mutation(
                label="J57 OpenDecisionsResponse 别名被改指别处：契约来源不再是生成的信封",
                path=_API_TS,
                old=b"export type OpenDecisionsResponse = GeneratedOpenDecisionsResponse;",
                new=b"export type OpenDecisionsResponse = GeneratedEventDiscoveryResponse;",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_report_callsite_reads_the_generated_contract",),
                note=(
                    "§53.54 的 J15、§53.58 的 J51 同族：**副本没回来，但契约来源已经换了**。"
                    "这里不动调用点 ⇒ 调用点那半仍绿，只红在「别名必须逐字是那一条」上。"
                    "目标故意挑一个**已存在**的生成名（改指不存在的名字就等于废掉整行，"
                    "那是在测「行还在不在」而不是「它指着谁」）。针单行、实测 1 次。"
                ),
            ),
            Mutation(
                label="J58 OpenDecisionsResponse.decisions 退回裸 dict：元素契约消失（静默）",
                path=_EVENT_MODEL,
                old=b"    decisions: list[DecisionReport]",
                new=b"    decisions: list[dict[str, Any]]",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_open_decisions_response_points_at_the_published_element",),
                note=(
                    "**静默**回归的典型（同 §53.55 的 J17、§53.58 的 J43、J52）："
                    "`OpenDecisionsResponse` 是 `FlexibleResponse`，退回 `list[dict[str, Any]]` 后"
                    "**仍然校验通过、仍然返回同样的字节、`tsc` 仍然全绿**，"
                    "生成出来的元素只是悄悄回到 `{ [k: string]: unknown }[]` —— "
                    "也就是前端当初不得不手写 17 字段副本的那个形状。"
                    "拦截点只能是**模型身份**判据。"
                    "🔴 针**止于行尾、不含换行** ⇒ 与 J59 的信封针（以 `decision_totals` 行 + CRLF 结尾）"
                    "字节区间不相交（实测：信封针终点 == 本针起点，重叠 False）。"
                ),
            ),
            Mutation(
                label="J59 OpenDecisionsResponse.count 加回默认值：已发布信封被静默放宽",
                path=_EVENT_MODEL,
                old=(
                    b"    count: int\r\n"
                    b"    total: int\r\n"
                    b"    limit: int\r\n"
                    b"    offset: int\r\n"
                    b"    decision_totals: dict[str, int]\r\n"
                ),
                new=(
                    b"    count: int = 0\r\n"
                    b"    total: int\r\n"
                    b"    limit: int\r\n"
                    b"    offset: int\r\n"
                    b"    decision_totals: dict[str, int]\r\n"
                ),
                guard_file=_T_GENERATE_TYPES,
                guards=("test_open_decisions_response_points_at_the_published_element",),
                note=(
                    "打的是**信封那半**（J58 是元素那半）。加回默认值会**静默地**把已发布信封放宽成 "
                    "`count?:` —— 生成文本仍「合法」，只有 `is_required()` 看得见。"
                    "🔴 **锚点必须带 CRLF**：本文件纯 CRLF；同一组合用裸 `\\n` 写命中 **0 次**（实测对照），"
                    "而单行针 `    count: int` 在 `event.py` 里出现 11 次。"
                    "🔴 针止于 `decision_totals` 行、**不含 `decisions` 行**，否则与 J58 重叠。"
                    "⚠️ 与 §53.58 的 J44、本节的 J53 是同一条判据在三个信封上各一次 —— "
                    "三个信封是**三个模型**，不能只变异一个（§4.2「一成员被 N 处消费要 N 条」）。"
                ),
            ),
            Mutation(
                label="J60 DecisionReport 丢掉 llm_telemetry：前端当初漏掉的那个键",
                path=_EVENT_MODEL,
                old=(
                    b"    llm_telemetry: LLMTelemetry | None\r\n"
                    b"    final_displayed_direction: str | None\r\n"
                    b"    final_downgrade_reason: str | None"
                ),
                new=(
                    b"    final_displayed_direction: str | None\r\n"
                    b"    final_downgrade_reason: str | None"
                ),
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_report_writes_all_eighteen_keys",),
                note=(
                    "**本节最有信息量的一条**：删掉的正是前端手写副本**漏掉**的那个键。"
                    "§53.58 的教训在这里是反着的 —— 那边的模型是**闭合**的、少声明会**静默丢键**；"
                    "这里 `DecisionReport` 也是闭合的，但生产者是**逐键构造**（不是铺开一行），"
                    "所以少声明**不会**丢键，只会**变成 500**（pydantic 校验响应时缺必填键）。"
                    "换句话说：同一个键，在「铺开」型生产者上表现成静默缺数据，"
                    "在「逐键构造」型生产者上表现成响亮失败 —— 不能按「模型是闭合的」推。"
                    "拦它的只能是**按名**的键集相等断言；**计数**型断言抓不到「换了哪个键」。"
                ),
            ),
            Mutation(
                label="J61 DecisionReport.actionable_recommendation 丢掉 | None：可缺席的块被声明成必填",
                path=_EVENT_MODEL,
                old=(
                    b"    actionable_recommendation: ActionableRecommendation | None\r\n"
                    b"    decision_quality: DecisionQuality | None"
                ),
                new=(
                    b"    actionable_recommendation: ActionableRecommendation\r\n"
                    b"    decision_quality: DecisionQuality | None"
                ),
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_nested_blocks_are_named_models",),
                note=(
                    "打的是**嵌套块的身份/可空性**（J62 打另一个块的取值宽窄）。"
                    "`actionable_recommendation` 是九个块里唯一**可为 None** 的："
                    "生产者写的是 `record.get(\"actionable_recommendation\")`，"
                    "而记录可能整块缺失 ⇒ 收掉 `| None` 会把「合法地没有建议」变成 500。"
                    "与 §53.58 的 J45 同方向（拿观测当保证、比生产者更紧）。"
                    "🔴 针必须带上一行 `decision_quality`：单行 `    actionable_recommendation: ActionableRecommendation | None` "
                    "在 `EventRecord` 里也出现（同一模型族），实测 2 次。"
                ),
            ),
            Mutation(
                label="J62 DecisionRisk.flags 从 list[Any] 收成 list[str]：按观测收窄",
                path=_EVENT_MODEL,
                old=b"    level: str | None\r\n    flags: list[Any]",
                new=b"    level: str | None\r\n    flags: list[str]",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_nested_blocks_are_named_models",),
                note=(
                    "**「按观测收窄」的首次登记**，方向与 §53.58 的 J45 相同、但来源不同："
                    "那里是「可空列被写成必填」，这里是「元素类型被收窄」。"
                    "store 模型声明 `Risk.flags: list[Any]`，生产者只 guard `isinstance(..., list)`，"
                    "47/47 行恰好都是字符串 —— 但那是**观测**不是**保证**。"
                    "收成 `list[str]` 看着像「更精确」，实则是把前端那句 `string[]` 的错误原样搬到后端。"
                    "代价（已在本节消费侧处理）：`decision-card.tsx` 因此改成渲染 `String(flag)`，"
                    "而不是让模型去迁就渲染。"
                    "🔴 针必须带上一行 `level`：单行 `    flags: list[Any]` 是本文件 `Risk.flags: list[Any] = []` 的**子串**，"
                    "实测 2 次；而 `    level: str | None` 单行也出现 2 次（`DecisionConfidence` 有一个）"
                    "⇒ 两行组合才是最小唯一单元（实测 1 次）。"
                ),
            ),
            Mutation(
                label="J63 /{event_id}/decision 的 response_model 退回裸 FlexibleResponse：具名契约重新不可见",
                path=_DIGEST_ROUTES,
                old=b'@router.get("/{event_id}/decision", response_model=DecisionReport)',
                new=b'@router.get("/{event_id}/decision", response_model=FlexibleResponse)',
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_event_decision_route_binds_the_report_model",),
                note=(
                    "§53.60：这是 §53.59 留下的**同族最后一个缺口**。"
                    "`/events/{event_id}/decision` 与 `/events/decisions/open` 共用**同一个生产者** "
                    "`build_decision_report`，后者经 `OpenDecisionsResponse.decisions` 已具名，"
                    "前者返回的是**无信封的裸报告**，于是它的 `response_model` 还停在裸 "
                    "`FlexibleResponse` ⇒ OpenAPI 发的是 `#/components/schemas/FlexibleResponse`，"
                    "报文到达浏览器时**没有已声明的形状** —— 正是 53.59 在姊妹端点上终结的状态。"
                    "拦它的判据是**路由对象身份**（`route.response_model is DecisionReport`），"
                    "不是源码文本：`FlexibleResponse` 是 `extra='allow'`、零字段，退回去之后"
                    "**仍然校验通过、仍然 200、`tsc` 仍然全绿**，唯一变化是已发布的 schema 变回不透明体。"
                    "🔴 **具名不是零字节差**：探针（`.workbuddy/audit-2026-10-03/probe_5360.py`，"
                    "59 用例 = 46 条生产真实行 + 13 条合成，`flexible`/`committed` 同树产出）实测"
                    "每个域内响应的 `$.probability` 子对象键序从 `estimated,baseline` 变为 "
                    "`baseline,estimated`（等长、canonical 相同 ⇒ **纯键序**）；"
                    "老板拍板**接受**（两端口径由此统一到模型序；备选的「改 `DecisionProbability` 字段序」"
                    "会把 `/decisions/open` 的键序翻回去，未取）。"
                    "复述「具名零字节差」之前必须带上这条前提 —— 它不成立。"
                    "🔴 **重塑隐患是模型的性质、不是本变异的**：`DecisionQuality`/`MarketQuality`/"
                    "`SourceReliability`/`LLMTelemetry` **全部字段都带默认值**（0 必填），"
                    "所以具名会把记录里**缺失的键实体化成默认值**、并**丢弃模型未声明的键**。"
                    "实测不可达：全库 257 条记录里 48 条 `market_quality` 是**旧 schema**"
                    "（`degraded`/`degrade_reason`/`low_liquidity_flag`）、48 条 `llm_telemetry` 部分缺失、"
                    "5 条 `source_reliability` 只缺 `error`，**全都没有 committed prediction** ⇒ "
                    "两个端点都到不了；171 个**有** prediction 的事件里 overlay **要么缺席要么与模型字段集完全一致**"
                    "（0 条会被重塑）。⚠️ 这条性质是 §53.59 引入、已活在 `/decisions/open` 上，"
                    "本端点只是继承 —— 哪天一条旧形记录**获得了 prediction**，它就会开始被重塑，届时要拍板。"
                    "🔴 针是**整行装饰器**、不含换行：`response_model=DecisionReport)` 单独算"
                    "在本文件命中 **1 次**（已字节复核），而 `response_model=FlexibleResponse)` 命中 **55 次**"
                    "⇒ 必须带路径前缀才是唯一单元。守卫读的是 `router.routes` 里的路由对象"
                    "（`include_router` 之前路径仍是 `/{event_id}/decision`），本文件、"
                    "`test_generate_types.py` 里都没有 `/{event_id}/decision` 或 "
                    "`response_model=DecisionReport` 字面量（各 0 次，已复核）⇒ 不自指。"
                ),
            ),
            Mutation(
                label="J64 /predictions/calibration 的 response_model 退回裸 FlexibleResponse：契约重新不可见",
                path=_DIGEST_ROUTES,
                old=b"response_model=PredictionCalibrationResponse)",
                new=b"response_model=FlexibleResponse)",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_calibration_routes_bind_the_named_models",),
                note=(
                    "§53.61：本轮具名的第一个端点。`calibration_summary()` 的两条"
                    "返回路径（scored 聚合与 `no_data` 早退）写**同 9 键**且逐键写全，"
                    "具名后探针域内 7/7 字节级一致（前提：模型字段序 = 生产者 dict 序）。"
                    "拦它的判据是**路由对象身份**（`route.response_model is "
                    "PredictionCalibrationResponse`），不是源码文本：裸 `FlexibleResponse` "
                    "是 `extra='allow'`、零字段，退回去之后**仍然校验通过、仍然 200、"
                    "`tsc` 仍然全绿**，唯一变化是已发布的 schema 变回不透明体。"
                    "守卫读 `router.routes`（`include_router` 之前路径仍是 "
                    "`/predictions/calibration`）。🔴 针 `response_model="
                    "PredictionCalibrationResponse)` 在本文件命中 **1 次**（已字节复核）；"
                    "裸 `FlexibleResponse)` 有 55 处，必须带模型名前缀才是唯一单元；"
                    "守卫文件里没有该字面量 ⇒ 不自指。"
                ),
            ),
            Mutation(
                label="J65 /predictions/calibration/buckets 的 response_model 退回裸 FlexibleResponse",
                path=_DIGEST_ROUTES,
                old=b"response_model=CalibrationBucketSummaryResponse)",
                new=b"response_model=FlexibleResponse)",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_calibration_routes_bind_the_named_models",),
                note=(
                    "§53.61：本轮具名的第二个端点，与 J64 同判据（路由对象身份）、"
                    "同一守卫的两个成员（§4.2：一成员被消费处就要自己的断言）。"
                    "`calibration_bucket_summary()` 两条路径写**同 4 键**；"
                    "`no_data` 是**映射为空**而非键缺席。路由函数体内的"
                    "**局部 import**（`from app.memory.prediction_store import "
                    "calibration_bucket_summary`）不在模块命名空间 ⇒ "
                    "探针的 patch 必须打 `app.memory.prediction_store` 侧而非 "
                    "`events_routes` 侧（`from`-import 按值绑定）；该事实已写进 "
                    "探针注释。🔴 针在本文件命中 **1 次**（已字节复核）。"
                ),
            ),
            Mutation(
                label="J66 信封字段 segment_min_samples 添默认值：必填放宽成可选",
                path=_EVENT_MODEL,
                old=b"    segment_min_samples: int\r\n    segments: dict[str, CalibrationSegment]",
                new=b"    segment_min_samples: int = 8\r\n    segments: dict[str, CalibrationSegment]",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_summary_envelope_and_elements",),
                note=(
                    "**「更松」方向**：两条生产路径都写该键，默认值只会把生成类型放宽成 "
                    "`?:`，没有任何「缺席」可描述（§Z8：`is_required()` 在模型层就等价于"
                    "生成文本里的 `?:`，不必读生成文件）。守卫用逐字段 `is_required()` "
                    "subtest 拦。🔴 针必须带后随行 `segments:`：单行 "
                    "`    segment_min_samples: int` 在 `CalibrationSegment` 里也出现"
                    "（同文件 2 处），带后随行才是唯一单元（已字节复核）。"
                ),
            ),
            Mutation(
                label="J67 信封删字段 mean_raw_edge：键集收缩、前端读不到该键",
                path=_EVENT_MODEL,
                old=b"    mean_raw_edge: float | None\r\n",
                new=b"",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_summary_envelope_and_elements",),
                note=(
                    "**「更松」的另一个方向**：信封是 `extra='allow'`，删掉声明字段后该键"
                    "**原样透传** ⇒ 线上字节不变、`tsc` 照样绿，唯一的拦截点是键集相等断言"
                    "（`set(fields) == 九键`）。与 J66 合起来钉住键集的两个失败方向："
                    "J66 放宽可选性、J67 收缩键集。🔴 整行删除；该行在本文件命中 **1 次**"
                    "（已字节复核），守卫文件里没有带前导四空格的该字面量 ⇒ 不自指。"
                ),
            ),
            Mutation(
                label="J68 CalibrationSegment.qualified 从 bool 放宽成 int | None：改线上字节",
                path=_EVENT_MODEL,
                old=b"    segment_min_samples: int\r\n    qualified: bool",
                new=b"    segment_min_samples: int\r\n    qualified: int | None",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_summary_envelope_and_elements",),
                note=(
                    "**「改字节」方向**：`qualified` 是 Python 比较"
                    "`(stats['n'] or 0) >= min_samples` 的产物，恒 `bool`；"
                    "声明成 `int | None` 后 pydantic 重序列化把 `True` 写成 `1`、"
                    "`False` 写成 `0` —— 与 §53.58 的 J46 同方向、与 §53.57 的 J37 互为反向。"
                    "🔴 针必须带前导行 `segment_min_samples: int`：裸 "
                    "`    qualified: bool` 作为**前缀**在 `qualified: bool | None = None`"
                    "（本文件 530 行）也命中；带上前导行才是唯一单元（已字节复核）。"
                    "与 J69 合起来钉住两个元素模型的可空性判据。"
                ),
            ),
            Mutation(
                label="J69 CalibrationCategory.brier_score 从裸 float 放宽成 float | None：把域外当可能",
                path=_EVENT_MODEL,
                old=b"    brier_score: float\r\n    skill_score: float",
                new=b"    brier_score: float | None\r\n    skill_score: float",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_summary_envelope_and_elements",),
                note=(
                    "**「更松」方向**：生产者**交不出 None** —— `by_category` 的"
                    "字典字面量里 `round(r['mean_brier'], 4)` 先于整个 dict 求值，"
                    "`mean_brier` 为 None 时先 500（域外，探针 CONTROL 已钉）⇒ "
                    "裸 `float` 才是忠实形状（口径：「裸类型 ⟺ 生产者交不出 null」，"
                    "§53.58）。🔴 针必须带后随行 `skill_score:`："
                    "`Calibration`（本文件 142 行）也有 `brier_score: float`，但带行内注释"
                    "⇒ 字节不命中；裸行对 `CalibrationCategory` 是唯一单元（已复核）。"
                ),
            ),
            Mutation(
                label="J70 CalibrationBucketCell 从 _frontend_export 白名单挪走：退回嵌套尾巴",
                path=_FRONTEND_EXPORT,
                old=b"    CalibrationBucketCell,\r\n",
                new=b"",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_every_reachable_plain_model_is_registered",),
                note=(
                    "**登记面**：普通 `BaseModel` 只有登记为根才拿 "
                    "`additionalProperties: false`；从白名单挪走后它退回**嵌套**渲染，"
                    "`json2ts` 补 `[k: string]: unknown` 尾巴 ⇒ 比后端契约松，而 "
                    "`tsc` 照样绿。守卫是**生产者驱动**的可达性测试"
                    "（沿字段注解递归展开），所以在**重新生成之前**就红。"
                    "只打 cell 一个成员：三个元素模型共用同一条规则、同一守卫，"
                    "逐成员变异按 §4.2 取代表（J66/J68/J69 已各自覆盖另外两个模型"
                    "的类型面）。🔴 整行删除；`    CalibrationBucketCell,`（import 形式）"
                    "在本文件命中 **1 次**，`__all__` 里的 `\"CalibrationBucketCell\",` "
                    "字节不同、不受影响（已复核）。"
                ),
            ),
            Mutation(
                label="J71 api.ts 校准调用点 generic 换成 Record<string, unknown>：契约旁路",
                path=_API_TS,
                old=b'api<PredictionCalibration>("/events/predictions/calibration")',
                new=b'api<Record<string, unknown>>("/events/predictions/calibration")',
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_calibration_callsites_read_the_generated_contract",),
                note=(
                    "**消费面**：`api<T>` = `res.json() as T`，cast 对形状什么都不主张 ⇒ "
                    "generic 旁路时 `tsc`/`vitest` **双双看不见**，唯一的拦截点是静态文本守卫"
                    "（§53.57 判据：**每个端点各自的 generic**；summary 与 buckets 各有"
                    "自己的别名与断言，一个共用 generic 对一端过松、对另一端虚假）。"
                    "🔴 针含 URL、在本文件命中 **1 次**（已字节复核）；"
                    "守卫文件里的别名断言把字符串拆成两段拼接、与针字节不同 ⇒ 不自指。"
                ),
            ),
            Mutation(
                label="J73 /loop/status 丢失 response_model_exclude_unset：subset 路径长出 3 个 null 键",
                path=_DIGEST_ROUTES,
                old=b"response_model=LoopStatusResponse,\r\n            response_model_exclude_unset=True)",
                new=b"response_model=LoopStatusResponse,\r\n            )",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_loop_status_route_binds_the_model_and_keeps_the_flag",),
                note=(
                    "**「必须有旗子」方向的首次登记**（§53.55 立的判据是"
                    "「具名前先读路由有没有旗子」，J73 钉的是反方向：有了具名"
                    "**就必须有**旗子）。`LoopRunView` 的 `id`/`error`/`result` "
                    "带默认值，flag 不在 ⇒ subset 路径（无 write key 的每次轮询）"
                    "把 3 个生产者从未写过的键实体化成 null —— **改线上字节**，"
                    "而 `tsc` 看不见。守卫读 `route.response_model_exclude_unset "
                    "is True`（APIRoute 对象属性）。🔴 针是**两行**（单行 "
                    "`response_model_exclude_unset=True)` 与 movers 路由相撞），"
                    "必须带前导行才唯一；跨行针带 CRLF（已字节复核 **1 次**）。"
                ),
            ),
            Mutation(
                label="J72 /loop/status 的 response_model 退回裸 FlexibleResponse：契约重新不可见",
                path=_DIGEST_ROUTES,
                old=b"response_model=LoopStatusResponse,",
                new=b"response_model=FlexibleResponse,",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_loop_status_route_binds_the_model_and_keeps_the_flag",),
                note=(
                    "§53.62：本轮具名的端点。与 J64 同判据（**路由对象身份**，"
                    "`route.response_model is LoopStatusResponse`），但**多轴**："
                    "同一守卫还钉 exclude_unset 旗子（J73 的方向），"
                    "⇒ J72 变异声称「只踩绑定」时，`new` 已逐旗子判据核过："
                    "裸 `FlexibleResponse` + 旗子仍 200、`tsc` 绿，只有 schema 变不透明体。"
                    "🔴 针 `response_model=LoopStatusResponse,` 在本文件命中 **1 次**"
                    "（已字节复核；带 `response_model=` 前缀避开 import 区同名），"
                    "守卫文件里没有该字面量 ⇒ 不自指。"
                ),
            ),
            Mutation(
                label="J74 LoopRunView.started_at 添默认值：双路径恒写的键被放宽成可选",
                path=_EVENT_MODEL,
                old=b"    started_at: str\r\n    finished_at: str | None",
                new=b'    started_at: str = ""\r\n    finished_at: str | None',
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_run_view_carries_both_shapes",),
                note=(
                    "**「更松」方向**：五键子集在**两条路径**都写（每行都带全部列），"
                    "默认值只会把生成类型放宽成 `?:`。守卫用 `subTest("
                    "subset_required=...)` 逐键拦。🔴 针必须带后随行 "
                    "`finished_at:`：单行 `    started_at: str` 在别处（如 "
                    "`RecentPredictionItem` 附近）可能重名，带上文才唯一"
                    "（已字节复核 **1 次**）。"
                ),
            ),
            Mutation(
                label="J75 LoopRunView 行序打乱（id 与 job_name 互换）：details 路径键序变化",
                path=_EVENT_MODEL,
                old=b"    id: str | None = None\r\n    job_name: str",
                new=b"    job_name: str\r\n    id: str | None = None",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_run_view_carries_both_shapes",),
                note=(
                    "**行序即契约**的守卫判据（§53.60 的键序教训产品化）："
                    "`LoopRunView` 按 loop_runs **行序**声明 ⇒ details 路径键序"
                    "字节稳定；打乱后 details 响应的键序变、canonical 不变 —— "
                    "与 J63 同类的「纯键序」线上差。守卫断言 `list(fields) == "
                    "行序`。🔴 针是**相邻两行整体互换**（新字节仍合法 Python）"
                    "（已字节复核 **1 次**）。"
                ),
            ),
            Mutation(
                label="J76 counts 删 pending_reviews 行：键集收缩、review-queue 深度重新不可见",
                path=_EVENT_MODEL,
                old=b"    pending_reviews: int\r\n",
                new=b"",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_envelope_and_nested_blocks",),
                note=(
                    "**「更松」方向**（与 J67 同型）：信封 `extra='allow'`，删声明"
                    "字段后该键**原样透传** ⇒ 字节不变、tsc 绿，唯一拦截是键集列表"
                    "断言。**手写副本当年就是这么漏掉它的** —— 副本缺 "
                    "`pending_reviews`/`breached_reviews`/`review_queue` 整块，"
                    "正是本节要防止的回归。🔴 整行删除（已字节复核 **1 次**）。"
                ),
            ),
            Mutation(
                label="J77 桶 oldest_age_hours 从 int | float | None 收成 float | None：int 3 变 3.0",
                path=_EVENT_MODEL,
                old=b"    oldest_age_hours: int | float | None\r\n    breached: int",
                new=b"    oldest_age_hours: float | None\r\n    breached: int",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_envelope_and_nested_blocks",),
                note=(
                    "**「改字节」方向**：census 的 age 可能是 int，裸 `float` 收 "
                    "int 时 pydantic 重序列化 `3` → `3.0` —— **JSON 字节变化**，"
                    "探针 `syn/queue_int_age` 用例专测（改前 bytes=True）。"
                    "🔴 针必须带后随行 `breached:`：Summary 块的同名字段后随行是 "
                    "`oldest_item_id`，带后随行才钉住**桶**而非块（已字节复核 "
                    "**1 次**）。"
                ),
            ),
            Mutation(
                label="J78 信封 calibration 添默认值：必填放宽成可选",
                path=_EVENT_MODEL,
                old=b"    counts: LoopStatusCounts\r\n    calibration: PredictionCalibrationResponse",
                new=b"    counts: LoopStatusCounts\r\n    calibration: PredictionCalibrationResponse | None = None",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_envelope_and_nested_blocks",),
                note=(
                    "**「更松」方向**：生产者恒写 `calibration`（九键全写），"
                    "默认值只会把信封放宽成可缺 —— 且 **`| None` 还会放宽注解**，"
                    "两个判据（`is_required` + 注解相等）各拦一半。🔴 针带前导行 "
                    "`counts:` 保证唯一（已字节复核 **1 次**）。"
                ),
            ),
            Mutation(
                label="J79 api.ts loop/status 调用点 generic 换成 Record<string, unknown>：契约旁路",
                path=_API_TS,
                old=b'api<LoopStatus>("/events/loop/status")',
                new=b'api<Record<string, unknown>>("/events/loop/status")',
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_loop_status_callsite_reads_the_generated_contract",),
                note=(
                    "**消费面**（与 J71 同判据）：`api<T>` cast 对形状不主张 ⇒ "
                    "旁路时 tsc/vitest 双盲，静态文本守卫是唯一拦截点。"
                    "🔴 针含 URL、在本文件命中 **1 次**（已字节复核）；"
                    "守卫文件里没有该字面量 ⇒ 不自指。"
                ),
            ),
            Mutation(
                label="J80 /calibration 的 response_model 退回裸 FlexibleResponse：契约重新不可见",
                path=_DIGEST_ROUTES,
                old=b"response_model=EventCalibrationResponse)",
                new=b"response_model=FlexibleResponse)",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_calibration_route_binds_the_model_and_declares_no_flag",),
                note=(
                    "§53.63：本轮具名的端点。与 J64/J72 同判据（**路由对象身份**，"
                    "`route.response_model is EventCalibrationResponse`）。裸 "
                    "`FlexibleResponse` 仍 200、`tsc` 绿，只有 schema 变不透明体。"
                    "🔴 针带右括号（装饰器是单行 `@router.get(\"/calibration\", "
                    "response_model=EventCalibrationResponse)`），在本文件命中 "
                    "**1 次**（已字节复核；带 `response_model=` 前缀避开 import 区"
                    "同名），守卫文件里没有该字面量 ⇒ 不自指。"
                ),
            ),
            Mutation(
                label="J81 信封 overall 添默认值：必填放宽成可选",
                path=_EVENT_MODEL,
                old=b"    overall: CalibrationAggregate\r\n    by_source:",
                new=b"    overall: CalibrationAggregate | None = None\r\n    by_source:",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_envelope_and_nested_blocks",),
                note=(
                    "**「更松」方向**：`summarize` 两条返回路径（live + no_data）"
                    "都恒写 `overall`，默认值只会把信封放宽成可缺 —— 且 **`| None` "
                    "还会放宽注解**，两个判据（`is_required` + 注解相等）各拦一半。"
                    "🔴 针带后随行 `by_source:` 保证唯一（已字节复核 **1 次**）。"
                ),
            ),
            Mutation(
                label="J82 by_source 元素退化成 dict[str, Any]：元素契约重新不可见",
                path=_EVENT_MODEL,
                old=b"    by_source: dict[str, CalibrationAggregate]\r\n",
                new=b"    by_source: dict[str, Any]\r\n",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_envelope_and_nested_blocks",),
                note=(
                    "**「更松」方向**（与 J58 元素退化同型）：裸 `dict[str, Any]` "
                    "渲染成 `{ [k: string]: unknown }` 值 ⇒ 前端读不动，且传输面"
                    "**零字节差**（validate 通过、原样透传），`tsc` 盲。守卫的注解"
                    "相等断言是唯一拦截点。🔴 针整行（已字节复核 **1 次**）。"
                ),
            ),
            Mutation(
                label="J83 CalibrationAggregate.brier_score 从 float | None 收成 float：no_data 路径 500",
                path=_EVENT_MODEL,
                old=b"    brier_score: float | None\r\n    skill_score: float | None",
                new=b"    brier_score: float\r\n    skill_score: float | None",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_envelope_and_nested_blocks",),
                note=(
                    "**「改字节/失败」方向**：no_data 路径写 `brier_score=None`，"
                    "收窄后 pydantic 响应校验 **500**（探针 CONTROL 同族失败模式）。"
                    "可空性是本节的**决定**，不是冗余。🔴 单行 `brier_score:` 在 "
                    "event.py 命中 **5 处**（三个校准族模型 + EventRecord），必须带"
                    "后随行 `skill_score:` 才钉住 `CalibrationAggregate`（已字节"
                    "复核 **1 次**）。"
                ),
            ),
            Mutation(
                label="J84 CategoryCoverage.unknown_rate 从 float | None 收成 float：total==0 路径 500",
                path=_EVENT_MODEL,
                old=b"    unknown_rate: float | None\r\n",
                new=b"    unknown_rate: float\r\n",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_envelope_and_nested_blocks",),
                note=(
                    "**「改字节/失败」方向**：`_category_coverage` 在 `total==0` 时"
                    "写 `unknown_rate=None`（**写键、值为 null**，不是缺键），收窄后"
                    "响应校验 **500**。🔴 单行针在本文件命中 **1 次**（已字节复核；"
                    "另一处 unknown_rate 是 docstring 文字），守卫文件里没有该字面量。"
                ),
            ),
            Mutation(
                label="J85 CategoryCoverage 删 classified 行：嵌套闭合模型把键从报文里删掉",
                path=_EVENT_MODEL,
                old=b"    total: int\r\n    classified: int",
                new=b"    total: int",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_envelope_and_nested_blocks",),
                note=(
                    "**「改字节」方向**（与 J76 的区别要写清）：`CategoryCoverage` "
                    "是**纯嵌套 BaseModel**（`extra='ignore'`，非 FlexibleResponse）"
                    "⇒ 删声明字段后生产者写的键被 pydantic **从报文里删掉**（不是 J76 "
                    "信封 `extra='allow'` 的原样透传）—— 无异常、tsc 盲（`res.json() "
                    "as T`），唯一拦截是键集列表断言。"
                    "🔴 **初稿锚 `unknown_rate` 行与本集 J84（同一行的收窄变异）互毁**"
                    "—— 任意落位顺序下后者的锚都被前者改写，整集 apply 必挂（"
                    "§53.62 J72/J73 是「可换序」救回；这里是同字节互斥，只能换宿主）"
                    "⇒ 换 `classified` 行，design 点不变：删 `CategoryCoverage` 的"
                    "任一声明字段效果相同。🔴 针带前导行 `total: int`（已字节复核 "
                    "**1 次**）。"
                ),
            ),
            Mutation(
                label="J86 CalibrationAggregate 的 grade 与 n 互换：元素键序线上差",
                path=_EVENT_MODEL,
                old=b"    grade: str\r\n    n: int",
                new=b"    n: int\r\n    grade: str",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_envelope_and_nested_blocks",),
                note=(
                    "**行序即契约**（J75 同判据）：模型字段序照抄 `_aggregate` 的 "
                    "dict 序 ⇒ 打乱后响应键序变、canonical 不变 —— 与 J63/J75 同类"
                    "「纯键序」线上差。守卫断言 `list(agg) == [...]` 按序。🔴 单行 "
                    "`grade: str` 在 event.py 命中 **5 处**，带后随行 `n: int` 才"
                    "唯一（已字节复核 **1 次**）。"
                ),
            ),
            Mutation(
                label="J87 api.ts /events/calibration 调用点 generic 换成 Record<string, unknown>：契约旁路",
                path=_API_TS,
                old=b'api<EventCalibrationResponse>("/events/calibration")',
                new=b'api<Record<string, unknown>>("/events/calibration")',
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_calibration_callsite_reads_the_generated_contract",),
                note=(
                    "**消费面**（与 J71/J79 同判据）：`api<T>` cast 对形状不主张 ⇒ "
                    "旁路时 tsc/vitest 双盲，静态文本守卫是唯一拦截点。🔴 针含 URL、"
                    "在本文件命中 **1 次**（已字节复核）；守卫文件里没有该字面量 ⇒ "
                    "不自指。"
                ),
            ),
            Mutation(
                label="J89 /api/health 丢失 response_model_exclude_unset：无 key 子集路径长出 3 个 null 键",
                path=_MAIN_APP,
                old=b"response_model=ApiHealthResponse,\r\n         response_model_exclude_unset=True)",
                new=b"response_model=ApiHealthResponse,\r\n         )",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_health_route_binds_the_model_and_keeps_the_flag",),
                note=(
                    "§53.64：与 J73 同判据但**更尖锐**——container healthcheck "
                    "**无 write key 轮询** ⇒ 五键子集是**主导路径**，旗子退场后 "
                    "`LoopRunView` 的 3 个 detail-only 默认值（id/error/result）"
                    "在几乎每次响应上实体化成 null（改线上字节）。503 降级状态由 "
                    "Response 参数设置、不受 response_model 影响（探针 "
                    "`syn/degraded_503` 钉住 503 存活）。🔴 针是两行（单行 "
                    "`response_model_exclude_unset=True)` 与 /loop/status 的 "
                    "装饰器相撞），必须带前导行才唯一；**与 J88 锚重叠但可换序**"
                    "（J89 的 new 保留第一行 ⇒ J88 的锚仍在），故本块在 J88 之前"
                    "落位（§53.62 J72/J73 同款）。已字节复核各命中 **1 次**。"
                ),
            ),
            Mutation(
                label="J88 /api/health 的 response_model 退回裸 FlexibleResponse：契约重新不可见",
                path=_MAIN_APP,
                old=b"response_model=ApiHealthResponse,",
                new=b"response_model=FlexibleResponse,",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_health_route_binds_the_model_and_keeps_the_flag",),
                note=(
                    "§53.64：本轮具名的端点（在**根 app** 上，非 events router；"
                    "守卫读 `app.main.app.routes`）。与 J64/J72/J80 同判据（路由"
                    "对象身份）。裸 `FlexibleResponse` 仍 200/503、`tsc` 绿，只有 "
                    "schema 变不透明体。🔴 针带 `response_model=` 前缀避开 import "
                    "区同名，在本文件命中 **1 次**（已字节复核）；与 J89 可换序"
                    "（J89 先落位后本锚仍在）。"
                ),
            ),
            Mutation(
                label="J90 ApiHealthResponse.scheduler_running 从 bool | None 收成 bool：None 窗口 500",
                path=_EVENT_MODEL,
                old=b"    scheduler_running: bool | None\r\n",
                new=b"    scheduler_running: bool\r\n",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_envelope_and_its_loop_reuse",),
                note=(
                    "**「改字节/失败」方向**：`scheduler.running` 在调度线程首次"
                    "应答前是 None（§53.62 钉在 `LoopSchedulerState.running` 的"
                    "同一个值），收窄后该窗口 500。探针 CONTROL "
                    "`scheduler_running_int` 钉的是同字段的**改写型**失败"
                    "（int 1 → true）——可空性与类型严格性都是决定。🔴 单行针在本"
                    "文件命中 **1 次**（已字节复核；`LoopSchedulerState` 的是 "
                    "`running:` 不相撞）。"
                ),
            ),
            Mutation(
                label="J91 ApiHealthResponse.loop 退化成 dict[str, Any]：复用模型退回未知体",
                path=_EVENT_MODEL,
                old=b"    loop: LoopStatusResponse\r\n",
                new=b"    loop: dict[str, Any]\r\n",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_envelope_and_its_loop_reuse",),
                note=(
                    "**「更松」方向**：`loop` 就是 `loop_status()` 的返回值——"
                    "与 `/loop/status` 同生产者，J72 刚把那边具名；这里退回裸 "
                    "dict ⇒ 生成类型变 `{ [k: string]: unknown }`，传输面**零字节"
                    "差**（透传），`tsc` 盲。守卫的 `assertIs(fields[\"loop\"]."
                    "annotation, LoopStatusResponse)` 是唯一拦截点——**第二份"
                    "loop 信封副本正是本家族要防的漂移**。🔴 整行针（已字节复核 "
                    "**1 次**）。"
                ),
            ),
            Mutation(
                label="J93 ApiHealthResponse.status 添默认值：恒写键被放宽成可选",
                path=_EVENT_MODEL,
                old=b"    status: str\r\n    version: str",
                new=b'    status: str = "ok"\r\n    version: str',
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_envelope_and_its_loop_reuse",),
                note=(
                    "**「更松」方向**：七键由 handler 一个 dict 字面量恒写，默认值"
                    "只会放宽成可缺。🔴 单行 `    status: str` 在 event.py 命中 "
                    "**3 处**（EventRecord/LoopRunView 等），必须带后随行 "
                    "`version:` 才钉住本信封（已字节复核 **1 次**）。**与 J92 "
                    "共享 version 行但都不改写它**（J93 的 new 逐字节保留 version "
                    "行、J92 删掉的是 version 行本身）⇒ 锚字节重叠、J93 的改动"
                    "不碰 J92 的锚 ⇒ 可换序类，但 J92 删行会毁 J93 的两行锚 ⇒ "
                    "本块在 J92 之前落位。"
                ),
            ),
            Mutation(
                label="J92 ApiHealthResponse 删 version 行：extra=allow 信封原样透传、tsc 盲",
                path=_EVENT_MODEL,
                old=b"    version: str\r\n    scheduler_running:",
                new=b"    scheduler_running:",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_envelope_and_its_loop_reuse",),
                note=(
                    "**「更松」方向**（J76 同型）：信封承 `FlexibleResponse`"
                    "（`extra='allow'`）⇒ 删声明字段后该键**原样透传** ⇒ 字节"
                    "不变、tsc 绿，唯一拦截是键集列表断言。手写 `ApiHealth` 当年"
                    "就是这么漏掉 `scheduler_enabled`/`scheduler_lock_skipped` "
                    "的——副本缺键正是本节要防止的回归。🔴 针带后随行 "
                    "`scheduler_running:`（已字节复核 **1 次**）；落位在 J93 之后"
                    "（J93 的锚含 version 行）。"
                ),
            ),
            Mutation(
                label="J94 scheduler_enabled 与 scheduler_lock_skipped 互换：信封键序线上差",
                path=_EVENT_MODEL,
                old=b"    scheduler_enabled: bool\r\n    scheduler_lock_skipped: bool",
                new=b"    scheduler_lock_skipped: bool\r\n    scheduler_enabled: bool",
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_envelope_and_its_loop_reuse",),
                note=(
                    "**行序即契约**（J75/J86 同判据）：模型字段序照抄 handler 的 "
                    "dict 字面量序 ⇒ 打乱后响应键序变、canonical 不变——「纯键序」"
                    "线上差。守卫断言 `list(fields) == [...]` 按序。🔴 针是相邻两行"
                    "整体互换（新字节仍合法 Python），与 J92/J93 的锚不相交"
                    "（已字节复核 **1 次**）。"
                ),
            ),
            Mutation(
                label="J95 api.ts /health 调用点 generic 换成 Record<string, unknown>：契约旁路",
                path=_API_TS,
                old=b'api<ApiHealth>("/health"',
                new=b'api<Record<string, unknown>>("/health"',
                guard_file=_T_GENERATE_TYPES,
                guards=("test_the_health_callsite_reads_the_generated_contract",),
                note=(
                    "**消费面**（与 J71/J79/J87 同判据）：`api<T>` cast 对形状不"
                    "主张 ⇒ 旁路时 tsc/vitest 双盲。守卫还钉同一行的 "
                    "`acceptStatuses: [503]`（降级态不是传输错误——删它会把 "
                    "503 变成前端连接故障）。🔴 针含 URL、在本文件命中 **1 次**"
                    "（已字节复核）；与 D6 新宿主（CalibrationDeviationRow）及 "
                    "J87 的窗口不相交；守卫文件里没有该字面量 ⇒ 不自指。"
                ),
            ),
        ),
    ),
)

SETS_BY_KEY = {mutation_set.key: mutation_set for mutation_set in SETS}


# --- engine ---------------------------------------------------------------


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _backup_dir(key: str) -> pathlib.Path:
    return BACKUP_ROOT / key


def _backup_name(key: str, rel: str) -> pathlib.Path:
    return _backup_dir(key) / rel.replace("/", "__")


def _paths(mutation_set: MutationSet) -> list[str]:
    return list(dict.fromkeys(m.path for m in mutation_set.mutations))


@dataclasses.dataclass(frozen=True)
class FileGuardRun:
    """One guard file's pytest run: whether it passed, and how it ended."""

    guard_file: str
    passed: bool
    tail: str


@dataclasses.dataclass(frozen=True)
class GuardRun:
    """Every guard file of one mutation, each run on its own.

    One pytest invocation per file rather than one combined invocation, because
    the two questions the phases ask are not each other's negation once a
    mutation names more than one file:

    * phases 1 and 3 need *every* file green, and
    * phase 2 needs *every* file red.

    A single combined run answers only ``returncode == 0``. That is "all green"
    for phase 1, but for phase 2 it is merely "at least one red" -- and for a
    mutation anchored to a shared definition (audit section 42.4) that is the
    wrong bar: one file firing turns the code non-zero, so a guard that had
    quietly become vacuous in some *other* file would hide behind its
    neighbours. Running each file separately makes the per-file fact observable,
    and ``every_file_red`` is the strict reading of it (audit section 45.5).
    """

    per_file: tuple[FileGuardRun, ...]

    @property
    def all_green(self) -> bool:
        """True only if every named file's guards passed."""
        return all(run.passed for run in self.per_file)

    @property
    def red_files(self) -> tuple[FileGuardRun, ...]:
        """The files whose run failed, in ``guard_files()`` order."""
        return tuple(run for run in self.per_file if not run.passed)

    @property
    def green_files(self) -> tuple[FileGuardRun, ...]:
        """The files whose run passed, in ``guard_files()`` order."""
        return tuple(run for run in self.per_file if run.passed)

    @property
    def every_file_red(self) -> bool:
        """True only if no named file passed -- and there was at least one.

        The leading ``bool(self.per_file)`` is not decoration: with no files,
        "no file passed" is vacuously true, so a mutation that named no guard
        file would verify as OK while running nothing at all. The inventory test
        ``test_every_guard_file_exists_and_every_guard_is_named`` keeps that case
        unreachable for real mutations; this stays explicit so the reading is
        correct even for a synthetic one.
        """
        return bool(self.per_file) and not self.green_files

    def tally(self) -> str:
        """``<red>/<total> files red`` -- the receipt's per-file count."""
        return f"{len(self.red_files)}/{len(self.per_file)} files red"

    def red_summary(self) -> str:
        """The last output line of every file whose run failed."""
        return "; ".join(f"{run.guard_file} -> {run.tail}" for run in self.red_files)

    def green_summary(self) -> str:
        """The last output line of every file whose run passed."""
        return "; ".join(f"{run.guard_file} -> {run.tail}" for run in self.green_files)


def _run_guards(mutation: Mutation) -> GuardRun:
    """Run a mutation's guards, one pytest invocation per guard file.

    ``-k`` filters within each file, so the same selector is applied to every
    file and a guard name that lives in only one of them simply matches nothing
    in the others. Returning per-file outcomes -- rather than one return code --
    is what lets phase 2 require that *each* file noticed the mutation; see
    ``GuardRun.every_file_red``. H1 (set ``multi-guard-file``) pins the loop
    below, because a version that stopped at the first file would still report a
    plausible outcome for the file it did run.
    """
    if not PY.is_file():
        raise SystemExit(f"interpreter not found: {PY}")
    runs: list[FileGuardRun] = []
    for guard_file in mutation.guard_files():
        runs.append(_run_one_guard_file(mutation, guard_file))
    return GuardRun(tuple(runs))


def _run_one_guard_file(mutation: Mutation, guard_file: str) -> FileGuardRun:
    """One pytest run, scoped to a single guard file and the named guards."""
    proc = subprocess.run(
        [
            str(PY),
            "-m",
            "pytest",
            guard_file,
            "-q",
            "-p",
            "no:cacheprovider",
            "-k",
            " or ".join(mutation.guards),
        ],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=GUARD_TIMEOUT,
    )
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return FileGuardRun(
        guard_file=guard_file,
        passed=proc.returncode == 0,
        tail=out.splitlines()[-1] if out else "",
    )


def cmd_list(args: argparse.Namespace) -> int:
    del args  # the subcommand takes no options
    for mutation_set in SETS:
        print(f"{mutation_set.key}  —  {mutation_set.title}")
        print(f"    {len(mutation_set.mutations)} 个变异")
        if mutation_set.rationale:
            print(f"    {mutation_set.rationale}")
        for index, mutation in enumerate(mutation_set.mutations, 1):
            grouped = f"  [group: {mutation.group}]" if mutation.group else ""
            print(f"      {index:>2}. {mutation.label}{grouped}")
        print()
    total = sum(len(s.mutations) for s in SETS)
    print(f"共 {len(SETS)} 套 / {total} 个变异。用 `verify [key]` 自动跑四阶段校验。")
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    mutation_set = SETS_BY_KEY[args.key]
    directory = _backup_dir(mutation_set.key)
    directory.mkdir(parents=True, exist_ok=True)
    for rel in _paths(mutation_set):
        src = REPO_ROOT / rel
        shutil.copyfile(src, _backup_name(mutation_set.key, rel))
        print(f"backed up {rel}  sha256={_sha(src.read_bytes())[:16]}")
    return 0


def _apply_preflight(targets: list[tuple[MutationSet, int, Mutation]]) -> list[str]:
    """Everything that must hold before an ``apply`` may write a single byte.

    Returns the problems rather than raising on the first one, for two reasons: a
    multi-position apply should report every bad needle at once, and a partial
    apply (first file rewritten, second needle missing) leaves a tree that is
    neither the original nor the intended state. So nothing is written until the
    whole selection has been checked.

    The group check is *reject*, not *skip*: skipping is right for a whole-set
    apply (a convenience for "make it red everywhere") but wrong when the
    positions were typed out. Naming both members of a group is an explicit
    request to rewrite the same bytes twice, which can only be a mistake.
    """
    problems: list[str] = []
    groups: dict[str, list[int]] = {}
    for _, position, mutation in targets:
        if mutation.group:
            groups.setdefault(mutation.group, []).append(position)
        path = REPO_ROOT / mutation.path
        if not path.is_file():
            problems.append(f"{position}: missing file {mutation.path}")
            continue
        found = count_needle(path.read_bytes(), mutation.old)
        if found != 1:
            problems.append(
                f"{position}: needle appears {found}x in {mutation.path} "
                f"(expected 1). The code it points at has moved or changed."
            )
    for group, positions in groups.items():
        if len(positions) > 1:
            problems.append(
                f"positions {', '.join(str(p) for p in positions)} share group "
                f"{group!r}: they rewrite the same bytes in opposite directions, "
                "so applying both would leave neither mutation in place"
            )
    return problems


def _write_mutations(targets: list[tuple[MutationSet, int, Mutation]]) -> None:
    for _, position, mutation in targets:
        path = REPO_ROOT / mutation.path
        path.write_bytes(
            rewrite_needle(path.read_bytes(), mutation.old, mutation.new)
        )
        print(f"mutated {position}: {mutation.label}  ({mutation.path})")


def cmd_apply(args: argparse.Namespace) -> int:
    mutation_set = SETS_BY_KEY[args.key]
    # Each token parses to a tuple (``5 8`` -> two one-tuples, ``27-30`` -> one
    # four-tuple); flatten so both spellings reach the same selector.
    positions = tuple(position for chunk in args.index for position in chunk)
    if not positions:
        return _apply_whole_set(mutation_set)

    # Same selector as ``verify``, so a position means the same mutation in both.
    targets = _select_verify_targets([mutation_set.key], positions)
    problems = _apply_preflight(targets)
    if problems:
        raise SystemExit(
            f"refusing to apply, {len(problems)} problem(s):\n  "
            + "\n  ".join(problems)
        )
    _write_mutations(targets)
    return 0


def _whole_set_targets(
    mutation_set: MutationSet,
) -> tuple[list[tuple[MutationSet, int, Mutation]], list[tuple[int, Mutation]]]:
    """Split a whole-set apply into (to apply, skipped for being grouped).

    A pure function so the skip rule -- keep the first member of a group, skip
    the rest -- is asserted by ``tests/test_mutation_verify.py`` rather than only
    being visible in a manual run's output.
    """
    targets: list[tuple[MutationSet, int, Mutation]] = []
    skipped: list[tuple[int, Mutation]] = []
    seen_groups: set[str] = set()
    for position, mutation in enumerate(mutation_set.mutations, 1):
        if mutation.group and mutation.group in seen_groups:
            skipped.append((position, mutation))
            continue
        if mutation.group:
            seen_groups.add(mutation.group)
        targets.append((mutation_set, position, mutation))
    return targets, skipped


def _apply_whole_set(mutation_set: MutationSet) -> int:
    """Apply the whole set, skipping the rest of a group and saying so.

    Skipping is only defensible here: the operator asked for "everything", and a
    group's members cannot all be in place at once. The skips are reported after
    the writes so the wording matches what happened -- the preflight means no byte
    is written until the whole selection has been checked, so a "skipped" line
    printed up front would describe a decision that had not been acted on yet.
    """
    targets, skipped = _whole_set_targets(mutation_set)
    problems = _apply_preflight(targets)
    if problems:
        raise SystemExit(
            f"refusing to apply, {len(problems)} problem(s):\n  "
            + "\n  ".join(problems)
        )
    _write_mutations(targets)
    for position, mutation in skipped:
        print(
            f"skipped {position}: {mutation.label}  "
            f"[与同组变异互斥，用 `apply {mutation_set.key} {position}` 单独跑]"
        )
    return 0


def cmd_revert(args: argparse.Namespace) -> int:
    mutation_set = SETS_BY_KEY[args.key]
    for rel in _paths(mutation_set):
        src = _backup_name(mutation_set.key, rel)
        if not src.is_file():
            raise SystemExit(f"no backup for {rel}; run `backup {mutation_set.key}` first")
        shutil.copyfile(src, REPO_ROOT / rel)
        print(f"restored {rel}  sha256={_sha((REPO_ROOT / rel).read_bytes())[:16]}")
    return 0


def _verify_one(mutation_set: MutationSet, index: int, mutation: Mutation) -> list[str]:
    problems: list[str] = []
    path = REPO_ROOT / mutation.path
    original = path.read_bytes()

    hits = count_needle(original, mutation.old)
    if hits != 1:
        return [f"needle appears {hits}x (expected 1)"]

    before = _run_guards(mutation)
    if not before.all_green:
        # Anything after this is unattributable: a guard was already red, so a
        # red after the mutation would prove nothing. Reported separately from a
        # needle problem because the two have different fixes. The tally names
        # which files were already red, so a selector that matches nothing in one
        # of several files does not read as "the whole guard was red".
        return [
            f"guard already RED before mutation in {before.tally()}"
            f" -> {before.red_summary()}"
        ]

    try:
        path.write_bytes(rewrite_needle(original, mutation.old, mutation.new))
        after = _run_guards(mutation)
    finally:
        path.write_bytes(original)

    restored_bytes = _sha(path.read_bytes()) == _sha(original)
    restored = _run_guards(mutation)

    if not after.every_file_red:
        # "Every file red", not "at least one": with several guard files the
        # weaker reading accepts a run where one file fired and the others were
        # never reached -- exactly the blind spot audit section 45.5 named. The
        # message names the files that stayed green so the difference is visible
        # in the receipt rather than only in the count.
        problems.append(
            f"stayed GREEN in {len(after.green_files)}/{len(after.per_file)} "
            f"file(s) -> {after.green_summary()}"
        )
    if not restored_bytes:
        problems.append("file bytes differ from before the mutation")
    if not restored.all_green:
        problems.append(f"still RED after restore -> {restored.red_summary()}")

    status = "OK " if not problems else "BAD"
    print(f"[{status}] {mutation_set.key} {index:>2}. {mutation.label}")
    print(f"        green before {before.all_green} | "
          f"red after mutation {after.tally()} | "
          f"green after restore {restored.all_green} | bytes restored {restored_bytes}")
    if after.red_files:
        print(f"        mutated run -> {after.red_summary()}")
    for problem in problems:
        print(f"        !! {problem}")
    return problems


def _parse_index_list(value: str) -> tuple[int, ...]:
    """Parse ``--index 27,30`` / ``--index 27-30`` into 1-based positions.

    Comma-separated rather than ``nargs="+"``: the ``verify`` subcommand already
    takes ``keys`` as ``nargs="*"``, and ``verify whitelist-fixtures --index 27 30``
    would be read as two set keys. One token cannot be misread.

    A token of the form ``a-b`` expands to the inclusive range ``a..b``, so a run
    of neighbouring mutations can be re-taken without typing each position. An
    ascending range only: ``30-27`` is rejected rather than silently reversed,
    because the positions settle the order the run reports them in.

    Value-level errors (empty entry, non-integer, reversed range) are reported by
    argparse, so a typo fails at argument parsing rather than after a run has
    started. Ranges are *not* range-checked here -- ``0-2`` parses and is then
    rejected by ``_select_verify_targets``' bounds check, so every out-of-range
    position gets the same message wherever it came from.
    """
    parts = [part.strip() for part in value.split(",")]
    if any(not part for part in parts):
        raise argparse.ArgumentTypeError(
            f"empty entry in --index {value!r}; write e.g. --index 27,30"
        )
    parsed: list[int] = []
    for part in parts:
        if part.count("-") == 1:
            start_text, end_text = (piece.strip() for piece in part.split("-"))
            if start_text.isdigit() and end_text.isdigit():
                start, end = int(start_text), int(end_text)
                if start > end:
                    raise argparse.ArgumentTypeError(
                        f"--index range {part!r} is reversed; write {end}-{start}"
                    )
                parsed.extend(range(start, end + 1))
                continue
        try:
            parsed.append(int(part))
        except ValueError:
            raise argparse.ArgumentTypeError(
                f"--index expects integers or ranges, got {part!r}"
            ) from None
    return tuple(parsed)


def _select_verify_targets(
    keys: list[str], indexes: tuple[int, ...] | None
) -> list[tuple[MutationSet, int, Mutation]]:
    """Resolve the (set, 1-based position, mutation) triples a verify run covers.

    ``indexes`` narrows the run to specific mutations, run in the order given --
    the receipt at the end of the run should be readable against the command line
    as typed. It is only meaningful with exactly one set, so asking for more than
    one is an error rather than a silent cross-set lookup: ``verify a b --index 3``
    has no single answer.

    Duplicates are rejected instead of de-duplicated: silently running fewer
    mutations than were typed would make the closing count disagree with the
    command line, and that count is the run's receipt.

    Kept out of ``cmd_verify`` so the selection rules -- unknown key, index out of
    range, index without exactly one set -- are asserted statically by
    ``tests/test_mutation_verify.py`` instead of only being discovered when a
    twelve-minute run is already underway.

    ``apply`` reuses this too, which is how the two commands are kept from
    drifting apart on what "position 3" means: the rule is enforced by sharing
    the function, not by remembering to keep two implementations in step.
    """
    given = list(keys)
    keys = given or [s.key for s in SETS]
    unknown = [k for k in keys if k not in SETS_BY_KEY]
    if unknown:
        raise SystemExit(
            f"unknown set(s): {', '.join(unknown)}; known: {', '.join(SETS_BY_KEY)}"
        )
    if indexes is None:
        return [
            (SETS_BY_KEY[key], position, mutation)
            for key in keys
            for position, mutation in enumerate(SETS_BY_KEY[key].mutations, 1)
        ]
    if len(keys) != 1:
        # Distinguish "named one set too many" from "named none at all": the
        # defaulted-to-every-set count would otherwise read as if the operator had
        # typed five keys.
        if not given:
            raise SystemExit(
                "--index needs a set key, e.g. verify whitelist-fixtures --index 27,30"
            )
        raise SystemExit(
            f"--index needs exactly one set, got {len(keys)}: {', '.join(keys)}"
        )
    mutation_set = SETS_BY_KEY[keys[0]]
    size = len(mutation_set.mutations)
    duplicates = sorted({index for index in indexes if indexes.count(index) > 1})
    if duplicates:
        raise SystemExit(
            f"duplicate --index: {', '.join(str(d) for d in duplicates)}; "
            f"每个序号只会跑一次，写两遍多半是笔误"
        )
    out_of_range = [index for index in indexes if not 1 <= index <= size]
    if out_of_range:
        raise SystemExit(
            f"index out of range: {', '.join(str(i) for i in out_of_range)} "
            f"({keys[0]} 有 {size} 个)"
        )
    return [
        (mutation_set, index, mutation_set.mutations[index - 1]) for index in indexes
    ]


def cmd_verify(args: argparse.Namespace) -> int:
    targets = _select_verify_targets(args.keys, args.index)

    failures: list[str] = []
    checked = 0
    printed_key: str | None = None
    for mutation_set, index, mutation in targets:
        if mutation_set.key != printed_key:
            print(f"\n=== {mutation_set.key} — {mutation_set.title} ===")
            printed_key = mutation_set.key
        checked += 1
        problems = _verify_one(mutation_set, index, mutation)
        failures += [f"{mutation_set.key} {index}: {p}" for p in problems]

    print(f"\n=== {checked} 个变异校验完毕 ===")
    if failures:
        print(f"FAILURES ({len(failures)}):")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("全部通过：每个变异在其修复被回退时都会让对应守卫变红，且还原后逐字节一致。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Byte-level mutation harness for the audit guards.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="show every set and mutation").set_defaults(
        func=cmd_list
    )

    p_verify = sub.add_parser(
        "verify", help="run each mutation through the four-phase cycle"
    )
    p_verify.add_argument("keys", nargs="*", help="set keys (default: all)")
    p_verify.add_argument(
        "-i",
        "--index",
        type=_parse_index_list,
        default=None,
        metavar="N[,N...]",
        help=(
            "verify only these mutations (1-based, comma-separated, e.g. -i 27,30); "
            "needs exactly one set key"
        ),
    )
    p_verify.set_defaults(func=cmd_verify)

    for name, func, help_text in (
        ("backup", cmd_backup, "snapshot a set's target files"),
        ("revert", cmd_revert, "restore a set's target files from the snapshot"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("key", choices=sorted(SETS_BY_KEY))
        p.set_defaults(func=func)

    p_apply = sub.add_parser(
        "apply", help="mutate a set (all, one position, or several)"
    )
    p_apply.add_argument("key", choices=sorted(SETS_BY_KEY))
    p_apply.add_argument(
        "index",
        nargs="*",
        type=_parse_index_list,
        metavar="N[,N...]",
        help=(
            "1-based positions, comma-separated or as ranges (e.g. 5 8 or 27-30); "
            "omit to apply the whole set"
        ),
    )
    p_apply.set_defaults(func=cmd_apply)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
