import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from pathlib import Path

from aiogram import Bot, Router
from aiogram.filters import Command, CommandObject, Filter
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.announce import SnapshotError, format_notification, load_snapshot, new_by_category, save_snapshot
from app.backup import CONTENT_PREFIX, CONTENT_SUFFIX, PREFIX, STAMP_FORMAT, SUFFIX, make_content_archive, make_db_copy
from app.broadcast import send_broadcast
from app.callbacks import BroadcastCb, MenuCb
from app.content import ContentError, ContentHolder, load_content
from app.content_edit import ContentEditor
from app.handlers.menu import safe_edit
from app.progress import ProgressService
from app.stats import collect_stats, format_stats_parts
from app.textutil import chunk_lines
from app.ui import _btn, _kb

log = logging.getLogger("polny_zachet")

MAX_BROADCAST_LEN = 4000  # лимит Telegram 4096 символов
MAX_DRAFTS = 100  # черновики живут в памяти; самые старые вытесняются
MAX_UPLOAD_BYTES = 49 * 1024 * 1024  # бот может отправить файл до 50 МБ

USAGE_ADD = ("Использование: /add <id категории> | <название> | <подсказка> | spoiler\n"
             "Подсказка и spoiler необязательны.")
USAGE_NEWCAT = "Использование: /newcat <id категории> | <название> | <первый пункт>"
USAGE_EDIT = ("Использование: /edit <id пункта> | <название> | <подсказка>\n"
              "Подсказка необязательна; «-» удаляет её.")
USAGE_HIDE = "Использование: /hide <id пункта>"


@dataclass(frozen=True)
class Draft:
    """Черновик рассылки, привязанный к сообщению предпросмотра."""
    text: str
    open_button: bool = False  # кнопка «Открыть чек-лист» под сообщением (уведомления о новых пунктах)
    snapshot_ids: frozenset[str] | None = None  # после отправки записать в снимок (только /notify_new)


def split_parts(args: str | None) -> list[str]:
    return [p.strip() for p in (args or "").split("|")]


class IsAdmin(Filter):
    async def __call__(self, event: Message | CallbackQuery, admin_ids: list[int]) -> bool:
        return event.from_user is not None and event.from_user.id in admin_ids


