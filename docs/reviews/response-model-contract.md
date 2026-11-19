# 响应模型契约检查清单（`MEMORY.md` §2 的长文）

> 这是 `MEMORY.md` §2 的**长文版**：注入版只留一句判据，逐条检查动作在这里。
>
> 🔴 **适用前提：端点返回 dict。** handler 返回 `Response` 实例时 `response_model` 校验是 no-op；
> `include_in_schema=False` 时根本没有 schema（§Z16）。两条任一不成立，本页全部不适用。
>
> 三件套流程（`models/event.py` 加 `XxxResponse` → 登记进 `_frontend_export.py` → `generate_types` 重生成，
> 前端 re-export **不手写 interface**；**第 4 步：模型必须登记为「根」才闭合**——pydantic2ts 收命名空间导入的类，
> `__all__` 不参与；**第 5 检查点：具名前先看 `response_model` 是不是裸 `FlexibleResponse`**）见 `MEMORY.md` §2。
>
> 取证：`docs/reviews/system-health-audit-2026-09-26.md` 的 §Z2–§Z40。

## ① 默认值：镜像 NOT NULL 列的不给默认值

- 带默认值的字段，生成的 TS 就是**可选**的 ⇒ 线上漏字段会**静默**落成默认值。镜像 NOT NULL 列**不给默认值**，
  响亮的 422 才是信号。
- 「必存但可以为 null」这种列，生成的 TS **表达不了** ⇒ **放宽接收端 prop**，不要在全仓撒 `?? null`
  ——那是把「不知道」和「没有」混成一个值。

**检查动作**：`generate_types` 之后回读生成的 interface，确认该字段没有变成 `?:`。

## ② 可选性：能静态钉，就别推理

- 忠实类型看**读法**，不看名字。
- **别把 DDL 当保证**：先跑探针问引擎（`PRAGMA table_info` / `is_required()`）。
- **别从同族模型互推**：同族两个模型可以有两种形状（§Z3 实测）。

**检查动作**：`Model.model_fields["x"].is_required()`。

## ③ 字段序

模型字段序 = 线上键序 = 生产者 dict 的**插入序**（§Z21）。改字段顺序会改 JSON 键序，按位置读的下游会漂移。

## ④ 旗子 `response_model_exclude_unset`

- **条件写键**（有时写、有时不写）必须：字段**带默认值** + 装饰器**带旗**。
- **无条件写**的键：**不需要旗**，而且**任何默认值都错**——给了默认值，漏写就静默成默认值。
- 「X 不需要旗」这类结论要连前提一起钉（`is False`），否则别人加了旗它就静默过期（§Z11）。

**检查动作**：具名前先**读装饰器**有没有旗。

## ⑤ 去键用 `Pick`，不用 `Omit`

`Omit<T, K>` 对**开放索引签名**的 interface 会塌成 `{[k: string]: unknown}`，把整个类型的信息丢掉（§Z28）。

## ⑥ 「开放」的三成因别混

| 成因 | 表现 | 收口方式 |
|---|---|---|
| (a) `extra="allow"` 故意开放 | 有意的透传 | `DELIBERATELY_OPEN` **钉集合相等** |
| (b) 纯嵌套 | 只登记了被嵌套的类 | **登记根**即闭合 |
| (c) 裸 `dict[str, Any]` | 字段没具名 | **具名 + 登记为根** |

**判尾巴**：接口体最后一行非空行恰是 `  [k: string]: unknown;`（两个空格缩进、分号结尾）。

## 附：消费侧三件事

- 手写模块**不得声明生成物已有的副本**；`res.json() as T` 是**零主张**——任何形状都通过。
- 守卫必须对该改动敏感；**N 条判据 ⇒ N 条变异**。
- ⚠️ `tsconfig` 排除测试文件 ⇒ `tsc` 全绿**不含测试**；int/float 漂移是整条线（pydantic 重序列化）；
  透传 ISO 串要声明 `str` 而不是 `datetime`。
