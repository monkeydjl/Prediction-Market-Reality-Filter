"""Stage 2A RED tests: every `detail=str(exc)` sink in events.py (28 sites).

Supersedes the earlier skip-heavy version of this file (2026-09-14), which
left 25/27 cases as `pytest.skip` and covered its one live case with a
sentinel-free payload (a vacuous green).

Each sink is driven by a REAL HTTP call through the mounted router. The service
callable inside the handler's `try:` block is monkeypatched to raise a
ValueError carrying a sentinel; the test asserts the HTTP response body does not
echo any forbidden token. Every sink has a unique (route, injection target,
parametrize id).

Compliance with the audit brief:
- No forbidden token (URL / key / ticket / Authorization / path / SQL) is
  printed in a test name, an assertion message, or a diff: assertions compute a
  boolean over the token set and fail with a content-free message.
- Companion GREEN tests confirm the normal business path still succeeds after a
  fix, so the suite is not a blanket "reject everything" gate.
"""
from __future__ import annotations

import contextlib
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# Sentinel exception text. Fabricated, but shaped like the classes of data the
# audit forbids echoing: an upstream URL with credentials, an absolute path, and
# a SQL fragment. If str(exc) reaches the response, these tokens appear in it.
_S_URL = "https://" + "evil.example/api?key=" + "SECRET_K" + "EY_123&token=BEARER_ABC"
_S_PATH = "C:\\" + "Windows\\System32\\config\\SAM"
_S_SQL = "'; DR" + "OP TABLE events; --"
SENTINEL_MESSAGE = f"upstream failure {_S_URL} {_S_PATH} {_S_SQL}"

# Tokens that must never surface in a client-visible detail. Kept as fragments so
# the literal never appears whole in this source or in any captured diff.
FORBIDDEN_TOKENS = (
    "evil.example",
    "SECRET_K" + "EY",
    "BEARER_ABC",
    "Windows\\System32",
    "DR" + "OP TABLE",
)

BASE = "/api/events"


def _leaks(detail_str: str) -> bool:
    """True if any forbidden token surfaced. Content is never returned."""
    return any(tok in detail_str for tok in FORBIDDEN_TOKENS)


# Source-to-sink map. Each row: (sink_no, line, route, patch_targets, has_body).
# patch_targets: service callables inside the handler try-block, bound at the
# events.py module namespace (all imported at module top), patched to raise the
# sentinel. `has_body` routes take a JSON body model (dict/list); an empty {}
# passes validation and reaches the handler.
#
# Sink 24 (line 661, api-football import) is additionally gated by
# _require_successful_api_football_validation_before_import(); that gate is
# neutralised so the ValueError reaches its own sink rather than a 409.
SINKS = [
    (2, 493, "/sports/world-cup/facts/import", ["import_sports_facts"], True),
    (4, 509, "/sports/world-cup/data/import", ["import_world_cup_data"], True),
    (5, 524, "/sports/world-cup/data/preview", ["world_cup_data_to_facts"], True),
    (6, 619, "/sports/world-cup/data/bundle/feeds/preview", ["preview_world_cup_source_bundle_feeds"], False),
    (7, 633, "/sports/world-cup/data/bundle/feeds/import", ["import_world_cup_source_bundle_feeds"], False),
    (8, 646, "/sports/world-cup/data/bundle/api-football/preview", ["preview_world_cup_api_football_bundle"], False),
    (9, 661, "/sports/world-cup/data/bundle/api-football/import", ["import_world_cup_api_football_bundle"], False),
    (10, 762, "/sports/world-cup/data/bundle/football-data/preview", ["preview_world_cup_football_data_standings"], False),
    (11, 781, "/sports/world-cup/data/bundle/football-data/import", ["import_world_cup_football_data_standings"], False),
    (12, 792, "/sports/world-cup/data/bundle/sportmonks/preview", ["preview_world_cup_sportmonks_bundle"], False),
    (13, 806, "/sports/world-cup/data/bundle/sportmonks/import", ["import_world_cup_sportmonks_bundle"], False),
    (14, 838, "/sports/world-cup/data/source/preview", ["preview_world_cup_data_file"], False),
    (15, 855, "/sports/world-cup/data/source/import", ["import_world_cup_data_file"], False),
    (16, 867, "/sports/world-cup/official-csv/preview", ["preview_world_cup_official_csv_source"], True),
    (17, 880, "/sports/world-cup/official-csv/import", ["import_world_cup_official_csv_source"], True),
    (18, 892, "/sports/world-cup/matches/preview", ["preview_world_cup_match_source"], True),
    (19, 905, "/sports/world-cup/matches/import", ["import_world_cup_match_source"], True),
    (20, 917, "/sports/world-cup/match-events/preview", ["preview_world_cup_match_events_source"], True),
    (21, 930, "/sports/world-cup/match-events/import", ["import_world_cup_match_events_source"], True),
    (22, 942, "/sports/world-cup/lineups/preview", ["preview_world_cup_lineups_source"], True),
    (23, 955, "/sports/world-cup/lineups/import", ["import_world_cup_lineups_source"], True),
    (25, 967, "/sports/world-cup/standings/preview", ["preview_world_cup_standings_source"], True),
    (26, 980, "/sports/world-cup/standings/import", ["import_world_cup_standings_source"], True),
    (27, 992, "/sports/world-cup/player-awards/preview", ["preview_world_cup_player_awards_source"], True),
    (28, 1005, "/sports/world-cup/player-awards/import", ["import_world_cup_player_awards_source"], True),
    (29, 1017, "/sports/world-cup/player-status/preview", ["preview_world_cup_player_status_source"], True),
    (30, 1030, "/sports/world-cup/player-status/import", ["import_world_cup_player_status_source"], True),
    (31, 1055, "/sports/world-cup/statistics/import", ["import_world_cup_statistics_source"], True),
]


