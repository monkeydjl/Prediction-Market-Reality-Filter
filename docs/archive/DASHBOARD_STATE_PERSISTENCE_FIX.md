# Dashboard 状态持久化修复报告

**日期**: 2026-06-12  
**问题**: 切换语言后，已发现的事件丢失  
**状态**: ✅ 已修复

---

## 🐛 问题描述

**用户报告**：
1. 用户在英文版发现事件
2. 切换到中文版
3. 已发现的事件全部消失 ❌

**根本原因**：
英文版和中文版是两个独立的 HTML 页面，JavaScript 状态（`events` 数组）不共享。切换页面时，新页面从空状态开始。

---

## 🔧 解决方案

### 使用 localStorage 跨页面共享状态

**存储的数据**：
```javascript
localStorage.setItem('eip_events', JSON.stringify(events));
localStorage.setItem('eip_selected', selectedId || '');
```

**存储时机**：
- 发现事件完成后 (`discover()`)
- 手动分析完成后 (`analyzeManual()`)

**加载时机**：
- 页面加载时立即执行 (`loadEventsFromStorage()`)

---

## 📝 实现细节

### 新增函数

#### 1. saveEventsToStorage()
```javascript
function saveEventsToStorage(){
  try{
    localStorage.setItem('eip_events', JSON.stringify(events));
    localStorage.setItem('eip_selected', selectedId || '');
  }catch(e){}
}
```

**功能**：将当前事件列表和选中的事件 ID 保存到 localStorage

**容错**：使用 try-catch 防止隐私模式或配额限制导致崩溃

#### 2. loadEventsFromStorage()
```javascript
function loadEventsFromStorage(){
  try{
    const stored = localStorage.getItem('eip_events');
    if(stored){
      events = JSON.parse(stored);
      selectedId = localStorage.getItem('eip_selected') || (events[0] ? events[0].event_id : null);
      if(events.length){
        renderEvents();
        renderReport(events.find(e => e.event_id === selectedId) || events[0]);
        updateStats();
      }
    }
  }catch(e){}
}
```

**功能**：
- 从 localStorage 恢复事件列表
- 恢复选中状态
- 自动渲染界面
- 更新统计数据

**容错**：JSON 解析失败时静默忽略

---

## 🔄 工作流程

### Before（修复前）

```
用户流程：
1. 英文版 - 发现 5 个事件 ✅
2. 点击"中文" ❌
3. 中文版加载 - events = [] (空)
4. 用户看到：无事件

结果：用户困惑，需要重新发现
```

### After（修复后）

```
用户流程：
1. 英文版 - 发现 5 个事件 ✅
   → saveEventsToStorage() 保存到 localStorage
   
2. 点击"中文" ✅
3. 中文版加载
   → loadEventsFromStorage() 读取 localStorage
   → 恢复 5 个事件 ✅
   
4. 用户看到：完整的事件列表

结果：无缝切换，数据保持
```

---

## ✅ 验证测试

### 功能测试

**测试场景 1：发现事件后切换**
```
1. 英文版点击"发现事件" ✅
2. 显示 N 个事件 ✅
3. 切换到中文版 ✅
4. 中文版显示相同的 N 个事件 ✅
5. 切换回英文版 ✅
6. 英文版仍显示 N 个事件 ✅
```

**测试场景 2：手动分析后切换**
```
1. 中文版手动分析 1 个事件 ✅
2. 显示分析结果 ✅
3. 切换到英文版 ✅
4. 英文版显示相同的分析结果 ✅
```

**测试场景 3：选中状态保持**
```
1. 发现多个事件，选中第 3 个 ✅
2. 切换语言 ✅
3. 第 3 个事件保持选中状态 ✅
4. 情报报告显示第 3 个事件 ✅
```

### 单元测试

```bash
python -m unittest discover -s tests
✅ 88 tests passed
```

### JavaScript 语法

```bash
node syntax check (English)  ✅
node syntax check (Chinese)  ✅
```

---

## 🎯 用户体验改进

### Before
```
发现事件 → 切换语言 → ❌ 事件消失
手动分析 → 切换语言 → ❌ 分析结果丢失
```

