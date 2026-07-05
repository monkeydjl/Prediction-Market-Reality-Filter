# LLM Gateway 多 API / 多模型故障切换设计

日期：2026-07-05  
状态：已批准方案 B，待实现  
范围：后端 LLM 调用层，不改变前端页面与现有业务输出结构

## 背景

当前后端已经有多个 LLM 调用点，但调用逻辑分散：

- `backend/app/services/probability_engine_service.py`
  - `_ask_ai`：主概率分析
  - `translate_title`：事件标题翻译
- `backend/app/services/openai_service.py`
  - `ask_llm`：旧版分析 helper
- `backend/app/services/cross_validation_service.py`
  - `_ask_second_model`：第二模型交叉验证
- `backend/app/services/translation_service.py`
  - `translate_fields`：批量字段翻译
- `backend/app/services/world_cup_ai_analysis_service.py`
- `backend/app/services/world_cup_ai_optimization_service.py`
- `backend/app/services/world_cup_engines/world_cup_ai_engine.py`
- `backend/app/services/llm_startup_check_service.py`

现有配置已有主模型、翻译模型、交叉验证模型的雏形：

- `OPENAI_API_KEY`
- `OPENAI_MODEL`
- `OPENAI_BASE_URL`，代码中映射为 `settings.DASHSCOPE_BASE_URL`
- `TRANSLATION_MODEL / TRANSLATION_BASE_URL / TRANSLATION_API_KEY`
- `CROSS_VALIDATION_MODEL / CROSS_VALIDATION_BASE_URL / CROSS_VALIDATION_API_KEY`

问题是：每个调用点自己创建 client、自己选 model、自己处理异常。一个 API 或模型出问题时，系统只能在局部 fallback 到确定性逻辑，不能自动尝试同 API 下的下一个模型，也不能在当前 API 全失败后切换到下一个 API。

## 目标

实现一个统一的 LLM Gateway 深模块，让业务层通过一个小接口调用 LLM，而 Gateway 内部负责：

1. 支持多个 LLM Provider/API，例如 DeepSeek、DashScope、OpenAI、OpenRouter，后续可扩展 Gemini、Claude、本地 OpenAI-compatible 服务。
2. 每个 Provider 下支持多个模型。
3. 调用失败时，优先在同一个 Provider 内切换下一个模型。
4. 当前 Provider 的模型全部失败后，再切换下一个 Provider。
5. 所有 Provider/模型都失败时，返回统一失败结果，让业务层继续使用现有 deterministic fallback。
6. 每次尝试记录 provider、model、状态、错误类型、耗时，避免静默降级。
7. 兼容现有 `OPENAI_*`、`TRANSLATION_*`、`CROSS_VALIDATION_*` 配置，避免一次改动破坏当前环境。

## 非目标

第一阶段不做以下内容：

- 不做前端 Provider 管理页面。
- 不实现复杂成本调度、动态权重调度。
- 不实现完整 provider 熔断管理 UI。
- 不强制接入非 OpenAI-compatible SDK。
- 不改变现有业务接口返回结构。
- 不移除现有 deterministic fallback。

这些可以作为第二阶段和第三阶段增强。

## 推荐方案：统一 LLMGateway 深模块

新增模块：

```text
backend/app/services/llm_gateway_service.py
```

第一版先使用单文件模块，避免过早拆包。等 Provider 类型、测试用例和迁移点稳定后，再拆成：

```text
backend/app/services/llm_gateway/
  __init__.py
  models.py
  routes.py
  adapters.py
  service.py
```

### 模块 Interface

业务层不直接创建 `AsyncOpenAI`，而是调用 Gateway：

```python
result = await complete_chat(
    task="probability_analysis",
    messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ],
    response_format={"type": "json_object"},
    temperature=0,
)
```

或者对 JSON 任务使用便利接口：

```python
result = await complete_json(
    task="probability_analysis",
    messages=messages,
    temperature=0,
)
```

`task` 是路由名，用于选择不同模型池：

