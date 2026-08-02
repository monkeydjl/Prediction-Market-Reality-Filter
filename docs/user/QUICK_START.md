# Event Intelligence Platform - Quick Start Guide

Last updated: 2026-06-14

## 🚀 Setup (5 minutes)

### 1. Install Dependencies

```bash
cd backend
pip install -r requirements.txt
```

### 2. Configure Environment

Create `.env` file:

```bash
# LLM Configuration (Required)
OPENAI_API_KEY=sk-your-key-here
OPENAI_MODEL=deepseek-v3.2
OPENAI_BASE_URL=https://api.deepseek.com

# Optional Settings
GNEWS_MAX_RESULTS=10
# CROSS_VALIDATION_MODEL=   # set a 2nd model id to enable cross-validation
```

### 3. Verify Installation

```bash
# Check dependencies
python -c "import feedparser, httpx, fastapi; print('✅ Dependencies OK')"

# Install test dependencies and run tests
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests
# Expected: 3600+ passed, a few skipped (opt-in live tests)
```

---

## 🎯 Core Usage

### Start the Server

```bash
python run.py
# Server runs at: http://localhost:8000
```

### Access Interfaces

- **Dashboard**: http://localhost:8000
- **API Docs**: http://localhost:8000/docs
- **API Root**: http://localhost:8000/api

### API Examples

#### Analyze an Event

```bash
curl -X POST http://localhost:8000/api/events/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "event_question": "Will Bitcoin reach $100,000 by end of 2026?",
    "baseline_probability": 45,
    "news_context": "Recent institutional adoption increasing."
  }'
```

**Response**:
```json
{
  "event_id": "...",
  "event_title": "Will Bitcoin reach $100,000 by end of 2026?",
  "probability": {
    "baseline": 45.0,
    "estimated": 49.42,
    "change": 4.4,
    "direction": "rising"
  },
  "credibility": { "score": 26, "level": "LOW" },
  "impact": { "score": 10, "level": "LOW" },
  "intelligence_report": {
    "headline": "...",
    "recommended_action": "Keep in watch mode..."
  }
}
```

#### Discover Events

```bash
curl http://localhost:8000/api/events/discover?limit=5
```

Prediction-market discovery currently includes Polymarket, Kalshi, and the public Limitless adapter by default. Opinion and Predict.fun are live adapter-capable but require `OPINION_API_KEY` and `PREDICT_FUN_API_KEY`; without keys they fail closed and contribute no events. Probable remains planned-only until an official API, indexer, or contract-event interface is verified. The new on-chain adapters do not participate in auto-resolution yet.

Config:

- `LIMITLESS_SOURCE_ENABLED` / `LIMITLESS_API_URL`: public Limitless market discovery.
- `OPINION_API_KEY`: enables Opinion Open API market discovery.
- `PREDICT_FUN_API_KEY`: enables Predict.fun beta API market discovery.

---

## 🧪 Testing

### Unit Tests (Fast, No API Calls)

```bash
python -m pytest tests
# 3600+ passed, a few skipped, no external API calls
```

### Integration Tests (Real API Calls)

Live integration tests cover both the core read paths (event analysis,
discovery, news collection) and the post-handoff write/report paths (manual
resolve + calibration, auto-resolve, calibration report, semantics,
cross-validation, open-web extraction). They make real LLM + network calls and
are opt-in (skipped by default under pytest — the class raises `unittest.SkipTest`
in `setUpClass` unless `RUN_LIVE_TESTS=1` is set; there is no dedicated pytest flag).

```bash
# Full integration suite (Windows cmd)
set RUN_LIVE_TESTS=1 && python -m pytest tests/test_integration_live.py
# 11 tests, ~1-3 minutes, requires OPENAI_API_KEY in .env

# Or run the file directly (auto-sets RUN_LIVE_TESTS=1)
python tests/test_integration_live.py

# Simple standalone live test (single LLM call)
python tests/manual/manual_live_integration.py
```

The opt-in LLM features (cross-validation, open-web extraction) only run their
live tests when `CROSS_VALIDATION_MODEL` / `OPEN_WEB_EXTRACTION_MODEL` are set
in `.env`; otherwise those two tests self-skip while the rest still run.


### Validation Checklist

```bash
# 1. Compile check
python -m compileall app tests

# 2. Unit tests
python -m pytest tests

# 3. Dashboard JS (only if you changed static/index.html or static/index_zh.html)
node -e "const fs=require('fs'); for (const file of ['static/index.html','static/index_zh.html']) { const html=fs.readFileSync(file,'utf8'); const m=html.match(/<script>([\s\S]*)<\/script>/); new Function(m?m[1]:''); } console.log('✅ dashboard scripts OK');"
```

---

## 📁 Project Structure

