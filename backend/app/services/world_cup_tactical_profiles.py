"""Tactical style tags for World Cup teams.

Provides structured tactical data that can be injected into AI prompts
and used for matchup analysis. Tags are manually curated based on
team playing style — a one-time effort for 48 teams.

Tactical dimensions:
- pressing: high / mid / low
- tempo: fast / moderate / slow
- width: wide / central / mixed
- build_up: direct / mixed / possession
- defense_line: high / mid / low
- set_piece_strength: strong / average / weak
- counter_attack: strong / average / weak
"""

from typing import Any

# Tactical style profiles for World Cup 2026 teams
# Each team has a profile with tactical dimensions
TACTICAL_PROFILES: dict[str, dict[str, str]] = {
    # South America
    "Brazil": {
        "pressing": "mid", "tempo": "fast", "width": "wide",
        "build_up": "mixed", "defense_line": "mid",
        "set_piece_strength": "average", "counter_attack": "strong",
        "style_summary": "Flair-based attacking, individual brilliance, strong counters",
    },
    "Argentina": {
        "pressing": "high", "tempo": "moderate", "width": "central",
        "build_up": "possession", "defense_line": "mid",
        "set_piece_strength": "strong", "counter_attack": "strong",
        "style_summary": "Messi-centric possession, aggressive press, clinical finishing",
    },
    "Colombia": {
        "pressing": "mid", "tempo": "fast", "width": "wide",
        "build_up": "direct", "defense_line": "mid",
        "set_piece_strength": "strong", "counter_attack": "strong",
        "style_summary": "Direct attacking, physical, strong on counters",
    },
    "Uruguay": {
        "pressing": "high", "tempo": "fast", "width": "central",
        "build_up": "direct", "defense_line": "high",
        "set_piece_strength": "strong", "counter_attack": "average",
        "style_summary": "Aggressive high press, direct, physical",
    },
    "Ecuador": {
        "pressing": "mid", "tempo": "moderate", "width": "mixed",
        "build_up": "mixed", "defense_line": "mid",
        "set_piece_strength": "strong", "counter_attack": "average",
        "style_summary": "Physical, altitude-adapted, strong set pieces",
    },
    # Europe
    "Germany": {
        "pressing": "high", "tempo": "fast", "width": "wide",
        "build_up": "possession", "defense_line": "high",
        "set_piece_strength": "strong", "counter_attack": "average",
        "style_summary": "Positional play, high press, efficient finishing",
    },
    "France": {
        "pressing": "mid", "tempo": "fast", "width": "wide",
        "build_up": "direct", "defense_line": "mid",
        "set_piece_strength": "strong", "counter_attack": "strong",
        "style_summary": "Athletic, transition-based, lethal counters with Mbappe",
    },
    "Spain": {
        "pressing": "high", "tempo": "slow", "width": "central",
        "build_up": "possession", "defense_line": "high",
        "set_piece_strength": "average", "counter_attack": "weak",
        "style_summary": "Tiki-taka possession, patient build-up, control-oriented",
    },
    "England": {
        "pressing": "high", "tempo": "fast", "width": "wide",
        "build_up": "mixed", "defense_line": "high",
        "set_piece_strength": "strong", "counter_attack": "strong",
        "style_summary": "High press, wing play, strong set pieces, Bellingham-driven",
    },
    "Netherlands": {
        "pressing": "high", "tempo": "fast", "width": "wide",
        "build_up": "possession", "defense_line": "high",
        "set_piece_strength": "strong", "counter_attack": "average",
        "style_summary": "Total football heritage, attacking fullbacks, high line",
    },
    "Portugal": {
        "pressing": "mid", "tempo": "moderate", "width": "central",
        "build_up": "possession", "defense_line": "mid",
        "set_piece_strength": "strong", "counter_attack": "strong",
        "style_summary": "Technical possession, individual quality, flexible formation",
    },
    "Belgium": {
        "pressing": "mid", "tempo": "moderate", "width": "central",
        "build_up": "mixed", "defense_line": "mid",
        "set_piece_strength": "average", "counter_attack": "strong",
        "style_summary": "Transitional play, De Bruyne orchestration, aging core",
    },
    "Croatia": {
        "pressing": "mid", "tempo": "slow", "width": "central",
        "build_up": "possession", "defense_line": "mid",
        "set_piece_strength": "average", "counter_attack": "average",
        "style_summary": "Midfield control, patient, experienced, Modric-led",
    },
    "Italy": {
        "pressing": "high", "tempo": "moderate", "width": "central",
        "build_up": "possession", "defense_line": "high",
        "set_piece_strength": "strong", "counter_attack": "average",
        "style_summary": "Tactical discipline, defensive tradition, catenaccio evolution",
    },
    "Switzerland": {
        "pressing": "mid", "tempo": "moderate", "width": "mixed",
        "build_up": "mixed", "defense_line": "mid",
        "set_piece_strength": "average", "counter_attack": "strong",
        "style_summary": "Compact defense, organized, counter-attacking",
    },
    # North America
    "USA": {
        "pressing": "high", "tempo": "fast", "width": "wide",
        "build_up": "direct", "defense_line": "high",
        "set_piece_strength": "strong", "counter_attack": "average",
        "style_summary": "Athletic, high-energy press, direct, physical",
    },
    "Mexico": {
        "pressing": "mid", "tempo": "fast", "width": "wide",
        "build_up": "mixed", "defense_line": "mid",
        "set_piece_strength": "strong", "counter_attack": "strong",
        "style_summary": "Technical wingers, home advantage at altitude, passionate",
    },
    "Canada": {
        "pressing": "mid", "tempo": "fast", "width": "central",
        "build_up": "direct", "defense_line": "mid",
        "set_piece_strength": "average", "counter_attack": "strong",
        "style_summary": "Counter-attacking, Davies-driven pace, developing",
    },
    # Asia
    "Japan": {
        "pressing": "high", "tempo": "fast", "width": "central",
        "build_up": "possession", "defense_line": "high",
        "set_piece_strength": "average", "counter_attack": "strong",
        "style_summary": "Technical, organized press, quick transitions, disciplined",
    },
    "South Korea": {
        "pressing": "mid", "tempo": "fast", "width": "central",
        "build_up": "direct", "defense_line": "mid",
        "set_piece_strength": "average", "counter_attack": "strong",
        "style_summary": "Pace-based, Son-driven, transitional, energetic",
    },
    "Iran": {
        "pressing": "low", "tempo": "slow", "width": "central",
        "build_up": "direct", "defense_line": "low",
        "set_piece_strength": "strong", "counter_attack": "strong",
        "style_summary": "Defensive solidity, compact, dangerous counters",
    },
    "Australia": {
        "pressing": "mid", "tempo": "fast", "width": "wide",
        "build_up": "direct", "defense_line": "mid",
        "set_piece_strength": "strong", "counter_attack": "average",
        "style_summary": "Physical, aerial threat, direct, high energy",
    },
    # Africa
    "Morocco": {
        "pressing": "mid", "tempo": "moderate", "width": "wide",
        "build_up": "mixed", "defense_line": "mid",
        "set_piece_strength": "strong", "counter_attack": "strong",
        "style_summary": "Defensive solidity, organized, lethal counters, 2022 semifinalist",
    },
    "Senegal": {
        "pressing": "high", "tempo": "fast", "width": "wide",
        "build_up": "direct", "defense_line": "high",
        "set_piece_strength": "average", "counter_attack": "strong",
        "style_summary": "Athletic, aggressive press, pace-based, physical",
    },
    "Nigeria": {
        "pressing": "mid", "tempo": "fast", "width": "central",
        "build_up": "direct", "defense_line": "mid",
        "set_piece_strength": "average", "counter_attack": "strong",
        "style_summary": "Pace-based, individual quality, transitional",
    },
    "Egypt": {
        "pressing": "low", "tempo": "slow", "width": "central",
        "build_up": "direct", "defense_line": "low",
        "set_piece_strength": "average", "counter_attack": "strong",
        "style_summary": "Defensive, Salah-dependent counters, organized",
    },
    "Cameroon": {
        "pressing": "mid", "tempo": "fast", "width": "central",
        "build_up": "direct", "defense_line": "mid",
        "set_piece_strength": "strong", "counter_attack": "average",
        "style_summary": "Physical, athletic, direct, strong aerially",
    },
}

