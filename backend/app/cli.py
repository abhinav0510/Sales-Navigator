"""
Command-line entry point for operators and developers.

    python -m app.cli migrate                 apply new SQL migrations (as sn_owner)
    python -m app.cli seed                    load plans + built-in place table
    python -m app.cli reset-db                DROP everything and re-create (dev/test only!)
    python -m app.cli gen-secrets             print fresh secrets for a new .env
    python -m app.cli create-admin EMAIL PW   create a platform-operator user + workspace
    python -m app.cli worker                  run the background search worker
"""
from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import text
from unidecode import unidecode

from app.core.config import BACKEND_DIR, get_settings, load_yaml
from app.core.db import as_json, execute, fetch_one, get_engine

MIGRATIONS_DIR = BACKEND_DIR / "migrations"


def migrate() -> list[str]:
    """Apply every migrations/NNN_*.sql not yet recorded in schema_migrations."""
    applied: list[str] = []
    with get_engine("owner").begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY, applied_at timestamptz DEFAULT now())")
        done = {r[0] for r in conn.exec_driver_sql("SELECT version FROM schema_migrations")}
    for path in sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql")):
        if path.stem in done:
            continue
        with get_engine("owner").begin() as conn:      # one transaction per migration
            # Run through the raw psycopg connection with NO parameters, so the
            # SQL's own % characters (e.g. format('%I')) are not read as placeholders.
            conn.connection.driver_connection.execute(path.read_text(encoding="utf-8"))
            conn.execute(text("INSERT INTO schema_migrations (version) VALUES (:v)"), {"v": path.stem})
        applied.append(path.stem)
    return applied


def seed() -> None:
    """Idempotent: plans (from config/plans.yaml) and the built-in place table."""
    from app.modules.geo.places_seed import CITIES, COUNTRIES, STATES

    plans = load_yaml("plans")["plans"]
    with get_engine("owner").begin() as conn:
        for code, p in plans.items():
            execute(conn, """INSERT INTO plan (code, name, price_paise, monthly_leads, seats, features)
                             VALUES (:c, :n, :p, :l, :s, CAST(:f AS jsonb))
                             ON CONFLICT (code) DO UPDATE SET name=EXCLUDED.name, price_paise=EXCLUDED.price_paise,
                               monthly_leads=EXCLUDED.monthly_leads, seats=EXCLUDED.seats, features=EXCLUDED.features""",
                    {"c": code, "n": p["name"], "p": p["price_paise"], "l": p["monthly_leads"],
                     "s": p["seats"], "f": as_json(p["features"])})

        if fetch_one(conn, "SELECT 1 AS x FROM geo_place LIMIT 1"):
            return                                      # place table already loaded
        country_names = {iso: name for iso, name, *_ in COUNTRIES}
        state_names = {code: name for code, name, _c, _a in STATES}

        def norm(s: str) -> str:
            return unidecode(s).lower().strip()

        for iso, name, lat, lon, pop, aliases in COUNTRIES:
            _insert_place(conn, "country", name, norm(name), iso, None, None, lat, lon, pop, [norm(a) for a in aliases])
        for code, name, iso, aliases in STATES:
            _insert_place(conn, "state", name, norm(name), iso, code, country_names.get(iso), None, None, 0,
                          [norm(a) for a in aliases])
        for name, code, iso, lat, lon, pop, aliases in CITIES:
            parent = state_names.get(code) if code else country_names.get(iso)
            _insert_place(conn, "city", name, norm(name), iso, code, parent, lat, lon, pop, [norm(a) for a in aliases])


def _insert_place(conn, kind, name, ascii_name, iso, admin1, parent, lat, lon, pop, aliases) -> None:
    execute(conn, """INSERT INTO geo_place (kind, name, ascii_name, country_iso2, admin1_code, parent_name,
                                            lat, lon, population, aliases)
                     VALUES (:k, :n, :a, :iso, :ad, :p, :lat, :lon, :pop, :al)""",
            {"k": kind, "n": name, "a": ascii_name, "iso": iso, "ad": admin1, "p": parent,
             "lat": lat, "lon": lon, "pop": pop, "al": aliases})


def reset_db() -> None:
    """Drop and rebuild the schema. Refuses to run in production."""
    if get_settings().env == "prod":
        raise SystemExit("reset-db is disabled in production")
    with get_engine("owner").begin() as conn:
        conn.exec_driver_sql("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    migrate()
    seed()


def create_admin(email: str, password: str) -> None:
    from app.modules.identity.service import signup

    result = signup(email=email, password=password, full_name="Platform Admin", company_name="Operator")
    with get_engine("system").begin() as conn:
        execute(conn, "UPDATE app_user SET is_platform_admin = true WHERE lower(email) = lower(:e)", {"e": email})
    print(f"Created platform admin {email} (workspace {result['workspace_id']})")


def main(argv: list[str]) -> None:
    cmd = argv[1] if len(argv) > 1 else "help"
    if cmd == "migrate":
        print("Applied:", migrate() or "nothing new")
    elif cmd == "seed":
        seed()
        print("Seed complete")
    elif cmd == "reset-db":
        reset_db()
        print("Database reset, migrated and seeded")
    elif cmd == "gen-secrets":
        from app.core.security import generate_secrets

        for k, v in generate_secrets().items():
            print(f"{k}={v}")
    elif cmd == "create-admin" and len(argv) == 4:
        create_admin(argv[2], argv[3])
    elif cmd == "worker":
        from app.workers.worker import run_forever

        run_forever()
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv)
