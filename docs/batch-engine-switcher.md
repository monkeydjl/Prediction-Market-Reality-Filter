# 批量引擎切换功能使用说明

## 功能概述

在世界杯引擎控制台（`/sports/world-cup/engine`）的"批量引擎操作"卡片中，可以把所选状态下的全部比赛批量切换到某个预测引擎：

### 1. 一键 ELO
- **引擎**: `elo_odds` (elo_odds_fusion)
- **特点**: 快速 ELO + 赔率融合引擎
- **适用场景**: 需要快速预测，依赖历史ELO评级和博彩赔率
- **预测速度**: 最快（秒级）
- **准确率**: 70-75%

### 2. 一键混合引擎
- **引擎**: `hybrid`
- **特点**: 完整混合引擎（规则 + AI推理）
- **适用场景**: 需要综合分析，包含AI优化的深度预测
- **预测速度**: 较慢（每场比赛数秒到数十秒）
- **准确率**: 取决于AI优化质量
- **注意**: 当AI服务不可用时，会降级为`rule_only`

### 3. 融合
- **引擎**: `integrated`
- **特点**: 融合 `elo_odds` 与 `hybrid` 两条结果
- **适用场景**: 两个引擎分歧较大时取更稳的一致口径

### 4. 高置信度
- **引擎**: `high_confidence`
- **特点**: 自动选择最佳引擎
- **适用场景**: 自动根据比赛特征选择置信度最高的引擎
- **预测速度**: 取决于选择的引擎

## 使用方法

1. 进入世界杯引擎控制台（`/sports/world-cup/engine`），可从世界杯看板顶部进入
2. 在"批量引擎操作"卡片里选择目标引擎和状态过滤（默认 `scheduled`）
3. 点击"批量切换引擎（流式）"，在弹出的确认框中二次确认
4. 流式进度条会逐场显示处理到第几场，结束后给出成功/失败统计
5. 页面不会自动刷新，回看板手动刷新即可看到新预测

## API 端点

### POST `/api/world-cup/predictions/batch-switch-engine`

**请求头**:
- `X-API-Key` (required): 后端 `API_WRITE_KEY`。

**参数**:
- `engine` (required): 目标引擎 (`elo_odds` | `hybrid` | `integrated` | `high_confidence`)
- `status_filter` (optional): 比赛状态过滤 (`scheduled` | `live` | `finished`，默认: `scheduled`)

**响应**:
```json
{
  "status": "ok",
  "total": 24,
  "succeeded": 24,
  "failed": 0,
  "skipped": 0
}
```

### GET `/api/world-cup/predictions/batch-switch-engine-stream`

需要同样的 `X-API-Key` 请求头。这个流式端点也会执行受保护的批量引擎切换，并持续返回进度事件。

前端使用 `fetch` streaming 调用该端点，因为浏览器原生 `EventSource` 不能发送自定义请求头。不要把 operator key 放进 URL query string。

## 技术实现

### 后端
- 新增 `/batch-switch-engine` 端点
- 查询指定状态的所有比赛
- 调用 `batch_predict_matches()` 使用指定引擎重新预测
- 返回批量处理结果统计

### 前端
- `engine-console.tsx`（`EngineConsole`），挂在 `/sports/world-cup/engine`
- 引擎下拉 + 状态过滤 + 二次确认，覆盖全量预测的操作不能一键触发
- 流式进度（当前场次 / 总场次）与结束后的成功/失败统计
- 组件卸载时 abort 进行中的流式请求

## 注意事项

1. **混合引擎降级**: 当AI服务不可用或超时时，hybrid引擎会自动降级为`rule_only`，这是正常行为
2. **处理时间**: 批量切换24场比赛可能需要数十秒到数分钟，取决于选择的引擎
3. **并发限制**: 后端有并发保护，避免同时处理过多请求
4. **手动刷新**: 切换完成后页面不会自动刷新，需要回看板手动刷新查看最新预测
