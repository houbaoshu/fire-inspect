import subprocess

from sqlalchemy import text

from app.core.database import engine


def main() -> None:
    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    subprocess.run(["alembic", "upgrade", "head"], check=True)


if __name__ == "__main__":
    main()
