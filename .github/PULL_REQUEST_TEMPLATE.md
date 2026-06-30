## 变更概述

<!-- 用1-2句话描述这个PR做了什么，为什么需要做 -->

## 变更类型

<!-- 勾选适用的类型 -->

- [ ] 🐛 Bug 修复
- [ ] ✨ 新功能
- [ ] ♻️ 重构
- [ ] 📊 数据迁移
- [ ] 🔧 配置变更
- [ ] 📝 文档
- [ ] 🚨 紧急修复 (Hotfix)
- [ ] 🧪 测试

## 影响范围

<!-- 标记受影响的模块 -->

**后端**:
- [ ] 管道编排 (`event_intelligence_service.py`)
- [ ] 数据持久化 (`event_store.py` / `prediction_store.py`)
- [ ] 校准/结算 (`calibration_*` / `event_resolve_service.py`)
- [ ] LLM 调用 (`probability_engine_service.py` / `ai_analysis_service.py`)
- [ ] API 路由 (`routes/`)
- [ ] 数据模型 (`models/`)
- [ ] 配置 (`config.py`)
- [ ] 新闻/事件源 (`news_filter_service.py` / `*_event_source.py`)
- [ ] 其他服务 (请说明): _____________

**前端**:
- [ ] Dashboard
- [ ] 事件列表/详情
- [ ] 决策报告
- [ ] 历史复盘
- [ ] API 客户端 (`lib/api.ts`)
- [ ] 类型定义 (`lib/types.ts`)
- [ ] 组件库
- [ ] 样式
- [ ] 其他 (请说明): _____________

## 自检清单

<!-- PR 创建前请逐项确认 -->

### 通用
- [ ] 无调试代码 (`print` / `console.log` / 注释掉代码)
- [ ] 无硬编码密钥 / token / 密码
- [ ] `.env` 未包含在 PR 中
- [ ] 本地运行全部测试通过

### Python 后端 (如适用)
- [ ] 公开函数有类型标注
- [ ] SQLite 查询全部参数化 (`?` 占位符)
- [ ] Pydantic 模型使用 `model_validate()`
- [ ] 异常处理非裸 `except:`
- [ ] 新增 service 有对应测试
- [ ] 跨存储写入有原子性保障 (如适用)

### TypeScript 前端 (如适用)
- [ ] 组件有 loading / error / empty 三态 (如涉及数据获取)
- [ ] API 响应有类型约束
- [ ] `useEffect` 依赖数组完整
- [ ] `npm run build` 通过

### 特殊变更 (如适用)
- [ ] **数据迁移**: 有 dry-run 模式 + 回滚方案
- [ ] **API 变更**: 前后端类型对齐，不破坏现有契约
- [ ] **LLM Prompt 变更**: 有 mock 测试 + 效果对比说明

## 测试说明

<!-- 描述如何验证这个PR -->

- [ ] 新增测试覆盖: _____________
- [ ] 手工验证步骤:
  1. 
  2. 
  3. 

## 相关 Issue

<!-- 使用 "Closes #N" 或 "Relates to #N" -->

Closes #

## 补充说明

<!-- 架构决策、已知限制、后续工作等 -->