### After
```
发现事件 → 切换语言 → ✅ 事件保留
手动分析 → 切换语言 → ✅ 分析保留
刷新页面 → ✅ 事件仍在（localStorage 持久化）
```

---

## 🔍 技术考量

### 为什么用 localStorage

**优点**：
- ✅ 跨页面共享数据
- ✅ 持久化（刷新后仍存在）
- ✅ 简单实现
- ✅ 无需服务器支持

**替代方案对比**：

| 方案 | 跨页面 | 持久化 | 复杂度 | 选择 |
|------|--------|--------|--------|------|
| localStorage | ✅ | ✅ | 低 | ✅ 采用 |
| sessionStorage | ✅ | ❌ | 低 | ❌ 关闭标签丢失 |
| URL 参数 | ✅ | ❌ | 中 | ❌ 数据量大 |
| 服务端 session | ✅ | ✅ | 高 | ❌ 需要后端改动 |

### 存储限制

**localStorage 配额**：
- 大多数浏览器：5-10 MB
- 当前数据：每个事件 ~2-5 KB
- 10 个事件：~50 KB
- **完全足够使用** ✅

**容错处理**：
- 使用 try-catch 包裹
- 失败时静默忽略
- 不影响核心功能

---

## 📋 修改的文件

| 文件 | 修改内容 |
|------|----------|
| `static/index.html` | 添加 `saveEventsToStorage()`, `loadEventsFromStorage()`, 调用时机 |
| `static/index_zh.html` | 添加 `saveEventsToStorage()`, `loadEventsFromStorage()`, 调用时机 |

**代码行数**：
- 新增函数：~30 行
- 调用点：2 处（每个文件）

---

## 🎓 额外收益

### 1. 刷新保持
用户刷新页面后，事件列表仍然保留。

### 2. 意外关闭恢复
用户不小心关闭标签页，重新打开后数据仍在。

### 3. 多标签共享
同一浏览器多个标签页可以看到相同的事件列表（localStorage 跨标签共享）。

---

## ⚠️ 已知限制

### 1. 隐私模式
某些浏览器的隐私模式禁用 localStorage，此时回退到原有行为（不持久化）。

**影响**：极小（使用 try-catch 容错）

### 2. 跨浏览器不共享
Chrome 和 Firefox 之间不共享 localStorage。

**影响**：符合预期（localStorage 是浏览器本地存储）

### 3. 清除浏览器数据
用户清除浏览器数据时会删除 localStorage。

**影响**：正常（用户主动清理）

---

## 🚀 未来优化（可选）

### 1. 过期清理
```javascript
// 添加时间戳，自动清理旧数据
{
  events: [...],
  timestamp: Date.now(),
  expires: 24 * 60 * 60 * 1000  // 24小时
}
```

### 2. 数据压缩
如果事件数量很大，可以压缩 JSON：
```javascript
// 使用 LZ-string 等压缩库
const compressed = LZString.compress(JSON.stringify(events));
```

### 3. 同步到后端
定期将事件列表同步到服务器，跨设备访问。

**当前不需要**：localStorage 已满足需求。

---

## 📊 对比总结

| 指标 | 修复前 | 修复后 |
|------|--------|--------|
| 切换语言数据保留 | ❌ | ✅ |
| 刷新页面数据保留 | ❌ | ✅ |
| 选中状态保留 | ❌ | ✅ |
| 代码复杂度 | 低 | 低（+30行） |
| 用户体验 | 差 | 优秀 |

---

## ✅ 总结

### 问题根源
两个独立页面的 JavaScript 状态不共享。

### 解决方法
使用 localStorage 在页面间共享事件数据。

### 实现质量
- ✅ 简单实现（~30 行代码）
- ✅ 完整容错（try-catch）
- ✅ 跨页面工作
- ✅ 持久化保存
- ✅ 所有测试通过

### 用户价值
无缝的语言切换体验，数据不丢失。

---

**修复耗时**: 15 分钟  
**测试状态**: ✅ 88/88  
**用户可用**: 立即可用  
**副作用**: 无

---

**报告生成**: 2026-06-12 20:00  
**执行者**: Claude Code
