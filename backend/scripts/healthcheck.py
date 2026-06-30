from __future__ import annotations

import json
import os
import sys
from typing import Callable, Mapping
from urllib.error import HTTPError
from urllib.request import Request, urlopen


Fetch = Callable[[str, float], tuple[int, bytes]]


class HealthcheckError(RuntimeError):
    pass


def _fetch_url(url: str, timeout: float) -> tuple[int, bytes]:
    request = Request(url, headers={"User-Agent": "PMRF-healthcheck/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", None)
            if status is None:
                status = response.getcode()
            return int(status), response.read(8192)
    except HTTPError as exc:
        return int(exc.code), exc.read(8192)


def _require_success(status: int, target: str) -> None:
    if status < 200 or status >= 300:
        raise HealthcheckError(f"{target} returned HTTP {status}")


def _check_local_health(url: str, timeout: float, fetch: Fetch) -> None:
    status, body = fetch(url, timeout)
    _require_success(status, "health endpoint")

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HealthcheckError("health endpoint did not return JSON") from exc

    if payload.get("status") != "ok":
        raise HealthcheckError(
            f"health endpoint status is {payload.get('status', '<missing>')!r}"
        )


def _ping_deadman(url: str, timeout: float, fetch: Fetch) -> None:
    status, _body = fetch(url, timeout)
    _require_success(status, "dead-man endpoint")


def run_healthcheck(
    environ: Mapping[str, str] | None = None,
    fetch: Fetch = _fetch_url,
) -> int:
    env = os.environ if environ is None else environ
    health_url = env.get(
        "PMRF_HEALTHCHECK_URL", "http://localhost:8000/api/health"
    ).strip()
    deadman_url = env.get("PMRF_DEADMAN_URL", "").strip()

    try:
        timeout = float(env.get("PMRF_HEALTHCHECK_TIMEOUT_SECONDS", "5"))
        _check_local_health(health_url, timeout, fetch)
        if deadman_url:
            _ping_deadman(deadman_url, timeout, fetch)
    except Exception as exc:
        print(f"PMRF healthcheck failed: {exc}", file=sys.stderr)
        return 1

    print("PMRF healthcheck ok")
    return 0


def main() -> int:
    return run_healthcheck()


if __name__ == "__main__":
    raise SystemExit(main())