# Default profile for teams not in the mapping
DEFAULT_PROFILE = {
    "pressing": "mid", "tempo": "moderate", "width": "mixed",
    "build_up": "mixed", "defense_line": "mid",
    "set_piece_strength": "average", "counter_attack": "average",
    "style_summary": "Balanced, no distinct tactical identity profiled",
}


def get_tactical_profile(team_name: str) -> dict[str, str]:
    """Get tactical profile for a team.

    Args:
        team_name: Team name

    Returns:
        Tactical profile dict with pressing, tempo, width, etc.
    """
    # Try exact match
    if team_name in TACTICAL_PROFILES:
        return TACTICAL_PROFILES[team_name]

    # Try case-insensitive
    lower_map = {k.lower(): v for k, v in TACTICAL_PROFILES.items()}
    if team_name.lower() in lower_map:
        return lower_map[team_name.lower()]

    return DEFAULT_PROFILE.copy()


def get_matchup_analysis(home_team: str, away_team: str) -> dict[str, Any]:
    """Analyze tactical matchup between two teams.

    Args:
        home_team: Home team name
        away_team: Away team name

    Returns:
        Matchup analysis with advantage assessment
    """
    home = get_tactical_profile(home_team)
    away = get_tactical_profile(away_team)

    # Determine tactical advantages
    advantages = []

    # Pressing vs Build-up: high press vs slow build-up = advantage
    if home["pressing"] == "high" and away["build_up"] == "possession":
        advantages.append(f"{home_team} high press may disrupt {away_team} possession build-up")
    if away["pressing"] == "high" and home["build_up"] == "possession":
        advantages.append(f"{away_team} high press may disrupt {home_team} possession build-up")

    # Counter-attack vs High defense line
    if home["counter_attack"] == "strong" and away["defense_line"] == "high":
        advantages.append(f"{home_team} counter-attack may exploit {away_team} high defensive line")
    if away["counter_attack"] == "strong" and home["defense_line"] == "high":
        advantages.append(f"{away_team} counter-attack may exploit {home_team} high defensive line")

    # Tempo mismatch
    if home["tempo"] == "fast" and away["tempo"] == "slow":
        advantages.append(f"{home_team} fast tempo may overwhelm {away_team} slow build-up")
    if away["tempo"] == "fast" and home["tempo"] == "slow":
        advantages.append(f"{away_team} fast tempo may overwhelm {home_team} slow build-up")

    # Set piece advantage
    if home["set_piece_strength"] == "strong" and away["set_piece_strength"] == "weak":
        advantages.append(f"{home_team} has significant set-piece advantage")
    if away["set_piece_strength"] == "strong" and home["set_piece_strength"] == "weak":
        advantages.append(f"{away_team} has significant set-piece advantage")

    return {
        "home_profile": home,
        "away_profile": away,
        "matchup_advantages": advantages,
        "style_contrast": {
            "tempo": f"{home['tempo']} vs {away['tempo']}",
            "pressing": f"{home['pressing']} vs {away['pressing']}",
            "build_up": f"{home['build_up']} vs {away['build_up']}",
            "defense_line": f"{home['defense_line']} vs {away['defense_line']}",
        },
    }


