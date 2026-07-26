"""Static football style stats: possession / shots / PPDA (P1-F6).

Soft multi-year-ish consensus levels (not live scrape).
Missing / empty name → None. Engine formula lives in MultiFactor (unchanged).
"""
from __future__ import annotations

# Soft static style. Keys are _normalize()'d English fixture names.
# Values: (possession_pct, shots_per90, ppda). Lower PPDA = stronger press.
# Operators update by PR. Not a live season snapshot.
_TEAM_STYLE: dict[str, tuple[float, float, float]] = {
    # EPL
    "arsenal": (57.0, 14.5, 9.5),
    "aston villa": (54.0, 13.0, 10.5),
    "bournemouth": (48.0, 12.5, 11.5),
    "brentford": (47.0, 12.0, 12.0),
    "brighton": (56.0, 14.0, 10.0),
    "brighton and hove albion": (56.0, 14.0, 10.0),
    "chelsea": (58.0, 14.5, 9.8),
    "crystal palace": (45.0, 11.5, 12.5),
    "everton": (43.0, 11.0, 13.5),
    "fulham": (50.0, 12.0, 11.8),
    "ipswich": (42.0, 10.5, 14.0),
    "ipswich town": (42.0, 10.5, 14.0),
    "leicester": (44.0, 11.0, 13.0),
    "leicester city": (44.0, 11.0, 13.0),
    "liverpool": (60.0, 16.0, 8.5),
    "manchester city": (65.0, 17.5, 9.0),
    "man city": (65.0, 17.5, 9.0),
    "manchester united": (54.0, 13.5, 11.0),
    "man united": (54.0, 13.5, 11.0),
    "man utd": (54.0, 13.5, 11.0),
    "newcastle": (52.0, 13.5, 10.2),
    "newcastle united": (52.0, 13.5, 10.2),
    "nottingham forest": (44.0, 11.5, 12.8),
    "southampton": (46.0, 11.0, 12.5),
    "tottenham": (57.0, 15.0, 9.6),
    "tottenham hotspur": (57.0, 15.0, 9.6),
    "spurs": (57.0, 15.0, 9.6),
    "west ham": (46.0, 12.0, 12.2),
    "west ham united": (46.0, 12.0, 12.2),
    "wolves": (47.0, 11.5, 12.0),
    "wolverhampton": (47.0, 11.5, 12.0),
    "wolverhampton wanderers": (47.0, 11.5, 12.0),
    # La Liga
    "real madrid": (58.0, 16.0, 9.8),
    "real madrid cf": (58.0, 16.0, 9.8),
    "barcelona": (64.0, 15.5, 9.2),
    "fc barcelona": (64.0, 15.5, 9.2),
    "atletico madrid": (50.0, 12.5, 10.5),
    "atlético madrid": (50.0, 12.5, 10.5),
    "atletico de madrid": (50.0, 12.5, 10.5),
    "sevilla": (52.0, 12.0, 11.5),
    "real sociedad": (55.0, 13.0, 10.8),
    "villarreal": (53.0, 13.0, 11.0),
    "athletic bilbao": (51.0, 12.5, 10.5),
    "athletic club": (51.0, 12.5, 10.5),
    "real betis": (52.0, 12.5, 11.2),
    "girona": (56.0, 13.5, 10.5),
    # Serie A
    "inter": (57.0, 15.0, 9.5),
    "inter milan": (57.0, 15.0, 9.5),
    "internazionale": (57.0, 15.0, 9.5),
    "ac milan": (55.0, 14.0, 10.2),
    "milan": (55.0, 14.0, 10.2),
    "juventus": (53.0, 13.0, 11.0),
    "napoli": (56.0, 14.5, 10.0),
    "roma": (52.0, 13.0, 11.0),
    "as roma": (52.0, 13.0, 11.0),
    "lazio": (53.0, 13.0, 10.8),
    "atalanta": (54.0, 15.5, 9.2),
    "fiorentina": (54.0, 13.5, 10.5),
    # Bundesliga
    "bayern munich": (62.0, 17.0, 8.8),
    "fc bayern munich": (62.0, 17.0, 8.8),
    "bayern münchen": (62.0, 17.0, 8.8),
    "fc bayern münchen": (62.0, 17.0, 8.8),
    "borussia dortmund": (58.0, 15.0, 9.8),
    "dortmund": (58.0, 15.0, 9.8),
    "bvb": (58.0, 15.0, 9.8),
    "rb leipzig": (55.0, 14.5, 9.5),
    "leipzig": (55.0, 14.5, 9.5),
    "bayer leverkusen": (58.0, 15.5, 9.0),
    "leverkusen": (58.0, 15.5, 9.0),
    "eintracht frankfurt": (52.0, 13.0, 11.0),
    "wolfsburg": (50.0, 12.5, 11.5),
    "borussia monchengladbach": (51.0, 13.0, 11.2),
    "monchengladbach": (51.0, 13.0, 11.2),
    # Ligue 1
    "psg": (66.0, 16.5, 9.0),
    "paris saint-germain": (66.0, 16.5, 9.0),
    "paris saint germain": (66.0, 16.5, 9.0),
    "marseille": (54.0, 13.5, 10.8),
    "olympique marseille": (54.0, 13.5, 10.8),
    "lyon": (55.0, 13.5, 10.5),
    "olympique lyonnais": (55.0, 13.5, 10.5),
    "monaco": (54.0, 14.0, 10.2),
    "as monaco": (54.0, 14.0, 10.2),
    "lille": (53.0, 13.0, 10.8),
    "lens": (52.0, 12.5, 11.0),
    "nice": (51.0, 12.0, 11.5),
    # Other frequent UCL / European
    "ajax": (58.0, 14.5, 10.0),
    "porto": (56.0, 14.0, 10.5),
    "fc porto": (56.0, 14.0, 10.5),
    "benfica": (57.0, 14.5, 10.2),
    "sporting": (56.0, 14.0, 10.5),
    "sporting cp": (56.0, 14.0, 10.5),
    "sporting lisbon": (56.0, 14.0, 10.5),
    "celtic": (62.0, 15.0, 10.0),
    "rangers": (58.0, 14.0, 10.8),
    "galatasaray": (55.0, 14.0, 10.5),
    "fenerbahce": (54.0, 13.5, 11.0),
    "shakhtar donetsk": (55.0, 13.5, 10.8),
    "red star belgrade": (52.0, 12.5, 11.5),
    "club brugge": (54.0, 13.0, 11.0),
    "psv": (58.0, 15.0, 10.0),
    "psv eindhoven": (58.0, 15.0, 10.0),
    "feyenoord": (56.0, 14.5, 10.2),
    "salzburg": (55.0, 14.0, 9.8),
    "rb salzburg": (55.0, 14.0, 9.8),
    "dynamo kyiv": (52.0, 12.5, 11.5),
    "slavia prague": (54.0, 13.0, 11.0),
}


def _normalize(name: str) -> str:
    return " ".join((name or "").lower().split())


def _clamp(val: float, lo: float, hi: float) -> float:
    if val < lo:
        return lo
    if val > hi:
        return hi
    return val


def stats_for_team(team_name: str) -> dict[str, float] | None:
    """Return soft style stats for a club name, or None if unknown/empty."""
    key = _normalize(team_name)
    if not key:
        return None
    row = _TEAM_STYLE.get(key)
    if row is None:
        return None
    try:
        poss, shots, ppda = float(row[0]), float(row[1]), float(row[2])
    except (TypeError, ValueError, IndexError):
        return None
    return {
        "possession_pct": round(_clamp(poss, 30.0, 75.0), 1),
        "shots_per90": round(_clamp(shots, 5.0, 25.0), 2),
        "ppda": round(_clamp(ppda, 5.0, 20.0), 2),
    }
