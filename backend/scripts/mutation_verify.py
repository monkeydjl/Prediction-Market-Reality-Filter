"""Byte-level mutation harness for every guard this audit added.

A green test that stays green when its fix is removed locks nothing. This script
reverts each tracked change one at a time and asserts the matching guard test
turns RED -- then puts it back and asserts the bytes are identical.

It replaces three near-identical per-batch scripts
(``mutation_verify_daily_digest.py``, ``mutation_verify_review_queue_flag.py``,
``mutation_verify_probability_probe.py``). Those all re-implemented the same
backup/apply/revert machinery, and one of them had grown a stronger check than
the other two while the other two had a per-index selector the first lacked.
Merging keeps the union of both: one inventory, one engine, five sets.

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
    2. they FAIL with the mutation applied           (the guard is load-bearing)
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

Needles are declared as raw ``bytes`` for the same reason, and **do not share one
line-ending convention**: the ``review-queue`` needles are line-terminated and
therefore carry CRLF, while the ``daily-digest``, ``probability-probe`` and
``voided-trade`` needles sit inside a single line and must not. Building a CRLF
needle from LF text would not match -- which fails loudly, because every apply
asserts the needle occurs exactly once. ``tests/test_mutation_verify.py`` pins
that rule statically (no bare LF in a needle) so a stale one fails in CI rather
than only during a ten-minute manual run.

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
PY = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
BACKUP_ROOT = pathlib.Path(tempfile.gettempdir()) / "pmrf-mutation-bak"
GUARD_TIMEOUT = 600

CRLF = b"\r\n"


@dataclasses.dataclass(frozen=True)
class Mutation:
    """One revertible change and the guard that must notice it."""

    label: str
    path: str
    old: bytes
    new: bytes
    guard_file: str
    guards: tuple[str, ...]
    group: str = ""
    note: str = ""


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
_ELO_ENGINE = "backend/app/kernel/engines/elo_odds_engine.py"
_SPORTS_FACT = "backend/app/services/sports_fact_service.py"
_LIMITLESS = "backend/app/services/limitless_event_source.py"
_GBM_ENGINE = "backend/app/kernel/engines/gbm_engine.py"
_CHALLENGE_ADAPTER = "backend/app/services/conclusion_challenge_world_cup_adapter.py"
_GUARDRAIL = "backend/app/services/guardrail_service.py"
_MARKET_QUALITY = "backend/app/services/market_quality_service.py"
_EXECUTION_QUALITY = "backend/app/services/execution_quality_service.py"
_SOURCE_RELIABILITY = "backend/app/services/source_reliability_service.py"
_PREDICTION_CALIBRATION = "backend/app/services/prediction_calibration_service.py"
_REVIEW_QUEUE_DETECTORS = "backend/app/services/review_queue_detectors.py"
_DOMAIN_RELIABILITY = "backend/app/services/domain_reliability_service.py"
_REPLAY_METRICS = "backend/app/replay/metrics.py"

_T_AVAILABILITY = "tests/test_football_live_availability_service.py"
_T_SCHEDULE = "tests/test_football_live_schedule_service.py"
_T_KALSHI = "tests/test_kalshi_event_source.py"
_T_STATISTICS = "tests/test_world_cup_statistics_source.py"
_T_CLUB_ELO = "tests/test_club_elo_service.py"
_T_MLB_ADAPTER = "tests/test_mlb_adapter.py"
_T_QUALITY = "tests/test_world_cup_quality_service.py"
_T_STAGE = "tests/test_knockout_stage_whitelist_consistency.py"
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
# constants. Each entry below pins one *constant definition*, so the mutation is
# independent of which call sites read it.
_QUALITY_METRICS = "backend/app/api/routes/quality_metrics.py"
_EVENT_INTELLIGENCE = "backend/app/services/event_intelligence_service.py"
_CONCLUSION_CHALLENGE = "backend/app/services/conclusion_challenge_service.py"
_DECISION_QUALITY = "backend/app/services/decision_quality_service.py"
_T_TRADES_STORE = "tests/test_simulated_trade_store.py"
_T_QUALITY_METRICS = "tests/test_quality_metrics.py"
_T_EVENT_INTELLIGENCE = "tests/test_event_intelligence_service.py"
_T_CONCLUSION_CHALLENGE = "tests/test_conclusion_challenge_service.py"
_T_DECISION_QUALITY = "tests/test_decision_quality_service.py"

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
        title="作废预测时冻结其模拟交易（V1–V4）",
        rationale=(
            "void_prediction 原本只把 predictions 置 voided，不碰 simulated_trades，"
            "于是每次非真实结算都留一笔永远 open 的模拟交易 —— 它既不进 closed 统计"
            "（没有结算值），也不会被 dangling 普查抓到（事件还在），只是永远挂在"
            "list_open_trades 里冒充持仓。V1 锁住那个调用点；V2/V3 锁住表重建的两个"
            "静默失效点（漏拷 id、CHECK 退回两态）；V4 锁住「终态必须是 voided 而不是"
            "继续 open」。"
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
        ),
    ),
    MutationSet(
        key="whitelist-fixtures",
        title="白名单 / 枚举常量的「循环 + 钉子」与关系型守卫（W1–W30）",
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
                guard_file=_T_QUALITY,
                guards=("test_engine_names_match_the_runnable_registry",),
                note="从质量报告的键集里删掉一个引擎（by_engine 少一个键，不报错）",
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
                label="W10 _KNOCKOUT_STAGES 增一个死成员 (round_of_32)",
                path=_PIPELINE,
                old=b'_KNOCKOUT_STAGES = {"round_of_16", "quarterfinal", "semifinal", "final"}',
                new=(
                    b'_KNOCKOUT_STAGES = {"round_of_16", "quarterfinal", '
                    b'"semifinal", "final", "round_of_32"}'
                ),
                guard_file=_T_STAGE,
                guards=("test_pipeline_knockout_names_are_producible_by_the_stage_map",),
                note="normalizer 永远产生不出来的名字 → is_knockout 恒 False",
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
                label="W17 guardrail_service._STRONG_DIRECTIONS 删掉 NO",
                path=_GUARDRAIL,
                old=b'_STRONG_DIRECTIONS = ("YES", "NO")',
                new=b'_STRONG_DIRECTIONS = ("YES",)',
                guard_file=_T_GUARDRAIL,
                guards=(
                    "test_fires_for_no_when_llm_degraded",
                    "test_two_rules_fire_preserves_existing_reason",
                ),
                note="§29：NO 不再被认作强方向 → 该方向跳过全部护栏",
            ),
            Mutation(
                label="W18 market_quality_service._STRONG_DIRECTIONS 删掉 NO",
                path=_MARKET_QUALITY,
                old=b'_STRONG_DIRECTIONS = ("YES", "NO")',
                new=b'_STRONG_DIRECTIONS = ("YES",)',
                guard_file=_T_MARKET_QUALITY,
                guards=(
                    "test_downgrade_no_to_wait_when_score_low",
                    "test_wide_spread_downgrades_no_direction",
                ),
                note="§29：NO 在低分 / 宽价差下不再降级为 WAIT",
            ),
            Mutation(
                label="W19 execution_quality_service._STRONG_DIRECTIONS 删掉 NO",
                path=_EXECUTION_QUALITY,
                old=b'_STRONG_DIRECTIONS = ("YES", "NO")',
                new=b'_STRONG_DIRECTIONS = ("YES",)',
                guard_file=_T_EXECUTION_QUALITY,
                guards=("test_raw_direction_no_is_downgraded_like_yes",),
                note="§31：NO 不再被认作强方向 → 不可执行时不再降级为 WAIT",
            ),
            Mutation(
                label="W20 source_reliability_service._STRONG_DIRECTIONS 删掉 NO",
                path=_SOURCE_RELIABILITY,
                old=b'_STRONG_DIRECTIONS = ("YES", "NO")',
                new=b'_STRONG_DIRECTIONS = ("YES",)',
                guard_file=_T_SOURCE_RELIABILITY,
                guards=("test_downgraded_flag_true_when_suggested_differs",),
                note="§32：NO 不再被认作强方向 → 该方向不再被降级为 WAIT",
            ),
            Mutation(
                label="W21 prediction_calibration_service._DIRECTIONAL 删掉 NO",
                path=_PREDICTION_CALIBRATION,
                old=b'_DIRECTIONAL = ("YES", "NO")',
                new=b'_DIRECTIONAL = ("YES",)',
                guard_file=_T_PREDICTION_CALIBRATION,
                guards=(
                    "test_case_insensitive",
                    "test_no_recommendation_no_outcome_correct",
                    "test_full_resolution_no_correct",
                ),
                note="§32：NO 不再算可检验方向 → direction_correct 直接返回 None",
            ),
            Mutation(
                label="W22 review_queue_detectors._CALLED_DIRECTIONS 删掉 NO",
                path=_REVIEW_QUEUE_DETECTORS,
                old=b'_CALLED_DIRECTIONS = frozenset({"YES", "NO"})',
                new=b'_CALLED_DIRECTIONS = frozenset({"YES"})',
                guard_file=_T_REVIEW_QUEUE_DETECTORS,
                guards=(
                    "test_high_value_downgraded_fires_for_a_no_call_too",
                    "test_fires_when_a_confident_no_call_resolves_yes",
                    "test_a_partial_resolution_above_zero_counts_as_yes",
                ),
                note="§32：NO 不再算「已下注的调用」→ NO 调用不再触发任何 review 触发器",
            ),
            Mutation(
                label="W23 domain_reliability_service._VALID_DIRECTIONS 删掉 NO",
                path=_DOMAIN_RELIABILITY,
                old=b'_VALID_DIRECTIONS = {"YES", "NO"}',
                new=b'_VALID_DIRECTIONS = {"YES"}',
                guard_file=_T_DOMAIN_RELIABILITY,
                guards=(
                    "test_no_direction_correct_support",
                    "test_no_direction_wrong_support",
                ),
                note="§32：NO 不再算合法方向 → 该记录的来源归因候选为空",
            ),
            Mutation(
                label="W24 replay/metrics._STRONG_DIRECTIONS 删掉 NO",
                path=_REPLAY_METRICS,
                old=b'_STRONG_DIRECTIONS = {"YES", "NO"}',
                new=b'_STRONG_DIRECTIONS = {"YES"}',
                guard_file=_T_REPLAY_METRICS,
                guards=(
                    "test_downgrades_caused_counted_for_a_no_base",
                    "test_conflict_case_collected_for_a_no_phase",
                ),
                note="§33：NO 不再算强方向 → NO→WAIT 的降级与冲突都不再计数",
            ),
            Mutation(
                label="W25 simulated_trade_store._REPORTED_DIRECTIONS 删掉 NO",
                path=_TRADES_STORE,
                old=b'_REPORTED_DIRECTIONS = ("YES", "NO")',
                new=b'_REPORTED_DIRECTIONS = ("YES",)',
                guard_file=_T_TRADES_STORE,
                guards=("test_by_direction_reports_both_strong_directions",),
                note="§34：by_direction 不再统计 NO 侧 —— 该文件原有断言只有『作废交易不出现」这一负向腿，删 NO 后照样成立",
            ),
            Mutation(
                label="W26 quality_metrics._STRONG_DISPLAY_DIRECTIONS 删掉 NO",
                path=_QUALITY_METRICS,
                old=b'_STRONG_DISPLAY_DIRECTIONS = ("YES", "NO")',
                new=b'_STRONG_DISPLAY_DIRECTIONS = ("YES",)',
                guard_file=_T_QUALITY_METRICS,
                guards=("test_anomalies_flags_wide_spread_not_downgraded",),
                note="§34：宽价差旗标的异常列表漏掉 NO 侧 —— 旧断言用 WAIT 记录做负控，对分支是否存在天然盲",
            ),
            Mutation(
                label="W27 event_intelligence._TRADABLE_DIRECTIONS 删掉 NO",
                path=_EVENT_INTELLIGENCE,
                old=b'_TRADABLE_DIRECTIONS = ("YES", "NO")',
                new=b'_TRADABLE_DIRECTIONS = ("YES",)',
                guard_file=_T_EVENT_INTELLIGENCE,
                guards=("test_persist_events_opens_the_trade_as_no_for_a_no_recommendation",),
                note="§34：NO 推荐被折回 YES 开仓 —— 该回退只在 entry_edge == 0 时可观测（后文会按 edge 符号覆写方向）",
            ),
            Mutation(
                label="W28 conclusion_challenge._STRONG_EVENT_DIRECTIONS 删掉 NO",
                path=_CONCLUSION_CHALLENGE,
                old=b'_STRONG_EVENT_DIRECTIONS = ("YES", "NO")',
                new=b'_STRONG_EVENT_DIRECTIONS = ("YES",)',
                guard_file=_T_CONCLUSION_CHALLENGE,
                guards=(
                    "test_strong_no_conclusion_without_support_is_insufficient_evidence",
                    "test_small_probability_change_counts_for_a_no_conclusion",
                ),
                note="§34：NO 不再算强方向 —— 证据门与计算门各读一次该常量，故守卫必须两条，缺一条即半空头",
            ),
            Mutation(
                label="W29 decision_quality._STRONG_DIRECTIONS 删掉 NO",
                path=_DECISION_QUALITY,
                old=b'_STRONG_DIRECTIONS = ("YES", "NO")',
                new=b'_STRONG_DIRECTIONS = ("YES",)',
                guard_file=_T_DECISION_QUALITY,
                guards=("test_rule4_empty_breakdown_downgrades_no_to_wait",),
                note="§34：Stage A 不再降级 NO —— 既有的 WAIT 用例是负控，负控在分支整体消失时照样绿",
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


def _run_guards(mutation: Mutation) -> tuple[bool, str]:
    """Return (all selected guards passed, last line of output)."""
    if not PY.is_file():
        raise SystemExit(f"interpreter not found: {PY}")
    proc = subprocess.run(
        [
            str(PY),
            "-m",
            "pytest",
            mutation.guard_file,
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
    return proc.returncode == 0, out.splitlines()[-1] if out else ""


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
        found = path.read_bytes().count(mutation.old)
        if found != 1:
            problems.append(
                f"{position}: needle appears {found}x in {mutation.path} "
                f"(expected 1). If the file's line endings changed, fix the "
                "needle rather than loosening this check."
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
        path.write_bytes(path.read_bytes().replace(mutation.old, mutation.new, 1))
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

    hits = original.count(mutation.old)
    if hits != 1:
        return [f"needle appears {hits}x (expected 1)"]

    green_before, tail_before = _run_guards(mutation)
    if not green_before:
        # Anything after this is unattributable: the guard was already red, so a
        # red after the mutation would prove nothing. Reported separately from a
        # needle problem because the two have different fixes.
        return [f"guard already RED before mutation -> {tail_before}"]

    try:
        path.write_bytes(original.replace(mutation.old, mutation.new, 1))
        passed_after, tail_after = _run_guards(mutation)
    finally:
        path.write_bytes(original)

    # _run_guards reports a *pass* flag, so a successful mutation shows up as
    # `passed_after is False`. Inverting here keeps the variable names honest:
    # red_after means "the guard went red", which is what this phase must prove.
    red_after = not passed_after

    restored_bytes = _sha(path.read_bytes()) == _sha(original)
    green_restored, tail_restored = _run_guards(mutation)

    if not red_after:
        problems.append(f"stayed GREEN with the fix reverted -> {tail_after}")
    if not restored_bytes:
        problems.append("file bytes differ from before the mutation")
    if not green_restored:
        problems.append(f"still RED after restore -> {tail_restored}")

    status = "OK " if not problems else "BAD"
    print(f"[{status}] {mutation_set.key} {index:>2}. {mutation.label}")
    print(f"        green before {green_before} | red after mutation {red_after} | "
          f"green after restore {green_restored} | bytes restored {restored_bytes}")
    if red_after:
        print(f"        mutated run -> {tail_after}")
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
