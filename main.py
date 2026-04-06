"""엔트리포인트: PTB 봇 + FastAPI 웹서버를 단일 asyncio 루프에서 구동."""
from __future__ import annotations

import asyncio
import logging
import sys

import uvicorn
from telegram import Update

import bot
import database as db
import web
from config import BOT_TOKEN, PORT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("main")


async def run() -> None:
    await db.init_db()

    if not BOT_TOKEN:
        log.error("BOT_TOKEN 이 설정되지 않았습니다. 환경변수를 확인하세요.")
        sys.exit(1)

    ptb_app = bot.build_application(BOT_TOKEN)
    await ptb_app.initialize()
    await ptb_app.start()
    await ptb_app.updater.start_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )
    log.info("Telegram bot started (polling)")

    fastapi_app = web.create_app(ptb_app)
    server = uvicorn.Server(
        uvicorn.Config(fastapi_app, host="0.0.0.0", port=PORT, log_level="info", loop="asyncio")
    )

    try:
        await server.serve()
    finally:
        log.info("Shutting down...")
        try:
            await ptb_app.updater.stop()
        except Exception:
            pass
        await ptb_app.stop()
        await ptb_app.shutdown()


def main() -> None:
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
