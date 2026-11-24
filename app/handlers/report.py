"""«Сообщить об ошибке» в пункте: пользователь описывает проблему одним сообщением, оно уходит админам.

Это не краудсорсинг: пользователи пункты не добавляют, исправляет контент только владелец.
Состояние диалога — FSM в MemoryStorage (после перезапуска бота ожидание сбрасывается).
Текст сообщения пользователя в логи не пишется.
"""
import logging
from html import escape
from time import monotonic

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.callbacks import ReportCancelCb, ReportCb
from app.content import ContentHolder
from app.handlers.menu import _key, _stale, safe_edit
from app.progress import ProgressService
from app.texts import (
    FALLBACK_TEXT,
    REPORT_CANCELLED_TEXT,
    REPORT_FAILED_TEXT,
    REPORT_LIMIT_TEXT,
    REPORT_NEED_TEXT,
    REPORT_PROMPT_TEXT,
    REPORT_THANKS_TEXT,
    REPORT_TOO_LONG_TEXT,
)
from app.ui import _btn, _kb

log = logging.getLogger("polny_zachet")

MAX_REPORT_LEN = 1000
REPORTS_PER_HOUR = 3
HOUR = 3600.0
WAIT_TTL = 10 * 60.0  # столько ждём описание ошибки, потом обычный текст снова считается обычным


class Report(StatesGroup):
    waiting = State()


def _allowed(report_times: dict[int, list[float]], user_id: int) -> bool:
    now = monotonic()
    recent = [t for t in report_times.get(user_id, []) if now - t < HOUR]
    if recent:
        report_times[user_id] = recent
    else:
        report_times.pop(user_id, None)
    return len(recent) < REPORTS_PER_HOUR


class CommandCancelsReport(BaseMiddleware):
    """Любая команда отменяет ожидание описания ошибки и выполняется как обычно."""

    async def __call__(self, handler, event: Message, data):
        state: FSMContext | None = data.get("state")
        if state is not None and event.text and event.text.startswith("/"):
            if await state.get_state() == Report.waiting.state:
                await state.clear()
                data["raw_state"] = None  # фильтры состояния читают уже вычисленное значение
        return await handler(event, data)


def build_router() -> Router:
    router = Router()
    router.message.outer_middleware(CommandCancelsReport())

    @router.callback_query(ReportCb.filter())
    async def on_report_button(cb: CallbackQuery, callback_data: ReportCb, state: FSMContext,
                               holder: ContentHolder, svc: ProgressService, channel_url: str,
                               miniapp_url: str, report_times: dict[int, list[float]]) -> None:
        if not isinstance(cb.message, Message):
            await cb.answer()
            return
        item = holder.content.items_by_id.get(callback_data.item)
        if item is None:
            await _stale(cb, cb.message, holder, svc, channel_url, miniapp_url)
            return
        if not _allowed(report_times, cb.from_user.id):
            await cb.answer(REPORT_LIMIT_TEXT, show_alert=True)
            return
        await cb.answer()
        prompt = await cb.message.answer(
            REPORT_PROMPT_TEXT.format(title=escape(item.title)),
            reply_markup=_kb([[_btn("Отмена", ReportCancelCb())]]),
        )
        await state.set_state(Report.waiting)
        await state.update_data(item_id=item.id, prompt=_key(prompt), asked_at=monotonic())

    @router.callback_query(ReportCancelCb.filter())
    async def on_report_cancel(cb: CallbackQuery, state: FSMContext) -> None:
        await cb.answer()
        await state.clear()
        if isinstance(cb.message, Message):
            await safe_edit(cb.message, REPORT_CANCELLED_TEXT, None)

    @router.message(StateFilter(Report.waiting), F.text, ~F.text.startswith("/"))
    async def on_report_text(message: Message, state: FSMContext, bot: Bot, holder: ContentHolder,
                             admin_ids: list[int], report_times: dict[int, list[float]]) -> None:
        data = await state.get_data()
        if monotonic() - data.get("asked_at", 0.0) > WAIT_TTL:
            await state.clear()  # ожидание устарело: это обычное сообщение
            await message.answer(FALLBACK_TEXT)
            return
        text = message.text.strip()
        if len(text) > MAX_REPORT_LEN:
            await message.answer(REPORT_TOO_LONG_TEXT.format(limit=MAX_REPORT_LEN))
            return
        user_id = message.from_user.id
        if not _allowed(report_times, user_id):
            await state.clear()
            await message.answer(REPORT_LIMIT_TEXT)
            return
        content = holder.content
        item = content.items_by_id.get(data.get("item_id", ""))
        if item is None:  # пункт убрали из контента, пока человек писал
            await state.clear()
            await message.answer(REPORT_FAILED_TEXT)
            return
        category = content.category(content.category_of_item[item.id])
        report = (f"⚠️ <b>Ошибка в пункте</b>\n"
                  f"Пункт: <code>{item.id}</code> — {escape(item.title)}\n"
                  f"Категория: {escape(category.title) if category else '—'}\n\n"
                  f"{escape(text)}")
        delivered = 0
        for admin_id in admin_ids:
            try:
                await bot.send_message(admin_id, report)
                delivered += 1
            except Exception as e:  # текст сообщения в лог не пишем, только тип ошибки
                log.warning("Не удалось переслать сообщение об ошибке админу %s: %s", admin_id, type(e).__name__)
        await state.clear()
        await _close_prompt(bot, data.get("prompt"))
        if delivered:
            report_times.setdefault(user_id, []).append(monotonic())
            await message.answer(REPORT_THANKS_TEXT)
        else:
            await message.answer(REPORT_FAILED_TEXT)  # попытка не засчитывается в лимит

    @router.message(StateFilter(Report.waiting))
    async def on_report_not_text(message: Message) -> None:
        await message.answer(REPORT_NEED_TEXT)

    return router


async def _close_prompt(bot: Bot, prompt: tuple[int, int] | list[int] | None) -> None:
    """Убрать кнопку «Отмена» у вопроса (необязательно: ошибка здесь ни на что не влияет)."""
    if not prompt:
        return
    try:
        await bot.edit_message_reply_markup(chat_id=prompt[0], message_id=prompt[1], reply_markup=None)
    except Exception as e:
        log.debug("Не удалось убрать кнопку у вопроса: %s", type(e).__name__)
