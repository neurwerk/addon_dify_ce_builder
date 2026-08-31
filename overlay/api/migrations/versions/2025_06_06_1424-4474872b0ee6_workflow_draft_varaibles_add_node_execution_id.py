# Modified by Neurwerk, 2025-2026: make concurrent index creation retry-safe.
# This Dify-derived file remains under the Dify Open Source License, based on
# Apache License 2.0 with additional conditions. See LICENSES/Dify-LICENSE and
# NOTICE-CHANGES.md.
"""Add workflow draft variable execution ID and workflow execution index.

Revision ID: 4474872b0ee6
Revises: 2adcbe1f5dfb
Create Date: 2025-06-06 14:24:44.213018

This overlay keeps the upstream schema change but makes its non-transactional
PostgreSQL index creation safe to retry after a later migration failure.
"""

import models
import sqlalchemy as sa
from alembic import op

revision = "4474872b0ee6"
down_revision = "2adcbe1f5dfb"
branch_labels = None
depends_on = None

_INDEX_NAME = "workflow_node_executions_tenant_id_idx"
_INDEX_COLUMNS = ("tenant_id", "workflow_id", "node_id", "created_at")
_INDEX_OPTIONS = (
    0,
    0,
    0,
    3,
)  # Final column is DESC with PostgreSQL's default NULLS FIRST.


def _is_pg(conn):
    return conn.dialect.name == "postgresql"


def _postgresql_index_state(conn):
    row = (
        conn.execute(
            sa.text(
                """
                SELECT
                    index_record.indisvalid AS valid,
                    index_record.indisready AS ready,
                    index_record.indisunique AS unique,
                    access_method.amname AS method,
                    ARRAY(
                        SELECT pg_get_indexdef(index_record.indexrelid, position, true)
                        FROM generate_series(1, index_record.indnatts) AS position
                        ORDER BY position
                    ) AS columns,
                    ARRAY(
                        SELECT option
                        FROM unnest(index_record.indoption::smallint[]) WITH ORDINALITY
                            AS index_option(option, position)
                        ORDER BY position
                    ) AS options,
                    pg_get_expr(index_record.indpred, index_record.indrelid) AS predicate
                FROM pg_index AS index_record
                JOIN pg_class AS index_class
                    ON index_class.oid = index_record.indexrelid
                JOIN pg_class AS table_class
                    ON table_class.oid = index_record.indrelid
                JOIN pg_namespace AS index_namespace
                    ON index_namespace.oid = index_class.relnamespace
                JOIN pg_namespace AS table_namespace
                    ON table_namespace.oid = table_class.relnamespace
                JOIN pg_am AS access_method
                    ON access_method.oid = index_class.relam
                WHERE index_namespace.nspname = current_schema()
                    AND table_namespace.nspname = current_schema()
                    AND index_class.relname = :index_name
                    AND table_class.relname = 'workflow_node_executions'
                """
            ),
            {"index_name": _INDEX_NAME},
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row is not None else None


def _postgresql_index_action(state):
    if state is None:
        return "create"
    if not state["valid"] or not state["ready"]:
        return "replace"

    expected = {
        "unique": False,
        "method": "btree",
        "columns": _INDEX_COLUMNS,
        "options": _INDEX_OPTIONS,
        "predicate": None,
    }
    actual = {
        "unique": state["unique"],
        "method": state["method"],
        "columns": tuple(state["columns"]),
        "options": tuple(state["options"]),
        "predicate": state["predicate"],
    }
    if actual != expected:
        raise RuntimeError(
            f"existing PostgreSQL index {_INDEX_NAME!r} does not match the expected definition"
        )
    return "reuse"


def _ensure_postgresql_index(conn):
    action = _postgresql_index_action(_postgresql_index_state(conn))
    if action == "reuse":
        return

    # Concurrent index operations commit independently from Alembic's revision
    # transaction. Reconcile their state explicitly so a retry can continue.
    with op.get_context().autocommit_block():
        if action == "replace":
            op.drop_index(
                op.f(_INDEX_NAME),
                table_name="workflow_node_executions",
                postgresql_concurrently=True,
            )
        op.create_index(
            op.f(_INDEX_NAME),
            "workflow_node_executions",
            [
                "tenant_id",
                "workflow_id",
                "node_id",
                sa.literal_column("created_at DESC"),
            ],
            unique=False,
            postgresql_concurrently=True,
        )


def upgrade():
    conn = op.get_bind()

    if _is_pg(conn):
        _ensure_postgresql_index(conn)
    else:
        op.create_index(
            op.f(_INDEX_NAME),
            "workflow_node_executions",
            [
                "tenant_id",
                "workflow_id",
                "node_id",
                sa.literal_column("created_at DESC"),
            ],
            unique=False,
        )

    with op.batch_alter_table("workflow_draft_variables", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("node_execution_id", models.types.StringUUID(), nullable=True)
        )


def downgrade():
    conn = op.get_bind()

    if _is_pg(conn):
        with op.get_context().autocommit_block():
            op.drop_index(op.f(_INDEX_NAME), postgresql_concurrently=True)
    else:
        op.drop_index(op.f(_INDEX_NAME))

    with op.batch_alter_table("workflow_draft_variables", schema=None) as batch_op:
        batch_op.drop_column("node_execution_id")
