"""Quota-aware request budget (ADR 0004).

The Gemini free tier enforces a number of requests per day, per project and model
(20 for gemini-3.7-flash on this project), resetting at midnight Pacific. The
budget keeps SkillMirror from intentionally spending the final requests: a
model's usable budget is its daily limit minus a safety reserve.

Before every provider request the gateway asks the budget for a slot:

    provider requests since the quota day began   (model_runs rows, cache hits excluded)
  + requests in flight in this process
  < daily limit - reserve

Otherwise `ModelBudgetExhaustedError` (transient) is raised before anything is
sent, and the worker defers the job without spending an attempt. Every provider
request counts, including ones that failed: the budget errs on the safe side.
Models without a configured limit are not budgeted; the provider's own 429 still
defers the job.
"""

import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from app.model_gateway.types import ModelBudgetExhaustedError

# Gemini API daily quotas reset at midnight Pacific time.
QUOTA_TIMEZONE = "America/Los_Angeles"


@dataclass(frozen=True)
class QuotaPolicy:
    daily_limits: Mapping[str, int] = field(default_factory=dict)
    reserve: int = 0
    timezone: str = QUOTA_TIMEZONE

    def limit_for(self, model: str) -> int | None:
        limit = self.daily_limits.get(model)
        return limit if limit and limit > 0 else None


class RequestLedger(Protocol):
    def provider_requests_since(self, provider: str, model: str, since: datetime) -> int: ...


@dataclass(frozen=True)
class BudgetStatus:
    model: str
    limit: int
    reserve: int
    used: int
    in_flight: int
    resets_at: datetime

    @property
    def available(self) -> int:
        """Requests SkillMirror may still send today without touching the reserve."""
        return max(self.limit - self.reserve - self.used - self.in_flight, 0)


class RequestBudget:
    def __init__(
        self,
        policy: QuotaPolicy,
        ledger: RequestLedger,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.policy = policy
        self._ledger = ledger
        self._now = now
        self._zone = ZoneInfo(policy.timezone)
        self._lock = threading.Lock()
        self._in_flight: dict[tuple[str, str], int] = {}

    def quota_day(self) -> tuple[datetime, datetime]:
        """(start, next reset) of the current quota day, as aware datetimes."""
        local = self._now().astimezone(self._zone)
        start = local.replace(hour=0, minute=0, second=0, microsecond=0)
        # Aware + timedelta is wall-clock arithmetic: a DST change still resets at midnight.
        return start, start + timedelta(days=1)

    def status(self, provider: str, model: str) -> BudgetStatus | None:
        limit = self.policy.limit_for(model)
        if limit is None:
            return None
        start, reset = self.quota_day()
        used = self._ledger.provider_requests_since(provider, model, start)
        return BudgetStatus(
            model=model,
            limit=limit,
            reserve=self.policy.reserve,
            used=used,
            in_flight=self._in_flight.get((provider, model), 0),
            resets_at=reset,
        )

    @contextmanager
    def request(self, provider: str, model: str) -> Iterator[None]:
        """Hold one request slot for the duration of a provider call (and its recording)."""
        key = (provider, model)
        with self._lock:
            status = self.status(provider, model)
            if status is not None and status.available <= 0:
                wait = max((status.resets_at - self._now()).total_seconds(), 1.0)
                raise ModelBudgetExhaustedError(
                    f"{model}: daily request budget spent ({status.used} used of "
                    f"{status.limit}, reserve {status.reserve}); resets at "
                    f"{status.resets_at.isoformat()}",
                    transient=True,
                    retry_after=wait,
                )
            self._in_flight[key] = self._in_flight.get(key, 0) + 1
        try:
            yield
        finally:
            with self._lock:
                self._in_flight[key] -= 1


def parse_daily_limits(value: str) -> dict[str, int]:
    """'model=limit,model=limit' -> {model: limit}. Empty or 'off' disables budgeting."""
    limits: dict[str, int] = {}
    text = value.strip()
    if not text or text.lower() in ("off", "none"):
        return limits
    for part in text.split(","):
        if not part.strip():
            continue
        model, sep, limit = part.partition("=")
        if not sep or not model.strip() or not limit.strip().isdigit():
            raise ValueError(f"expected model=requests_per_day, got {part.strip()!r}")
        limits[model.strip()] = int(limit.strip())
    return limits
