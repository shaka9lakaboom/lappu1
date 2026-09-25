"""Change an account's application role (operator action; architecture §4, ADR 0001 §5, ADR 0008).

Roles are never granted through the API or read from token claims: every account signs up as
STUDENT, and only the operator, with DATABASE_URL, promotes it. The change and an OPERATOR audit
event (`ROLE_CHANGE`) are one transaction. Demoting to STUDENT is refused while the account
still holds TEACHER memberships (the guard of migration 0009); remove those first.

    cd services/backend
    .venv/Scripts/python scripts/grant_role.py --email teacher@example.edu --role TEACHER
    .venv/Scripts/python scripts/grant_role.py --user-id <uuid> --role ADMIN
    .venv/Scripts/python scripts/grant_role.py --email someone@example.edu --show

DATABASE_URL comes from the environment or services/backend/.env.
"""

import argparse
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg  # noqa: E402

from app.admin.audit import record  # noqa: E402
from app.auth.roles import APP_ROLES  # noqa: E402
from app.core.config import get_settings  # noqa: E402


def change_role(conn: psycopg.Connection, user_id: UUID, role: str) -> tuple[str, str]:
    """(old role, new role); the audit event is written in the same transaction."""
    with conn.transaction():
        row = conn.execute(
            "select role::text from public.profiles where id = %s for update", (user_id,)
        ).fetchone()
        if row is None:
            raise SystemExit(f"no profile for user {user_id}")
        old = row[0]
        if old != role:
            conn.execute(
                "update public.profiles set role = %s::public.app_role where id = %s",
                (role, user_id),
            )
            record(
                conn,
                actor=None,
                action="ROLE_CHANGE",
                entity_type="profile",
                entity_id=user_id,
                metadata={"from": old, "to": role},
            )
    return old, role


def main() -> None:  # pragma: no cover - thin CLI
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    who = parser.add_mutually_exclusive_group(required=True)
    who.add_argument("--email")
    who.add_argument("--user-id", type=UUID)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--role", choices=APP_ROLES)
    action.add_argument("--show", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    if settings.database_url is None:
        raise SystemExit("DATABASE_URL is not set")
    with psycopg.connect(settings.database_url.get_secret_value(), prepare_threshold=None) as conn:
        if args.email:
            row = conn.execute(
                "select id from auth.users where lower(email) = lower(%s)", (args.email,)
            ).fetchone()
            if row is None:
                raise SystemExit("no account with that e-mail")
            user_id = row[0]
        else:
            user_id = args.user_id
        if args.show:
            row = conn.execute(
                "select role::text from public.profiles where id = %s", (user_id,)
            ).fetchone()
            print(f"{user_id}: {row[0] if row else 'no profile'}")
            return
        old, new = change_role(conn, user_id, args.role)
        print(f"{user_id}: {old} -> {new}" if old != new else f"{user_id}: already {new}")


if __name__ == "__main__":  # pragma: no cover
    main()
