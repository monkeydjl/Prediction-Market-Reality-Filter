# World Cup Prediction Factor System - Technical Documentation

## Overview

The enhanced factor system implements a comprehensive, production-grade prediction signal framework based on sports analytics best practices. It captures 5 distinct categories of predictive signals, from team strength fundamentals to market wisdom.

## Architecture

```
Raw Data Sources
    ↓
Factor Calculation (5 Categories)
    ↓
Normalized Signals (0.0 - 2.0 scale)
    ↓
Prediction Engines (Rule-based + AI)
    ↓
Final Match Predictions
```

## Factor Categories

### 1. Team Strength Factors (Fundamentals)

**Purpose**: Quantify intrinsic team quality independent of context

**Signals**:
- **Elo Rating**: Universal skill rating (1000-2200)
  - Calculated from FIFA ranking if not available
  - Formula: `Elo = 2200 - (fifa_rank × 6)`
  
- **Attack Strength**: Goals + Expected Goals (xG)
  - Normalized to 1.5 goals/game = 1.0 strength
  - Range: 0.0 (weak) to 2.0 (elite)
  
- **Defense Strength**: Inverted goals conceded + xGA
  - 1.2 goals conceded = 1.0 strength
  - Higher is better (clean sheets)
  
- **Form Rating**: Recent match results (W/D/L)
  - 3-1-0 points system
  - Range: 0.0 (all losses) to 1.0 (all wins)
  
- **Squad Availability**: Injury + suspension impact
  - Key player injuries: -0.1 per player
  - Regular injuries: -0.3 × (injured / squad_size)
  - Max penalty: -0.5

**Why It Matters**: These are the most stable predictors over time. A strong team stays strong regardless of venue or fatigue.

---

### 2. Historical Performance (H2H & Context)

**Purpose**: Capture matchup-specific patterns and psychological edges

**Signals**:
- **Head-to-Head Records**:
  - Win rates: home/draw/away from historical meetings
  - Dominance: `(home_wins - away_wins) / total` (-1 to +1)
  - Goal averages: typical scoring patterns
  
- **Opponent Quality Performance**:
  - Win rate vs top-tier opponents (FIFA top 20)
  - Win rate vs mid-tier (FIFA 21-80)
  - Win rate vs low-tier (FIFA 80+)

**Why It Matters**: Some teams have a "bogey team" they struggle against despite overall strength. H2H reveals these dynamics.

**Example**: 
```
Spain vs Italy: Spain 60% overall form, but 30% vs Italy historically
→ H2H overrides generic form rating
```

---

### 3. Situational Context (External Factors)

**Purpose**: Account for match-specific conditions affecting performance

**Signals**:
- **Fatigue**:
  - 0-2 days rest: high fatigue (-0.10 modifier)
  - 3-4 days: medium fatigue (-0.05)
  - 5+ days: fully rested (0.0)
  - Schedule density: matches in last 14 days
  
- **Venue Impact**:
  - Home advantage: +0.05 (World Cup neutral venues)
  - Travel distance: >3000km = -0.05, >5000km = -0.08
  - Jet lag risk for intercontinental travel
  
- **Altitude**:
  - >2000m elevation: -0.10 for non-adapted teams
  - Examples: Mexico City (2240m), La Paz (3640m)
  
- **Climate**:
  - Tropical/extreme heat: -0.05 for non-adapted
  - Teams from similar climates get 0.0 penalty

**Why It Matters**: A team on 2 days rest traveling 5000km to high altitude can lose 0.2+ expected goals.

**Example**:
```
European team traveling to Mexico City:
  Travel: -0.08
  Altitude: -0.10
  Total: -0.18 modifier (~15% performance drop)
```

---

### 4. Market & External Signals (Wisdom of Crowds)

**Purpose**: Leverage aggregated information from betting markets and valuations

**Signals**:
- **Betting Odds** (STRONGEST SINGLE PREDICTOR):
  - Convert decimal odds to implied probabilities
  - Remove bookmaker margin (overround)
  - Extract market confidence from favorite/underdog gap
  
  Formula:
  ```
  Implied probability = 1 / decimal_odds
  Normalized = implied / (sum of all implied)
  ```
  
  Example:
  ```
  Home 2.10 → 47.6% implied
  Draw 3.20 → 31.3% implied  
  Away 3.50 → 28.6% implied
  Total = 107.5% (7.5% margin)
  
  Normalized:
  Home: 44.3%
  Draw: 29.1%
  Away: 26.6%
  ```

- **Squad Market Value**:
  - Transfermarkt total squad value (€ millions)
  - Value ratio: home_value / away_value
  - Quality tiers: elite (>€800M), top (>€500M), mid (>€300M), low (<€300M)

- **Sentiment** (optional):
  - Media mentions and social buzz
  - Public sentiment scores
  - Momentum indicators

**Why It Matters**: Betting markets aggregate information from thousands of experts, sharp bettors, and statistical models. They often outperform individual models.

**Research Finding**: Closing betting odds explain ~70% of variance in match outcomes (higher than any single statistical model).

---

### 5. Model Features (Derived Signals)

**Purpose**: Create composite features for machine learning models

**Signals**:
- **Elo Win Probability**:
  ```
  P(home wins) = 1 / (1 + 10^(-elo_diff / 400))
  ```
  
- **Matchup Advantages**:
  - Home attack vs away defense: `home_attack / away_defense`
  - Away attack vs home defense: `away_attack / home_defense`
  
- **Form Differential**: `home_form - away_form`

