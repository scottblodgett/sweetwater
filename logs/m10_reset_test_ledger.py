"""One-off, M10. Empty `sw_ops_test` between two live runs without `pytest` (which drops and re-migrates
the schema and cannot run while a demo ledger is loaded, cookbook #18 and #41). Refuses any store but
`test`, through the same allowlist `memory.py` enforces on DROP.

    SW_OPS_TARGET=test PYTHONPATH=. .venv/Scripts/python.exe logs/m10_reset_test_ledger.py
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from src.agent.memory import SCHEMA_TEST, assert_droppable_schema, resolve_store, store_session

TABLES = ("incidents", "chaos_events", "ticks", "shift_reports", "audit_receipts", "checkpoints", "checkpoint_blobs", "checkpoint_writes")


async def main() -> None:
    target = resolve_store()
    if target.schema != SCHEMA_TEST:
        raise SystemExit(f"refusing: this only empties {SCHEMA_TEST}, and the resolved store is {target.name} ({target.schema}). Set SW_OPS_TARGET=test.")
    schema = assert_droppable_schema(target.schema)
    async with store_session(url=target.url, schema=schema) as session:
        for table in TABLES:
            await session.execute(text(f'TRUNCATE TABLE "{schema}"."{table}" RESTART IDENTITY CASCADE'))
        await session.commit()
    print(f"emptied {len(TABLES)} tables in {schema}: {', '.join(TABLES)}")


if __name__ == "__main__":
    asyncio.run(main())
