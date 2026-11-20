"""Рассылка: не быстрее 20 сообщений в секунду."""
import asyncio
import logging
from dataclasses import dataclass

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup

log = logging.getLogger("polny_zachet")

RATE_PER_SEC = 20
MAX_RETRIES = 5


@dataclass
class BroadcastReport:
    delivered: int = 0
    blocked: int = 0
    errors: int = 0

    def format(self) -> str:
        return (f"Рассылка завершена.\nДоставлено: {self.delivered}\n"
                f"Заблокировали бота: {self.blocked}\nОшибки: {self.errors}")


async def send_broadcast(bot: Bot, user_ids: list[int], text: str,
                         interval: float = 1 / RATE_PER_SEC,
                         reply_markup: InlineKeyboardMarkup | None = None) -> BroadcastReport:
    report = BroadcastReport()
    for tg_id in user_ids:
        for _ in range(MAX_RETRIES):
            try:
                # parse_mode=None: текст админа уходит как есть, без разбора HTML
                await bot.send_message(tg_id, text, parse_mode=None, reply_markup=reply_markup)
                report.delivered += 1
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after)
                continue  # ждём и повторяем того же получателя
            except TelegramForbiddenError:
                report.blocked += 1
            except TelegramAPIError:
                log.warning("Рассылка: ошибка отправки получателю", exc_info=True)
                report.errors += 1
            break
        else:
            report.errors += 1  # исчерпали попытки из-за flood-лимита
        await asyncio.sleep(interval)
    return report