- `default`
- `probability_analysis`
- `translation`
- `cross_validation`
- `world_cup`
- `startup_check`

如果指定 task 没有独立配置，则回退到 `default` route。

### 返回模型

Gateway 返回结构化结果，而不是裸字符串：

```python
@dataclass(frozen=True)
class LLMAttempt:
    provider: str
    model: str
    status: Literal["success", "failed", "skipped"]
    error_type: str | None
    error_message: str | None
    latency_ms: int

@dataclass(frozen=True)
class LLMResult:
    ok: bool
    content: str | None
    json_data: dict[str, Any] | None
    provider: str | None
    model: str | None
    attempts: list[LLMAttempt]
    usage: dict[str, int] | None
    degraded_reason: str | None
```

业务层只需要判断：

```python
if result.ok and result.json_data:
    return result.json_data
raise RuntimeError(result.degraded_reason or "LLM unavailable")
```

这样现有业务 fallback 路径仍然可以复用。

## Fallback 规则

核心顺序：

```text
for provider in configured_providers:
    if provider is not configured:
        record skipped
        continue

    for model in provider.models:
        try:
            call provider/model
            validate content/json
            return success
        except retryable_or_validation_error:
            record failed
            continue to next model

return failed result with all attempts
```

也就是：

```text
DeepSeek:
  deepseek-chat      -> 失败
  deepseek-reasoner  -> 失败

DashScope:
  qwen-plus          -> 成功，返回

OpenAI:
  不再调用，因为前面已成功
```

如果所有 Provider 都失败：

```text
LLMResult(ok=False, degraded_reason="all_routes_failed", attempts=[...])
```

业务层继续走当前已经存在的确定性降级逻辑。

## 失败分类

Gateway 将错误归一化，便于 fallback 和日志审计。

会触发下一个模型的错误：

- `timeout`
- `rate_limit`
- `provider_5xx`
- `network_error`
- `auth_error`
- `model_not_found`
- `empty_response`
- `invalid_json`
- `schema_validation_failed`
- `provider_error_in_content`

第一版不做复杂重试循环，只做：

- SDK 自带小重试或 `LLM_MAX_RETRIES_PER_MODEL` 控制；
- 当前模型失败后换下一个模型；
- 当前 Provider 全失败后换下一个 Provider。

### Provider 错误文本泄漏

现有 `translate_title` 已经遇到过 Provider 把错误写进正常 content 的情况，例如“负载过高 / rate limit / too many requests”。Gateway 需要统一识别这些文本：

- `负载过高`
- `rate limit`
- `too many requests`
- `temporarily unavailable`
- `overloaded`
- `model is busy`

如果命中，则视为失败并尝试下一个模型，而不是把错误文本当正常结果返回。

## 配置设计

新增 route 配置：

```env
LLM_ROUTE_DEFAULT=deepseek:deepseek-chat,deepseek-reasoner|dashscope:qwen-plus,qwen-turbo|openai:gpt-4o-mini
LLM_ROUTE_PROBABILITY_ANALYSIS=deepseek:deepseek-chat,deepseek-reasoner|dashscope:qwen-plus|openai:gpt-4o-mini
LLM_ROUTE_TRANSLATION=dashscope:qwen-turbo,qwen-plus|deepseek:deepseek-chat
LLM_ROUTE_CROSS_VALIDATION=deepseek:deepseek-reasoner,deepseek-chat|openai:gpt-4o-mini
LLM_ROUTE_WORLD_CUP=deepseek:deepseek-reasoner,deepseek-chat|openai:gpt-4o-mini
LLM_ROUTE_STARTUP_CHECK=deepseek:deepseek-chat|dashscope:qwen-turbo|openai:gpt-4o-mini
LLM_TIMEOUT_SECONDS=45
LLM_MAX_RETRIES_PER_MODEL=1
```

Provider 配置：

