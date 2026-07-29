import asyncio
import time

import asyncpg

from tenderos_worker.settings import settings


async def _check_connectivity() -> None:
    conn = await asyncpg.connect(dsn=settings.postgres_dsn.replace("+asyncpg", ""))
    try:
        await conn.execute("SELECT 1")
    finally:
        await conn.close()


def main() -> None:
    # Real outbox polling (reading outbox_events, enqueuing RQ jobs) arrives
    # in B2 once the schema exists. For now this proves the container can
    # reach Postgres and stays alive as a distinct process from `worker`.
    while True:
        asyncio.run(_check_connectivity())
        time.sleep(5)


if __name__ == "__main__":
    main()
