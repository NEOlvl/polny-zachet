import asyncio
import logging
from time import monotonic

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardMarkup, Message

from app import ui
from app.callbacks import CatCb, DeleteCb, ItemCb, MenuCb, SearchCb
from app.card import render_card
from app.content import ContentHolder
from app.progress import ProgressService
from app.search import MAX_RESULTS, SearchResult, SearchState, clean_query, find_items
from app.spoilers import SpoilerState
from app.texts import (
    ABOUT_TEXT,
    DELETE_CANCEL_TEXT,
    DELETE_CONFIRM_TEXT,
    DELETE_DONE_TEXT,
    FALLBACK_TEXT,
    FIND_HINT,
    FIND_NOTHING,
    FIND_SPOILER_ALERT,
    NOTIFY_OFF_TEXT,
    NOTIFY_ON_TEXT,
    PRIVACY_TEXT,
    SHARE_COOLDOWN_TEXT,
    SHARE_FAILED_TEXT,
    STALE_TEXT,
)

SHARE_COOLDOWN_SECONDS = 30
log = logging.getLogger("polny_zachet")

# Админ-команды: для не-админов молча игнорируются, обычный текст их не перехватывает.
ADMIN_COMMANDS = ("stats", "reload", "broadcast", "add", "newcat", "edit", "hide", "cat", "export", "notify_new")


async def safe_edit(message: Message, text: str, markup: InlineKeyboardMarkup | None) -> None:
    try:
        await message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise


def _key(message: Message) -> tuple[int, int]:
    return message.chat.id, message.message_id


async def _show_home(message: Message, holder: ContentHolder, svc: ProgressService,
                     tg_id: int, channel_url: str, edit: bool) -> None:
    summary = await svc.summary(tg_id, holder.content)
    text, markup = ui.home_screen(summary, channel_url)
    if edit:
        await safe_edit(message, text, markup)
    else:
        await message.answer(text, reply_markup=markup)


async def _stale(cb: CallbackQuery, message: Message, holder: ContentHolder,
                 svc: ProgressService, channel_url: str) -> None:
    await cb.answer(STALE_TEXT)
    await _show_home(message, holder, svc, cb.from_user.id, channel_url, edit=True)


async def _show_category(message: Message, holder: ContentHolder, svc: ProgressService,
                         spoilers: SpoilerState, tg_id: int, cat_id: str, page: int,
                         focus: str | None = None, only_undone: bool = False) -> bool:
    category = holder.content.category(cat_id)
    if category is None:
        return False
    done = await svc.done_ids(tg_id)
    text, markup = ui.category_screen(category, done, page, spoilers.opened(_key(message)), focus, only_undone)
    await safe_edit(message, text, markup)
    return True


async def _share_card(cb: CallbackQuery, message: Message, holder: ContentHolder, svc: ProgressService,
                      bot: Bot, share_times: dict[int, float]) -> None:
    """Прислать PNG-карточку прогресса; не чаще раза в SHARE_COOLDOWN_SECONDS на пользователя."""
    user_id = cb.from_user.id
    now = monotonic()
    last = share_times.get(user_id)
    if last is not None and now - last < SHARE_COOLDOWN_SECONDS:
        await cb.answer(SHARE_COOLDOWN_TEXT)
        return
    share_times[user_id] = now  # сразу: двойное нажатие не запустит вторую генерацию
    if len(share_times) > 1000:
        for uid in [u for u, t in share_times.items() if now - t >= SHARE_COOLDOWN_SECONDS]:
            del share_times[uid]
    await cb.answer()
    try:
        summary = await svc.summary(user_id, holder.content)
        png = await asyncio.to_thread(render_card, summary)
        me = await bot.me()
        link = f"https://t.me/{me.username}?start=share" if me.username else ""
        caption = f"Мой прогресс: {summary.percent}% ({summary.done}/{summary.total})"
        if link:
            caption += f"\nОтмечай своё: {link}"
        await message.answer_photo(BufferedInputFile(png, filename="polny-zachet.png"), caption=caption)
    except Exception:
        log.exception("Не удалось отправить карточку прогресса")
        share_times.pop(user_id, None)  # сбой не должен блокировать повторную попытку
        await message.answer(SHARE_FAILED_TEXT)


