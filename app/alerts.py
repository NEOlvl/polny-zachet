"""Оповещения админов в Telegram: сообщение о запуске и дублирование ошибок из логов.

Правила:
- записи уровня ERROR и выше уходят каждому админу;
- одинаковая ошибка (то же место в коде, тот же шаблон сообщения, тот же тип исключения) — не чаще
  1 раза в WINDOW секунд; повторы внутри окна считаются и приходят одной сводкой в конце окна;
- сбой отправки не роняет бота и не порождает новых оповещений: записи, возникшие во время отправки,
  и записи логгера polny_zachet.alerts игнорируются;
- текст сообщений пользователей не передаётся: берутся только наши сообщения лога и данные исключения.
"""
import asyncio
import contextvars
import logging
import time
from html import escape

from aiogram import Bot

log = logging.getLogger("polny_zachet.alerts")

WINDOW = 5 * 60.0
MAX_TEXT = 3500  # лимит Telegram 4096 символов
MAX_KEYS = 200  # сколько разных ошибок помним одновременно

# True внутри отправки оповещения: всё, что залогировано в это время, повторно не оповещает
_SENDING = contextvars.ContextVar("alert_sending", default=False)


def _window_text(window: float) -> str:
    return f"{window / 60:g} мин" if window >= 60 else f"{window:g} с"


def format_record(record: logging.LogRecord) -> str:
    text = f"⚠️ <b>Ошибка в боте</b>\n{escape(record.name)}: {escape(record.getMessage())}"
    if record.exc_info and record.exc_info[1] is not None:
        exc = record.exc_info[1]
        detail = str(exc).strip()
        line = type(exc).__name__ + (f": {detail}" if detail else "")
        text += f"\n<code>{escape(line[:300])}</code>"
    return text[:MAX_TEXT]


def _key(record: logging.LogRecord) -> tuple:
    exc_type = record.exc_info[0].__name__ if record.exc_info and record.exc_info[0] else None
    return record.name, record.levelno, str(record.msg), exc_type


class _Entry:
    __slots__ = ("title", "last_sent", "suppressed", "timer")

    def __init__(self, title: str, last_sent: float):
        self.title = title
        self.last_sent = last_sent
        self.suppressed = 0
        self.timer: asyncio.TimerHandle | None = None


class AdminAlertHandler(logging.Handler):
    def __init__(self, bot: Bot, admin_ids: list[int], loop: asyncio.AbstractEventLoop,
                 window: float = WINDOW, clock=time.monotonic):
        super().__init__(level=logging.ERROR)
        self._bot = bot
        self._admin_ids = list(admin_ids)
        self._loop = loop
        self._window = window
        self._clock = clock
        self._entries: dict[tuple, _Entry] = {}
        self._tasks: set[asyncio.Task] = set()

    # --- logging.Handler: вызывается из любого потока, под self.lock ---

    def emit(self, record: logging.LogRecord) -> None:
        if _SENDING.get() or record.name.startswith("polny_zachet.alerts"):
            return
        try:
            key = _key(record)
            now = self._clock()
            entry = self._entries.get(key)
            if entry is not None and (entry.suppressed > 0 or now - entry.last_sent < self._window):
                entry.suppressed += 1
                if entry.timer is None:
                    delay = max(0.0, entry.last_sent + self._window - now)
                    entry.timer = True  # место занято до фактического планирования
                    self._in_loop(self._arm_timer, key, delay)
                return
            self._prune(now)
            title = record.getMessage()
            self._entries[key] = _Entry(title, now)
            self._in_loop(self._send_soon, format_record(record))
        except Exception:
            pass  # оповещение не должно ломать логирование

    # --- внутреннее ---

    def _in_loop(self, fn, *args) -> None:
        try:
            self._loop.call_soon_threadsafe(fn, *args)
        except RuntimeError:  # цикл уже закрыт (остановка бота)
            pass

    def _prune(self, now: float) -> None:
        if len(self._entries) < MAX_KEYS:
            return
        for key in [k for k, e in self._entries.items()
                    if e.suppressed == 0 and now - e.last_sent >= self._window]:
            del self._entries[key]

    def _arm_timer(self, key: tuple, delay: float) -> None:
        entry = self._entries.get(key)
        if entry is not None:
            entry.timer = self._loop.call_later(delay, self._flush, key)

    def _flush(self, key: tuple) -> None:
        """Конец окна: прислать сводку о повторах и начать новое окно."""
        self.acquire()
        try:
            entry = self._entries.get(key)
            if entry is None or entry.suppressed == 0:
                return
            count, entry.suppressed, entry.timer = entry.suppressed, 0, None
            entry.last_sent = self._clock()
            text = (f"⚠️ <b>Ошибка повторилась ещё {count} раз(а)</b> за {_window_text(self._window)}\n"
                    f"{escape(entry.title)[:500]}")
        finally:
            self.release()
        self._send_soon(text)

    def _send_soon(self, text: str) -> None:
        task = self._loop.create_task(self._send(text))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _send(self, text: str) -> None:
        token = _SENDING.set(True)
        try:
            for admin_id in self._admin_ids:
                try:
                    await self._bot.send_message(admin_id, text)
                except Exception as e:
                    log.warning("Не удалось отправить оповещение админу %s: %s", admin_id, type(e).__name__)
        finally:
            _SENDING.reset(token)

    async def drain(self) -> None:
        """Дождаться уже запущенных отправок (для остановки и тестов)."""
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)


async def notify_started(bot: Bot, admin_ids: list[int], categories: int, items: int) -> None:
    """«Бот запущен» каждому админу. Сбой у одного админа не мешает остальным и боту."""
    text = f"Бот запущен. Контент: {categories} категорий, {items} пунктов"
    for admin_id in admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except Exception as e:
            log.warning("Не удалось сообщить админу %s о запуске: %s", admin_id, type(e).__name__)
