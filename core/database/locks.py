from __future__ import annotations

from django.db import connections

BOOKING_RESOURCE_ADVISORY_LOCK_NAMESPACE = 20_260_101


def acquire_postgres_advisory_xact_lock(
    *,
    namespace: int,
    lock_key: int,
    using: str = "default",
) -> None:
    """Acquire a PostgreSQL transaction-level advisory lock.

    The strict helper intentionally fails on non-PostgreSQL databases so code
    cannot silently pretend to have production-grade locking when it does not.
    Higher-level services may choose a test/local fallback outside production.
    """

    connection = connections[using]
    if not connection.in_atomic_block:
        raise RuntimeError("PostgreSQL advisory transaction lock requires transaction.atomic().")
    if connection.vendor != "postgresql":
        raise RuntimeError("PostgreSQL advisory locks require a PostgreSQL database.")

    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", [namespace, lock_key])