def format_tactical_summary(home_team: str, away_team: str) -> str:
    """Format tactical analysis as a text summary for AI prompt injection.

    Args:
        home_team: Home team name
        away_team: Away team name

    Returns:
        Formatted text string for prompt injection
    """
    matchup = get_matchup_analysis(home_team, away_team)
    home = matchup["home_profile"]
    away = matchup["away_profile"]

    text = f"""Tactical Analysis:
{home_team}: {home['style_summary']}
  - Pressing: {home['pressing']}, Tempo: {home['tempo']}, Width: {home['width']}
  - Build-up: {home['build_up']}, Defense line: {home['defense_line']}
  - Set pieces: {home['set_piece_strength']}, Counter-attack: {home['counter_attack']}

{away_team}: {away['style_summary']}
  - Pressing: {away['pressing']}, Tempo: {away['tempo']}, Width: {away['width']}
  - Build-up: {away['build_up']}, Defense line: {away['defense_line']}
  - Set pieces: {away['set_piece_strength']}, Counter-attack: {away['counter_attack']}

Key Matchup Dynamics:"""

    for adv in matchup["matchup_advantages"]:
        text += f"\n- {adv}"

    if not matchup["matchup_advantages"]:
        text += "\n- No significant tactical mismatch detected"

    return text