def build_router() -> Router:
    router = Router()

    # --- команды ---

    @router.message(CommandStart())
    async def cmd_start(message: Message, holder: ContentHolder, svc: ProgressService,
                        channel_url: str) -> None:
        await _show_home(message, holder, svc, message.from_user.id, channel_url, edit=False)


    @router.message(Command("find"))
    async def cmd_find(message: Message, command: CommandObject, holder: ContentHolder,
                       svc: ProgressService, searches: SearchState) -> None:
        query = clean_query(command.args)  # текст запроса в логи не пишется
        if not query:
            await message.answer(FIND_HINT)
            return
        content = holder.content
        done = await svc.done_ids(message.from_user.id)
        found = find_items(content, done, query)
        if not found:
            await message.answer(FIND_NOTHING)
            return
        shown = found[:MAX_RESULTS]
        text, markup = ui.search_screen(query, shown, done, len(found))
        sent = await message.answer(text, reply_markup=markup)
        searches.put(_key(sent), SearchResult(query, tuple(i.id for i, _ in shown), len(found)))


    @router.callback_query(SearchCb.filter())
    async def on_search_item(cb: CallbackQuery, callback_data: SearchCb, holder: ContentHolder,
                             svc: ProgressService, searches: SearchState, channel_url: str) -> None:
        if not isinstance(cb.message, Message):
            await cb.answer()
            return
        message = cb.message
        content = holder.content
        result = searches.get(_key(message))
        item = content.items_by_id.get(callback_data.item)
        if result is None or item is None or item.id not in result.item_ids:
            await _stale(cb, message, holder, svc, channel_url)
            return
        done = await svc.done_ids(cb.from_user.id)
        if item.spoiler and item.id not in done:
            await cb.answer(FIND_SPOILER_ALERT)  # скрытый спойлер в поиске не отмечается и не раскрывается
            return
        await svc.toggle(cb.from_user.id, item.id)
        done = await svc.done_ids(cb.from_user.id)
        shown = [(content.items_by_id[i], content.category(content.category_of_item[i]))
                 for i in result.item_ids if i in content.items_by_id]
        text, markup = ui.search_screen(result.query, shown, done, result.total)
        await safe_edit(message, text, markup)
        await cb.answer()


    @router.message(Command("about"))
    async def cmd_about(message: Message) -> None:
        await message.answer(ABOUT_TEXT)


    @router.message(Command("notify_off"))
    async def cmd_notify_off(message: Message, svc: ProgressService) -> None:
        await svc.set_notify(message.from_user.id, False)
        await message.answer(NOTIFY_OFF_TEXT)


    @router.message(Command("notify_on"))
    async def cmd_notify_on(message: Message, svc: ProgressService) -> None:
        await svc.set_notify(message.from_user.id, True)
        await message.answer(NOTIFY_ON_TEXT)


    @router.message(Command("privacy"))
    async def cmd_privacy(message: Message) -> None:
        await message.answer(PRIVACY_TEXT)


    @router.message(Command("delete_me"))
    async def cmd_delete_me(message: Message) -> None:
        text, markup = ui.delete_confirm_screen(DELETE_CONFIRM_TEXT)
        await message.answer(text, reply_markup=markup)


    @router.callback_query(DeleteCb.filter())
    async def on_delete(cb: CallbackQuery, callback_data: DeleteCb, svc: ProgressService) -> None:
        await cb.answer()
        if not isinstance(cb.message, Message):
            return
        if callback_data.confirm:
            await svc.delete_user(cb.from_user.id)
            await safe_edit(cb.message, DELETE_DONE_TEXT, None)
        else:
            await safe_edit(cb.message, DELETE_CANCEL_TEXT, None)


    # --- меню ---

    @router.callback_query(MenuCb.filter())
    async def on_menu(cb: CallbackQuery, callback_data: MenuCb, holder: ContentHolder,
                      svc: ProgressService, spoilers: SpoilerState, channel_url: str, bot: Bot,
                      share_times: dict[int, float]) -> None:
        if not isinstance(cb.message, Message):
            await cb.answer()
            return
        message = cb.message
        spoilers.clear(_key(message))
        action = callback_data.action
        if action == "list":
            text, markup = ui.list_screen(await svc.summary(cb.from_user.id, holder.content))
            await safe_edit(message, text, markup)
        elif action == "prog":
            text, markup = ui.progress_screen(await svc.summary(cb.from_user.id, holder.content))
            await safe_edit(message, text, markup)
        elif action == "home":
            await _show_home(message, holder, svc, cb.from_user.id, channel_url, edit=True)
        elif action == "find":
            await message.answer(FIND_HINT)
        elif action == "share":
            await _share_card(cb, message, holder, svc, bot, share_times)
            return
        else:
            await _stale(cb, message, holder, svc, channel_url)
            return
        await cb.answer()


    @router.callback_query(CatCb.filter())
    async def on_category(cb: CallbackQuery, callback_data: CatCb, holder: ContentHolder,
                          svc: ProgressService, spoilers: SpoilerState, channel_url: str) -> None:
        if not isinstance(cb.message, Message):
            await cb.answer()
            return
        spoilers.clear(_key(cb.message))  # смена страницы скрывает спойлеры
        ok = await _show_category(cb.message, holder, svc, spoilers, cb.from_user.id,
                                  callback_data.cat, callback_data.page,
                                  only_undone=callback_data.only)
        if ok:
            await cb.answer()
        else:
            await _stale(cb, cb.message, holder, svc, channel_url)


    @router.callback_query(ItemCb.filter())
    async def on_item(cb: CallbackQuery, callback_data: ItemCb, holder: ContentHolder,
                      svc: ProgressService, spoilers: SpoilerState, channel_url: str) -> None:
        if not isinstance(cb.message, Message):
            await cb.answer()
            return
        message = cb.message
        content = holder.content
        item = content.items_by_id.get(callback_data.item)
        if item is None:
            await _stale(cb, message, holder, svc, channel_url)
            return

        key = _key(message)
        # Первое нажатие на скрытый спойлер только раскрывает. Отмеченный спойлер уже виден,
        # поэтому нажатие сразу снимает отметку.
        if (item.spoiler and not spoilers.is_open(key, item.id)
                and not await svc.is_done(cb.from_user.id, item.id)):
            spoilers.open(key, item.id)
        else:
            await svc.toggle(cb.from_user.id, item.id)

        await _show_category(message, holder, svc, spoilers, cb.from_user.id,
                             content.category_of_item[item.id], callback_data.page, focus=item.id,
                             only_undone=callback_data.only)
        await cb.answer()


    @router.callback_query(F.data)
    async def on_unknown(cb: CallbackQuery, holder: ContentHolder, svc: ProgressService,
                         channel_url: str) -> None:
        """Любая кнопка, которую бот не распознал (старый формат и т.п.)."""
        if isinstance(cb.message, Message):
            await _stale(cb, cb.message, holder, svc, channel_url)
        else:
            await cb.answer(STALE_TEXT)

    # Должен быть последним: ловит всё, что не разобрали остальные хендлеры.
    # Админ-команды исключены: для не-админов они по-прежнему игнорируются молча.
    @router.message(F.text, ~Command(*ADMIN_COMMANDS))
    async def on_other_text(message: Message) -> None:
        await message.answer(FALLBACK_TEXT)

    return router
