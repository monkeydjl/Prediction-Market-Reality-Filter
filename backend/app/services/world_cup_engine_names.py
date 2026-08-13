"""The set of World Cup prediction engine names, as a type.

Lives in its own module so the API layer can annotate its request bodies and
query parameters with it — importing the name from
``world_cup_prediction_pipeline`` would drag that module (and its ~80
transitive imports) into application startup, while every route deliberately
imports the pipeline lazily, inside the handler.

Keep the members in sync with the runtime whitelist in
``run_prediction_pipeline`` Step 3.
"""

from typing import Literal

PredictionEngine = Literal[
    "elo_odds", "hybrid", "integrated", "high_confidence", "gbm", "auto"
]
