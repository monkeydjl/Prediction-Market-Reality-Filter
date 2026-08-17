#!/usr/bin/env python3
"""Print read-only Phase 9 live prediction evidence as JSON.

Unlike eval_applied_params.py, this reports settled production prediction
coverage per sport/competition/engine. It never changes feature flags,
calibration, optimized parameters, or stored predictions.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    from app.kernel.kernel_db import init_kernel_db
    from app.services.phase9_live_evidence_service import build_live_evidence_report

    init_kernel_db()
    print(json.dumps(build_live_evidence_report(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
