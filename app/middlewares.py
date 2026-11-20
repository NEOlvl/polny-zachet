import logging

from aiogram import BaseMiddleware
from aiogram.types import Message

from app.progress import ProgressService

log = logging.getLogger("polny_zachet")


def start_source(message: Message) -> str | None:
    """Метка из «/start <метка>» (t.me/<бот>?start=<метка>)."""
    if not message.text:
        return None
    parts = message.text.split(maxsplit=1)
    if parts[0].split("@")[0] != "/start" or len(parts) < 2:
        return None
    return parts[1].strip()


class TouchUserMiddleware(BaseMiddleware):
    """Регистрирует пользователя и обновляет last_seen на каждое действие."""

    def __init__(self, svc: ProgressService):
        self._svc = svc

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user is not None and not user.is_bot:
            source = start_source(event) if isinstance(event, Message) else None
            try:
                await self._svc.touch_user(user.id, user.username, source)
            except Exception:
                log.exception("Не удалось обновить last_seen")
        return await handler(event, data)
