# LLM Gateway Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build方案 B：a centralized backend LLM Gateway that supports multiple Provider/API routes, multiple models per Provider, same-Provider model fallback, next-Provider fallback, legacy config compatibility, attempts telemetry, and migration of the highest-value LLM call sites.

**Architecture:** Add one deep module at `backend/app/services/llm_gateway_service.py`. Business services call `complete_chat()` or `complete_json()` and keep their existing fallback behavior when the Gateway returns/raises failure. Route parsing and provider resolution live inside the module so callers do not duplicate fallback logic.

**Tech Stack:** Python 3, FastAPI backend, `openai.AsyncOpenAI` for OpenAI-compatible providers, `unittest.IsolatedAsyncioTestCase`, existing `app.core.config.settings`.

## Global Constraints

- Keep existing business response shapes unchanged.
- Keep existing deterministic fallback paths; Gateway failure must not break analysis, translation, or World Cup predictions.
- First implementation supports OpenAI-compatible Chat Completions providers only.
- If no `LLM_ROUTE_*` variables are configured, use existing `OPENAI_API_KEY`, `OPENAI_MODEL`, and `OPENAI_BASE_URL`/`settings.DASHSCOPE_BASE_URL` behavior.
- Do not add a frontend Provider management UI in this phase.
- Do not introduce YAML/JSON route config files; use `provider:model1,model2|provider2:model3` env strings.
- Make network-free tests by injecting fake clients/factories.

---

## File Structure

- Create `backend/app/services/llm_gateway_service.py`
  - Dataclasses: `LLMProviderConfig`, `LLMModelRoute`, `LLMAttempt`, `LLMResult`.
  - Exceptions: `LLMGatewayError`.
  - Public interface: `complete_chat()`, `complete_json()`, `build_route()`, `parse_route_string()`, `reset_llm_gateway_clients_for_tests()`.
  - Internal helpers: provider config resolution, cached AsyncOpenAI client creation, error classification, provider error content detection, usage extraction.
- Create `backend/tests/test_llm_gateway_service.py`
  - Unit tests for route parsing, fallback ordering, invalid JSON fallback, skipped missing key providers, legacy route compatibility, usage pass-through.
- Modify `backend/app/core/config.py`
  - Add `LLM_ROUTE_DEFAULT`, task route variables, `LLM_TIMEOUT_SECONDS`, `LLM_MAX_RETRIES_PER_MODEL`, and provider-specific env fields for DeepSeek, DashScope, OpenAI, OpenRouter.
- Modify `backend/app/services/probability_engine_service.py`
  - Replace direct `AsyncOpenAI` use in `_ask_ai` and `translate_title` with Gateway calls.
  - Preserve `get_client()` and `get_translation_client()` temporarily for non-migrated callers/tests if needed, but route these two call sites through Gateway.
- Modify `backend/app/services/world_cup_engines/world_cup_ai_engine.py`
  - Replace direct `get_client().chat.completions.create()` with `complete_json(task="world_cup")`.
- Modify focused tests:
  - `backend/tests/test_ai_analysis_service.py` if direct mocks depend on `_ask_ai` behavior.
  - `backend/tests/test_world_cup_ai_engine.py` to mock `complete_json` instead of OpenAI client where needed.

---

### Task 1: Gateway route parsing and config resolution

**Files:**
- Create: `backend/app/services/llm_gateway_service.py`
- Modify: `backend/app/core/config.py`
- Test: `backend/tests/test_llm_gateway_service.py`

**Interfaces:**
- Produces: `parse_route_string(route: str) -> list[LLMModelRoute]`
- Produces: `build_route(task: str = "default") -> list[LLMModelRoute]`
- Produces: `reset_llm_gateway_clients_for_tests() -> None`

- [ ] **Step 1: Write failing route parsing/config tests**

Add tests like:

```python
class LLMGatewayRouteTests(unittest.TestCase):
    def test_parse_route_string_keeps_provider_and_model_order(self):
        routes = gateway.parse_route_string(
            "deepseek:deepseek-chat,deepseek-reasoner|dashscope:qwen-plus"
        )
        self.assertEqual(
            [(r.provider, r.models) for r in routes],
            [
                ("deepseek", ["deepseek-chat", "deepseek-reasoner"]),
                ("dashscope", ["qwen-plus"]),
            ],
        )

    def test_build_route_uses_legacy_openai_when_no_new_route_exists(self):
        with patch.object(gateway.settings, "LLM_ROUTE_DEFAULT", ""), \
             patch.object(gateway.settings, "OPENAI_MODEL", "deepseek-chat"):
            routes = gateway.build_route("default")
        self.assertEqual(routes[0].provider, "legacy_openai")
        self.assertEqual(routes[0].models, ["deepseek-chat"])
```

- [ ] **Step 2: Run failing tests**

Run: `python -m unittest backend.tests.test_llm_gateway_service -v`

Expected: FAIL because `llm_gateway_service` does not exist.

- [ ] **Step 3: Add config fields**

Add fields to `Settings` near existing LLM settings:

```python
LLM_ROUTE_DEFAULT: str = os.getenv("LLM_ROUTE_DEFAULT", "")
LLM_ROUTE_PROBABILITY_ANALYSIS: str = os.getenv("LLM_ROUTE_PROBABILITY_ANALYSIS", "")
LLM_ROUTE_TRANSLATION: str = os.getenv("LLM_ROUTE_TRANSLATION", "")
LLM_ROUTE_CROSS_VALIDATION: str = os.getenv("LLM_ROUTE_CROSS_VALIDATION", "")
LLM_ROUTE_WORLD_CUP: str = os.getenv("LLM_ROUTE_WORLD_CUP", "")
LLM_ROUTE_STARTUP_CHECK: str = os.getenv("LLM_ROUTE_STARTUP_CHECK", "")
LLM_TIMEOUT_SECONDS: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "45"))
LLM_MAX_RETRIES_PER_MODEL: int = int(os.getenv("LLM_MAX_RETRIES_PER_MODEL", "1"))
LLM_PROVIDER_DEEPSEEK_BASE_URL: str = os.getenv("LLM_PROVIDER_DEEPSEEK_BASE_URL", "https://api.deepseek.com")
LLM_PROVIDER_DEEPSEEK_API_KEY: str = os.getenv("LLM_PROVIDER_DEEPSEEK_API_KEY", "")
LLM_PROVIDER_DASHSCOPE_BASE_URL: str = os.getenv("LLM_PROVIDER_DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
LLM_PROVIDER_DASHSCOPE_API_KEY: str = os.getenv("LLM_PROVIDER_DASHSCOPE_API_KEY", "")
LLM_PROVIDER_OPENAI_BASE_URL: str = os.getenv("LLM_PROVIDER_OPENAI_BASE_URL", "")
LLM_PROVIDER_OPENAI_API_KEY: str = os.getenv("LLM_PROVIDER_OPENAI_API_KEY", "")
LLM_PROVIDER_OPENROUTER_BASE_URL: str = os.getenv("LLM_PROVIDER_OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
LLM_PROVIDER_OPENROUTER_API_KEY: str = os.getenv("LLM_PROVIDER_OPENROUTER_API_KEY", "")
```

- [ ] **Step 4: Implement parser and route builder**

Create dataclasses and route helpers. `parse_route_string` trims whitespace, ignores empty segments, rejects segments without provider/model by skipping them, and preserves order. `build_route(task)` maps task to setting name and falls back to default then legacy.

- [ ] **Step 5: Run route tests**

Run: `python -m unittest backend.tests.test_llm_gateway_service -v`

Expected: PASS for route tests; later tests not present yet.

- [ ] **Step 6: Commit**

```bash
git add backend/app/core/config.py backend/app/services/llm_gateway_service.py backend/tests/test_llm_gateway_service.py
git commit -m "feat: add llm gateway route config"
```

---

### Task 2: Gateway chat/json fallback execution

