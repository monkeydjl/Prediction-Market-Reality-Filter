"""What counts as "the API namespace" for this app.

One rule, two consumers: the static mount must not answer for it
(``app/core/static_serving.py``) and ``HEAD`` is answered there as ``GET``
(``app/core/head_support.py``). Two spellings of the predicate would drift, and
``/apiary`` is exactly the case a careless prefix test gets wrong.
"""

from __future__ import annotations

API_ROOT = "/api"


def is_api_path(request_path: str) -> bool:
    """Whether a request path belongs to the API namespace.

    ``/apiary`` must not: a prefix test on ``"/api"`` alone would fence off any
    future page or asset whose name merely starts with those three letters.
    """
    return request_path == API_ROOT or request_path.startswith(API_ROOT + "/")
