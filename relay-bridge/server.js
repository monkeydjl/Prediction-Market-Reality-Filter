/**
 * relay-bridge — 把 Relay.app 的 Claude Opus 4.8 能力桥接成本地 API
 *
 * 暴露两个兼容端点：
 *   POST /v1/chat/completions  (OpenAI 兼容，供 Codex / Opencode 等)
 *   POST /v1/messages         (Anthropic 兼容，供 Claude Code 等)
 *   GET  /v1/models
 *   GET  /health
 *
 * 工作流：客户端 -> 本代理 -> Relay.app Webhook -> Prompt Opus 4.8 -> Respond -> 返回
 *
 * 零依赖，仅用 Node.js 内置模块。
 */

const http = require('http');
const https = require('https');
const fs = require('fs');
const path = require('path');

// ---------------------------------------------------------------------------
// 配置加载：config.json 优先，环境变量兜底
// ---------------------------------------------------------------------------
const CONFIG_PATH = path.join(__dirname, 'config.json');

function loadConfig() {
  let fileCfg = {};
  try {
    if (fs.existsSync(CONFIG_PATH)) {
      fileCfg = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'));
    }
  } catch (e) {
    console.warn('[relay-bridge] 读取 config.json 失败，将使用环境变量:', e.message);
  }

  return {
    port: process.env.RELAY_BRIDGE_PORT || fileCfg.port || 8787,
    // Relay.app workflow 的 webhook URL（在 Relay 里建好 workflow 后拿到）
    relayWebhookUrl: process.env.RELAY_WEBHOOK_URL || fileCfg.relayWebhookUrl || '',
    // 调用 Relay 的超时（毫秒）。Relay webhook 自身 30s 超时，这里给点缓冲
    relayTimeoutMs: process.env.RELAY_TIMEOUT_MS || fileCfg.relayTimeoutMs || 55000,
    // 对外暴露的模型名
    modelName: process.env.RELAY_MODEL_NAME || fileCfg.modelName || 'claude-opus-4-8',
    // 简单鉴权 token（可选）。客户端需在 Authorization: Bearer <token> 里带上
    apiKey: process.env.RELAY_BRIDGE_API_KEY || fileCfg.apiKey || '',
    // 是否开启请求日志
    verbose: fileCfg.verbose !== undefined ? fileCfg.verbose : true,
  };
}

const CFG = loadConfig();

if (!CFG.relayWebhookUrl) {
  console.error('\n[relay-bridge] 未配置 relayWebhookUrl！');
  console.error('  请在 config.json 里填入 Relay.app workflow 的 webhook URL，');
  console.error('  或设置环境变量 RELAY_WEBHOOK_URL。\n');
  process.exit(1);
}

// ---------------------------------------------------------------------------
// 工具函数
// ---------------------------------------------------------------------------

/** 读取请求 body */
function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    req.on('error', reject);
  });
}

/** 发送 JSON 响应 */
function sendJson(res, status, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': '*',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
  });
  res.end(body);
}

/** 发送 SSE 流式响应 */
function sendSseHeaders(res) {
  res.writeHead(200, {
    'Content-Type': 'text/event-stream; charset=utf-8',
    'Cache-Control': 'no-cache',
    'Connection': 'keep-alive',
    'Access-Control-Allow-Origin': '*',
  });
}

function sseWrite(res, obj) {
  res.write(`data: ${JSON.stringify(obj)}\n\n`);
}

function sseEnd(res) {
  res.write('data: [DONE]\n\n');
  res.end();
}