**Files:**
- Modify: `backend/app/services/llm_gateway_service.py`
- Test: `backend/tests/test_llm_gateway_service.py`

**Interfaces:**
- Produces: `async complete_chat(...) -> LLMResult`
- Produces: `async complete_json(...) -> LLMResult`
- Consumes: `build_route(task)` from Task 1.

- [ ] **Step 1: Write failing fallback tests**

Add fake client classes where `chat.completions.create` is an `AsyncMock`. Test cases:

```python
async def test_complete_json_falls_back_to_next_model_same_provider(self):
    calls = []
    async def fake_create(**kwargs):
        calls.append((kwargs["model"], kwargs))
        if kwargs["model"] == "bad-model":
            raise RuntimeError("rate limit")
        return fake_response('{"ok": true}', model="good-model")

    result = await gateway.complete_json(
        task="default",
        messages=[{"role": "user", "content": "x"}],
        route=[gateway.LLMModelRoute("p1", ["bad-model", "good-model"])],
        client_factory=lambda provider: fake_client(fake_create),
        provider_configs={"p1": gateway.LLMProviderConfig("p1", "key", "http://example")},
    )

    self.assertTrue(result.ok)
    self.assertEqual(result.model, "good-model")
    self.assertEqual([a.model for a in result.attempts], ["bad-model", "good-model"])
```

Also add tests for next Provider fallback, all failed, invalid JSON, missing API key skipped, provider error text in content, and usage extraction.

- [ ] **Step 2: Run failing fallback tests**

Run: `python -m unittest backend.tests.test_llm_gateway_service -v`

Expected: FAIL because `complete_chat` and `complete_json` are missing/incomplete.

- [ ] **Step 3: Implement `complete_chat`**

Implement signature:

```python
async def complete_chat(
    *,
    task: str = "default",
    messages: list[dict[str, str]],
    temperature: float = 0,
    max_tokens: int | None = None,
    response_format: dict[str, Any] | None = None,
    route: list[LLMModelRoute] | None = None,
    provider_configs: dict[str, LLMProviderConfig] | None = None,
    client_factory: Callable[[LLMProviderConfig], Any] | None = None,
) -> LLMResult:
```

Build kwargs for `client.chat.completions.create`, call models in configured order, append one `LLMAttempt` per skipped/failed/success model, classify errors, and return first valid response.

- [ ] **Step 4: Implement `complete_json`**

Call `complete_chat(..., response_format={"type": "json_object"})`, parse `result.content` with `json.loads`, return `ok=False` and continue fallback on invalid JSON. Keep `json_data` populated on success.

- [ ] **Step 5: Run Gateway tests**

Run: `python -m unittest backend.tests.test_llm_gateway_service -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/llm_gateway_service.py backend/tests/test_llm_gateway_service.py
git commit -m "feat: add llm gateway fallback execution"
```

---

### Task 3: Migrate probability analysis and title translation

**Files:**
- Modify: `backend/app/services/probability_engine_service.py`
- Test: `backend/tests/test_ai_analysis_service.py`
- Test: `backend/tests/test_llm_gateway_service.py` only if Gateway behavior needs extra coverage

**Interfaces:**
- Consumes: `complete_json(task="probability_analysis", ...) -> LLMResult`
- Consumes: `complete_chat(task="translation", ...) -> LLMResult`
- Preserves: `_ask_ai(...) -> dict[str, Any]`
- Preserves: `translate_title(question: str) -> str`

- [ ] **Step 1: Write/adjust tests for `_ask_ai` Gateway usage**

Patch `app.services.probability_engine_service.complete_json` to return `LLMResult(ok=True, json_data={...})`; assert `_ask_ai` returns the same parsed dict and includes `_llm_usage` when usage exists.

- [ ] **Step 2: Write/adjust tests for `translate_title` fallback**

Patch `complete_chat` to return `LLMResult(ok=False, ...)`; assert `translate_title("Will X happen?") == "Will X happen?"` truncated to current behavior length.