def _client() -> TestClient:
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    "sink_no,line,route,targets,has_body",
    SINKS,
    ids=[f"sink{n}_line{ln}" for (n, ln, *_rest) in SINKS],
)
def test_str_exc_sink_does_not_echo_exception_text(
    sink_no: int, line: int, route: str, targets: list[str], has_body: bool
):
    """A ValueError raised by the handler's service must not reach the client.

    RED (pre-fix): handler does `detail=str(exc)`, so the sentinel surfaces.
    GREEN (post-fix): handler returns a fixed message; no token surfaces.
    """
    from app.api.security import settings as security_settings

    client = _client()
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(security_settings, "ALLOW_OPEN_WRITES", True))
        # Sink 24 (line 661) sits behind a validation gate; neutralise it so the
        # injected ValueError lands on this sink, not the 409 pre-check.
        if line == 661:
            stack.enter_context(
                patch(
                    "app.api.routes.events._require_successful_api_football_validation_before_import",
                    return_value=None,
                )
            )
        for target in targets:
            stack.enter_context(
                patch(
                    f"app.api.routes.events.{target}",
                    side_effect=ValueError(SENTINEL_MESSAGE),
                )
            )
        resp = client.post(f"{BASE}{route}", json={} if has_body else None)

    # Status contract for a ValueError at these sinks is 422 today; the fix must
    # preserve the code, so assert it explicitly rather than accepting anything.
    assert resp.status_code == 422, f"sink {sink_no} (line {line}): status {resp.status_code}"
    detail_str = str(resp.json().get("detail", ""))
    # Compute the verdict OUTSIDE the assert: pytest rewrites the assert
    # expression and would otherwise print the leaked detail (with the sentinel)
    # into the failure report. Asserting a pre-computed bool keeps content out.
    leaked = _leaks(detail_str)
    assert not leaked, (
        f"sink {sink_no} (line {line}, {route}) echoed a forbidden token in the "
        f"HTTP detail (content withheld from this message per audit rule)"
    )


# --- GREEN companions: the normal business path must still work after a fix. ---


def test_data_preview_normal_business_succeeds():
    """Companion for sink 5 (line 524): a valid data payload previews facts."""
    from app.api.security import settings as security_settings

    client = _client()
    with patch.object(security_settings, "ALLOW_OPEN_WRITES", True):
        resp = client.post(
            f"{BASE}/sports/world-cup/data/preview",
            json={
                "matches": [
                    {"match_id": "wc2026_t", "home_team": "Brazil", "away_team": "Argentina"}
                ]
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert "converted_fact_count" in body and "facts" in body


def test_statistics_preview_already_fixed_message_stays_safe():
    """Regression: line 1042 (statistics/preview) already uses a fixed message.

    It is NOT one of the 28 str(exc) sinks; this asserts it stays safe even when
    its service raises a sentinel-bearing ValueError.
    """
    from app.api.security import settings as security_settings

    client = _client()
    with patch.object(security_settings, "ALLOW_OPEN_WRITES", True):
        with patch(
            "app.api.routes.events.preview_world_cup_statistics_source",
            side_effect=ValueError(SENTINEL_MESSAGE),
        ):
            resp = client.post(
                f"{BASE}/sports/world-cup/statistics/preview", json={}
            )
    assert resp.status_code == 422
    leaked = _leaks(str(resp.json().get("detail", "")))
    assert not leaked


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
