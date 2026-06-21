# 代码审查标准 · Code Review Standards

> Prediction Market Reality Filter 项目代码审查标准
> 
> 版本: v1.0 | 生效日期: 2026-06-20 | 强制范围: 全栈 (Backend + Frontend)

---

## 目录

1. [审查哲学](#1-审查哲学)
2. [优先级定义](#2-优先级定义)
3. [后端审查标准 (Python/FastAPI)](#3-后端审查标准)
4. [前端审查标准 (TypeScript/Next.js/React)](#4-前端审查标准)
5. [数据持久化审查标准](#5-数据持久化审查标准)
6. [管道与集成审查标准](#6-管道与集成审查标准)
7. [通用审查标准](#7-通用审查标准)

---

## 1. 审查哲学

### 核心理念

> **审查是教学，不是批斗。每条意见都应教会作者一个原则。**

| 原则 | 含义 |
|------|------|
| **Reality First** | 代码最终服务于"现实是唯一真相源"的架构哲学。审查时优先关注数据完整性和正确性。 |
| **Fail-Closed** | 不确定时选择拒绝而非接受。不做有损数据完整性的妥协。 |
| **Isolation by Design** | 单点故障不得拖垮全链路。并发操作必须隔离。 |
| **可观测性优先** | 生产级代码必须可追踪、可诊断、可恢复。 |

### 审查者行为准则

1. **具体而非笼统** — 说"第42行可能存在SQL注入"而非"安全问题"
2. **解释为什么** — 不只说改什么，要解释原因
3. **建议而非命令** — "考虑用X因为Y"而非"改成X"
4. **分级标注** — 每条意见必须标注优先级 (🔴/🟡/💭)
5. **表扬好的代码** — 巧妙的方案和干净的代码值得明确肯定
6. **一次审查，完整反馈** — 不分多次逐条发送意见

---

## 2. 优先级定义

### 🔴 P0 · Blocker — 必须修复，阻塞合并

**定义**: 合并后会导致数据丢失、安全漏洞、系统崩溃或不可逆损坏。

| 类别 | 典型场景 |
|------|----------|
| **数据安全** | 跨存储写入无原子性、数据静默丢失、SQL注入、敏感信息泄漏 |
| **正确性** | 概率计算错误、管道断点、边界条件遗漏导致错误结果 |
| **崩溃风险** | 未捕获异常导致进程退出、无限循环、内存泄漏 |
| **破坏性变更** | API契约不兼容、数据模型schema破坏性迁移 |

### 🟡 P1 · Suggestion — 应当修复，不阻塞合并但需Issue跟踪

**定义**: 影响可维护性、可观测性或性能，但不会立即导致故障。

| 类别 | 典型场景 |
|------|----------|
| **可观测性** | 关键路径缺少日志、无trace_id、错误信息不明确 |
| **可维护性** | 过度耦合、代码重复 >3处、命名误导、魔法数字 |
| **性能** | N+1查询、不必要的大对象分配、I/O未批处理 |
| **测试缺口** | 核心逻辑无测试、边界条件未覆盖、错误路径无验证 |
| **类型安全** | `dict[str, Any]` 缺乏 TypedDict 约束、缺少返回类型标注 |

### 💭 P2 · Nit — 优化建议，非阻塞

| 类别 | 典型场景 |
|------|----------|
| **命名优化** | 变量/函数名可更清晰 |
| **文档补充** | docstring 可更详细 |
| **代码风格** | 与项目风格不一致 (如 linter 已覆盖则跳过) |
| **替代方案** | 有更优雅的实现方式但当前方案也可用 |

---

## 3. 后端审查标准 (Python/FastAPI)

### 3.1 🔴 P0 检查项

#### SEC-001: SQL注入防护

```python
# ❌ 致命 — 字符串拼接
query = f"SELECT * FROM predictions WHERE event_id = '{event_id}'"

# ✅ 正确 — 参数化查询 (本项目标准)
db.reading("SELECT * FROM predictions WHERE event_id = ?", (event_id,))
```

**检查要点**:
- 所有 SQLite 查询必须使用 `?` 占位符 + 参数元组
- 动态表名/列名需用白名单校验
- **无需额外检查**: 本项目已统一使用参数化查询，此条为强制回归检查

#### SEC-002: 配置与密钥安全

```python
# ❌ 致命 — 硬编码密钥
OPENAI_API_KEY = "sk-abc123..."

# ✅ 正确
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
```

**检查要点**:
- 零硬编码密钥容忍度
- 配置项必须通过 `Settings` 类 (`backend/app/core/config.py`) 集中管理
- `.env` 不得提交，`.env.example` 不含真实密钥
- 日志不得输出 API Key

#### DATA-001: 跨存储写入原子性

本项目的核心风险点：**`resolve_with_calibration()` 依次写 JSON → JSONL → SQLite，中间失败产生永久不一致。**

```python
# ❌ 致命 — 多存储无事务协调
event_store.save(event)       # 成功 → JSON 已更新
audit_store.write(entry)      # 失败 → JSONL 未记录，永久失控
prediction_store.freeze(p)    # 失败 → SQLite 未更新

# ✅ 要求 — 至少满足以下之一:
# 方案A: 原子标记 + 补偿 (推荐)
#  1. 先在 SQLite 写一个 pending 标记
#  2. 写 JSON + JSONL  
#  3. 更新 SQLite 标记为 committed
#  4. 启动时扫描 pending 记录执行补偿
#
# 方案B: 全或无 + 回滚
#  将所有副作用收集，最后一次性提交; 任何步骤失败全部回滚
```

**检查要点**:
- 任何跨越 JSON+SQLite 边界的写入必须有原子性保障
- 不允许"部分成功"的静默状态
- `event_store.py` (JSON) 和 `prediction_store.py` (SQLite) 的联动写操作必须审查

#### DATA-002: 数据合并策略正确性

```python
# ❌ 致命 — 同event_id再次出现时覆盖已有预测
# 本项目的 first-sight 策略: ON CONFLICT DO NOTHING
# 必须确保不会丢失校准数据

# 审查要点:
# 1. 合并策略是否明确文档化？
# 2. 是否会静默丢弃重要字段？
# 3. 是否存在 UNIQUE 约束缺失导致的重复记录？
```

#### PIPE-001: 管道断点 (Loop Breakpoints)

本项目核心流程的 9 个阶段必须全部联通：

```
Scheduler → Discover → Event → Market Link → Freeze Prediction 
  → Resolve Outcome → Calibration → Trust → Decision Report → [反馈]
```

**检查要点**:
- 每个阶段的输出是否满足下一阶段的输入契约？
- 异常情况下的降级路径是否明确？
- 休眠片段 (Dormant Segments) 是否有晋升机制？

#### ERR-001: 关键路径错误处理

```python
# ❌ 致命 — 裸 except 吞掉所有异常  
try:
    critical_operation()
except:
    pass

# ❌ 致命 — 关键路径无错误处理
def resolve_event(event_id):
    outcome = fetch_from_polymarket(event_id)  # 网络可能失败
    save(outcome)  # 磁盘可能满
    
# ✅ 要求
def resolve_event(event_id):
    try:
        outcome = fetch_from_polymarket(event_id)
    except NetworkError as e:
        logger.error("Polymarket fetch failed: %s", e)
        raise ResolutionError(f"Failed to resolve {event_id}") from e
    save(outcome)
```

#### ERR-002: Pydantic验证旁路

```python
# ❌ 致命 — 绕过模型验证直接构造
event = EventRecord(**untrusted_dict)  # 危险: 依赖 ** 展开

# ✅ 要求 — 显式验证
event = EventRecord.model_validate(untrusted_dict)
```

### 3.2 🟡 P1 检查项

#### TYP-001: 类型标注完整性

```python
# ❌ 应修复 — 缺少返回类型
def analyze_event(event_id, context):
    return {"probability": 0.75, "confidence": "high"}

# ✅ 建议
from typing import TypedDict

class AnalysisResult(TypedDict):
    probability: float
    confidence: str

def analyze_event(event_id: str, context: dict[str, Any]) -> AnalysisResult:
    ...
```

**检查要点**:
- 公开函数必须有返回类型标注
- `dict[str, Any]` 在内部 API 边界处应替换为 `TypedDict` 或 Pydantic 模型
- 建议在 CI 中启用 `mypy --strict` 逐步推行 (当前为建议，非强制)

#### OBS-001: 日志与可观测性

```python
# ❌ 应修复 — 无结构化日志
print(f"Event {event_id} resolved")

# ✅ 建议
logger.info("event_resolved event_id=%s outcome=%s duration_ms=%d", 
            event_id, outcome, duration_ms)
```

**检查要点**:
- 使用 `logger.info/exc_info=True` 而非 `print()`
- 关键操作记录开始/结束/耗时
- 日志包含足够的上下文用于故障排查
- 建议: 为 scheduler 每次运行生成 `run_id` 贯穿全链路

#### TST-001: 测试覆盖要求

| 代码类型 | 最小测试要求 |
|----------|-------------|
| 纯函数/数学工具 (如 `calibration`, `brier_score`) | **必须**: 边界值 + 典型值 + 异常输入 |
| Service 模块 | **必须**: mock 外部依赖，覆盖正常路径 + 主要错误路径 |
| API 路由 | **建议**: FastAPI TestClient 集成测试 |
| LLM 调用路径 | **必须**: mock LLM 响应，验证输入构建和输出解析逻辑 |

```python
# 审查时检查:
# 1. 新 service 是否有对应测试？
# 2. 错误路径是否被覆盖？
# 3. 边界条件: 空输入、极值、None 是否处理？
```

#### DUP-001: 代码重复

```python
# 审查要点 — 出现 3 次以上的相同逻辑必须提取为共享函数
# 典型需要提取的模式:
# - safe_float / _clamp01 调用链
# - text_match 预处理逻辑
# - 错误日志格式化模式
```

### 3.3 💭 P2 检查项

#### DOC-001: 文档完整性

- 公开函数应有 docstring (Google 或 NumPy 风格)
- 复杂算法应有行内注释解释"为什么"而非"做什么"
- 架构决策应在 `docs/` 目录有对应的 ADR (Architecture Decision Record)

#### NAM-001: 命名约定

```python
# 💭 可改进
def process(d): ...           # 函数名不明确
events = get()                # 变量名太泛

# ✅ 建议
def resolve_event_outcome(event_id: str) -> Outcome: ...
active_events = event_store.get_active()
```

---

## 4. 前端审查标准 (TypeScript/Next.js/React)

### 4.1 🔴 P0 检查项

#### FE-SEC-001: XSS 防护

```tsx
// ❌ 致命 — dangerouslySetInnerHTML 未消毒
<div dangerouslySetInnerHTML={{ __html: userInput }} />

// ✅ 要求 — 如需使用，必须经过 DOMPurify 消毒
import DOMPurify from 'dompurify';
<div dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(userInput) }} />
```

#### FE-DATA-001: API 响应类型安全

```typescript
// ❌ 致命 — 无类型约束
const data = await fetch('/api/events').then(r => r.json());
// data 类型为 any，属性访问无编译期保护

// ✅ 要求
import { EventRecord } from '@/lib/types';
const data: EventRecord[] = await eventsApi.list();
```

**检查要点**:
- `types.ts` 中的类型定义是否与后端 Pydantic 模型保持一致？
- 是否存在 `any` 类型的 API 响应？

#### FE-ERR-001: 错误状态管理

```tsx
// ❌ 致命 — 未处理加载和错误状态
function EventList() {
  const [events, setEvents] = useState([]);
  useEffect(() => {
    eventsApi.list().then(setEvents); // 网络失败静默吞掉
  }, []);
  return <Table data={events} />;
}

// ✅ 要求 — 必须有 loading / error / empty 三态处理
function EventList() {
  const [events, setEvents] = useState<EventRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // ... 完整的三态逻辑
}
```

### 4.2 🟡 P1 检查项

#### FE-PERF-001: 不必要的重新渲染

```tsx
// 🟡 应修复 — 每次渲染都创建新回调
<button onClick={() => handleClick(event.id)}>Click</button>

// ✅ 建议 — 使用 useCallback 稳定引用
const handleClick = useCallback((id: string) => { ... }, []);
```

#### FE-DEP-001: useEffect 依赖完整性

```tsx
// 🟡 应修复 — 缺少依赖
useEffect(() => {
  fetchData(eventId);  // eventId 变化时不会重新获取
}, []);  // 缺少 eventId 依赖

// ✅ 建议 — 完整依赖数组
useEffect(() => {
  fetchData(eventId);
}, [eventId, fetchData]);
```

#### FE-TST-001: 前端测试

**当前状态**: 前端无测试。建议从以下开始:

| 优先级 | 测试类型 | 覆盖目标 |
|--------|----------|----------|
| 高 | 工具函数 (`lib/format.ts`, `lib/adapt.ts`) | Vitest 单元测试 |
| 中 | API 客户端 mock 测试 (`lib/api.ts`) | msw mock |
| 低 | 组件交互测试 | React Testing Library |

### 4.3 💭 P2 检查项

- Tailwind 类名是否使用 `clsx` / `tailwind-merge` 正确组合？
- 组件是否过大 (>200行) 需要拆分？
- 是否有未使用的 import 或 dead code？

---

## 5. 数据持久化审查标准

本项目的核心特征: **双层持久化 (JSON + SQLite)**。审查标准必须覆盖这种架构的特殊风险。

### 5.1 🔴 P0 检查项

#### STORE-001: JSON 原子写入合规

```python
# 审查: 所有 JSON 写入是否通过 write_json_atomic？
# write_json_atomic 保证: temp_file + os.replace = 原子替换

# 检查路径: 所有 event_store.py 和 file_store.py 的写入调用
```

#### STORE-002: SQLite 连接管理合规

```python
# 审查: 是否使用上下文管理器获取连接？
# ❌ 裸连接 — 可能泄漏
conn = sqlite3.connect(DB_PATH)

# ✅ 通过 with_db 获取
with get_db().writing() as db:
    db.execute(...)
```

#### STORE-003: 读写锁正确使用

```python
# 审查要点:
# - reading() 用于 SELECT
# - writing() 用于 INSERT/UPDATE/DELETE
# - 混合读写在同一个 writing() 上下文中完成
# - 跨存储操作是否有补偿机制？
```

### 5.2 🟡 P1 检查项

#### STORE-004: 迁移兼容性

```python
# 审查: schema 变更时是否更新了 _MIGRATIONS 字典？
# 审查: 迁移是否可重复安全执行 (idempotent)？

_MIGRATIONS = {
    1: "ALTER TABLE predictions ADD COLUMN calibration_score REAL",
    # 新增迁移项时确认:
    # 1. 使用 ALTER TABLE (SQLite 限制)  
    # 2. 不破坏已有数据
    # 3. 有对应的 PRAGMA user_version 递增
}
```

#### STORE-005: 数据完整性约束

```python
# 审查: 
# 1. UNIQUE 约束是否正确设置？
# 2. FOREIGN KEY 是否启用？（本项目已启用: foreign_keys=ON）
# 3. NOT NULL 约束是否合理？
```

---

## 6. 管道与集成审查标准

### 6.1 🔴 P0 检查项

#### PIPE-002: 并行隔离正确性

本项目广泛使用 `asyncio.gather(return_exceptions=True)`:

```python
# 审查要点:
# 1. 并发任务之间是否真正独立？
# 2. return_exceptions=True 后是否检查了异常类型？
# 3. 部分失败后整体结果是否正确聚合？

results = await asyncio.gather(
    fetch_polymarket(),
    fetch_manifold(),
    fetch_kalshi(),
    return_exceptions=True
)

# ✅ 要求: 区分异常和有效结果
valid_results = [r for r in results if not isinstance(r, Exception)]
exceptions = [r for r in results if isinstance(r, Exception)]
if exceptions:
    logger.warning("Some sources failed: %s", exceptions)
```

#### PIPE-003: 调度器可靠性

```python
# 已知风险 (来自5份AI审计报告):
# APScheduler 在进程内, 无持久化 job store
# 错过运行无法补跑
# 多 worker 可能重复触发

# 审查时关注:
# 1. 是否有防止重复执行的机制？
# 2. 错过运行后是否有补偿逻辑？
# 3. scheduler 启动/停止是否正确管理？
```

### 6.2 🟡 P1 检查项

- 外部 API 调用是否有超时设置？
- LLM 调用失败后是否有回退策略？
- 异步任务是否有合理的并发限制？

---

## 7. 通用审查标准

### 7.1 🔴 P0: 全局强制

| 检查项 | 说明 |
|--------|------|
| **无 exec/eval/pickle** | 项目中完全禁止 |
| **无硬编码密钥** | API Key, Token, Password 必须通过环境变量 |
| **无裸 except** | 至少指定 `except Exception`，关键路径需精确异常类型 |
| **无未处理的文件I/O错误** | 文件操作必须在 try/except 内 |
| **API契约兼容** | 公共 API (尤其是 `/api/` 路由) 不得做破坏性变更 |

### 7.2 🟡 P1: 应当修复

| 检查项 | 说明 |
|--------|------|
| **魔法数字** | 阈值、超时、限制值必须命名常量 |
| **函数长度** | >100行考虑拆分 |
| **嵌套深度** | >4层嵌套考虑提取 |
| **循环依赖** | 模块间不得形成循环导入 |
| **import 顺序** | 标准库 → 第三方 → 项目内部 |

### 7.3 💭 P2: 优化建议

| 检查项 | 说明 |
|--------|------|
| **注释质量** | 代码解释"为什么"而非"做什么" |
| **变量命名** | 避免单字母 (除非循环变量) |
| **文档到位** | 公开接口有 docstring |

---

## 附录 A: 快速参考表

### 后端高频检查 (15秒扫描)

```
□ 参数化查询 (?, ?) 非字符串拼接
□ 跨存储写入有原子性保障
□ 关键路径有 try/except + logger
□ except 非裸 (至少 Exception)
□ 无硬编码密钥
□ 管道 9 阶段连通性
□ Pydantic model_validate() 非 **unpacking
```

### 前端高频检查 (15秒扫描)

```
□ 无 dangerouslySetInnerHTML (或已消毒)
□ API 响应有类型约束 (非 any)
□ 组件有 loading/error/empty 三态
□ useEffect 依赖数组完整
□ 无未处理的 Promise rejection
```

### 数据层高频检查

```
□ SQLite 写入通过 writing() 上下文
□ JSON 写入通过 write_json_atomic
□ 迁移版本递增且幂等
□ UNIQUE 约束与业务逻辑一致
```

---

## 附录 B: 优先级决策矩阵

| 场景 | 级别 |
|------|------|
| 数据可能丢失或损坏 | 🔴 P0 |
| 安全漏洞 (注入/XSS/泄漏) | 🔴 P0 |
| 进程可能崩溃 | 🔴 P0 |
| API 破坏性变更 | 🔴 P0 |
| 管道功能断裂 | 🔴 P0 |
| 关键路径无错误处理 | 🔴 P0 |
| 缺少日志/监控 | 🟡 P1 |
| 代码重复 >3处 | 🟡 P1 |
| 缺少类型标注 | 🟡 P1 |
| 测试缺口 | 🟡 P1 |
| N+1 查询/性能问题 | 🟡 P1 |
| 命名可改进 | 💭 P2 |
| 注释可更详细 | 💭 P2 |
| 风格不一致 | 💭 P2 |

---

> **维护声明**: 本标准随项目演进持续更新。触发更新的条件:
> - 发现新的安全漏洞模式
> - 引入新技术栈
> - 架构发生重大变更
> - 团队反馈审查标准需调整
> 
> 审查者: Code Review Expert (expert:agents) | 由代码审查专家维护
