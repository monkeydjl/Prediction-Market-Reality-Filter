# PMRF Documentation

## Getting Started
- [Quick Start](user/QUICK_START.md) — Install, configure, run
- [User Guide](user/USER_GUIDE.md) — Full usage guide
- [中文使用教程](user/中文使用教程.md) — Chinese tutorial

## Architecture & Design
- [Architecture Overview](dev/ARCHITECTURE.md) — System context, data flow, deployment
- [Architecture Philosophy](user/ARCHITECTURE_PHILOSOPHY.md) — 8 design principles
- [Design System](dev/DESIGN.md) — Visual design (OKLCH colors, typography, components)
- [Product Context](dev/PRODUCT.md) — User personas, brand, anti-patterns

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
