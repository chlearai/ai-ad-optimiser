"""Keep login columns independent of Mini Robos reporting-table setup."""
from sqlalchemy import inspect, text

from backend.db import database, mini_robos_models


def run_migration(engine):
    for column in ("access_mini_robos", "edit_mini_robos"):
        with engine.begin() as connection:
            if engine.dialect.name == "postgresql":
                connection.execute(text(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {column} BOOLEAN NOT NULL DEFAULT FALSE"))
            elif column not in {field["name"] for field in inspect(connection).get_columns("users")}:
                connection.execute(text(f"ALTER TABLE users ADD COLUMN {column} BOOLEAN NOT NULL DEFAULT FALSE"))

    tables = [table for table in database.Base.metadata.sorted_tables if table.name.startswith("robo_")]
    with engine.begin() as connection:
        database.Base.metadata.create_all(connection, tables=tables)
        if engine.dialect.name == "postgresql":
            roles = tuple(connection.execute(text(
                "SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated')"
            )).scalars())
            for table in tables:
                name = connection.dialect.identifier_preparer.format_table(table)
                connection.execute(text(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY"))
                connection.execute(text(f"REVOKE ALL ON TABLE {name} FROM PUBLIC"))
                for role in roles:
                    connection.execute(text(f"REVOKE ALL ON TABLE {name} FROM {role}"))
