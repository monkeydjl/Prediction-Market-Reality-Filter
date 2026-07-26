"""Static football referee home-bias (P1-F8).

Soft directional priors (not live FA/league season stats).
Missing / empty name → None. Engine formula lives in MultiFactor (unchanged).
"""
from __future__ import annotations

# Soft home bias in roughly [-0.15, 0.15]. Keys are _normalize()'d English names.
# Positive → slight home favor via home_rate = 0.5 + 0.5 * bias.
# Operators update by PR.
_REFEREE_HOME_BIAS: dict[str, float] = {
    # EPL
    "michael oliver": 0.04,
    "anthony taylor": 0.03,
    "paul tierney": 0.02,
    "stuart attwell": 0.01,
    "craig pawson": 0.02,
    "simon hooper": 0.01,
    "robert jones": 0.02,
    "john brooks": 0.01,
    "david coote": 0.00,
    "andre marriner": 0.02,
    "martin atkinson": 0.03,
    "mike dean": 0.05,
    "chris kavanagh": 0.01,
    "jarred gillett": 0.01,
    "thomas bramall": 0.00,
    "peter bankes": 0.01,
    "andy madley": 0.01,
    "tim robinson": 0.00,
    # La Liga / Spain
    "jesus gil manzano": 0.03,
    "jesús gil manzano": 0.03,
    "antonio mateu lahoz": 0.04,
    "carlos del cerro grande": 0.02,
    "jose maria sanchez martinez": 0.02,
    "josé maría sánchez martínez": 0.02,
    "alejandro hernandez hernandez": 0.02,
    "alejandro hernández hernández": 0.02,
    # Serie A / Italy
    "daniele orsato": 0.03,
    "davide massa": 0.02,
    "marco guida": 0.02,
    "maurizio mariani": 0.01,
    "fabio maresca": 0.01,
    "daniele doveri": 0.02,
    "simone sozza": 0.01,
    # Bundesliga / Germany
    "felix brych": 0.03,
    "daniel siebert": 0.02,
    "felix zwayer": 0.02,
    "tobias stieler": 0.01,
    "deniz aytekin": 0.02,
    "sascha stegemann": 0.01,
    # Ligue 1 / France
    "clement turpin": 0.03,
    "clément turpin": 0.03,
    "francois leterrier": 0.01,
    "françois leterrier": 0.01,
    "benoit bastien": 0.02,
    "benoît bastien": 0.02,
    "ruddy buquet": 0.01,
    # UEFA / international frequent
    "szymon marciniak": 0.03,
    "danny makkelie": 0.02,
    "bjorn kuipers": 0.03,
    "björn kuipers": 0.03,
    "cuneyt cakir": 0.02,
    "cüneyt çakır": 0.02,
    "slavko vincic": 0.02,
    "slavko vinčić": 0.02,
    "istvan kovacs": 0.01,
    "istván kovács": 0.01,
    "halil umut meler": 0.01,
    "artur soares dias": 0.01,
    "ovidiu hategan": 0.01,
    "ovidiu hațegan": 0.01,
}


def _normalize(name: str) -> str:
    return " ".join((name or "").lower().split())


def bias_for_referee(name: str) -> float | None:
    """Soft home-win bias for a referee display name, or None if unknown/empty.

    Bias is in [-0.25, 0.25] where positive favors home win share via:
      home_rate = 0.5 + 0.5 * bias
    """
    key = _normalize(name)
    if not key:
        return None
    val = _REFEREE_HOME_BIAS.get(key)
    if val is None:
        return None
    try:
        b = float(val)
    except (TypeError, ValueError):
        return None
    if b < -0.25:
        b = -0.25
    elif b > 0.25:
        b = 0.25
    return round(b, 2)
