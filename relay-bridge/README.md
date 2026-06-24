# relay-bridge

把 **Relay.app** 里的 Claude Opus 4.8 能力，桥接成本地 OpenAI / Anthropic 兼容 API，供 Codex、Opencode、Claude Code 等桌面工具直接调用。

## 工作原理

```
Codex / Opencode / Claude Code
        │  (标准 OpenAI/Anthropic 协议)
        ▼
  relay-bridge 本地代理 (localhost:8787)
        │  (转发 prompt)
        ▼
  Relay.app Webhook 触发器
        │
        ▼
  Prompt Claude Opus 4.8 (不消耗你的 Anthropic API 额度)
        │
        ▼
  Respond to Webhook → 返回结果
        │
        ▼
  relay-bridge 转成标准格式返回给客户端
```

**零依赖**，仅需 Node.js 18+，单文件运行。

---

## 第一步：在 Relay.app 里创建 Workflow

这是核心步骤。你需要在 Relay.app 里建一个 workflow，接收 webhook 请求，调用 Opus 4.8，再返回结果。

### 1. 创建 workflow

1. 登录 [relay.app](https://relay.app)，点 **Create workflow**
2. 触发器选 **Webhook**

### 2. 添加 "Prompt Claude Opus 4.8" 动作

1. 点 **+ Add step**
2. 搜索 **Anthropic**，选 **Prompt Claude Opus 4.8**
3. 在 Prompt 字段里填入：
   ```
   {{trigger.body.prompt}}
   ```
   （这会引用 webhook 请求体里的 prompt 字段）
4. 其他参数（Max tokens、Temperature）可留默认，或引用 `{{trigger.body.max_tokens}}`、`{{trigger.body.temperature}}`

### 3. 添加 "Respond to webhook" 步骤

1. 点 **+ Add step**
2. 搜索 **Respond to webhook**
3. Response body 填入以下 JSON（把 AI 输出包在里面）：
   ```json
   {
     "output": "{{prompt.output}}",
     "model": "claude-opus-4-8"
   }
   ```
   > 注意：`{{prompt.output}}` 里的 `prompt` 是上一步动作的名称。如果你改了动作名，这里要对应改。

### 4. 复制 Webhook URL

1. 回到 workflow 顶部的 Webhook 触发器
2. 复制显示的 URL（形如 `https://api.relay.app/webhooks/xxxx`）
3. 保存并启用 workflow

---

## 第二步：配置 relay-bridge

编辑 `config.json`：

```json
{
  "relayWebhookUrl": "https://api.relay.app/webhooks/你的webhook-id",
  "port": 8787,
  "modelName": "claude-opus-4-8",
  "relayTimeoutMs": 55000,
  "apiKey": "",
  "verbose": true
}
```

| 字段 | 说明 |
|------|------|
| `relayWebhookUrl` | 上一步复制的 Relay webhook URL |
| `port` | 本地监听端口，默认 8787 |
| `modelName` | 对外暴露的模型名 |
| `relayTimeoutMs` | 调用 Relay 的超时，默认 55 秒 |
| `apiKey` | 可选鉴权 token，留空则不校验。建议设一个随机字符串 |

也支持环境变量覆盖：`RELAY_WEBHOOK_URL`、`RELAY_BRIDGE_PORT`、`RELAY_BRIDGE_API_KEY` 等。

---

## 第三步：启动

双击 `start.bat`，或命令行运行：

```powershell
cd relay-bridge
node server.js
```

看到如下输出即成功：

```
┌─────────────────────────────────────────────────────┐
│  relay-bridge 已启动                                │
│  监听端口 : 8787
│  模型名   : claude-opus-4-8
│  Relay   : https://api.relay.app/webhooks/xxxx...
└─────────────────────────────────────────────────────┘
```

验证健康检查：浏览器打开 `http://localhost:8787/health`

---

## 第四步：接入桌面工具

### Codex CLI

编辑 `~/.codex/config.toml`（没有就新建）：

```toml
model = "claude-opus-4-8"
openai_base_url = "http://localhost:8787/v1"
```

设置环境变量（任选一种）：

```powershell
# PowerShell
$env:OPENAI_API_KEY = "你在 config.json 里设的 apiKey，留空就随便填"
$env:OPENAI_BASE_URL = "http://localhost:8787/v1"
```

或写入系统环境变量后重启终端，再运行 `codex`。

### Codex 桌面端

进入 **Settings → Developers → Custom AI Service**：

- **Endpoint**: `http://localhost:8787/v1`
- **API Key**: 填 config.json 里的 apiKey（没设就随便填）
- **Model Name**: `claude-opus-4-8`

### Opencode

Opencode 支持自定义 provider。在配置文件里添加：

```json
{
  "provider": {
    "baseURL": "http://localhost:8787/v1",
    "apiKey": "你的apiKey或任意值",
    "model": "claude-opus-4-8"
  }
}
```

### Claude Code（Anthropic 协议）

```powershell
$env:ANTHROPIC_BASE_URL = "http://localhost:8787"
$env:ANTHROPIC_API_KEY = "你的apiKey或任意值"
```

然后运行 `claude`。relay-bridge 的 `/v1/messages` 端点兼容 Anthropic 协议。

### 通用 OpenAI 客户端

任何支持自定义 base URL 的工具都能用：

- **Base URL**: `http://localhost:8787/v1`
- **API Key**: config.json 里的 apiKey
- **Model**: `claude-opus-4-8`

---

## 快速测试

启动服务后，另开终端测试：

```powershell
# 测试 OpenAI 协议
curl http://localhost:8787/v1/chat/completions `
  -H "Content-Type: application/json" `
  -d '{\"model\":\"claude-opus-4-8\",\"messages\":[{\"role\":\"user\",\"content\":\"说一句你好\"}]}'
```

---

## 已知限制

| 限制 | 说明 |
|------|------|
| **30 秒超时** | Relay.app webhook 最多等 30 秒。超长任务会失败，建议拆分 prompt |
| **非真流式** | Relay 不支持 streaming，本代理把完整结果切块模拟流式返回 |
| **credits 有限** | Relay 免费版 500 AI credits/月，Pro 2000/月。编程场景消耗快 |
| **并发限制** | Free 档 2 并发，Pro 档 10 并发 |
| **无 tool_use** | function calling / tool use 无法完整支持，会转成文本上下文 |
| **无图片输入** | 图片会被替换成 `[image]` 占位符 |

> credits 是 Relay 订阅自带的，不额外扣你的 Anthropic API 额度。但用完就没了，适合轻量任务。

---

## 文件说明

```
relay-bridge/
├── server.js              # 代理服务主程序（零依赖）
├── config.json            # 你的配置（需填写 webhook URL）
├── config.example.json    # 配置模板
├── start.bat              # Windows 一键启动
└── README.md              # 本文件
```

## 故障排查

**Q: 启动报错 "未配置 relayWebhookUrl"**
A: 检查 config.json 里是否填了 Relay webhook URL。

**Q: 调用返回 504 超时**
A: Relay workflow 执行超过 30 秒。缩短 prompt，或在 Relay workflow 里检查是否有卡住的步骤。

**Q: 返回内容是空或乱码**
A: 检查 Relay workflow 里 "Respond to webhook" 步骤的 body 是否正确引用了 `{{prompt.output}}`。动作名要和引用对应。

**Q: 401 Unauthorized**
A: config.json 里设了 apiKey，但客户端没带正确的 Authorization header。
