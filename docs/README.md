# PMRF Documentation

## Getting Started
- [Quick Start](user/QUICK_START.md) — Install, configure, run
- [User Guide](user/USER_GUIDE.md) — Full usage guide
- [中文使用教程](user/中文使用教程.md) — Chinese tutorial
- [World Cup Facts Guide](user/WORLD_CUP_FACTS_GUIDE.md) — Import structured sports facts and preview deterministic World Cup resolution

## Architecture & Design
- [Architecture Overview](dev/ARCHITECTURE.md) — System context, data flow, deployment
- [Architecture Philosophy](user/ARCHITECTURE_PHILOSOPHY.md) — 8 design principles
- [Design System](dev/DESIGN.md) — Visual design (OKLCH colors, typography, components)
- [Product Context](dev/PRODUCT.md) — User personas, brand, anti-patterns
- [World Cup Prediction System Design](dev/WORLD_CUP_PREDICTION_SYSTEM_DESIGN.md) — 世界杯预测系统设计与优先级

## Architecture Decision Records
- [ADR-001: JSON File Store](dev/adr/001-json-file-store.md)
- [ADR-002: Next.js Static Export](dev/adr/002-nextjs-static-export.md)
- [ADR-003: FastAPI over Flask](dev/adr/003-fastapi-over-flask.md)

## Data & Database
- [Database Design](user/DATABASE_DESIGN.md) — SQLite schema, migration strategy
- [Event Intelligence Platform](dev/Event%20Intelligence%20Platform.md) — Product vision, data model

## Operations
- [Runbook](ops/RUNBOOK.md) — Production settings, health check, backups, supervision

## Development
- [**可做项 / 优化全量清单（2026-07-17）**](dev/OPPORTUNITY_BACKLOG_2026-07-17.md) — 安全、引擎因子、精度、前端、工程债务与路线图
- [Esports boundary](dev/ESPORTS_BOUNDARY.md) — 电竞赛道占位边界（无假盘口 / 接入前置条件）
- [ADR-004 Esports data adapter](dev/adr/004-esports-data-adapter.md) — 电竞/LoL adapter 决策（**Accepted** 2026-07-22）
- [V2 Roadmap](user/V2_ROADMAP.md)
- [V2 Refactor Plan](user/V2_REFACTOR_PLAN.md)
- [Integration Test Report](dev/INTEGRATION_TEST_REPORT.md)

## History
- [Changelog](../CHANGELOG.md)
- [Archive](archive/) — Historical dashboard/phase summaries and change logs

## Reviews & Audits
- [Reviews Index](reviews/README.md) — All code reviews, multi-AI audits, Go/No-Go assessments (consolidated 2026-06-21)
- [Consolidated Issue Registry](reviews/consolidated-issue-registry-2026-06-21.md) — All ~150 deduped issues with verified status
- [Open Issues (verified)](reviews/open-issues-verified-2026-06-21.md) — Remaining open issues with file:line evidence (P0 all fixed, no blockers)
- [P0 Fix Report](reviews/p0-fix-report-2026-06-21.md) — 3 launch-blocking P0 fixes + verification (518 passed)

## External References
- `/docs` — OpenAPI (Swagger) auto-generated
- `README.md` — Project overview and quick start
- [World Cup facts sample](examples/world-cup-facts.sample.json) — Example import payload for the sports facts API
- [World Cup data sample](examples/world-cup-data.sample.json) — Example match-data payload for trusted data-source import
- [World Cup CSV data sample](examples/world-cup-data-csv.sample.json) — Example JSON-wrapped CSV payload for trusted data-source import
- [World Cup official CSV source sample](examples/world-cup-official-csv-source.sample.json) — Strict fixed-column CSV profile for official source imports
- [World Cup match-source sample](examples/world-cup-match-source.sample.json) — Example raw fixture/result payload normalized by the match-source adapter
- [World Cup match-events source sample](examples/world-cup-match-events-source.sample.json) — Example raw match card/event payload normalized into discipline facts
- [World Cup lineups source sample](examples/world-cup-lineups-source.sample.json) — Example raw starting-XI payload normalized into lineup facts
- [World Cup standings-source sample](examples/world-cup-standings-source.sample.json) — Example raw standings payload normalized into qualification facts
- [World Cup player-awards source sample](examples/world-cup-player-awards-source.sample.json) — Example raw top-scorers payload normalized into player-award facts
- [World Cup player-status source sample](examples/world-cup-player-status-source.sample.json) — Example raw injury/availability/suspension/lineup payload normalized into player-status facts
- [World Cup statistics source sample](examples/world-cup-statistics-source.sample.json) — Example raw team/player statistics payload normalized into stat facts
- [World Cup source bundle sample](examples/world-cup-source-bundle.sample.json) — Example multi-source payload that previews/imports several World Cup feeds in one request