```env
LLM_PROVIDER_DEEPSEEK_BASE_URL=https://api.deepseek.com
LLM_PROVIDER_DEEPSEEK_API_KEY=...

LLM_PROVIDER_DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_PROVIDER_DASHSCOPE_API_KEY=...

LLM_PROVIDER_OPENAI_BASE_URL=
LLM_PROVIDER_OPENAI_API_KEY=...

LLM_PROVIDER_OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
LLM_PROVIDER_OPENROUTER_API_KEY=...
```

### 兼容旧配置

如果没有配置任何 `LLM_ROUTE_*`，Gateway 自动从旧配置生成 route：

```text
default route:
  provider: legacy_openai
  base_url: settings.DASHSCOPE_BASE_URL
  api_key: settings.OPENAI_API_KEY
  models: [settings.OPENAI_MODEL]
```

翻译任务兼容：

```text
translation route:
  provider: legacy_translation if TRANSLATION_API_KEY/BASE_URL/MODEL 存在
  fallback: default route
```

交叉验证任务兼容：

```text
cross_validation route:
  provider: legacy_cross_validation if CROSS_VALIDATION_MODEL 存在
  fallback: default route
```

这样第一版上线后，即使 `.env` 没有新增任何变量，系统行为也应与当前一致。

## 数据流

### 概率分析

当前：

```text
analyze_market -> probability_engine_service._ask_ai -> AsyncOpenAI
```

改为：

```text
analyze_market -> probability_engine_service._ask_ai -> LLMGateway.complete_json(task="probability_analysis")
```

如果 Gateway 成功：

- 返回 JSON dict；
- 保留 `_llm_usage`；
- 继续现有 normalize / confidence / risk 流程。

如果 Gateway 失败：

- `_ask_ai` 抛出统一异常或返回 failed；
- `analyze_market` 捕获；
- 使用现有 `build_deterministic_fallback_analysis`；
- 日志包含全部 attempts。

### 标题翻译

当前：

```text
translate_title -> translation client -> 单模型
```

改为：

```text
translate_title -> LLMGateway.complete_chat(task="translation")
```

如果所有模型失败：

- 返回英文标题截断版本；
- 不阻断事件展示。

### 世界杯 AI 引擎

当前：

```text
world_cup_ai_engine -> AsyncOpenAI 单模型
```

改为：

```text
world_cup_ai_engine -> LLMGateway.complete_json(task="world_cup")
```

如果失败：

- 继续使用现有 deterministic / Elo / odds 输出；
- 不让夺冠概率或比赛预测页面因为 LLM 报错无数据。

## 日志与可观测性

每次 Gateway 调用至少记录：

- task
- provider
- model
- status
- error_type
- latency_ms
- attempts_count
- final provider/model

成功示例：

```text
LLM gateway success task=probability_analysis provider=dashscope model=qwen-plus attempts=3 latency_ms=1820
```

失败示例：

```text
LLM gateway failed task=probability_analysis attempts=5 reason=all_routes_failed
```

业务层 warning 应包含 compact attempts 摘要，避免“看起来系统正常，其实全部走 deterministic fallback”。

## 测试设计

新增：

```text
backend/tests/test_llm_gateway_service.py
```

覆盖：

1. 单 Provider 多模型：第一个模型失败，第二个模型成功。
2. 多 Provider：第一个 Provider 全失败，第二个 Provider 成功。
3. 所有模型失败：返回 `ok=False`，attempts 完整。
4. 缺 API key：该 Provider skipped，不阻断后续 Provider。
5. 非 JSON 返回：`complete_json` 识别 `invalid_json` 并 fallback。
6. Provider 错误文本泄漏：content 含“负载过高 / rate limit”时 fallback。
7. 旧配置兼容：无 `LLM_ROUTE_*` 时生成 legacy route。
8. usage 透传：成功响应里的 token usage 进入 `LLMResult.usage`。

迁移核心调用点时补充现有测试：

