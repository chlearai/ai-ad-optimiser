"""Add the Sakha access flag and tenant-scoped reporting tables."""
from sqlalchemy import inspect, text

from backend.db import database, models as core_models
from backend.db import sakha_models as models


def run_migration(engine=None):
    engine = database.engine if engine is None else engine
    # Commit the column used by every user query before touching the mirror.
    with engine.begin() as connection:
        if engine.dialect.name == "postgresql":
            connection.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS access_sakha BOOLEAN NOT NULL DEFAULT FALSE"))
        elif "access_sakha" not in {column["name"] for column in inspect(connection).get_columns("users")}:
            connection.execute(text("ALTER TABLE users ADD COLUMN access_sakha BOOLEAN NOT NULL DEFAULT FALSE"))

    tables = [
        cls.__table__ for cls in (
            models.SakhaClient, models.SakhaPerson, models.SakhaDeal, models.SakhaDealStageEvent,
            models.SakhaActivity, models.SakhaTask, models.SakhaSourceStatus,
            models.SakhaIntegrationStatus, models.SakhaPipeline, models.SakhaOwner,
            models.SakhaIssue, models.SakhaRateBudget,
        )
    ]
    with engine.begin() as connection:
        core_models.Base.metadata.create_all(connection, tables=tables)
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