- [ ] **Step 3: Run targeted tests and confirm failure**

Run: `python -m unittest backend.tests.test_ai_analysis_service -v`

Expected: FAIL where direct OpenAI mocking no longer matches or Gateway functions are not imported.

- [ ] **Step 4: Modify `_ask_ai`**

Import `complete_json`. Replace direct client call with:

```python
result = await complete_json(
    task="probability_analysis",
    messages=[...],
    temperature=0,
)
if not result.ok or result.json_data is None:
    raise RuntimeError(result.degraded_reason or "LLM unavailable")
parsed = dict(result.json_data)
if result.usage is not None:
    parsed["_llm_usage"] = result.usage
return parsed
```

- [ ] **Step 5: Modify `translate_title`**

Import `complete_chat`. Replace direct client call with task `translation`, keep prompt text and current fallback behavior. If `result.ok` is false or content empty/error-like, return `question[:120]`.

- [ ] **Step 6: Run targeted tests**

Run: `python -m unittest backend.tests.test_ai_analysis_service backend.tests.test_llm_gateway_service -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/probability_engine_service.py backend/tests/test_ai_analysis_service.py backend/tests/test_llm_gateway_service.py
git commit -m "feat: route probability analysis through llm gateway"
```

---

### Task 4: Migrate World Cup AI engine

**Files:**
- Modify: `backend/app/services/world_cup_engines/world_cup_ai_engine.py`
- Test: `backend/tests/test_world_cup_ai_engine.py`

**Interfaces:**
- Consumes: `complete_json(task="world_cup", ...) -> LLMResult`
- Preserves: `predict_score_ai(...) -> dict[str, Any] | None`

- [ ] **Step 1: Adjust tests to mock Gateway**

Replace `get_client` patching with `patch("app.services.world_cup_engines.world_cup_ai_engine.complete_json", new=AsyncMock(...))` for valid JSON, invalid JSON, and exception cases.

- [ ] **Step 2: Run targeted tests and confirm failure**

Run: `python -m unittest backend.tests.test_world_cup_ai_engine -v`

Expected: FAIL until implementation imports/uses `complete_json`.

- [ ] **Step 3: Modify `predict_score_ai`**

Replace direct `get_client().chat.completions.create` with:

```python
result = await complete_json(
    task="world_cup",
    messages=[{"role": "user", "content": prompt}],
    temperature=0.3,
)
if not result.ok or result.json_data is None:
    return None
ai_data = result.json_data
```

Keep existing validation, clamping, and return structure.

- [ ] **Step 4: Run targeted tests**

Run: `python -m unittest backend.tests.test_world_cup_ai_engine backend.tests.test_llm_gateway_service -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/world_cup_engines/world_cup_ai_engine.py backend/tests/test_world_cup_ai_engine.py
git commit -m "feat: route world cup ai through llm gateway"
```

---

### Task 5: Verification and completion audit

**Files:**
- No required code changes unless tests reveal regressions.

**Interfaces:**
- Verifies all previous tasks.

- [ ] **Step 1: Run focused backend tests**

Run:

```bash
python -m unittest backend.tests.test_llm_gateway_service backend.tests.test_world_cup_ai_engine backend.tests.test_ai_analysis_service -v
```

Expected: PASS.

- [ ] **Step 2: Compile backend Python**

Run:

```bash
python -m compileall backend/app
```

Expected: PASS with no syntax errors.

- [ ] **Step 3: Inspect fallback-related search results**

Run:

```bash
rg -n "complete_json\(|complete_chat\(|LLM_ROUTE_DEFAULT|LLM_PROVIDER_DEEPSEEK" backend/app backend/tests -S
```

Expected: Gateway config and migrated call sites are visible.

- [ ] **Step 4: Commit fixes if verification required changes**

If files changed during verification:

```bash
git add <specific files>
git commit -m "test: verify llm gateway integrations"
```

- [ ] **Step 5: Final status**

Run: `git status --short`

Expected: clean working tree.
