from pydantic import BaseModel
from typing import Optional


class MarketModel(BaseModel):
    id: str
    slug: Optional[str] = None
    event_slug: Optional[str] = None  # parent event slug for URL (gamma API events[0].slug)
    question: str
    yes_price: Optional[float] = None
    no_price: Optional[float] = None
    volume: Optional[float] = None
    liquidity: Optional[float] = None
    closed: bool = False
    archived: bool = False
    resolved: bool = False
    end_date: Optional[str] = None
