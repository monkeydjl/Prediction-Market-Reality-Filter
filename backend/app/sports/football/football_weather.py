"""Static football home-city climate by month (P1-F7 residual weather).

Soft multi-year climate priors (not live forecasts). Missing / empty /
bad month → None. MultiFactor does not consume weather this round.
"""
from __future__ import annotations

_CONDITIONS = frozenset({"clear", "mild", "rain", "cold", "hot"})

# Seasonal templates: (DJF, MAM, JJA, SON) each (temp_c, condition)
_TEMPLATES: dict[str, tuple[tuple[float, str], tuple[float, str], tuple[float, str], tuple[float, str]]] = {
    "london": ((5.0, "rain"), (11.0, "mild"), (18.0, "mild"), (12.0, "rain")),
    "manchester": ((4.0, "rain"), (10.0, "mild"), (17.0, "mild"), (11.0, "rain")),
    "liverpool": ((5.0, "rain"), (10.0, "mild"), (17.0, "mild"), (11.0, "rain")),
    "birmingham": ((4.5, "rain"), (10.5, "mild"), (17.5, "mild"), (11.5, "rain")),
    "newcastle": ((4.0, "cold"), (9.0, "mild"), (16.0, "mild"), (10.0, "rain")),
    "madrid": ((7.0, "mild"), (14.0, "clear"), (28.0, "hot"), (16.0, "clear")),
    "barcelona": ((10.0, "mild"), (15.0, "mild"), (26.0, "hot"), (18.0, "clear")),
    "seville": ((12.0, "mild"), (17.0, "clear"), (30.0, "hot"), (20.0, "clear")),
    "bilbao": ((9.0, "rain"), (13.0, "mild"), (21.0, "mild"), (15.0, "rain")),
    "milan": ((4.0, "cold"), (13.0, "mild"), (25.0, "hot"), (14.0, "mild")),
    "rome": ((8.0, "mild"), (14.0, "mild"), (27.0, "hot"), (17.0, "clear")),
    "naples": ((10.0, "mild"), (15.0, "mild"), (27.0, "hot"), (18.0, "clear")),
    "turin": ((3.0, "cold"), (12.0, "mild"), (24.0, "hot"), (13.0, "mild")),
    "munich": ((0.0, "cold"), (10.0, "mild"), (19.0, "mild"), (10.0, "rain")),
    "dortmund": ((2.0, "cold"), (10.0, "mild"), (19.0, "mild"), (11.0, "rain")),
    "leipzig": ((0.5, "cold"), (10.0, "mild"), (20.0, "mild"), (10.5, "rain")),
    "paris": ((5.0, "rain"), (12.0, "mild"), (21.0, "mild"), (13.0, "mild")),
    "marseille": ((9.0, "mild"), (14.0, "clear"), (26.0, "hot"), (17.0, "clear")),
    "lyon": ((4.0, "cold"), (12.0, "mild"), (23.0, "hot"), (13.0, "mild")),
    "amsterdam": ((4.0, "rain"), (10.0, "mild"), (18.0, "mild"), (11.0, "rain")),
    "lisbon": ((12.0, "mild"), (15.0, "mild"), (24.0, "hot"), (18.0, "clear")),
    "porto": ((10.0, "rain"), (14.0, "mild"), (21.0, "mild"), (16.0, "rain")),
    "glasgow": ((3.0, "cold"), (8.0, "rain"), (15.0, "mild"), (9.0, "rain")),
    "istanbul": ((6.0, "cold"), (12.0, "mild"), (24.0, "hot"), (15.0, "mild")),
}


def _months_from_template(
    tpl: tuple[tuple[float, str], tuple[float, str], tuple[float, str], tuple[float, str]],
) -> list[tuple[float, str]]:
    """Expand DJF/MAM/JJA/SON into 12 (temp, condition) rows (Jan=1 index 0)."""
    djf, mam, jja, son = tpl
    out: list[tuple[float, str]] = []
    for m in range(1, 13):
        if m in (12, 1, 2):
            out.append(djf)
        elif m in (3, 4, 5):
            out.append(mam)
        elif m in (6, 7, 8):
            out.append(jja)
        else:
            out.append(son)
    return out


