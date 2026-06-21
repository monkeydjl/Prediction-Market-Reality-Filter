# Probability Watch 前端系统审查报告

**审查日期**: 2025-06-20  
**审查范围**: `frontend/` 完整代码库  
**技术栈**: Next.js 16 + React 19 + TypeScript + Tailwind CSS v4 + Recharts

---

## 一、项目概览

| 维度 | 当前状态 |
|------|---------|
| 框架 | Next.js 16 App Router, React 19 |
| 构建 | dev: Next.js server + proxy; prod: 静态导出 (SSG) |
| 样式 | Tailwind CSS v4, OKLCH 暗色主题 |
| 状态管理 | 纯 React (useState/useEffect)，无全局状态库 |
| 类型安全 | TypeScript strict mode, 完整类型覆盖 |
| API 层 | 统一 fetch 封装, 中文错误消息, 13 个端点 |
| 测试 | **无测试框架、无测试文件、无 test 脚本** |
| 无障碍 | 基础支持 (aria-hidden, 语义 HTML, reduced-motion) |

---

## 二、需要修复的问题 (Bug / 缺陷)

### 🔴 P0 — 严重

#### 1. 完全缺失测试基础设施
- **问题**: 无任何测试框架 (Jest/Vitest)、无测试文件、`package.json` 无 `test` 脚本
- **风险**: 重构或新增功能时无回归保护，生产环境可能引入严重回归
- **修复建议**: 引入 Vitest + @testing-library/react，为核心组件、API 层、adapt 层编写单元测试

#### 2. `api.ts` — Content-Type 头会被调用方覆盖
- **文件**: `src/lib/api.ts:37-40`
- **问题**: `fetch(BASE + path, { headers: { "Content-Type": "application/json" }, ...init })`  
  如果 `init` 包含任何自定义 `headers`，整个 headers 对象会被替换，导致 `Content-Type` 丢失。
  POST 请求（如 `analyze`, `setTracking`）可能在特定场景下发送错误的 Content-Type。
- **修复建议**: 合并 headers 而非替换：
  ```typescript
  headers: { "Content-Type": "application/json", ...init?.headers },
  ```

#### 3. `api.ts` — 缺少网络层错误处理
- **文件**: `src/lib/api.ts:36-46`
- **问题**: `fetch()` 在网络不可用、DNS 失败、CORS 错误时直接 throw，不做任何处理  
  用户会看到浏览器原生错误 "Failed to fetch" 而非友好的中文提示
- **修复建议**: 在 `api()` 中添加 try/catch 包裹 fetch，统一返回中文错误

---

### 🟡 P1 — 中等

#### 4. 事件列表无分页 — 数据量增长后性能劣化
- **文件**: `src/app/page.tsx` (list 100条), `src/app/history/page.tsx` (list 200条)
- **问题**: 一次性加载全部事件，无分页/无限滚动。事件数量超过 500+ 时渲染性能会明显下降
- **修复建议**: 
  - 后端添加分页参数 (offset/limit)
  - 前端引入虚拟列表 (TanStack Virtual) 或服务端分页

#### 5. 所有页面导航都重新加载数据 — 无缓存层
- **文件**: 所有 page.tsx
- **问题**: 每次从 `/events` 回退到 `/` 都会重新请求全量数据，即使数据未变化  
  用户体验差（始终显示加载态），后端压力大
- **修复建议**: 引入 SWR 或 TanStack Query 实现客户端缓存和请求去重

#### 6. `analyze` 页 — 基准概率空值校验逻辑缺陷
- **文件**: `src/app/analyze/page.tsx:29-32`
- **问题**: `!Number.isFinite(baselineValue) || baseline.trim() === ""`  
  `Number("")` 返回 `0` 是有限值，第二个条件永远不会触发。
  用户清空输入框点击提交时不会得到错误提示，而是以 0 作为基准发送。
- **修复建议**: 先检查 `baseline.trim() === ""`，再检查数值范围

#### 7. decisions 页 — 重复的 loading 状态管理
- **文件**: `src/app/decisions/page.tsx:34-43`
- **问题**: `refresh()` 设置 `setLoading(true)`，但 `load()` 内部不管理 loading 状态  
  如果 `load()` 快速完成而 `refresh()` 尚未执行 finally，状态可能出现不一致
- **修复建议**: 统一由 `load()` 管理 loading 状态，`refresh` 只做 try/catch 包裹

#### 8. 缺少 `not-found.tsx` — 无效路由无用户提示
- **文件**: 不存在 `src/app/not-found.tsx`
- **问题**: 访问 `/random-route` 等不存在的路径时，Next.js 显示默认 404 页面，不符合品牌风格
- **修复建议**: 创建 `src/app/not-found.tsx`，复用 AppNav + 错误页设计风格

---

### 🔵 P2 — 低优先级

#### 9. 全局错误边界仅有一层 — 局部崩溃会导致整页刷新
- **文件**: `src/app/error.tsx`
- **问题**: 如果 MoversBoard 或 EventTable 等非关键组件出错，整个 Dashboard 页面都会显示错误  
  用户无法查看其他仍有价值的数据
