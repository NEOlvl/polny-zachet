import asyncio
import logging
import sys
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, ErrorEvent

from app.alerts import AdminAlertHandler, notify_started
from app.cardbodies import CardBodyState
from app.config import load_settings
from app.content import ContentError, ContentHolder, load_content
from app.db import make_engine, make_session_factory
from app.handlers import admin, menu, report
from app.middlewares import TouchUserMiddleware
from app.progress import ProgressService
from app.search import SearchState
from app.spoilers import SpoilerState

log = logging.getLogger("polny_zachet")

COMMANDS = [
    BotCommand(command="start", description="Главное меню"),
    BotCommand(command="find", description="Поиск по пунктам"),
    BotCommand(command="privacy", description="Какие данные хранятся"),
    BotCommand(command="about", description="О проекте"),
    BotCommand(command="delete_me", description="Удалить мои данные"),
]


async def on_error(event: ErrorEvent) -> bool:
    log.error("Ошибка в обработчике", exc_info=event.exception)
    return True


def build_dispatcher(holder: ContentHolder, svc: ProgressService, channel_url: str = "",
                     admin_ids: list[int] | None = None,
                     content_dir: str | Path = "content",
                     db_path: str | Path = "data/bot.db",
                     snapshot_path: str | Path = "data/announced_items.json") -> Dispatcher:
    dp = Dispatcher(holder=holder, svc=svc, sessions=svc.sessions, spoilers=SpoilerState(),
                    searches=SearchState(), card_bodies=CardBodyState(), share_times={}, report_times={},
                    channel_url=channel_url, admin_ids=list(admin_ids or []), broadcasts={},
                    content_dir=Path(content_dir), content_lock=asyncio.Lock(), db_path=Path(db_path),
                    snapshot_path=Path(snapshot_path))
    touch = TouchUserMiddleware(svc)
    dp.message.outer_middleware(touch)
    dp.callback_query.outer_middleware(touch)
    dp.errors.register(on_error)
    dp.include_router(admin.build_router())  # раньше menu: у menu есть «ловушка» для неизвестных кнопок
    dp.include_router(report.build_router())  # раньше menu: ловит ответ на «Ошибка в пункте»
    dp.include_router(menu.build_router())
    return dp


async def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = load_settings()

    try:
        content = load_content()
    except ContentError as e:
        log.error("Контент не прошёл валидацию: %s", e)
        sys.exit(1)
    log.info("Контент загружен: категорий %d, пунктов %d",
             len(content.categories), content.total_items)

    engine = make_engine()
    svc = ProgressService(make_session_factory(engine))

    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = build_dispatcher(ContentHolder(content), svc, settings.channel_url, settings.admin_ids)
    await bot.set_my_commands(COMMANDS)

    alerts = None
    if settings.admin_ids:
        # ошибки (ERROR и выше) из любых логгеров дублируются админам в Telegram
        alerts = AdminAlertHandler(bot, settings.admin_ids, asyncio.get_running_loop())
        logging.getLogger().addHandler(alerts)
        await notify_started(bot, settings.admin_ids, len(content.categories), content.total_items)

    log.info("Бот запущен")
    try:
        await dp.start_polling(bot)
    finally:
        if alerts is not None:
            logging.getLogger().removeHandler(alerts)
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
