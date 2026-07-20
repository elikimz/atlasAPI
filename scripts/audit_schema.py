"""Compare the SQLAlchemy ORM metadata against a migrated database schema.

Usage:
    python3 scripts/audit_schema.py sqlite:////tmp/atlas-auth-audit.db
"""

import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.models.models import Base  # noqa: E402,F401 - imports all mapped models


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python3 scripts/audit_schema.py <sync-database-url>")
        return 2

    engine = create_engine(sys.argv[1])
    inspector = inspect(engine)
    actual_tables = set(inspector.get_table_names())
    expected_tables = set(Base.metadata.tables)

    missing_tables = sorted(expected_tables - actual_tables)
    unexpected_tables = sorted(actual_tables - expected_tables - {"alembic_version"})

    print("MISSING_TABLES=" + ",".join(missing_tables))
    print("UNEXPECTED_TABLES=" + ",".join(unexpected_tables))

    for table_name in sorted(expected_tables & actual_tables):
        expected_columns = set(Base.metadata.tables[table_name].columns.keys())
        actual_columns = {column["name"] for column in inspector.get_columns(table_name)}
        missing_columns = sorted(expected_columns - actual_columns)
        if missing_columns:
            print(f"MISSING_COLUMNS[{table_name}]=" + ",".join(missing_columns))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
