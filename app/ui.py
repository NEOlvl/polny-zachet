"""Экраны бота: чистые функции «данные -> (текст, клавиатура)». Текст — HTML."""
from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.callbacks import (
    CardCloseCb,
    CardToggleCb,
    CatCb,
    DeleteCb,
    InfoCb,
    MenuCb,
    ReportCb,
    SearchCb,
)
from app.content import Category, Item
from app.progress import ProgressSummary, percent
from app.search import MAX_RESULTS
from app.texts import ALL_DONE_TEXT, SPOILER_HIDDEN, START_TEXT

PAGE_SIZE = 8
ROW_SIZE = 4
BAR_WIDTH = 10
SEARCH_CARD_PAGE = -1  # page в InfoCb/CardToggleCb: карточка открыта из поиска, всегда отдельным сообщением


def page_count(total_items: int) -> int:
    return max(1, -(-total_items // PAGE_SIZE))


def clamp_page(page: int, total_items: int) -> int:
    return min(max(page, 0), page_count(total_items) - 1)


def bar(p: int, width: int = BAR_WIDTH) -> str:
    filled = p * width // 100
    return "█" * filled + "░" * (width - filled)


def _btn(text: str, data) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data.pack())


def _kb(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def home_screen(summary: ProgressSummary, channel_url: str = "") -> tuple[str, InlineKeyboardMarkup]:
    text = f"{START_TEXT}\n\nОбщий прогресс: {summary.percent}% ({summary.done}/{summary.total})"
    rows = [[_btn("Чек-лист", MenuCb(action="list")), _btn("Мой прогресс", MenuCb(action="prog"))]]
    rows.append([_btn("Поиск", MenuCb(action="find"))])
    if channel_url:
        rows.append([InlineKeyboardButton(text="Канал", url=channel_url)])
    return text, _kb(rows)


def list_screen(summary: ProgressSummary) -> tuple[str, InlineKeyboardMarkup]:
    text = "<b>Чек-лист</b>\nВыбери категорию:"
    rows = [
        [_btn(f"{c.title} — {c.done}/{c.total}", CatCb(cat=c.category_id, page=0))]
        for c in summary.categories
    ]
    rows.append([_btn("« В меню", MenuCb(action="home"))])
    return text, _kb(rows)


def progress_screen(summary: ProgressSummary) -> tuple[str, InlineKeyboardMarkup]:
    lines = [
        "<b>Мой прогресс</b>",
        f"{bar(summary.percent)} {summary.percent}%",
        f"Всего: {summary.done}/{summary.total}",
        "",
    ]
    for c in summary.categories:
        lines.append(f"{escape(c.title)}: {c.done}/{c.total} ({c.percent}%)")
    return "\n".join(lines), _kb([
        [_btn("Поделиться", MenuCb(action="share"))],
        [_btn("« В меню", MenuCb(action="home"))],
    ])


def category_screen(
    category: Category,
    done: set[str],
    page: int,
    opened: set[str] = frozenset(),
    only_undone: bool = False,
) -> tuple[str, InlineKeyboardMarkup]:
    """Страница категории. Кнопка пункта открывает карточку (скрытый спойлер сначала раскрывается).
    opened — раскрытые спойлеры (отмеченные спойлеры видны всегда). only_undone — фильтр «только неотмеченные»:
    отмеченные пункты не показываются, страницы и номера считаются по оставшимся."""
    total = len(category.items)
    done_count = sum(1 for i in category.items if i.id in done)
    items = [i for i in category.items if i.id not in done] if only_undone else category.items
    page = clamp_page(page, len(items))
    pages = page_count(len(items))
    start = page * PAGE_SIZE
    chunk = items[start:start + PAGE_SIZE]

    lines = [f"<b>{escape(category.title)}</b> — {done_count}/{total} ({percent(done_count, total)}%)"]
    if only_undone:
        lines.append("Фильтр: только неотмеченные")
    if not items:
        lines += ["", ALL_DONE_TEXT]
    else:
        lines += [f"Страница {page + 1}/{pages}", ""]
    buttons = []
    for n, item in enumerate(chunk, start=start + 1):
        mark = "✅" if item.id in done else "⬜"
        hidden = item.spoiler and item.id not in opened and item.id not in done  # отмеченный уже виден
        title = SPOILER_HIDDEN if hidden else escape(item.title)
        lines.append(f"{n}. {mark} {title}")
        buttons.append(_btn(f"{mark} {n}", InfoCb(item=item.id, page=page, only=only_undone)))

    rows = [buttons[i:i + ROW_SIZE] for i in range(0, len(buttons), ROW_SIZE)]
    nav = []
    if page > 0:
        nav.append(_btn("« Назад", CatCb(cat=category.id, page=page - 1, only=only_undone)))
    if page < pages - 1:
        nav.append(_btn("Вперёд »", CatCb(cat=category.id, page=page + 1, only=only_undone)))
    if nav:
        rows.append(nav)
    toggle_text = "Показать все" if only_undone else "Только неотмеченные"
    rows.append([_btn(toggle_text, CatCb(cat=category.id, page=0, only=not only_undone))])
    rows.append([_btn("« К списку", MenuCb(action="list"))])
    return "\n".join(lines), _kb(rows)


CAPTION_LIMIT = 1024
PHOTO_CREDIT = "Фото взято с сайта POWERPYX.COM"


def card_parts(item: Item, category: Category, is_done: bool) -> tuple[str, str]:
    """Карточка пункта: (заголовок, тело). Заголовок: название, категория, статус; тело: описание, подсказка, советы."""
    header = "\n".join([
        f"<b>{escape(item.title)}</b>",
        f"Категория: {escape(category.title)}",
        "Статус: ✅ отмечено" if is_done else "Статус: ⬜ не отмечено",
    ])
    blocks = []
    if item.description:
        blocks.append(escape(item.description))
    if item.hint:
        blocks.append(f"💡 {escape(item.hint)}")
    if item.tips:
        blocks.append(f"📌 Советы: {escape(item.tips)}")
    if not (item.description or item.tips or item.photo):
        blocks.append("Описание пока не добавлено.")
    return header, "\n\n".join(blocks)


def card_screen(item: Item, category: Category, is_done: bool, page: int = 0, only_undone: bool = False,
                with_photo: bool = False, close: bool = False) -> tuple[str, InlineKeyboardMarkup, str]:
    """Экран карточки: (текст, клавиатура, дополнительное сообщение с телом).
    Для карточки с фото подпись ограничена 1024 символами: если всё не помещается, в подпись идёт только
    заголовок, а тело возвращается третьим значением (его присылают отдельным сообщением)."""
    header, body = card_parts(item, category, is_done)
    text = f"{header}\n\n{body}" if body else header
    overflow = ""
    if with_photo:
        if len(text) + len(PHOTO_CREDIT) + 2 > CAPTION_LIMIT:
            text, overflow = header, body
        text = f"{text}\n\n{PHOTO_CREDIT}"  # приписка под каждым фото, тратит часть лимита подписи
    toggle = "Снять отметку" if is_done else "Отметить"
    rows = [
        [_btn(toggle, CardToggleCb(item=item.id, page=page, only=only_undone))],
        [_btn("⚠️ Ошибка в пункте", ReportCb(item=item.id))],
    ]
    if with_photo or close:
        rows.append([_btn("Закрыть", CardCloseCb())])
    else:
        rows.append([_btn("« К списку", CatCb(cat=category.id, page=page, only=only_undone))])
    return text, _kb(rows), overflow


def search_screen(query: str, results: list[tuple[Item, Category]], done: set[str],
                  total: int) -> tuple[str, InlineKeyboardMarkup]:
    """Выдача поиска. Спойлер, который перестал быть отмеченным, показывается скрытым."""
    lines = [f"<b>Поиск:</b> «{escape(query)}» — найдено {total}"]
    if total > len(results):
        lines.append(f"Показаны первые {MAX_RESULTS}, уточни запрос.")
    rows = []
    for item, category in results:
        marked = item.id in done
        hidden = item.spoiler and not marked
        title = SPOILER_HIDDEN if hidden else item.title
        # скрытый спойлер карточкой не раскрывается: кнопка только подсказывает, что делать
        data = SearchCb(item=item.id) if hidden else InfoCb(item=item.id, page=SEARCH_CARD_PAGE)
        rows.append([_btn(f"{'✅' if marked else '⬜'} {title} — {category.title}", data)])
    rows.append([_btn("« В меню", MenuCb(action="home"))])
    return "\n".join(lines), _kb(rows)


def delete_confirm_screen(text: str) -> tuple[str, InlineKeyboardMarkup]:
    return text, _kb([[
        _btn("Да, удалить", DeleteCb(confirm=True)),
        _btn("Отмена", DeleteCb(confirm=False)),
    ]])
