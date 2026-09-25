# --- Daily maintenance (P8 H7) ---------------------------------------------------------------


class _Report:
    def __init__(self, remaining: bool = False) -> None:
        self.remaining = remaining


def test_daily_maintenance_runs_once_per_utc_day() -> None:
    from datetime import UTC, datetime, timedelta

    from app.jobs.worker import Worker

    calls: list[int] = []
    worker = Worker(object(), {}, daily=lambda: calls.append(1) or _Report())  # type: ignore[arg-type]
    today = datetime(2026, 9, 25, 23, 0, tzinfo=UTC)
    assert worker.run_daily(today) and not worker.run_daily(today + timedelta(minutes=30))
    assert worker.run_daily(today + timedelta(hours=2))  # a new UTC day
    assert len(calls) == 2


def test_a_full_batch_runs_again_and_a_failure_never_stops_the_worker() -> None:
    from datetime import UTC, datetime

    from app.jobs.worker import Worker

    now = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
    reports = iter([_Report(remaining=True), _Report()])
    worker = Worker(object(), {}, daily=lambda: next(reports))  # type: ignore[arg-type]
    assert worker.run_daily(now) and worker.run_daily(now) and not worker.run_daily(now)

    def boom():
        raise RuntimeError("database unavailable")

    failing = Worker(object(), {}, daily=boom)  # type: ignore[arg-type]
    assert failing.run_daily(now) and failing.run_daily(now)  # retried, never raised