/** 把 OpenAI/Anthropic 的 messages 数组转成一段纯文本 prompt */
function messagesToPrompt(messages) {
  if (!Array.isArray(messages)) return String(messages || '');

  let systemParts = [];
  let dialogue = [];

  for (const msg of messages) {
    const role = msg.role || 'user';
    const text = contentToText(msg.content);

    if (role === 'system') {
      systemParts.push(text);
    } else if (role === 'user') {
      dialogue.push(`Human: ${text}`);
    } else if (role === 'assistant') {
      dialogue.push(`Assistant: ${text}`);
    } else if (role === 'tool' || role === 'function') {
      // 工具结果当作 user 上下文喂进去
      dialogue.push(`Human: [tool result] ${text}`);
    }
  }

  let prompt = '';
  if (systemParts.length) {
    prompt += systemParts.join('\n\n') + '\n\n';
  }
  prompt += dialogue.join('\n\n');
  // 引导模型以 Assistant 身份回答
  prompt += '\n\nAssistant: ';
  return prompt.trim();
}

/** 把 content（字符串或数组）转成纯文本 */
function contentToText(content) {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content
      .map((part) => {
        if (typeof part === 'string') return part;
        if (part.type === 'text') return part.text || '';
        if (part.type === 'image_url') return '[image]';
        if (part.type === 'image') return '[image]';
        if (part.type === 'tool_use') return `[tool_use: ${part.name}] ${JSON.stringify(part.input || {})}`;
        if (part.type === 'tool_result') return contentToText(part.content);
        return '';
      })
      .join('\n');
  }
  return String(content || '');
}

/** 调用 Relay.app webhook，返回解析后的 JSON */
function callRelay(payload) {
  return new Promise((resolve, reject) => {
    const url = new URL(CFG.relayWebhookUrl);
    const bodyStr = JSON.stringify(payload);

    const options = {
      method: 'POST',
      hostname: url.hostname,
      port: url.port || 443,
      path: url.pathname + url.search,
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(bodyStr),
        'User-Agent': 'relay-bridge/1.0',
      },
      timeout: CFG.relayTimeoutMs,
    };

    const req = https.request(options, (resp) => {
      const chunks = [];
      resp.on('data', (c) => chunks.push(c));
      resp.on('end', () => {
        const raw = Buffer.concat(chunks).toString('utf8');
        if (CFG.verbose) {
          console.log(`[relay] HTTP ${resp.statusCode} | ${raw.length} bytes`);
        }
        try {
          const json = JSON.parse(raw);
          resolve({ status: resp.statusCode, json, raw });
        } catch (e) {
          // Relay 可能返回非 JSON（比如超时默认响应）
          resolve({ status: resp.statusCode, json: null, raw });
        }
      });
    });

    req.on('timeout', () => {
      req.destroy(new Error(`Relay 调用超时（${CFG.relayTimeoutMs}ms）`));
    });
    req.on('error', reject);

    req.write(bodyStr);
    req.end();
  });
}

/** 从 Relay 返回里提取模型输出文本 */
function extractOutput(relayResp) {
  const j = relayResp.json;
  if (!j) return relayResp.raw || '';

  // 兼容多种可能的返回结构
  if (typeof j.output === 'string') return j.output;
  if (typeof j.text === 'string') return j.text;
  if (typeof j.result === 'string') return j.result;
  if (typeof j.content === 'string') return j.content;
  if (Array.isArray(j.content)) return contentToText(j.content);
  if (j.data && typeof j.data.output === 'string') return j.data.output;

  // 兜底：把整个 JSON 当文本返回（方便调试）
  return JSON.stringify(j);
}

