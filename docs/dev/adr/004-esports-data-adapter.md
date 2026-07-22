# ADR-004: Esports data adapter (竞猜 module) — LoL v1

**Status**: Accepted  
**Date**: 2026-07-21 (Proposed) · **Accepted**: 2026-07-22  
**Related**: `docs/dev/ESPORTS_BOUNDARY.md`, Kernel MultiAdapter, betting catalog, Phase 7 market bridge

## Context

竞猜 hub 已将 **电竞** 列为 `coming_soon` / `placeholder`。在写 adapter、引擎或假赛程之前，需要可执行的决策：

- 首发 title
- Kernel sport / match_id 前缀
- 数据源门禁
- 与现有足球/NBA MultiAdapter 路径的关系

Kernel 现状：prefix MultiAdapter（`wc-`、`epl-`、`nba-`…）→ FeatureBuilder → Engine → Learning / Edge / Settlement。电竞与传统体育差异：

| 维度 | 传统体育（已有） | 电竞 |
|------|------------------|------|
| 赛制 | 固定时长 / 固定局制 | Bo1/Bo3/Bo5，地图池 / BP |
| 身份 | 俱乐部/国家相对稳定 | 战队 + 转会 + 替补/首发 |
| 市场 | 1X2 / moneyline 为主 | 系列赛胜者、地图让分、总局数 |
| 结果权威 | 联赛官方 + 数据商 | 官方 API / 赛事页 / 书商，冲突常见 |

产品约束（不变）：

- **不做自动下注 / 仓位**
- **不展示假盘口或假赛程**
- 新 feature flag **默认 OFF**
- 世界杯 `/api/world-cup/*` 双轨保留，本 ADR 不合并

## Decision

### D1 — 首发 title：League of Legends（LoL）

v1 **只**服务 LoL。CS2 / Dota 2 等后续 title 各自走独立 sport + 前缀 + 引擎，不塞进同一个 “esports engine”。

### D2 — Kernel 编码：per-title

| 字段 | v1 值 |
|------|--------|
| `sport.code` | `lol` |
| match_id 前缀 | `lol-` |
| competition 示例 | `lol_lck` / `lol_lpl` / `lol_lec` / `lol_worlds`（具体码表在接源时冻结） |
| Catalog hub | 现有 `id=esports` 保持伞型入口；实现就绪后可增加 `id=lol` 子卡或把 esports 卡片 `href` 指向 LoL 列表 |
| Feature flag | `PHASE_LOL_ENABLED`（默认 **OFF**） |

**禁止**：把 LoL 伪装成 `football` / `basketball` 引擎输入。

### D3 — 数据源门禁：官方/合作 API 优先

在 **可信赛程 + 结果源**（官方或书面合作 API，含 SLA/配额/ToS）落实前：

- **不**注册 MultiAdapter 生产路径上的 `LolAdapter`
- **不**打开 `PHASE_LOL_ENABLED`
- **不**从书商标题反推赛程作为唯一真相
- **不**用爬虫社区 API 作为 settlement 唯一源（可作辅助 enrichment，须在实现 plan 中单独标注）

允许后续实现 plan 中的 **operator CSV/JSON 导入** 作为 **dry-run / 集成测试** 夹具，但：

- 仅在 flag OFF 的测试或显式 `LOL_DRY_RUN_IMPORT=true`（名称在实现时确定，默认 OFF）下使用
- 不得在默认生产配置展示为 “今日赛程”

### D4 — 架构落点（实现时）

1. **Adapter**：`LolAdapter` 实现现有 `DataAdapter` Protocol；注册 `lol-` 到 MultiAdapter（与 NBA 相同模式）。
2. **Identity**：系列赛（series）为一等 `match_id`；地图（map）可作为子事件或 `stage`/`custom`，v1 **主市场**为系列赛胜者（home/away 两队）。
3. **Features / Engine**：新建 `LolFeatureBuilder` + `LolEngine`（或 v0 **market-only** 引擎：仅融合盘口，无假 Elo）。**禁止**默认复用 `EloOddsEngine` / `FootballMultiFactorEngine` 权重。
4. **Markets**：Phase 7 桥接在 **match identity 稳定后** 再接；settlement 必须以结果源为准，不得 invent 比分/地图分。
5. **Catalog / UI**：
   - 门禁未满足：`coming_soon` + `ESPORTS_BOUNDARY` 文案
   - 门禁满足且 flag OFF：catalog 可显示 `adapter_likely=false`，仍无赛程
   - flag ON 且 adapter 注册：`track=kernel`，`kernel_sport=lol`，列表走 `GET /api/predictions/matches?sport=lol`