- `backend/tests/test_ai_analysis_service.py`
- `backend/tests/test_translation_service.py`
- `backend/tests/test_cross_validation_service.py`
- `backend/tests/test_world_cup_ai_engine.py`
- `backend/tests/test_operational_readiness.py`

## 实施分阶段

### 阶段 1：Gateway 基础能力

- 添加 `llm_gateway_service.py`。
- 添加 route parser。
- 添加 provider config resolver。
- 添加 `complete_chat` / `complete_json`。
- 添加错误分类和 attempts 记录。
- 添加 tests。
- 不迁移业务调用点，先证明 Gateway 自身正确。

### 阶段 2：迁移核心调用点

优先顺序：

1. `probability_engine_service._ask_ai`
2. `probability_engine_service.translate_title`
3. `world_cup_engines/world_cup_ai_engine.py`
4. `world_cup_ai_analysis_service.py`
5. `translation_service.translate_fields`
6. `cross_validation_service._ask_second_model`
7. `openai_service.ask_llm`
8. `llm_startup_check_service.validate_primary_llm_startup`

每迁移一个调用点都保持旧返回结构不变。

### 阶段 3：增强能力

- Provider/model 冷却时间。
- Circuit breaker。
- 成本与延迟指标。
- `/api/ops/llm-status` 状态接口。
- 前端状态展示。

## 验收标准

### 阶段 1 验收

Gateway 基础能力完成后，以下条件必须成立：

1. 可以从 route 字符串解析多个 Provider，每个 Provider 可以包含多个模型。
2. 同 Provider 内模型失败会自动尝试下一个模型。
3. 当前 Provider 的模型全部失败后，会自动尝试下一个 Provider。
4. 所有 Provider 都失败时，Gateway 返回 `ok=False` 和完整 attempts。
5. 不配置新 `LLM_ROUTE_*` 时，现有 `OPENAI_*` 配置仍能生成 legacy route。
6. Gateway 单元测试覆盖 fallback 顺序、JSON 校验、缺 key、错误文本泄漏和全失败路径。

### 方案 B 完成验收（阶段 1 + 阶段 2）

核心调用点迁移完成后，以下条件必须成立：

1. 可以配置多个 Provider，每个 Provider 可以配置多个模型。
2. 同 Provider 内模型失败会自动尝试下一个模型。
3. 当前 Provider 的模型全部失败后，会自动尝试下一个 Provider。
4. 所有 Provider 都失败时，业务层继续走现有 deterministic fallback，不中断分析或页面数据生成。
5. 概率分析、标题翻译、世界杯 AI 至少完成核心迁移。
6. logs 或 result attempts 能看出实际尝试了哪些 provider/model，以及失败原因。
7. 不配置新 `LLM_ROUTE_*` 时，现有 `.env` 仍然可用。
8. Gateway 单元测试覆盖 fallback 顺序、JSON 校验、缺 key、错误文本泄漏和全失败路径。

## 风险与控制

### 风险：一次迁移太多导致业务回归

控制：先做 Gateway 单元测试，再按调用点逐个迁移。每个迁移点保持原有返回结构。

### 风险：Provider 配置格式复杂

控制：第一版只支持简单 route 字符串：

```text
provider:model1,model2|provider2:model3
```

不做 YAML/JSON 配置文件。

### 风险：某些 Provider 不完全兼容 OpenAI chat completions

控制：第一版只保证 OpenAI-compatible Provider。非兼容 Provider 后续通过 Adapter 扩展。

### 风险：所有 LLM 失败时输出质量下降但不明显

控制：每次全失败必须 warning log，并保留 attempts 摘要。现有 telemetry / replay 中的 degraded 标记继续使用。

## 后续实现建议

实现时优先保持小步提交：

1. `feat: add llm gateway fallback core`
2. `feat: route probability analysis through llm gateway`
3. `feat: route translation through llm gateway`
4. `feat: route world cup ai through llm gateway`
5. `test: cover llm gateway failure modes`

如果阶段 1 和阶段 2 在同一轮完成，也要避免大范围无关重构。