/** 生成简易 ID */
function genId(prefix) {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

/** 简易鉴权 */
function checkAuth(req) {
  if (!CFG.apiKey) return true; // 未配置则不校验
  const auth = req.headers['authorization'] || '';
  const token = auth.replace(/^Bearer\s+/i, '').trim();
  return token === CFG.apiKey;
}

// ---------------------------------------------------------------------------
// OpenAI 兼容：/v1/chat/completions
// ---------------------------------------------------------------------------
async function handleChatCompletions(req, res) {
  let body;
  try {
    body = JSON.parse(await readBody(req));
  } catch (e) {
    return sendJson(res, 400, { error: { message: 'Invalid JSON body' } });
  }

  const prompt = messagesToPrompt(body.messages || []);
  const stream = body.stream === true;

  const relayPayload = {
    prompt,
    max_tokens: body.max_tokens || 4096,
    temperature: body.temperature ?? 1,
    model: body.model || CFG.modelName,
    // 原始请求留存，方便 Relay workflow 里调试
    _meta: {
      source: 'relay-bridge',
      original_model: body.model,
      stream,
    },
  };

  if (CFG.verbose) {
    console.log(`\n[chat] model=${body.model} stream=${stream} prompt=${prompt.length} chars`);
  }

  let relayResp;
  try {
    relayResp = await callRelay(relayPayload);
  } catch (e) {
    return sendJson(res, 502, {
      error: { message: `Relay 调用失败: ${e.message}`, type: 'relay_bridge_error' },
    });
  }

  // 检查是否超时（Relay 返回默认响应）
  if (relayResp.status >= 500 || (relayResp.raw || '').includes('timed out')) {
    return sendJson(res, 504, {
      error: {
        message: 'Relay.app workflow 执行超时（超过 30s 限制）。请缩短 prompt 或拆分任务。',
        type: 'relay_timeout',
      },
    });
  }

  const output = extractOutput(relayResp);
  const created = Math.floor(Date.now() / 1000);
  const id = genId('chatcmpl');

  if (stream) {
    // 模拟流式：把完整输出按块切分发送
    sendSseHeaders(res);
    const chunkSize = 12;
    for (let i = 0; i < output.length; i += chunkSize) {
      const piece = output.slice(i, i + chunkSize);
      sseWrite(res, {
        id,
        object: 'chat.completion.chunk',
        created,
        model: CFG.modelName,
        choices: [{ index: 0, delta: { content: piece }, finish_reason: null }],
      });
    }
    sseWrite(res, {
      id,
      object: 'chat.completion.chunk',
      created,
      model: CFG.modelName,
      choices: [{ index: 0, delta: {}, finish_reason: 'stop' }],
    });
    return sseEnd(res);
  }

  return sendJson(res, 200, {
    id,
    object: 'chat.completion',
    created,
    model: CFG.modelName,
    choices: [
      {
        index: 0,
        message: { role: 'assistant', content: output },
        finish_reason: 'stop',
      },
    ],
    usage: {
      prompt_tokens: Math.ceil(prompt.length / 4),
      completion_tokens: Math.ceil(output.length / 4),
      total_tokens: Math.ceil((prompt.length + output.length) / 4),
    },
  });
}

// ---------------------------------------------------------------------------
// Anthropic 兼容：/v1/messages
// ---------------------------------------------------------------------------
async function handleMessages(req, res) {
  let body;
  try {
    body = JSON.parse(await readBody(req));
  } catch (e) {
    return sendJson(res, 400, { error: { message: 'Invalid JSON body' } });
  }

  // Anthropic 格式：system 是顶层字段，messages 不含 system role
  const messages = body.messages || [];
  if (body.system) {
    messages.unshift({ role: 'system', content: body.system });
  }

  const prompt = messagesToPrompt(messages);
  const stream = body.stream === true;

  const relayPayload = {
    prompt,
    max_tokens: body.max_tokens || 4096,
    temperature: body.temperature ?? 1,
    model: body.model || CFG.modelName,
    _meta: { source: 'relay-bridge', original_model: body.model, stream, protocol: 'anthropic' },
  };

  if (CFG.verbose) {
    console.log(`\n[messages] model=${body.model} stream=${stream} prompt=${prompt.length} chars`);
  }

  let relayResp;
  try {
    relayResp = await callRelay(relayPayload);
  } catch (e) {
    return sendJson(res, 502, {
      error: { message: `Relay 调用失败: ${e.message}`, type: 'relay_bridge_error' },
    });
  }

  if (relayResp.status >= 500 || (relayResp.raw || '').includes('timed out')) {
    return sendJson(res, 504, {
      error: {
        message: 'Relay.app workflow 执行超时（超过 30s 限制）。请缩短 prompt 或拆分任务。',
        type: 'relay_timeout',
      },
    });
  }

  const output = extractOutput(relayResp);
  const id = genId('msg');
  const created = Math.floor(Date.now() / 1000);

  if (stream) {
    sendSseHeaders(res);
    const chunkSize = 12;
    for (let i = 0; i < output.length; i += chunkSize) {
      const piece = output.slice(i, i + chunkSize);
      sseWrite(res, {
        type: 'content_block_delta',
        index: 0,
        delta: { type: 'text_delta', text: piece },
      });
    }
    sseWrite(res, { type: 'content_block_stop', index: 0 });
    sseWrite(res, { type: 'message_delta', delta: { stop_reason: 'end_turn' } });
    sseWrite(res, { type: 'message_stop' });
    return sseEnd(res);
  }

  return sendJson(res, 200, {
    id,
    type: 'message',
    role: 'assistant',
    model: CFG.modelName,
    content: [{ type: 'text', text: output }],
    stop_reason: 'end_turn',
    usage: {
      input_tokens: Math.ceil(prompt.length / 4),
      output_tokens: Math.ceil(output.length / 4),
    },
  });
}

// ---------------------------------------------------------------------------
// 路由
// ---------------------------------------------------------------------------
const server = http.createServer(async (req, res) => {
  // CORS 预检
  if (req.method === 'OPTIONS') {
    res.writeHead(204, {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Headers': '*',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    });
    return res.end();
  }

  const url = req.url.split('?')[0];

  // 健康检查（不需要鉴权）
  if (url === '/health' && req.method === 'GET') {
    return sendJson(res, 200, {
      status: 'ok',
      model: CFG.modelName,
      relay_configured: !!CFG.relayWebhookUrl,
      auth_required: !!CFG.apiKey,
    });
  }

  // 模型列表
  if (url === '/v1/models' && req.method === 'GET') {
    if (!checkAuth(req)) return sendJson(res, 401, { error: { message: 'Unauthorized' } });
    return sendJson(res, 200, {
      object: 'list',
      data: [
        { id: CFG.modelName, object: 'model', created: 1700000000, owned_by: 'relay-bridge' },
      ],
    });
  }

  // 以下端点需要鉴权
  if (!checkAuth(req)) {
    return sendJson(res, 401, { error: { message: 'Unauthorized: 缺少或错误的 API Key' } });
  }

  try {
    if (url === '/v1/chat/completions' && req.method === 'POST') {
      return await handleChatCompletions(req, res);
    }
    if (url === '/v1/messages' && req.method === 'POST') {
      return await handleMessages(req, res);
    }
  } catch (e) {
    console.error('[relay-bridge] 处理请求出错:', e);
    return sendJson(res, 500, { error: { message: e.message, type: 'internal_error' } });
  }

  sendJson(res, 404, { error: { message: `Not found: ${req.method} ${url}` } });
});

server.listen(CFG.port, () => {
  console.log('┌─────────────────────────────────────────────────────┐');
  console.log('│  relay-bridge 已启动                                │');
  console.log('├─────────────────────────────────────────────────────┤');
  console.log(`│  监听端口 : ${CFG.port}`.padEnd(55) + '│');
  console.log(`│  模型名   : ${CFG.modelName}`.padEnd(55) + '│');
  console.log(`│  鉴权     : ${CFG.apiKey ? '已开启' : '未开启'}`.padEnd(55) + '│');
  console.log(`│  Relay   : ${CFG.relayWebhookUrl.slice(0, 38)}...`.padEnd(55) + '│');
  console.log('├─────────────────────────────────────────────────────┤');
  console.log('│  OpenAI 端点:   http://localhost:' + CFG.port + '/v1/chat/completions');
  console.log('│  Anthropic端点: http://localhost:' + CFG.port + '/v1/messages');
  console.log('└─────────────────────────────────────────────────────┘');
});
