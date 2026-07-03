# Review Queue Detectors Completion — Design Spec

**Date**: 2026-07-03
**Spec source**: `2026-06-30-production-readiness-gaps.md` §6.2
**Status**: Approved (brainstorming complete, ready for plan)
**Scope**: 补全 §6.2 人工复核队列剩余 2 个检测器

---

## 1. 背景

`production-readiness-gaps.md` §6.2 列出 5 个 review queue trigger 来源，其中 3 个已实现：

| # | trigger | 状态 |
|---|---|---|
| 1 | `high_value_downgraded` | ✅ 已实现 |
| 2 | `source_market_conflict` | ✅ 已实现 |
| 3 | `outcome_prediction_mismatch` | ✅ 已实现 |
| 4 | `auto_resolve_low_confidence` | ❌ 本 spec |
| 5 | `audit_inconsistency` | ❌ 本 spec |

现有基础设施（已实现，本 spec 复用）：

- [review_queue_store.py](file:///e:/Github/Prediction%20Market%20Reality%20Filter/backend/app/memory/review_queue_store.py) — SQLite store + INSERT-only audit log，`enqueue_item` 在 `(event_id, trigger)` pending 时 refresh-in-place（不产生重复行）
- [review_queue_detectors.py](file:///e:/Github/Prediction%20Market%20Reality%20Filter/backend/app/services/review_queue_detectors.py) — 纯函数 `detect_review_candidates(record, ...)`，已被 `event_intelligence_service._build_all_overlays` 在 overlay build 后调用
- [audit_quality_consistency.py](file:///e:/Github/Prediction%20Market%20Reality%20Filter/backend/scripts/audit_quality_consistency.py) — 批量审计 CLI，5 类冲突检测，输出 text/json，ERROR 退出码非零
- 5 个 locked reviewer actions：`confirm` / `override` / `request_more_evidence` / `mark_bad_source` / `mark_bad_resolution`
- vocabulary lock：`reason` / `note` 字段 banned terms: long/short/buy/sell/position/kelly/order

---

## 2. 架构

两个检测器性质不同，采用不同集成方式：

### 检测器 1 — `auto_resolve_low_confidence`（实时）

- **纯函数** `_detect_auto_resolve_low_confidence(record, *, confidence_threshold)` 加入 `review_queue_detectors.py`
- 检查 `outcome.source == "auto_market"` AND `outcome.status == "resolved"` AND `confidence` 是数字 AND `confidence < threshold`
- **Public wrapper** `detect_auto_resolve_low_confidence(record, *, confidence_threshold)` 暴露窄接口，hook 不跨模块 import 私有函数
- **Hook 位置**：`event_resolve_service.resolve_with_calibration()` 末尾、`return updated` 之前，紧邻现有 domain_reliability hook
  - 只在 `updated is not None` 时执行（resolve 失败不 enqueue）
  - 内部用 `(updated or {}).get("record") or {}` 双保险取 record
  - 只调 `detect_auto_resolve_low_confidence` 单个 detector（不调 `detect_review_candidates` 全量，避免重复跑已在 overlay build 时跑过的 detector）
  - Best-effort：detector / enqueue 失败只 log warning，不阻塞 resolve

### 检测器 2 — `audit_inconsistency`（批量）

- **不加入** `detect_review_candidates()`（审计检查是全局扫描/运维诊断，不是单 record overlay build 的一部分）
- 给 `audit_quality_consistency.py` CLI 加 `--enqueue` flag
- 按 `event_id` 聚合所有 ERROR 为一条 item，`context.conflicts` 数组携带该 event 全部 ERROR 的 conflict_type / message / field_values
- store 的 `(event_id, trigger)` dedup 让重跑 audit 变成 refresh-in-place（最新 conflicts 覆盖旧 context，不产生重复 pending 行）
- **只 enqueue ERROR**，WARN/INFO 不入队

### Trigger 文档登记

`review_queue_detectors.py` 顶部注释扩展，登记全部 5 种 trigger 类型，注明 `auto_resolve_low_confidence` 有独立 public wrapper，`audit_inconsistency` 是 batch CLI only（无 `_detect_*` 函数）。

---

## 3. 前置修复：auto_resolve_events fuzzy path confidence

[event_resolve_service.py](file:///e:/Github/Prediction%20Market%20Reality%20Filter/backend/app/services/event_resolve_service.py) 三处 `resolve_with_calibration(confidence=...)`：

| 行号 | 路径 | 现状 | 修改 |
|---|---|---|---|
| L342 | contract-id settle（已 verified link） | `1.0` | **保持 `1.0`** |
| L431 | fuzzy verified path | `1.0` | **改为 `confidence=score`**（真实 match score） |
| L526 | direct settle（Manifold source_id API） | `1.0` | **保持 `1.0`** |

只有 fuzzy verified path 改动，因为它已经有真实 `score` 但被丢弃。修改后 `outcome.confidence` 范围：

- contract-id / direct settle：`1.0`
- fuzzy verified path：`[AUTO_VERIFY_THRESHOLD, 1.0]` = `[0.90, 1.0]`

`AUTO_VERIFY_THRESHOLD` 默认 `0.90`（[config.py L440](file:///e:/Github/Prediction%20Market%20Reality%20Filter/backend/app/core/config.py)），fuzzy verified gate 在 `score >= AUTO_VERIFY_THRESHOLD` 时通过。

### 测试断言（锁定）

- fuzzy path 改后：断言 `outcome.confidence == score`（具体 score 值通过 fixture 控制，例如 0.92）
- contract-id path：断言 `outcome.confidence == 1.0`
- direct settle path：断言 `outcome.confidence == 1.0`

---

## 4. 检测器 1 详细设计

### 4.1 纯检测器

[review_queue_detectors.py](file:///e:/Github/Prediction%20Market%20Reality%20Filter/backend/app/services/review_queue_detectors.py) 新增：

```python
def _detect_auto_resolve_low_confidence(
    record: dict[str, Any],
    *,
    confidence_threshold: float = 0.95,
) -> list[dict[str, Any]]:
    """Flag auto-resolved events whose match confidence is below threshold.

    Only fires for outcome.source == "auto_market" (manual resolves are
    trusted). Defensive: skips records missing outcome / non-numeric
    confidence / non-resolved status.
    """
    outcome = record.get("outcome")
    if not isinstance(outcome, dict):
        return []
    if outcome.get("status") != "resolved":
        return []
    if outcome.get("source") != "auto_market":
        return []
    confidence = outcome.get("confidence")
    if not isinstance(confidence, (int, float)):
        return []
    if confidence >= confidence_threshold:
        return []
    return [{
        "trigger": "auto_resolve_low_confidence",
        "severity": "WARN",
        "reason": f"自动结算置信度 {confidence:.2f} 低于阈值 {confidence_threshold:.2f}",
        "context": {
            "outcome_source": "auto_market",
            "outcome_confidence": confidence,
            "confidence_threshold": confidence_threshold,
            "actual_outcome": outcome.get("actual_outcome"),
        },
    }]
```

阈值默认 `0.95`：fuzzy verified 的 score 下界是 `AUTO_VERIFY_THRESHOLD=0.90`，所以 `[0.90, 0.95)` 区间会被捕捉，给 reviewer 复核刚过 gate 但偏低置信的 auto-resolve。contract-id / direct settle 路径 confidence=1.0，永不命中。

### 4.2 Public wrapper

```python
def detect_auto_resolve_low_confidence(
    record: dict[str, Any],
    *,
    confidence_threshold: float = 0.95,
) -> list[dict[str, Any]]:
    """Public wrapper for the auto-resolve low-confidence detector.

    Exposed separately from ``_detect_auto_resolve_low_confidence`` so the
    resolve_with_calibration hook can import a narrow public API instead
    of reaching into a private function. Returns the same candidate list.
    """
    return _detect_auto_resolve_low_confidence(
        record, confidence_threshold=confidence_threshold,
    )
```

### 4.3 detect_review_candidates 扩展

`detect_review_candidates()` 签名新增 `auto_resolve_confidence_threshold` 参数：

```python
def detect_review_candidates(
    record: dict[str, Any],
    *,
    mismatch_confidence_threshold: float = 0.75,
    auto_resolve_confidence_threshold: float = 0.95,
) -> list[dict[str, Any]]:
    ...
    candidates.extend(_detect_auto_resolve_low_confidence(
        record, confidence_threshold=auto_resolve_confidence_threshold,
    ))
    return candidates
```

这让 `event_intelligence_service._build_all_overlays` 现有调用路径也覆盖新 detector（虽然 overlay build 时通常还没有 outcome，detector 会因 `outcome is None` 直接返回 — 但保持调用统一性，避免后续忘记加）。

### 4.4 Hook 集成

[event_resolve_service.py](file:///e:/Github/Prediction%20Market%20Reality%20Filter/backend/app/services/event_resolve_service.py) `resolve_with_calibration` 末尾、`return updated` 之前，紧邻现有 domain_reliability hook：

```python
# Plan 4 §6.2: auto-resolve low-confidence review queue detector.
# Best-effort: detector failure never blocks resolution. Only runs when
# resolve_event actually produced an updated record — a failed resolve
# must not enqueue a review item. Only this one detector runs here
# (other detectors already ran in event_intelligence_service during
# overlay build; auto-resolve does not re-run overlays).
if settings.REVIEW_QUEUE_ENABLED and updated is not None:
    try:
        from app.services.review_queue_detectors import detect_auto_resolve_low_confidence
        from app.memory import review_queue_store
        record = (updated or {}).get("record") or {}
        eid = record.get("event_id")
        if eid:
            for cand in detect_auto_resolve_low_confidence(
                record,
                confidence_threshold=settings.REVIEW_QUEUE_AUTO_RESOLVE_CONFIDENCE,
            ):
                try:
                    review_queue_store.enqueue_item(
                        event_id=eid,
                        trigger=cand["trigger"],
                        severity=cand["severity"],
                        reason=cand["reason"],
                        context=cand["context"],
                    )
                except Exception:
                    logger.warning("review_queue enqueue failed", exc_info=True)
    except Exception:
        logger.warning("review_queue detector run failed", exc_info=True)

return updated
```

注意：`updated is not None` 是显式前置门（resolve_event 返回 None 表示 event_id 不存在/失败）；内部仍用 `(updated or {}).get("record") or {}` 双保险。

### 4.5 配置项

[config.py](file:///e:/Github/Prediction%20Market%20Reality%20Filter/backend/app/core/config.py) 新增（紧邻现有 `REVIEW_QUEUE_MISMATCH_CONFIDENCE` L684-685）：

```python
REVIEW_QUEUE_AUTO_RESOLVE_CONFIDENCE: float = float(
    os.getenv("REVIEW_QUEUE_AUTO_RESOLVE_CONFIDENCE", "0.95")
)
```

`.env.example` 同步新增条目，注释说明：低于此 confidence 的 auto-resolve 事件进入复核队列。

### 4.6 测试覆盖

- 纯检测器（`test_review_queue_detectors.py` 新增）：
  - `outcome.source="auto_market"` + `confidence=0.92` + threshold=0.95 → 命中，severity=WARN
  - `outcome.source="manual"` → 不命中
  - `outcome.status="invalid"` → 不命中
  - `outcome` 缺失 / `confidence` 非数字 → 不命中（防御性）
  - `confidence=0.95` (== threshold) → 不命中（`<` 严格）
  - `confidence=1.0` → 不命中
- Hook 集成（`test_event_resolve_service.py` 新增）：
  - fuzzy path 改后：`outcome.confidence == score`（fixture 控制 score=0.92）
  - contract-id path：`outcome.confidence == 1.0`
  - direct settle path：`outcome.confidence == 1.0`
  - `REVIEW_QUEUE_ENABLED=false` → hook 不执行（byte-identical 行为）
  - `REVIEW_QUEUE_ENABLED=true` + low confidence → enqueue_item 被调用（mock store）
  - `updated is None`（event_id 不存在）→ hook 不执行
  - detector 抛异常 → 不阻塞 resolve，只 log warning

---

## 5. 检测器 2 详细设计

### 5.1 CLI flag

[audit_quality_consistency.py](file:///e:/Github/Prediction%20Market%20Reality%20Filter/backend/scripts/audit_quality_consistency.py) argparse 新增：

```python
parser.add_argument(
    "--enqueue",
    action="store_true",
    help="Enqueue ERROR-severity conflicts into the review queue "
         "(WARN/INFO are not enqueued). Requires review_queue store.",
)
```

### 5.2 聚合逻辑

在所有 entry 审计完、得到 `conflicts: list[Conflict]` 后：

```python
if args.enqueue:
    from app.memory import review_queue_store
    # Group ERROR conflicts by event_id so each event gets ONE audit
    # item carrying all its ERROR-level conflicts in context. Store's
    # (event_id, trigger) dedup then collapses re-runs into refresh-in-place
    # semantics — no information loss, no duplicate pending rows.
    errors_by_event: dict[str, list[Conflict]] = {}
    for c in conflicts:
        if c.severity == ERROR:
            errors_by_event.setdefault(c.event_id, []).append(c)

    for event_id, event_errors in errors_by_event.items():
        codes = [c.conflict_type for c in event_errors]
        reason = (
            event_errors[0].message
            if len(event_errors) == 1
            else f"审计发现 {len(event_errors)} 个 ERROR 级一致性冲突：{', '.join(codes)}"
        )
        try:
            review_queue_store.enqueue_item(
                event_id=event_id,
                trigger="audit_inconsistency",
                severity=ERROR,
                reason=reason,
                context={
                    "conflicts": [
                        {
                            "conflict_type": c.conflict_type,
                            "message": c.message,
                            "field_values": c.field_values,
                        }
                        for c in event_errors
                    ],
                },
            )
        except Exception as exc:
            print(
                f"[WARN] enqueue failed for {event_id}: {exc}",
                file=sys.stderr,
            )
```

**语义**：同 event 一条 `audit_inconsistency` pending item，`context.conflicts` 数组携带全部 ERROR。Store dedup 让重跑 audit 变成 refresh（最新 conflicts 覆盖旧 context，不产生重复 pending 行）。

**vocabulary lock**：`reason` 字段会经过 `enqueue_item` 内部的 `_check_vocabulary`。审计 message 中已避免 banned terms（long/short/buy/sell/position/kelly/order）—— 现有 audit 检查的 message 是 "market_quality.wide_spread_flag=True but..." 这类技术描述，不含 banned terms。如果未来新增检查可能引入 banned term，需在 message 中规避。

### 5.3 测试覆盖

`test_audit_quality_consistency.py` 新增（如不存在则创建）：

- `--enqueue` flag 解析测试
- 单 ERROR → reason = 该 conflict 的 message，context.conflicts 长度为 1
- 多 ERROR 同 event → reason = 聚合描述（"审计发现 N 个..."），context.conflicts 长度为 N，含全部 conflict_type
- 多 ERROR 跨 event → 每个 event 一条 enqueue 调用
- WARN/INFO only → `errors_by_event` 为空，不调用 enqueue
- `--enqueue` + store 抛异常 → stderr 输出 `[WARN]`，audit 报告仍正常输出（exit code 不变）
- 混合 ERROR + WARN → 只 ERROR 进 errors_by_event

---

## 6. Trigger 文档登记

[review_queue_detectors.py](file:///e:/Github/Prediction%20Market%20Reality%20Filter/backend/app/services/review_queue_detectors.py) 顶部 docstring 扩展：

```python
"""Review queue trigger detectors (Plan 4 §6.2).

Pure functions that scan a single event record and return review-queue
candidate dicts. No I/O, no LLM, no settings reads — the orchestrator
calls ``detect_review_candidates`` and decides whether to enqueue.

Each candidate is a dict with keys:
    trigger   — one of the locked trigger type strings
    severity  — "WARN" or "ERROR"
    reason    — Chinese reason string (vocabulary-locked)
    context   — dict of relevant field values for the reviewer

Trigger types (locked):
    high_value_downgraded        — act signal but final direction is WAIT/AVOID
    source_market_conflict       — source_reliability says WAIT but market_quality
                                   does not (strong cross-overlay conflict)
    outcome_prediction_mismatch  — resolved outcome contradicts a high-confidence
                                   prediction
    auto_resolve_low_confidence  — auto-resolved event with match confidence
                                   below threshold (real-time hook in
                                   resolve_with_calibration; only trigger
                                   with its own public wrapper
                                   detect_auto_resolve_low_confidence)
    audit_inconsistency          — batch audit CLI only (audit_quality_consistency
                                   --enqueue). No _detect_* function here —
                                   conflicts come from audit_quality_consistency
                                   checks, enqueued directly via store.
"""
```

---

## 7. 文件清单

### 修改

| 文件 | 改动 |
|---|---|
| [backend/app/services/event_resolve_service.py](file:///e:/Github/Prediction%20Market%20Reality%20Filter/backend/app/services/event_resolve_service.py) | L431 fuzzy path `confidence=score`；末尾加 review queue hook |
| [backend/app/services/review_queue_detectors.py](file:///e:/Github/Prediction%20Market%20Reality%20Filter/backend/app/services/review_queue_detectors.py) | 顶部 docstring 登记 5 triggers；新增 `_detect_auto_resolve_low_confidence` + `detect_auto_resolve_low_confidence` wrapper；`detect_review_candidates` 扩展参数 |
| [backend/app/core/config.py](file:///e:/Github/Prediction%20Market%20Reality%20Filter/backend/app/core/config.py) | 新增 `REVIEW_QUEUE_AUTO_RESOLVE_CONFIDENCE` |
| [backend/.env.example](file:///e:/Github/Prediction%20Market%20Reality%20Filter/backend/.env.example) | 新增 `REVIEW_QUEUE_AUTO_RESOLVE_CONFIDENCE=0.95` 条目 |
| [backend/scripts/audit_quality_consistency.py](file:///e:/Github/Prediction%20Market%20Reality%20Filter/backend/scripts/audit_quality_consistency.py) | 新增 `--enqueue` flag + 聚合逻辑 |

### 新增测试

| 文件 | 覆盖 |
|---|---|
| [backend/tests/test_review_queue_detectors.py](file:///e:/Github/Prediction%20Market%20Reality%20Filter/backend/tests/test_review_queue_detectors.py) | 扩展：`_detect_auto_resolve_low_confidence` 单测（命中/不命中/防御性） |
| [backend/tests/test_event_resolve_service.py](file:///e:/Github/Prediction%20Market%20Reality%20Filter/backend/tests/test_event_resolve_service.py) | 扩展：fuzzy path confidence=score 断言；hook 集成测试 |
| `backend/tests/test_audit_quality_consistency.py` | 新增（如不存在）：`--enqueue` 聚合逻辑测试 |

---

## 8. 范围边界

### 包含

- fuzzy path confidence 修复（前置）
- 检测器 1 纯函数 + public wrapper + hook 集成 + 配置项
- 检测器 2 CLI `--enqueue` flag + 按 event 聚合逻辑
- trigger 文档登记
- 上述全部测试

### 不包含

- 不抽 audit service 模块（CLI 直接调 store）
- 不修改 `detect_review_candidates` 现有 3 个 detector 的行为
- 不修改 review_queue_store 的 schema / dedup 语义
- 不修改 contract-id / direct settle path 的 confidence（保持 1.0）
- 不新增 reviewer action（5 个 locked actions 已够用）
- 不做前端 UI 改动（review queue 已有 API，前端后续单独做）

---

## 9. 验收标准

- [ ] fuzzy verified path `outcome.confidence == score`（非 1.0）
- [ ] contract-id / direct settle path `outcome.confidence == 1.0`（不变）
- [ ] `REVIEW_QUEUE_ENABLED=false` 时 resolve_with_calibration 行为 byte-identical
- [ ] `REVIEW_QUEUE_ENABLED=true` + auto_market + confidence < threshold → enqueue_item 被调用
- [ ] manual resolve 不触发 detector
- [ ] resolve 失败（updated is None）不触发 hook
- [ ] `audit_quality_consistency.py --enqueue` 把 ERROR 按 event 聚合入队
- [ ] WARN/INFO 不入队
- [ ] 同 event 多 ERROR → 一条 item，context.conflicts 含全部
- [ ] `review_queue_detectors.py` 顶部 docstring 登记 5 种 trigger
- [ ] 全部测试通过，无回归
