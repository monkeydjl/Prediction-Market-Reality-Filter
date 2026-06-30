# 批量引擎切换功能使用说明

## 功能概述

在世界杯预测页面的"自动调教"标签页中，新增了三个一键批量切换预测引擎的按钮：

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

### 3. 一键高置信度
- **引擎**: `high_confidence`
- **特点**: 自动选择最佳引擎
- **适用场景**: 自动根据比赛特征选择置信度最高的引擎
- **预测速度**: 取决于选择的引擎

## 使用方法

1. 进入世界杯预测页面（`/world-cup`）
2. 点击顶部"自动调教"标签
3. 在"批量切换预测引擎"卡片中，点击任一按钮
4. 等待批量切换完成（显示成功/失败统计）
5. 页面将在2秒后自动刷新，显示新的预测结果

## API 端点

### POST `/api/world-cup/predictions/batch-switch-engine`

**请求头**:
- `X-API-Key` (required): 后端 `API_WRITE_KEY`。

**参数**:
- `engine` (required): 目标引擎 (`elo_odds` | `hybrid` | `high_confidence`)
- `status_filter` (optional): 比赛状态过滤 (默认: `scheduled`)

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
- 新组件 `BatchEngineSwitcher.tsx`
- 三个可视化按钮（带图标和颜色区分）
- 实时显示切换进度和结果
- 成功后自动刷新页面

## 注意事项

1. **混合引擎降级**: 当AI服务不可用或超时时，hybrid引擎会自动降级为`rule_only`，这是正常行为
2. **处理时间**: 批量切换24场比赛可能需要数十秒到数分钟，取决于选择的引擎
3. **并发限制**: 后端有并发保护，避免同时处理过多请求
4. **自动刷新**: 切换成功后页面会自动刷新，显示最新预测结果
