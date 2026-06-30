# 批量切换功能 - 500错误诊断指南

## 当前状态

### ✅ 已确认正常的部分
- 后端API直接访问正常（PowerShell测试通过）
- 后端CORS配置正确（允许POST请求）
- 代码逻辑无误

### ❌ 问题症状
- 用户在浏览器中点击切换按钮时收到 "HTTP 500: Internal Server Error"

## 诊断步骤

### 步骤1: 查看后端日志

后端现在会记录详细的错误信息到日志文件：

```powershell
# 查看错误日志
cd backend
Get-Content uvicorn_error.log -Tail 50

# 查看标准输出
Get-Content uvicorn.log -Tail 50
```

### 步骤2: 浏览器开发者工具检查

1. 打开浏览器开发者工具（F12）
2. 切换到 **Network** 标签
3. 点击批量切换按钮
4. 找到 `batch-switch-engine` 请求
5. 查看：
   - **Status**: 是否真的是500？
   - **Response**: 错误详细信息
   - **Headers**: 请求和响应头
   - **Timing**: 请求是否超时

### 步骤3: 浏览器控制台检查

在 **Console** 标签中查看：
- 是否有JavaScript错误
- 是否有CORS错误
- 是否有网络错误

### 步骤4: 使用测试页面

直接访问测试页面（绕过React应用）：

```
http://localhost:3000/test-batch-switch.html
```

这个页面直接调用后端API，可以排除前端框架问题。

## 可能的原因

### 原因1: 并发冲突
如果快速多次点击按钮，可能导致数据库锁冲突。

**解决方案**: 确保只点击一次，等待完成

### 原因2: 数据库连接问题
SQLite在高并发下可能出现锁定问题。

**检查方法**:
```powershell
# 检查数据库文件是否被锁定
cd backend
Get-Process | Where-Object { $_.Path -like "*python*" }
```

**解决方案**: 重启后端服务

### 原因3: batch_predict_matches内部错误
处理24场比赛时某一场出错导致整个批次失败。

**检查**: 查看后端日志中的详细错误堆栈

### 原因4: 内存不足
批量处理可能消耗大量内存。

**检查**:
```powershell
Get-Process -Name python | Select-Object Id, WS
```

## 最新改进

### 后端改进
- ✅ 添加详细的日志记录
- ✅ 添加try-except错误捕获
- ✅ 返回具体错误信息（不是空500）

### 前端改进
- ✅ 解析JSON错误响应并显示detail字段
- ✅ 3分钟超时保护
- ✅ Fallback到直接后端访问
- ✅ 显示完整错误信息

## 下一步行动

请执行以下操作并反馈结果：

1. **查看后端日志**：
   ```powershell
   cd backend
   Get-Content uvicorn_error.log -Tail 50
   ```

2. **在浏览器中打开F12**，切换到Network标签，再次点击切换按钮

3. **查看batch-switch-engine请求的Response**，复制完整错误信息

4. **或者访问测试页面**: http://localhost:3000/test-batch-switch.html

有了具体错误信息后，我可以精确定位问题并修复。
