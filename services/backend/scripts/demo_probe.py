"""Read-only probes for `npm run demo:check` / `npm run demo:verify` (P9). Never writes, never
prints a secret: only names, flags, model ids, counts and migration versions.

    python scripts/demo_probe.py config     the backend's Settings exactly as the demo loads
                                            them (services/backend/.env + the demo overrides the
                                            caller put in the environment): valid? worker on?
                                            which models and budget?
    python scripts/demo_probe.py database   READ ONLY: hosted reachable, applied migrations,
                                            open jobs, stuck jobs

Each prints one JSON object on stdout and exits 0 (1 when the probe itself fails).
"""

import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from pydantic import ValidationError  # noqa: E402

from app.core.config import Settings  # noqa: E402

EXTENSION_ORIGIN = "chrome-extension://cohpimnabjigooghbigblennedbplojm"


def _settings() -> Settings:
    return Settings(_env_file=BACKEND / ".env")


def config() -> dict:
    try:
        s = _settings()
    except ValidationError as exc:
        # Field and message only: pydantic's `input` may hold a secret, so it is never printed.
        return {
            "ok": False,
            "errors": [
                {"field": ".".join(str(p) for p in e["loc"]) or "settings", "message": e["msg"]}
                for e in exc.errors()
            ],
        }
    limits = s.daily_request_limits
    return {
        "ok": True,
        "app_env": s.app_env,
        "database_url": "set" if s.database_url else "unset",
        "database_host": urlsplit(s.database_url.get_secret_value()).hostname
        if s.database_url
        else None,
        "supabase_host": urlsplit(str(s.supabase_url)).hostname if s.supabase_url else None,
        "gemini_api_key": "set" if s.gemini_api_key else "unset",
        "worker_enabled": s.worker_enabled,
        "worker_will_start": bool(
            s.app_env != "test" and s.worker_enabled and s.database_url and s.gemini_api_key
        ),
        "generation_model": s.gemini_generation_model,
        "routine_model": s.gemini_routine_model,
        "routing": "free-tier" if s.gemini_routine_model else "architecture-default",
        "daily_limits": limits,
        "quota_reserve": s.model_quota_reserve,
        "generation_rpm": s.gemini_generation_rpm,
        "turn_analysis_mode": s.turn_analysis_mode,
        "cors_allows_extension": EXTENSION_ORIGIN in s.cors_origins,
        "cors_allows_web": any(o.endswith(":3000") for o in s.cors_origins),
    }


def database() -> dict:
    import psycopg

    s = _settings()
    if s.database_url is None:
        return {"ok": False, "error": "DATABASE_URL is not set"}
    try:
        with psycopg.connect(
            s.database_url.get_secret_value(), prepare_threshold=None, connect_timeout=10
        ) as conn:
            with conn.transaction():
                conn.execute("set transaction read only")
                migrations = [
                    r[0]
                    for r in conn.execute(
                        "select version from supabase_migrations.schema_migrations order by 1"
                    ).fetchall()
                ]
                jobs = dict(
                    conn.execute(
                        "select state::text, count(*) from public.processing_jobs "
                        "where state not in ('COMPLETED', 'FAILED') group by 1"
                    ).fetchall()
                )
                stuck_pending = conn.execute(
                    "select count(*) from public.processing_jobs where state = 'PENDING' "
                    "and available_at < now() - interval '3 minutes'"
                ).fetchone()[0]
                stuck_processing = conn.execute(
                    "select count(*) from public.processing_jobs where state = 'PROCESSING' "
                    "and locked_at < now() - make_interval(secs => %s)",
                    (s.worker_stale_after_seconds,),
                ).fetchone()[0]
    except psycopg.Error as exc:
        return {"ok": False, "error": type(exc).__name__}
    return {
        "ok": True,
        "host": urlsplit(s.database_url.get_secret_value()).hostname,
        "migrations": migrations,
        "open_jobs": jobs,
        "stuck_pending": stuck_pending,
        "stuck_processing": stuck_processing,
    }


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    probes = {"config": config, "database": database}
    if command not in probes:
        print(json.dumps({"ok": False, "error": f"usage: demo_probe.py {'|'.join(probes)}"}))
        return 1
    print(json.dumps(probes[command](), default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