### D5 — 本 ADR 交付边界（2026-07-22）

**本次只接受决策与文档**，不包含：

- `LolAdapter` / engine 代码
- `PHASE_LOL_ENABLED` 配置项落地（可在实现 plan 第一步再加）
- 真实 API key 或供应商合同

## Architecture sketch（实现参考）

```text
[Trusted LoL schedule+result API]
        │
        ▼
  LolAdapter (prefix lol-)
        │
        ▼
  MultiAdapter ── fetch_schedule(sport=lol) / sync_schedule
        │
        ▼
  LolFeatureBuilder → LolEngine (v0 market-only | v1 rating)
        │
        ▼
  Learning / Edge / Settlement（复用 Kernel；市场链接 Phase 7）
```

竞猜 UX：

```text
Hub 电竞卡 → /sports/betting/esports（门禁前）
           → 或 /sports/betting/lol + /sports?sport=lol（门禁后）
```

## Prerequisites checklist（打开 flag 前必须全部满足）

| # | 门禁 | 完成定义 |
|---|------|----------|
| P1 | 联赛/赛区范围 | 写明 v1 覆盖哪些 league（如 LCK/LPL/LEC/Worlds），其余明确 out of scope |
| P2 | 赛程源 | 文档化 endpoint、auth、更新频率、时区、取消/改期语义 |
| P3 | 结果源 | 与赛程同源或冲突策略（以谁为准、延迟多久可 settle） |
| P4 | 身份映射 | 战队 ID ↔ 显示名 ↔ 市场 slug 规则；转会窗口处理 |
| P5 | 市场映射 | v1 仅 series winner；地图盘/让分标注为 v2+ |
| P6 | 法律与 ToS | 数据源许可允许缓存与展示；不违反平台 ToS |
| P7 | 空态 UX | flag ON 但无今日赛时的文案；**永不**用假盘填充 |
| P8 | 测试 | adapter contract tests + 至少 1 条 dry-run 结算样例（可合成，须标注） |

## Consequences

- ✅ 首发 title 与编码明确，避免 “esports 万能引擎”
- ✅ 数据源门禁阻止假盘与过早耦合
- ✅ 与 MultiAdapter / 竞猜 catalog 现有模式一致
- ⚠️ LoL 实现被阻塞到有可信源；产品需推进供应商或官方接入
- ⚠️ 多赛区与版本补丁会影响特征；引擎 roadmap 需单独 research spike
- ⚠️ Catalog 伞型 `esports` 与 per-title `lol` 的 IA 需在实现 plan 里定一种演进（推荐：先 esports 落地页说明 LoL v1，再加 lol 子卡）

## Alternatives considered

| 方案 | 结论 |
|------|------|
| **CS2 首发** | 未选；产品选定 LoL |
| **sport=`esports` 伞型 + 单引擎** | 拒绝作为 v1：多 title 特征语义不同，难维护 |
| **CSV 运营导入作生产默认** | 拒绝；仅允许 dry-run / 测试夹具 |
| **足球 MultiFactor 映射** | 拒绝：BP/地图/经济差无法映射 |
| **独立微服务** | 延期；单 title 时 Kernel 足够 |
| **本次写空骨架 adapter** | 延期；本 ADR 仅 Accepted 决策 + 门禁 |

## Implementation order

Canonical plan: [`docs/superpowers/plans/2026-07-22-lol-esports-adapter.md`](../superpowers/plans/2026-07-22-lol-esports-adapter.md)

1. Task 0: `docs/dev/lol/GATES.md` (P1–P8) — blocks production HTTP source  
2. Config `PHASE_LOL_ENABLED` + competition codes  
3. Dry-run JSON import + `LolAdapter` + market-only engine  
4. Kernel registration + catalog/FE + RUNBOOK  
5. Later plan: vendor `LolScheduleSource` after GATES P2/P3/P6

## References

- `docs/dev/ESPORTS_BOUNDARY.md`
- `backend/app/sports/football/adapters/multi_adapter.py`
- `backend/app/kernel/betting_catalog.py`
- `frontend/src/lib/betting/competition-catalog.ts`
- 竞猜模块 commits（catalog / status / sync / chips）
