import importlib.util
import sys
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "overlay/api/migrations/versions/2025_06_06_1424-4474872b0ee6_workflow_draft_varaibles_add_node_execution_id.py"
)


@pytest.fixture
def migration(monkeypatch):
    alembic = ModuleType("alembic")
    alembic.op = object()
    monkeypatch.setitem(sys.modules, "alembic", alembic)
    monkeypatch.setitem(sys.modules, "models", ModuleType("models"))
    monkeypatch.setitem(sys.modules, "sqlalchemy", ModuleType("sqlalchemy"))

    spec = importlib.util.spec_from_file_location("retry_safe_migration", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _valid_index_state():
    return {
        "valid": True,
        "ready": True,
        "unique": False,
        "method": "btree",
        "columns": ["tenant_id", "workflow_id", "node_id", "created_at"],
        "options": [0, 0, 0, 3],
        "predicate": None,
    }


def test_missing_postgresql_index_is_created(migration):
    assert migration._postgresql_index_action(None) == "create"


@pytest.mark.parametrize(
    "state_override",
    [
        {"valid": False},
        {"ready": False},
    ],
)
def test_invalid_postgresql_index_is_replaced(migration, state_override):
    state = _valid_index_state() | state_override

    assert migration._postgresql_index_action(state) == "replace"


def test_exact_valid_postgresql_index_is_reused(migration):
    assert migration._postgresql_index_action(_valid_index_state()) == "reuse"


@pytest.mark.parametrize(
    "state_override",
    [
        {"unique": True},
        {"method": "hash"},
        {"columns": ["tenant_id", "workflow_id", "node_id", "created_on"]},
        {"options": [0, 0, 0, 0]},
        {"predicate": "tenant_id IS NOT NULL"},
    ],
)
def test_conflicting_valid_postgresql_index_fails_closed(migration, state_override):
    state = _valid_index_state() | state_override

    with pytest.raises(RuntimeError, match="does not match the expected definition"):
        migration._postgresql_index_action(state)


@pytest.mark.parametrize(
    ("state", "expected_operations"),
    [
        (None, ["autocommit-enter", "create", "autocommit-exit"]),
        (
            _valid_index_state() | {"valid": False},
            ["autocommit-enter", "drop", "create", "autocommit-exit"],
        ),
        (_valid_index_state(), []),
    ],
)
def test_postgresql_index_reconciliation(migration, monkeypatch, state, expected_operations):
    operations = []

    @contextmanager
    def autocommit_block():
        operations.append("autocommit-enter")
        yield
        operations.append("autocommit-exit")

    fake_op = SimpleNamespace(
        get_context=lambda: SimpleNamespace(autocommit_block=autocommit_block),
        f=lambda value: value,
        create_index=lambda *_args, **_kwargs: operations.append("create"),
        drop_index=lambda *_args, **_kwargs: operations.append("drop"),
    )
    monkeypatch.setattr(migration, "op", fake_op)
    monkeypatch.setattr(migration, "sa", SimpleNamespace(literal_column=lambda value: value))
    monkeypatch.setattr(migration, "_postgresql_index_state", lambda _conn: state)

    migration._ensure_postgresql_index(object())

    assert operations == expected_operations


def test_api_dockerfile_copies_migration_overlay():
    dockerfile = (MIGRATION_PATH.parents[4] / "docker/api.Dockerfile").read_text()

    assert MIGRATION_PATH.name in dockerfile
