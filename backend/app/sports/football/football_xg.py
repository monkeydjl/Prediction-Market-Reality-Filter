"""Static football attack xG per 90 (P1-F5).

Soft multi-year-ish consensus levels (not live scrape).
Missing / empty name → None. Engine formula lives in MultiFactor (unchanged).
"""
from __future__ import annotations

# Soft static attack xG/90. Keys are _normalize()'d English fixture names.
# Operators update by PR. Not a live season snapshot.
_TEAM_XG: dict[str, float] = {
    # EPL
    "arsenal": 1.85,
    "aston villa": 1.55,
    "bournemouth": 1.45,
    "brentford": 1.40,
    "brighton": 1.50,
    "brighton and hove albion": 1.50,
    "chelsea": 1.75,
    "crystal palace": 1.35,
    "everton": 1.20,
    "fulham": 1.40,
    "ipswich": 1.15,
    "ipswich town": 1.15,
    "leicester": 1.25,
    "leicester city": 1.25,
    "liverpool": 2.05,
    "manchester city": 2.15,
    "man city": 2.15,
    "manchester united": 1.60,
    "man united": 1.60,
    "man utd": 1.60,
    "newcastle": 1.65,
    "newcastle united": 1.65,
    "nottingham forest": 1.35,
    "southampton": 1.15,
    "tottenham": 1.70,
    "tottenham hotspur": 1.70,
    "spurs": 1.70,
    "west ham": 1.40,
    "west ham united": 1.40,
    "wolves": 1.30,
    "wolverhampton": 1.30,
    "wolverhampton wanderers": 1.30,
    # La Liga
    "real madrid": 2.10,
    "real madrid cf": 2.10,
    "barcelona": 2.00,
    "fc barcelona": 2.00,
    "atletico madrid": 1.55,
    "atlético madrid": 1.55,
    "atletico de madrid": 1.55,
    "sevilla": 1.35,
    "real sociedad": 1.45,
    "villarreal": 1.50,
    "athletic bilbao": 1.45,
    "athletic club": 1.45,
    "real betis": 1.40,
    "girona": 1.45,
    # Serie A
    "inter": 1.90,
    "inter milan": 1.90,
    "internazionale": 1.90,
    "ac milan": 1.70,
    "milan": 1.70,
    "juventus": 1.65,
    "napoli": 1.80,
    "roma": 1.55,
    "as roma": 1.55,
    "lazio": 1.50,
    "atalanta": 1.75,
    "fiorentina": 1.45,
    # Bundesliga
    "bayern munich": 2.20,
    "fc bayern munich": 2.20,
    "bayern münchen": 2.20,
    "fc bayern münchen": 2.20,
    "borussia dortmund": 1.85,
    "dortmund": 1.85,
    "bvb": 1.85,
    "rb leipzig": 1.75,
    "leipzig": 1.75,
    "bayer leverkusen": 1.90,
    "leverkusen": 1.90,
    "eintracht frankfurt": 1.50,
    "wolfsburg": 1.35,
    "borussia monchengladbach": 1.40,
    "monchengladbach": 1.40,
    # Ligue 1
    "psg": 2.15,
    "paris saint-germain": 2.15,
    "paris saint germain": 2.15,
    "marseille": 1.55,
    "olympique marseille": 1.55,
    "lyon": 1.50,
    "olympique lyonnais": 1.50,
    "monaco": 1.65,
    "as monaco": 1.65,
    "lille": 1.50,
    "lens": 1.45,
    "nice": 1.40,
    # Other frequent UCL / European
    "ajax": 1.55,
    "porto": 1.50,
    "fc porto": 1.50,
    "benfica": 1.55,
    "sporting": 1.50,
    "sporting cp": 1.50,
    "sporting lisbon": 1.50,
    "celtic": 1.45,
    "rangers": 1.40,
    "galatasaray": 1.50,
    "fenerbahce": 1.45,
    "shakhtar donetsk": 1.40,
    "red star belgrade": 1.30,
    "club brugge": 1.35,
    "psv": 1.55,
    "psv eindhoven": 1.55,
    "feyenoord": 1.50,
    "salzburg": 1.45,
    "rb salzburg": 1.45,
    "dynamo kyiv": 1.25,
    "slavia prague": 1.30,
}


def _normalize(name: str) -> str:
    return " ".join((name or "").lower().split())


def xg_for_team(team_name: str) -> float | None:
    """Return soft attack xG per 90 for a club name, or None if unknown/empty."""
    key = _normalize(team_name)
    if not key:
        return None
    val = _TEAM_XG.get(key)
    if val is None:
        return None
    try:
        xg = float(val)
    except (TypeError, ValueError):
        return None
    if xg < 0.8:
        xg = 0.8
    elif xg > 2.5:
        xg = 2.5
    return round(xg, 4)
