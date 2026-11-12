"""Run engine + shop in one process, with no Docker, Redis or PostgreSQL.

    uv run python scripts/dev_local.py        # shop on http://127.0.0.1:8080

State is in memory (fakeredis, no persistence) and there is no TLS proxy, so JA4
is absent. Good for working on the shop, the telemetry script and the rules.
"""

from __future__ import annotations

import asyncio
import os

import uvicorn
from fakeredis import FakeAsyncRedis
from gandalf_engine.app import create_app as create_engine
from gandalf_engine.config import Settings as EngineSettings
from gandalf_engine.storage import NullStorage
from gandalf_shop.app import Settings as ShopSettings
from gandalf_shop.app import create_app as create_shop

KEY = "dev-local"


async def main() -> None:
    engine = create_engine(
        EngineSettings(service_key=KEY), redis=FakeAsyncRedis(), storage=NullStorage()
    )
    shop = create_shop(
        ShopSettings(
            engine_url="http://127.0.0.1:8011",
            service_key=KEY,
            lab_mode=True,
            shadow=os.environ.get("DEV_SHADOW") == "1",  # never block: handy for UI work
        )
    )
    servers = [
        uvicorn.Server(uvicorn.Config(engine, host="127.0.0.1", port=8011, log_level="warning")),
        uvicorn.Server(uvicorn.Config(shop, host="127.0.0.1", port=8080, log_level="info")),
    ]
    print("loja:  http://127.0.0.1:8080\nmotor: http://127.0.0.1:8011  (chave: dev-local)")
    await asyncio.gather(*(s.serve() for s in servers))


if __name__ == "__main__":
    asyncio.run(main())