# club normalize key → template name
_CLUB_TEMPLATE: dict[str, str] = {
    # EPL / England
    "arsenal": "london",
    "chelsea": "london",
    "tottenham": "london",
    "tottenham hotspur": "london",
    "spurs": "london",
    "west ham": "london",
    "west ham united": "london",
    "crystal palace": "london",
    "fulham": "london",
    "brentford": "london",
    "manchester city": "manchester",
    "man city": "manchester",
    "manchester united": "manchester",
    "man united": "manchester",
    "man utd": "manchester",
    "liverpool": "liverpool",
    "everton": "liverpool",
    "aston villa": "birmingham",
    "newcastle": "newcastle",
    "newcastle united": "newcastle",
    "brighton": "london",
    "brighton and hove albion": "london",
    "wolves": "birmingham",
    "wolverhampton": "birmingham",
    "wolverhampton wanderers": "birmingham",
    "nottingham forest": "birmingham",
    # Spain
    "real madrid": "madrid",
    "real madrid cf": "madrid",
    "atletico madrid": "madrid",
    "atlético madrid": "madrid",
    "atletico de madrid": "madrid",
    "barcelona": "barcelona",
    "fc barcelona": "barcelona",
    "sevilla": "seville",
    "real betis": "seville",
    "athletic bilbao": "bilbao",
    "athletic club": "bilbao",
    "real sociedad": "bilbao",
    "villarreal": "barcelona",
    "girona": "barcelona",
    # Italy
    "inter": "milan",
    "inter milan": "milan",
    "internazionale": "milan",
    "ac milan": "milan",
    "milan": "milan",
    "juventus": "turin",
    "napoli": "naples",
    "roma": "rome",
    "as roma": "rome",
    "lazio": "rome",
    "atalanta": "milan",
    "fiorentina": "rome",
    # Germany
    "bayern munich": "munich",
    "fc bayern munich": "munich",
    "bayern münchen": "munich",
    "fc bayern münchen": "munich",
    "borussia dortmund": "dortmund",
    "dortmund": "dortmund",
    "bvb": "dortmund",
    "rb leipzig": "leipzig",
    "leipzig": "leipzig",
    "bayer leverkusen": "dortmund",
    "leverkusen": "dortmund",
    "eintracht frankfurt": "munich",
    # France
    "psg": "paris",
    "paris saint-germain": "paris",
    "paris saint germain": "paris",
    "marseille": "marseille",
    "olympique marseille": "marseille",
    "lyon": "lyon",
    "olympique lyonnais": "lyon",
    "monaco": "marseille",
    "as monaco": "marseille",
    "lille": "paris",
    "lens": "paris",
    "nice": "marseille",
    # Europe
    "ajax": "amsterdam",
    "psv": "amsterdam",
    "psv eindhoven": "amsterdam",
    "feyenoord": "amsterdam",
    "porto": "porto",
    "fc porto": "porto",
    "benfica": "lisbon",
    "sporting": "lisbon",
    "sporting cp": "lisbon",
    "sporting lisbon": "lisbon",
    "celtic": "glasgow",
    "rangers": "glasgow",
    "galatasaray": "istanbul",
    "fenerbahce": "istanbul",
}

_MONTHLY: dict[str, list[tuple[float, str]]] = {
    k: _months_from_template(v) for k, v in _TEMPLATES.items()
}


def _normalize(name: str) -> str:
    return " ".join((name or "").lower().split())


def climate_for_home(team_name: str, month: int) -> dict[str, float | str] | None:
    """Soft home-city climate for a fixture month, or None if unknown/empty/bad month."""
    key = _normalize(team_name)
    if not key:
        return None
    try:
        m = int(month)
    except (TypeError, ValueError):
        return None
    if m < 1 or m > 12:
        return None
    tpl_name = _CLUB_TEMPLATE.get(key)
    if tpl_name is None:
        return None
    months = _MONTHLY.get(tpl_name)
    if not months:
        return None
    temp, cond = months[m - 1]
    try:
        t = float(temp)
    except (TypeError, ValueError):
        return None
    if t < -15.0:
        t = -15.0
    elif t > 45.0:
        t = 45.0
    c = str(cond).strip().lower()
    if c not in _CONDITIONS:
        c = "mild"
    return {"temp_c": round(t, 1), "condition": c}