def build_router() -> Router:
    router = Router()
    # Для остальных пользователей админ-команды молча игнорируются.
    router.message.filter(IsAdmin())
    router.callback_query.filter(IsAdmin())

    @router.message(Command("stats"))
    async def cmd_stats(message: Message, holder: ContentHolder, sessions: async_sessionmaker) -> None:
        stats = await collect_stats(sessions, holder.content)
        for part in format_stats_parts(stats):  # длинный отчёт приходит несколькими сообщениями
            await message.answer(part)

    @router.message(Command("reload"))
    async def cmd_reload(message: Message, holder: ContentHolder, content_dir: Path) -> None:
        try:
            new = load_content(content_dir)
        except ContentError as e:
            await message.answer(f"Контент не прошёл валидацию, остаётся старый.\n\n{escape(str(e))}")
            return
        except Exception as e:
            log.exception("Ошибка при перезагрузке контента")
            await message.answer(f"Не удалось перечитать контент, остаётся старый.\n\n{escape(repr(e))}")
            return
        holder.content = new
        await message.answer(f"Контент обновлён: категорий {len(new.categories)}, пунктов {new.total_items}.")

    # --- наполнение контента из Telegram ---

    async def _apply(message: Message, holder: ContentHolder, content_dir: Path,
                     content_lock: asyncio.Lock, operation, fmt) -> None:
        """Записать правку под общей блокировкой, перечитать контент как по /reload, ответить."""
        async with content_lock:
            try:
                result = operation(ContentEditor(content_dir))
            except ContentError as e:
                await message.answer(f"Не записано, файл не изменён.\n\n{escape(str(e))}")
                return
            except Exception as e:
                log.exception("Ошибка при записи контента")
                await message.answer(f"Не удалось записать контент, файл не изменён.\n\n{escape(repr(e))}")
                return
            try:
                holder.content = load_content(content_dir)
            except Exception as e:
                log.exception("Контент записан, но не перечитался")
                await message.answer(f"Файл записан, но перечитать контент не удалось: {escape(repr(e))}\n"
                                     "Остаётся старый контент, проверь файлы и отправь /reload.")
                return
        await message.answer(fmt(result))

    @router.message(Command("add"))
    async def cmd_add(message: Message, command: CommandObject, holder: ContentHolder,
                      content_dir: Path, content_lock: asyncio.Lock) -> None:
        parts = split_parts(command.args)
        if not 2 <= len(parts) <= 4 or not parts[0] or not parts[1]:
            await message.answer(USAGE_ADD)
            return
        category_id, title = parts[0], parts[1]
        hint = parts[2] if len(parts) > 2 and parts[2] not in ("", "-") else None
        spoiler = False
        if len(parts) == 4:
            if parts[3].lower() != "spoiler":
                await message.answer(f"Четвёртая часть — только слово spoiler.\n\n{USAGE_ADD}")
                return
            spoiler = True
        await _apply(
            message, holder, content_dir, content_lock,
            lambda ed: ed.add_item(category_id, title, hint, spoiler),
            lambda r: f"Добавлено: <code>{r.item_id}</code> в «{escape(r.category_title)}», теперь {r.count} пунктов",
        )

    @router.message(Command("newcat"))
    async def cmd_newcat(message: Message, command: CommandObject, holder: ContentHolder,
                         content_dir: Path, content_lock: asyncio.Lock) -> None:
        parts = split_parts(command.args)
        if len(parts) != 3 or not all(parts):
            await message.answer(USAGE_NEWCAT)
            return
        category_id, title, first = parts
        await _apply(
            message, holder, content_dir, content_lock,
            lambda ed: ed.new_category(category_id, title, first),
            lambda r: (f"Создана категория «{escape(r.title)}» (<code>{r.category_id}</code>), "
                       f"первый пункт: <code>{r.item_id}</code>"),
        )

    @router.message(Command("edit"))
    async def cmd_edit(message: Message, command: CommandObject, holder: ContentHolder,
                       content_dir: Path, content_lock: asyncio.Lock) -> None:
        parts = split_parts(command.args)
        if not 2 <= len(parts) <= 3 or not parts[0] or not parts[1]:
            await message.answer(USAGE_EDIT)
            return
        item_id, title = parts[0], parts[1]
        raw_hint = parts[2] if len(parts) == 3 else ""
        await _apply(
            message, holder, content_dir, content_lock,
            lambda ed: ed.edit_item(item_id, title, hint=raw_hint if raw_hint not in ("", "-") else None,
                                    clear_hint=raw_hint == "-"),
            lambda r: f"Изменено: <code>{r.item_id}</code> в «{escape(r.category_title)}»",
        )

    @router.message(Command("hide"))
    async def cmd_hide(message: Message, command: CommandObject, holder: ContentHolder,
                       content_dir: Path, content_lock: asyncio.Lock) -> None:
        item_id = (command.args or "").strip()
        if not item_id or " " in item_id or "|" in item_id:
            await message.answer(USAGE_HIDE)
            return
        await _apply(
            message, holder, content_dir, content_lock,
            lambda ed: ed.hide_item(item_id),
            lambda r: (f"Удалено из списка: <code>{r.item_id}</code> («{escape(r.title)}») из "
                       f"«{escape(r.category_title)}», осталось {r.count}. Прогресс в базе не тронут."),
        )

    @router.message(Command("cat"))
    async def cmd_cat(message: Message, command: CommandObject, holder: ContentHolder) -> None:
        content = holder.content
        arg = (command.args or "").strip()
        if not arg:
            lines = []
            for c in content.categories:
                hinted = sum(1 for i in c.items if i.hint)
                lines.append(f"<code>{c.id}</code> — {escape(c.title)}: {len(c.items)} пунктов, "
                             f"с подсказкой {hinted}")
            total = sum(len(c.items) for c in content.categories)
            total_hinted = sum(1 for c in content.categories for i in c.items if i.hint)
            lines.append(f"\nИтого: категорий {len(content.categories)}, пунктов {total}, "
                         f"с подсказкой {total_hinted}")
        else:
            category = content.category(arg)
            if category is None:
                await message.answer(f"Категория «{escape(arg)}» не найдена. Список: /cat")
                return
            lines = [f"«{escape(category.title)}» (<code>{category.id}</code>): {len(category.items)} пунктов\n"]
            for i in category.items:
                lines.append(f"<code>{i.id}</code> — {escape(i.title)}{' 🔒' if i.spoiler else ''}"
                             f"{' 💬' if i.hint else ''}")
        for chunk in chunk_lines(lines):
            await message.answer(chunk)

    @router.message(Command("export"))
    async def cmd_export(message: Message, content_dir: Path, content_lock: asyncio.Lock,
                         db_path: Path) -> None:
        """Прислать архив content/ и свежую копию базы документами."""
        try:
            async with content_lock:
                archive = make_content_archive(content_dir)
            db_copy = make_db_copy(db_path)
        except Exception as e:
            log.exception("Ошибка при выгрузке")
            await message.answer(f"Не удалось подготовить выгрузку: {escape(repr(e))}")
            return
        stamp = datetime.now(UTC).strftime(STAMP_FORMAT)
        for data, name, caption in (
            (archive, f"{CONTENT_PREFIX}{stamp}{CONTENT_SUFFIX}", "Архив папки content/"),
            (db_copy, f"{PREFIX}{stamp}{SUFFIX}", "Копия базы (прогресс пользователей)"),
        ):
            if len(data) > MAX_UPLOAD_BYTES:
                await message.answer(f"{name}: {len(data) // 1024 // 1024} МБ, это больше лимита Telegram "
                                     "на файлы бота. Скачай файл с сервера (scp).")
                continue
            await message.answer_document(BufferedInputFile(data, filename=name), caption=caption)

    async def _preview(message: Message, svc: ProgressService, broadcasts: dict[tuple[int, int], Draft],
                       draft: Draft, title: str) -> None:
        """Предпросмотр с числом получателей (notify = true) и кнопками «Отправить» / «Отмена»."""
        count = len(await svc.notify_user_ids())
        preview = await message.answer(
            f"{title}. Получателей: {count}.\n\n— — —\n{escape(draft.text)}\n— — —",
            reply_markup=_kb([[_btn("Отправить", BroadcastCb(confirm=True)),
                               _btn("Отмена", BroadcastCb(confirm=False))]]),
        )
        # черновик привязан к сообщению предпросмотра: кнопка под ним отправит именно этот текст
        broadcasts[(preview.chat.id, preview.message_id)] = draft
        while len(broadcasts) > MAX_DRAFTS:
            broadcasts.pop(next(iter(broadcasts)))

    @router.message(Command("notify_new"))
    async def cmd_notify_new(message: Message, holder: ContentHolder, svc: ProgressService,
                             broadcasts: dict[tuple[int, int], Draft], snapshot_path: Path) -> None:
        content = holder.content
        current = frozenset(content.items_by_id)
        try:
            snapshot = load_snapshot(snapshot_path)
        except SnapshotError as e:
            await message.answer(f"{escape(str(e))}\n\nУдали файл снимка, чтобы создать его заново.")
            return
        if snapshot is None:
            save_snapshot(current, snapshot_path)
            await message.answer("Снимок создан, рассылать нечего")
            return
        new = new_by_category(content, snapshot)
        if not new:
            await message.answer("Новых пунктов нет, рассылать нечего")
            return
        draft = Draft(format_notification(new), open_button=True, snapshot_ids=current)
        await _preview(message, svc, broadcasts, draft, "Предпросмотр уведомления о новых пунктах")

    @router.message(Command("broadcast"))
    async def cmd_broadcast(message: Message, command: CommandObject, svc: ProgressService,
                            broadcasts: dict[tuple[int, int], Draft]) -> None:
        text = (command.args or "").strip()
        if not text:
            await message.answer("Использование: /broadcast <текст>")
            return
        if len(text) > MAX_BROADCAST_LEN:
            await message.answer(f"Слишком длинный текст: {len(text)} символов, максимум {MAX_BROADCAST_LEN}.")
            return
        await _preview(message, svc, broadcasts, Draft(text), "Предпросмотр рассылки")

    @router.callback_query(BroadcastCb.filter())
    async def on_broadcast(cb: CallbackQuery, callback_data: BroadcastCb, bot: Bot,
                           svc: ProgressService, broadcasts: dict[tuple[int, int], Draft],
                           snapshot_path: Path) -> None:
        await cb.answer()
        if not isinstance(cb.message, Message):
            return
        key = (cb.message.chat.id, cb.message.message_id)
        draft = broadcasts.pop(key, None)  # pop: повторное нажатие не отправит дважды
        if draft is None:
            await safe_edit(cb.message, "Эта рассылка уже обработана или устарела.", None)
            return
        if not callback_data.confirm:
            await safe_edit(cb.message, "Рассылка отменена.", None)
            return
        ids = await svc.notify_user_ids()
        await safe_edit(cb.message, f"Рассылка идёт… получателей: {len(ids)}", None)
        markup = _kb([[_btn("Открыть чек-лист", MenuCb(action="list"))]]) if draft.open_button else None
        report = await send_broadcast(bot, ids, draft.text, reply_markup=markup)
        log.info("Рассылка завершена: доставлено %d, заблокировали %d, ошибки %d",
                 report.delivered, report.blocked, report.errors)
        result = report.format()
        if draft.snapshot_ids is not None:
            try:
                save_snapshot(draft.snapshot_ids, snapshot_path)
            except OSError:
                log.exception("Не удалось обновить снимок объявленных пунктов")
                result += "\n\nВнимание: снимок не обновился, следующее /notify_new повторит эти пункты."
        await safe_edit(cb.message, result, None)

    return router