- **修复建议**: 为每个独立 section 包裹 ErrorBoundary

#### 10. Sparkline SVG 缺少无障碍描述
- **文件**: `src/components/sparkline.tsx:41-46`
- **问题**: SVG 标记了 `aria-hidden="true"`，屏幕阅读器用户获取不到趋势信息
- **修复建议**: 添加 `<title>` 和 `<desc>` 元素描述数据趋势

#### 11. 暗色主题下无亮色模式支持
- **文件**: `src/app/globals.css`
- **问题**: 所有颜色变量在 `:root` 直接定义为暗色值，无法切换到亮色模式  
  对于在明亮环境使用或偏好亮色主题的用户不友好
- **修复建议**: 添加 `.light` 类或 media query 支持

#### 12. 无环境变量校验
- **问题**: `NEXT_PUBLIC_API_BASE` 如果错误配置会导致所有 API 请求失败，但无启动时校验
- **修复建议**: 添加构建时环境变量校验

---

## 三、建议增加的功能模块

### ⭐ 高优先级

| 序号 | 模块 | 说明 | 依赖 |
|------|------|------|------|
| 1 | **数据获取层 (SWR/TanStack Query)** | 缓存、去重、自动重试、后台刷新 | 无 |
| 2 | **测试框架 (Vitest + RTL)** | 核心组件和工具函数的单元测试 | 无 |
| 3 | **Skeleton 加载态** | 代替纯文本"加载中…"，提升感知性能 | @/components/ui |
| 4 | **Toast 通知系统** | 操作成功/失败的非阻塞提示（跟踪决策等场景） | sonner 或自研 |
| 5 | **分页组件** | 事件列表超过 50 条时的分页或虚拟滚动 | TanStack Virtual |
| 6 | **组件级 ErrorBoundary** | 保护页面局部不受单个组件崩溃影响 | React ErrorBoundary |

### ⭐ 中优先级

| 序号 | 模块 | 说明 |
|------|------|------|
| 7 | **not-found.tsx** | 自定义 404 页面 |
| 8 | **亮/暗主题切换** | 基于 CSS 变量，支持用户偏好和手动切换 |
| 9 | **页面过渡动画** | View Transitions API 或 framer-motion |
| 10 | **Web Vitals 监控** | 集成 `web-vitals` 库，上报 LCP/FID/CLS |
| 11 | **自动重试机制** | API 层对 503/网络错误自动重试 2-3 次 |
| 12 | **PWA 离线支持** | Service Worker + manifest.json，离线可查看缓存数据 |

### ⭐ 低优先级

| 序号 | 模块 | 说明 |
|------|------|------|
| 13 | **搜索过滤增强** | 事件标题的全文搜索（当前仅支持领域和状态筛选） |
| 14 | **键盘快捷键** | 如 `Ctrl+K` 快速搜索、`Escape` 关闭模态框 |
| 15 | **数据导出** | 将事件列表/复盘数据导出为 CSV/JSON |
| 16 | **深色模式打印样式** | 打印时自动切换为亮色主题 |

---

## 四、代码质量评估

| 维度 | 评分 | 说明 |
|------|------|------|
| 类型安全 | ⭐⭐⭐⭐⭐ | strict mode, 完整类型覆盖, 每个页面/组件都有明确类型 |
| 错误处理 | ⭐⭐⭐⭐ | 每个页面都有 try/catch，有加载/错误/空三态 UI，有 cancel 保护 |
| API 层设计 | ⭐⭐⭐⭐ | 统一封装，中文错误映射，端点定义清晰 |
| 组件复用 | ⭐⭐⭐⭐ | indicators 组件设计精巧，adapt 层解耦好 |
| 无障碍 | ⭐⭐⭐ | 有基础支持但不够完整，无 ARIA role，无 skip link |
| 性能优化 | ⭐⭐⭐ | 无虚拟列表，无数据缓存，无代码分割 |
| 可测试性 | ⭐ | 无任何测试基础设施 |
| 文档 | ⭐⭐ | 代码内注释较好，但无组件文档 |

**总体评分**: 3.3/5

---

## 五、建议修复优先级路线图

### 第一阶段（本周）
1. 修复 `api.ts` Content-Type 头覆盖问题（1行改）
2. 修复 `analyze` 页空值校验逻辑（3行改）
3. 添加 `not-found.tsx`

### 第二阶段（下周）
4. 引入 Vitest + 为核心工具函数编写测试
5. 引入 TanStack Query 替换手动 fetch 管理
6. 实现 Skeleton 加载态

### 第三阶段（两周内）
7. 事件列表分页/虚拟滚动
8. Toast 通知系统
9. 组件级 ErrorBoundary

### 第四阶段（一个月内）
10. PWA 离线支持
11. 亮色主题切换
12. Web Vitals 监控

---

*审查人: Frontend Developer Agent*  
*基于对 frontend/ 目录下 15 个源文件的完整审查*
