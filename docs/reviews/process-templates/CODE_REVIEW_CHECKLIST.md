# 代码审查清单 · Review Checklist

> 快速参考清单 — 审查者和开发者通用
>
> 版本: v1.0 | 配合 `CODE_REVIEW_STANDARDS.md` 使用

---

## 开发者自检清单 (Pre-PR)

创建 PR 前逐项确认:

### 通用
- [ ] 无调试代码 (`print()`, `console.log()`, 注释掉的代码)
- [ ] 无硬编码密钥 / token / 密码
- [ ] `.env` 不在变更文件中
- [ ] 本地运行全部测试通过
- [ ] 逻辑分支皆被覆盖（正常路径 + 主要错误路径）

### Python 后端
- [ ] 公开函数有类型标注和 docstring
- [ ] 异常处理非裸 `except:`（至少 `except Exception`）
- [ ] SQLite 查询全部参数化 (`?` 占位符)
- [ ] Pydantic 模型使用 `model_validate()` 非 `**unpacking`
- [ ] 新增 service 有对应测试文件
- [ ] 跨存储写入有原子性保障或补偿方案
- [ ] 涉及 `event_store.py` + `prediction_store.py` 联动 → 架构审查者指定
- [ ] 涉及 LLM 调用 → mock 响应验证过输入构建和输出解析

### TypeScript 前端
- [ ] 组件有 loading / error / empty 三态
- [ ] API 响应有明确类型，非 `any`
- [ ] `useEffect` 依赖数组完整
- [ ] 无 `dangerouslySetInnerHTML`（或已 DOMPurify 消毒）
- [ ] `npm run build` 通过

---

## 审查者检查清单 (Review Time)

### 🔴 P0: 快速致命问题扫描 (5 分钟)

```
□ 密钥/密码/Token 是否被硬编码？
□ SQL 是否使用参数化查询 (非字符串拼接)？
□ 跨存储 (JSON+SQLite) 写入是否有原子性保障？
□ 管道 9 阶段是否有新断裂点？
□ Pydantic 模型是否被 **unpacking 绕过验证？
□ 关键路径是否有 try/except (非裸 except)？
□ dangerouslySetInnerHTML 是否已消毒？
□ API 契约是否有破坏性变更？
```

### 🟡 P1: 质量检查 (15 分钟)

```
□ 新函数是否有返回类型标注？
□ 魔法数字是否定义为常量？
□ 关键操作是否有结构化日志？
□ 错误信息是否包含足够上下文？
□ 是否有 >3 处重复代码？
□ useEffect 依赖数组是否完整？
□ 新 service 是否有测试？
□ 错误路径是否被测试覆盖？
□ 文件 I/O 是否有异常处理？
□ 外部 API 调用是否有超时？
```

### 💭 P2: 优化建议 (时间允许)

```
□ 命名是否清晰准确？
□ 注释是否解释"为什么"而非"做什么"？
□ 函数是否过长 (>100行)？
□ 嵌套是否过深 (>4层)？
□ Tailwind 类名组织是否合理？
□ 是否有替代实现方式更优雅？
```

---

## 数据持久化专项检查

```
□ JSON 写入是否通过 write_json_atomic()？
□ SQLite 连接是否通过 reading() / writing() 上下文管理器？
□ 新增表的 UNIQUE 约束是否与业务逻辑一致？
□ 迁移脚本是否幂等 (可重复执行不出错)？
□ JSON 文件损坏是否有隔离/恢复机制？
□ 读写锁使用是否正确 (reading for SELECT, writing for INSERT/UPDATE/DELETE)？
```

---

## 管道专项检查

```
□ 新阶段输入是否满足前一阶段输出契约？
□ asyncio.gather 的 return_exceptions=True 后是否检查了异常类型？
□ Scheduler 任务是否有重复执行保护？
□ 休眠片段 (Dormant Segments) 是否有晋升路径？
□ 外部 API 失败是否不会阻塞主流程？
```

---

## 高风险变更额外检查

以下变更类型触发额外审查:

### 数据迁移
- [ ] 迁移脚本有 dry-run 模式
- [ ] 迁移脚本有回滚指令
- [ ] 在测试数据上验证过
- [ ] JSON 文件有备份步骤
- [ ] 架构审查者已参与

### LLM 调用路径
- [ ] Prompt 变更是否记录了效果对比？
- [ ] 输出解析是否有格式变化的容错？
- [ ] 有 mock 测试覆盖新的 prompt 模板？

### API 路由
- [ ] 参数验证是否完整 (类型、范围、必填)？
- [ ] 错误响应格式是否一致？
- [ ] 是否影响前端 API 客户端 (`lib/api.ts`)？

### 配置新增
- [ ] `.env.example` 已更新
- [ ] 有默认值
- [ ] 名称与现有命名一致
- [ ] 已在 `Settings` 类注册

---

## 审查完成确认

```
□ 所有 P0 已解决
□ 所有 P1 已修复或有跟踪 Issue
□ 审查总结已写在 PR 评论中
□ 对好的代码/设计给予了肯定
□ PR 状态已更新 (Approve / Request Changes)
```

---

> **使用说明**: 
> - **开发者**: 创建 PR 前自检，聚焦 P0 和 P1 项
> - **审查者**: 审查时逐领域检查，P0 必须全部覆盖，P1 尽量覆盖
> - **打印友好**: 本清单可直接打印作为纸质检查表
