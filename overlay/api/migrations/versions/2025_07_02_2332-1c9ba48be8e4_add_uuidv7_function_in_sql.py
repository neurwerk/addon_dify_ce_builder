# Modified by Neurwerk, 2025-2026: use PostgreSQL 18's native uuidv7 function.
# This Dify-derived file remains under the Dify Open Source License, based on
# Apache License 2.0 with additional conditions. See LICENSES/Dify-LICENSE and
# NOTICE-CHANGES.md. The additional retained terms below continue to apply.
"""Add UUIDv7 SQL helpers without shadowing PostgreSQL 18's native function.

Revision ID: 1c9ba48be8e4
Revises: 58eb7bdb93fe
Create Date: 2025-07-02 23:32:38.484499

The function implementation is derived from postgres-uuidv7-sql by Daniel
Verite under the following license retained from Dify's upstream migration:

Copyright (c) 2024, Daniel Verite

Permission to use, copy, modify, and distribute this software and its
documentation for any purpose, without fee, and without a written agreement is
hereby granted, provided that the above copyright notice and this paragraph and
the following two paragraphs appear in all copies.

In no event shall Daniel Verite be liable to any party for direct, indirect,
special, incidental, or consequential damages, including lost profits, arising
out of the use of this software and its documentation, even if Daniel Verite has
been advised of the possibility of such damage.

Daniel Verite specifically disclaims any warranties, including, but not limited
to, the implied warranties of merchantability and fitness for a particular
purpose. The software provided hereunder is on an "AS IS" basis, and Daniel
Verite has no obligations to provide maintenance, support, updates,
enhancements, or modifications.
"""

import models  # noqa: F401
import sqlalchemy as sa
from alembic import op

revision = "1c9ba48be8e4"
down_revision = "58eb7bdb93fe"
branch_labels = None
depends_on = None


def _is_pg(conn):
    return conn.dialect.name == "postgresql"


def _has_native_uuidv7(conn):
    result = conn.execute(
        sa.text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM pg_proc AS function
                JOIN pg_namespace AS namespace
                    ON namespace.oid = function.pronamespace
                WHERE namespace.nspname = 'pg_catalog'
                    AND function.proname = 'uuidv7'
            )
            """
        )
    )
    return bool(result.scalar()) if result is not None else False


def upgrade():
    conn = op.get_bind()
    if not _is_pg(conn):
        return

    if not _has_native_uuidv7(conn):
        op.execute(
            sa.text(
                r"""
/* Main function to generate a uuidv7 value with millisecond precision */
CREATE FUNCTION public.uuidv7() RETURNS uuid
AS
$$
SELECT encode(
               set_bit(
                       set_bit(
                               overlay(uuid_send(gen_random_uuid()) placing
                                       substring(int8send((extract(epoch from clock_timestamp()) * 1000)::bigint) from
                                                 3)
                                       from 1 for 6),
                               52, 1),
                       53, 1), 'hex')::uuid;
$$ LANGUAGE SQL VOLATILE PARALLEL SAFE;

COMMENT ON FUNCTION public.uuidv7() IS
    'Generate a uuid-v7 value with a 48-bit timestamp (millisecond precision) and 74 bits of randomness';
"""
            )
        )

    op.execute(
        sa.text(
            r"""
CREATE FUNCTION public.uuidv7_boundary(timestamptz) RETURNS uuid
AS
$$
SELECT encode(
               overlay('\x00000000000070008000000000000000'::bytea
                       placing substring(int8send(floor(extract(epoch from $1) * 1000)::bigint) from 3)
                       from 1 for 6),
               'hex')::uuid;
$$ LANGUAGE SQL STABLE STRICT PARALLEL SAFE;

COMMENT ON FUNCTION public.uuidv7_boundary(timestamptz) IS
    'Generate a non-random uuidv7 with the given timestamp (first 48 bits) and all random bits to 0. As the smallest possible uuidv7 for that timestamp, it may be used as a boundary for partitions.';
"""
        )
    )


def downgrade():
    conn = op.get_bind()
    if not _is_pg(conn):
        return

    op.execute(sa.text("DROP FUNCTION IF EXISTS public.uuidv7()"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS public.uuidv7_boundary(timestamptz)"))
