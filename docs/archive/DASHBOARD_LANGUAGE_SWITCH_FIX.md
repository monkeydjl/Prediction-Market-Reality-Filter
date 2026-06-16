# Dashboard 语言切换问题修复报告

**日期**: 2026-06-12  
**问题**: 从 `/dashboard` 无法正常切换到中文版  
**状态**: ✅ 已修复

---

## 🐛 问题描述

**用户报告**：
- ✅ 直接访问 `http://localhost:8000/static/index_zh.html` 正常
- ❌ 从 `http://localhost:8000/dashboard` 点击"中文"链接报错

**根本原因**：
相对路径问题。当用户访问 `/dashboard` 时，点击相对链接 `href="index_zh.html"` 会被解析为 `/index_zh.html`（不存在），而不是 `/static/index_zh.html`。

---

## 🔧 解决方案

### 采用的方案：使用绝对路径

**修改内容**：

1. **英文版** (`static/index.html`)
   ```html
   <!-- Before -->
   <a href="index_zh.html" class="lang-switch">中文</a>
   
   <!-- After -->
   <a href="/static/index_zh.html" class="lang-switch">中文</a>
   ```

2. **中文版** (`static/index_zh.html`)
   ```html
   <!-- Already correct -->
   <a href="/dashboard" class="lang-switch">English</a>
   ```

### 为什么不用 `/dashboard/zh` 路由

**尝试过但放弃的方案**：
```python
@app.get("/dashboard/zh", include_in_schema=False)
async def serve_dashboard_zh():
    dashboard = _STATIC / "index_zh.html"
    if dashboard.exists():
        return FileResponse(str(dashboard))
    return {"error": "Chinese dashboard not found"}
```

**放弃原因**：
- Uvicorn 热重载没有生效（需要完全重启）
- 增加不必要的复杂性
- 静态文件挂载已经可以满足需求

---

## ✅ 验证结果

### 链接验证

```bash
# 英文版链接检查
curl http://localhost:8000/dashboard | grep 'href="/static/index_zh.html"'
✅ 找到正确链接

# 中文版链接检查  
curl http://localhost:8000/static/index_zh.html | grep 'href="/dashboard"'
✅ 找到正确链接
```

### 切换流程测试

```
1. 用户访问 http://localhost:8000/dashboard
   ✅ 显示英文界面

2. 点击右上角"中文"链接
   ✅ 跳转到 http://localhost:8000/static/index_zh.html
   ✅ 显示中文界面

3. 点击右上角"English"链接
   ✅ 跳转回 http://localhost:8000/dashboard
   ✅ 显示英文界面
```

### 测试套件

```bash
python -m unittest discover -s tests
✅ 88 tests passed
```

---

## 📋 URL 设计

### 最终方案

| 语言 | URL | 文件 |
|------|-----|------|
| 英文 | `/dashboard` | `static/index.html` (通过路由) |
| 中文 | `/static/index_zh.html` | `static/index_zh.html` (静态文件) |

### 为什么不对称

**理由**：
1. ✅ `/dashboard` 更简洁，适合作为主入口
2. ✅ 中文版使用静态路径，避免额外路由配置
3. ✅ 用户不会直接输入 URL，都是通过链接跳转

**如果想要对称**：
可以添加 `/dashboard/zh` 路由，但需要：
- 完全重启服务器（不能依赖热重载）
- 在部署时确保路由生效
- 增加维护复杂度

---

## 🎯 用户体验

### 修复前

```
用户：点击"中文" 
浏览器：加载 /index_zh.html 
结果：404 Not Found ❌
```

### 修复后

```
用户：点击"中文"
浏览器：加载 /static/index_zh.html
结果：显示中文界面 ✅

用户：点击"English"
浏览器：加载 /dashboard
结果：显示英文界面 ✅
```

---

## 📝 相关文件

### 修改的文件

1. `static/index.html` - 更新中文链接为绝对路径
2. `app/main.py` - 移除了不必要的 `/dashboard/zh` 路由

### 未修改的文件

- `static/index_zh.html` - 链接已经是正确的绝对路径

---

## 🔍 技术细节

### 相对路径 vs 绝对路径

**相对路径问题**：
```
当前 URL：http://localhost:8000/dashboard
相对链接：href="index_zh.html"
解析结果：http://localhost:8000/index_zh.html ❌
```

**绝对路径解决**：
```
当前 URL：http://localhost:8000/dashboard
绝对链接：href="/static/index_zh.html"
解析结果：http://localhost:8000/static/index_zh.html ✅
```

### FastAPI 静态文件挂载

```python
app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")
```

这意味着：
- `static/index_zh.html` 可通过 `/static/index_zh.html` 访问
- 无需额外路由配置
- 支持所有静态资源（CSS、JS、图片等）

---

## 🎉 总结

### 问题根源

相对路径在不同 URL 层级下解析不一致。

### 解决方法

使用绝对路径确保链接在任何页面都正确解析。

### 验证状态

✅ 链接正确  
✅ 切换正常  
✅ 测试通过  
✅ 用户体验良好

---

**修复耗时**: 10 分钟  
**测试状态**: ✅ 88/88 通过  
**用户可用**: 立即可用

---

**报告生成**: 2026-06-12 19:45  
**执行者**: Claude Code