```
backend/
├── app/
│   ├── main.py                    # FastAPI app
│   ├── api/
│   │   ├── router.py              # Route aggregator
│   │   └── routes/
│   │       └── events.py          # Event endpoints
│   ├── services/
│   │   ├── event_intelligence_service.py    # Core EIP logic
│   │   ├── event_collection_service.py      # News collection
│   │   ├── probability_engine_service.py    # LLM + probability
│   │   ├── evidence_extraction_service.py   # Evidence analysis
│   │   └── *_source_service.py              # Source adapters
│   ├── models/
│   │   └── event.py               # Event data models
│   └── memory/
│       └── event_store.py         # Event persistence
├── tests/
│   ├── test_*.py                  # Unit tests (3600+ tests)
│   └── test_integration_live.py   # Integration tests (11 tests)
├── static/
│   ├── index.html                 # English dashboard
│   └── index_zh.html              # Chinese dashboard
├── docs/
│   ├── PROJECT_PROGRESS.md        # English progress log
│   ├── 工程进度.md                 # Chinese progress log
│   └── Event Intelligence Platform.md
├── requirements.txt               # Python dependencies
├── run.py                         # Server launcher
└── .env                           # Configuration (create this)
```

---

## 🎨 Key Concepts

### Event Record

```python
{
  "event_id": str,           # Unique identifier
  "event_title": str,        # Question being analyzed
  "probability": {           # Probability assessment
    "baseline": float,       # Starting probability (e.g., 45.0%)
    "estimated": float,      # AI-estimated probability
    "change": float,         # Difference
    "direction": str         # "rising" or "falling"
  },
  "credibility": {           # Trust score
    "score": int,            # 0-100
    "level": str             # LOW/MEDIUM/HIGH
  },
  "impact": {                # Impact score
    "score": int,            # 0-100
    "level": str             # LOW/MEDIUM/HIGH
  },
  "intelligence_report": {   # Human-readable report
    "headline": str,
    "why_it_matters": str,
    "probability_assessment": str,
    "recommended_action": str
  },
  "value_score": int         # Overall value (0-100)
}
```

### Evidence Profile

```python
{
  "strength": float,         # 0-1, evidence quality
  "direction": str,          # "positive", "negative", "neutral"
  "conflict": float,         # 0-1, internal contradiction
  "freshness": float,        # 0-1, how recent
  "source_quality": float,   # 0-1, source reliability
  "coverage": int            # Number of unique sources
}
```

---

## 🔧 Configuration

### LLM Providers

**Qwen (DashScope)**:
```env
OPENAI_API_KEY=sk-...
OPENAI_MODEL=qwen-math-turbo
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

**DeepSeek**:
```env
OPENAI_API_KEY=sk-...
OPENAI_MODEL=deepseek-chat
OPENAI_BASE_URL=https://api.deepseek.com
```

**OpenAI**:
```env
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4
# Leave OPENAI_BASE_URL empty for default
```

### News Sources

Configure in `app/core/config.py`:
- RSS feeds (default: 7 sources)
- Google News (via gnews library)
- Federal Reserve press releases
- SEC EDGAR filings
- BLS economic data

---

## 📊 Monitoring

### Event Store

- **File**: `event_store.json`
- **Format**: JSON, one entry per event_id
- **Fields**: `{event_id, first_seen, last_updated, record}`

### Event Audit Log

- **File**: `event_audit.jsonl`
- **Format**: JSON Lines, one snapshot per scan
- **Use**: Track probability changes over time

### Compute Cache

- `event_cache.json` - 1-hour LLM compute cache (safe to delete)

---

## 🐛 Troubleshooting

### ImportError: No module named 'feedparser'

```bash
pip install -r requirements.txt
```

### UnicodeEncodeError on Windows

Test scripts now handle this automatically. If you see it elsewhere:

```python
import sys, codecs
sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
```

### LLM API Error

1. Check `.env` file exists and has correct API key
2. Verify API key is valid: `python -c "from app.core.config import settings; print(settings.OPENAI_API_KEY[:10])"`
3. Test API directly: `python tests/manual/manual_live_integration.py`

### No Events Discovered

- Active event sources such as Polymarket, Kalshi, or Open Web extraction may be disabled, rate-limited, or unavailable
- Try with `use_cache=False`
- Check network connectivity
- Open Web extraction requires `OPEN_WEB_EXTRACTION_MODEL`

---

## 📚 Resources

- **中文完整使用教程**: `docs/user/中文使用教程.md`
- **Full User Guide**: `docs/user/USER_GUIDE.md`
- **Project Progress**: `backend/docs/PROJECT_PROGRESS.md`
- **Integration Test Report**: `docs/dev/INTEGRATION_TEST_REPORT.md`
- **Skills**: `.claude/skills/` (prime-context, event-conventions, run-checks, etc.)

---

## ✅ Validation Checklist

Before considering the platform ready:

- ✅ Dependencies installed
- ✅ Unit tests pass (3600+ tests, a few skipped)
- ✅ Integration tests pass (11 tests)
- ✅ Server starts successfully
- ✅ Event analysis works with real LLM
- ✅ Event discovery works with prediction-market sources
- ✅ Dashboard inline JavaScript syntax check passes
- ⚠️ Browser-rendered Dashboard smoke test recommended
- ⚠️ Load testing performed (not done yet)
- ⚠️ Security audit (not done yet)

---

**Last Updated**: 2026-06-14  
**Project Version**: 0.3.0  
**Status**: Core features validated; production hardening remains
