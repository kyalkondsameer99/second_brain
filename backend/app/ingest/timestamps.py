from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional

def choose_source_time(
    user_provided: Optional[datetime] = None,
    extracted: Optional[datetime] = None,
    fallback: Optional[datetime] = None,
) -> datetime:
    """
    Deterministic precedence:
    user_provided > extracted > fallback > now()
    Always returns UTC.
    """
    for t in (user_provided, extracted, fallback):
        if t:
            if t.tzinfo is None:
                return t.replace(tzinfo=timezone.utc)
            return t.astimezone(timezone.utc)
    return datetime.now(timezone.utc)
