import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "overlay/api/migrations/versions/2025_07_02_2332-1c9ba48be8e4_add_uuidv7_function_in_sql.py"
)


@pytest.fixture
def migration(monkeypatch):
    alembic = ModuleType("alembic")
    alembic.op = object()
    monkeypatch.setitem(sys.modules, "alembic", alembic)
    monkeypatch.setitem(sys.modules, "models", ModuleType("models"))
    monkeypatch.setitem(sys.modules, "sqlalchemy", ModuleType("sqlalchemy"))

    spec = importlib.util.spec_from_file_location("uuidv7_pg18_migration", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _configure(migration, monkeypatch, *, dialect="postgresql", native=False, offline=False):
    executed = []
    result = None if offline else SimpleNamespace(scalar=lambda: native)
    connection = SimpleNamespace(
        dialect=SimpleNamespace(name=dialect),
        execute=lambda statement: result,
    )
    monkeypatch.setattr(
        migration,
        "op",
        SimpleNamespace(
            get_bind=lambda: connection,
            execute=lambda statement: executed.append(str(statement)),
        ),
    )
    monkeypatch.setattr(migration, "sa", SimpleNamespace(text=lambda value: value))
    return executed


def test_postgresql_18_uses_native_uuidv7_and_creates_boundary(migration, monkeypatch):
    executed = _configure(migration, monkeypatch, native=True)

    migration.upgrade()

    assert len(executed) == 1
    assert "CREATE FUNCTION public.uuidv7_boundary" in executed[0]
    assert "CREATE FUNCTION public.uuidv7()" not in executed[0]


@pytest.mark.parametrize("offline", [False, True])
def test_older_or_offline_postgresql_creates_both_functions(migration, monkeypatch, offline):
    executed = _configure(migration, monkeypatch, native=False, offline=offline)

    migration.upgrade()

    assert len(executed) == 2
    assert "CREATE FUNCTION public.uuidv7()" in executed[0]
    assert "COMMENT ON FUNCTION public.uuidv7()" in executed[0]
    assert "CREATE FUNCTION public.uuidv7_boundary" in executed[1]


def test_downgrade_cannot_drop_postgresql_native_function(migration, monkeypatch):
    executed = _configure(migration, monkeypatch)

    migration.downgrade()

    assert executed == [
        "DROP FUNCTION IF EXISTS public.uuidv7()",
        "DROP FUNCTION IF EXISTS public.uuidv7_boundary(timestamptz)",
    ]


def test_non_postgresql_upgrade_and_downgrade_are_noops(migration, monkeypatch):
    executed = _configure(migration, monkeypatch, dialect="mysql")

    migration.upgrade()
    migration.downgrade()

    assert executed == []


def test_api_dockerfile_copies_uuidv7_overlay():
    dockerfile = (MIGRATION_PATH.parents[4] / "docker/api.Dockerfile").read_text()

    assert MIGRATION_PATH.name in dockerfile
