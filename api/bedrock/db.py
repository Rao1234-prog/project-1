"""Connection management for Bedrock.

The service layer always connects as the append-only ``bedrock_app`` role
(``BEDROCK_DATABASE_URL``) — never as the superuser. The immutability and
proposals-only guarantees are the database's job, not this code's; connecting as
a low-privilege role is what makes that real.
"""
from __future__ import annotations

import os
from psycopg_pool import ConnectionPool


def app_dsn() -> str:
    dsn = os.environ.get("BEDROCK_DATABASE_URL")
    if not dsn:
        raise RuntimeError("BEDROCK_DATABASE_URL is not set")
    return dsn


def _configure(conn) -> None:
    # Autocommit so an explicit ``with conn.transaction()`` is a real top-level
    # BEGIN/COMMIT. That matters: the balance check and hash-chain link are a
    # DEFERRED constraint trigger that fires at COMMIT, and we need that COMMIT
    # to happen inside the transaction block so a violation is raised where the
    # service layer can catch it (not swallowed into a savepoint).
    conn.autocommit = True


def make_pool(dsn: str | None = None, **kwargs) -> ConnectionPool:
    """Create a connection pool. Pools open lazily so importing is cheap."""
    kwargs.setdefault("configure", _configure)
    pool = ConnectionPool(dsn or app_dsn(), open=False, **kwargs)
    pool.open()
    return pool