- **Quality Scores**: Combined Elo + form rating

- **Expected Total Goals**: Average of both teams' goal rates

**Why It Matters**: These derived features are non-linear combinations that capture interaction effects (e.g., "strong attack meets weak defense").

**Usage in XGBoost**:
```python
features = [
    'elo_difference',
    'elo_win_probability',
    'home_matchup_advantage',
    'away_matchup_advantage',
    'form_differential',
    'quality_gap',
    'market_implied_home',  # From betting odds
    'venue_total_modifier',
    'fatigue_differential',
]
```

---

## Integration with Prediction Engines

### Rule-Based Engine (Poisson)
```python
# Use core strength factors
home_lambda = (
    home_attack_strength * 
    away_defense_weakness * 
    (1 + venue_modifier + fatigue_modifier)
)

# Apply Poisson distribution
P(home scores k goals) = e^(-λ) * λ^k / k!
```

### AI Engine (LLM)
```python
# Provide comprehensive context
prompt = f"""
Team Strength:
  Home Elo: {elo_home}, Attack: {attack_home}
  Away Elo: {elo_away}, Defense: {defense_away}

Market Signal:
  Betting odds imply: {market_home_win}% home win
  
Context:
  Fatigue: Home {fatigue_home}, Away {fatigue_away}
  H2H: Last 5 matches show {h2h_pattern}
  
Predict score considering all factors.
"""
```

### Hybrid Fusion
```python
# Weight by reliability
rule_weight = 0.7  # Stable, mathematically sound
ai_weight = 0.3    # Captures tactical nuances

final_prediction = (
    rule_prediction * rule_weight +
    ai_prediction * ai_weight
)

# Adjust confidence based on signal agreement
if abs(rule_score - ai_score) < 0.5:
    confidence += 0.1  # Models agree
else:
    confidence -= 0.1  # Models disagree
```

---

## Data Requirements

### Minimum (Core Predictions)
- Goals per game
- Goals conceded per game
- Recent W/D/L record
- Days since last match

### Recommended (Good Predictions)
+ Elo rating or FIFA ranking
+ Expected goals (xG, xGA)
+ H2H historical data
+ Injury/suspension list

### Optimal (Best Predictions)
++ Betting market odds
++ Squad market values
++ Venue details (travel, altitude, climate)
++ Schedule density metrics

---

## Factor Weighting Philosophy

**By Predictive Power** (approximate):
1. **Betting Odds**: 30-40% (if available)
2. **Elo Rating**: 25-30%
3. **Recent Form**: 10-15%
4. **H2H History**: 5-10%
5. **Situational Context**: 5-10%
6. **Squad Value**: 3-5%

**Why Odds Are Strongest**:
- Aggregate thousands of expert opinions
- Include information we can't measure (locker room dynamics, motivation)
- Self-correcting through market efficiency
- Penalize wrong predictions with real money

**When To Downweight Odds**:
- Low liquidity markets (small tournaments)
- Very early odds (before lineups known)
- Suspicious line movements (potential manipulation)

---

## Testing & Validation

### Test Scenario: Brazil vs Argentina
```
Elo: Brazil 2100 vs Argentina 2050 (+50 advantage)
Form: Brazil 76.7% vs Argentina 70.0%
Squad: Brazil €850M vs Argentina €900M (-6% value)
Injuries: Argentina -3 players (-0.14 impact)
Odds: Brazil 2.10 (44.3% implied)

Model Output:
  Elo model: 57.1% Brazil win
  Market: 44.3% Brazil win
  → Calibration gap suggests odds underrate Brazil
  → Or injuries more impactful than Elo captures
```

### Validation Metrics
- **Calibration**: Do 60% predictions win 60% of the time?
- **Discrimination**: Can model separate 80% favorites from 50/50 tossups?
- **Brier Score**: Mean squared error of probability predictions
- **Log Loss**: Penalizes confident wrong predictions

---

## Future Enhancements

1. **Player-Level Modeling**:
   - Individual xG, xA, defensive actions
   - Player-vs-player matchups (striker vs CB)
   - Formation impact analysis

2. **Dynamic In-Game Updates**:
   - Real-time score → update expected goals
   - Red cards → recalculate probabilities
   - Substitutions → adjust team strength

3. **Ensemble Models**:
   - XGBoost classifier
   - Neural network regression
   - Weighted ensemble of all models

4. **Causal Inference**:
   - Isolate true impact of each factor
   - Control for confounding variables
   - Counterfactual predictions

---

## References

- Dixon & Coles (1997): "Modelling Association Football Scores"
- Constantinou & Fenton (2012): "Solving the Problem of Inadequate Scoring Rules for Assessing Probabilistic Football Forecasts"
- FiveThirtyEight Soccer Power Index methodology
- Transfermarkt market value methodology
- Odds Portal historical betting data

---

## API Usage

```python
from app.services.world_cup_enhanced_factors import calculate_comprehensive_factors

factors = calculate_comprehensive_factors(
    home_team_name="Brazil",
    away_team_name="Argentina",
    home_team_stats={
        "elo_rating": 2100,
        "goals_per_game": 2.1,
        "goals_conceded_per_game": 0.8,
        # ... more stats
    },
    away_team_stats={...},
    h2h_data={...},
    venue_data={...},
    betting_odds={...},
    context={...}
)

# Access by category
strength = factors['home_strength']
market = factors['market_signals']
model_features = factors['model_features']
```

---

**Version**: 1.0  
**Last Updated**: 2026-06-23  
**Maintained By**: Prediction Market Reality Filter Team
