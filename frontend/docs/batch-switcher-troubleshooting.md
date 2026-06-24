# 批量引擎切换功能故障排查

## 已修复的问题

### 问题：点击切换提示"切换失败"

**原因**：
1. Next.js代理对POST请求有默认超时限制
2. 批量处理24场比赛需要30秒-2分钟，超过默认超时
3. 前端没有足够长的超时配置

**修复方案**：
1. ✅ 前端增加3分钟超时（`AbortController` with 180000ms timeout）
2. ✅ 添加fallback机制：代理失败时自动尝试直接访问 `http://localhost:8000`
3. ✅ 添加处理进度提示，告知用户预计需要时间
4. ✅ 改进错误提示，区分超时错误

## 使用说明

1. **打开页面**：访问 `/world-cup` → 点击"自动调教"标签
2. **点击按钮**：选择三个引擎之一
3. **耐心等待**：看到蓝色进度提示框，显示"正在批量切换引擎...预计需要30秒-2分钟"
4. **查看结果**：成功后显示绿色结果框，2秒后自动刷新页面

## 预期处理时间

| 引擎 | 预计时间 | 说明 |
|------|---------|------|
| ELO | 30-60秒 | 最快，只需ELO计算和赔率融合 |
| 混合引擎 | 1-2分钟 | 较慢，包含AI推理（如AI不可用会降级为rule_only） |
| 高置信度 | 取决于选择 | 根据比赛特征自动选择最佳引擎 |

## 如果仍然失败

### 检查后端
```powershell
# 检查后端是否运行
Get-Process -Name python

# 测试后端API
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/world-cup/predictions/matches?limit=1"
```

### 检查前端
```powershell
# 检查前端是否运行
Get-Process -Name node

# 访问测试页面（直接调用后端，绕过代理）
# 浏览器打开: http://localhost:3000/test-batch-switch.html
```

### 浏览器控制台
打开浏览器开发者工具（F12），查看：
- **Console标签**：是否有JavaScript错误或警告
- **Network标签**：查看API请求状态、耗时、响应内容

## 技术细节

### 前端实现
- 使用 `AbortController` 设置3分钟超时
- 捕获代理错误后自动fallback到直接后端访问
- 显示实时处理状态和结果统计

### 后端实现
- 批量查询所有scheduled比赛
- 调用 `batch_predict_matches()` 重新预测
- 返回详细统计（总计、成功、失败、跳过）

### 可能的CORS问题
如果直接访问后端失败，检查后端CORS配置：
```python
# backend/app/main.py
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)
```
