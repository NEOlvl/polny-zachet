import logging

import pytest
import pytest_asyncio
from aiogram import Bot
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from test_flow import USER, FakeSession, Harness

from app.callbacks import SearchCb
from app.content import ContentHolder, build_content
from app.db import Base, make_engine, make_session_factory
from app.main import COMMANDS, build_dispatcher
from app.progress import ProgressService
from app.search import SearchResult, SearchState, clean_query, find_items, normalize
from app.texts import FIND_HINT

DOCS = [
    ("a.yaml", {"id": "cars", "title": "Машины", "order": 10, "items": [
        {"id": "car_001", "title": "Ёлочный Автомобиль", "hint": "Стоит у МАЯКА"},
        {"id": "car_002", "title": "Красный кабриолет"},
        {"id": "car_003", "title": "Секретный спорткар", "hint": "рядом с маяком", "spoiler": True},
    ]}),
    ("b.yaml", {"id": "spots", "title": "Места", "order": 20, "items": [
        {"id": f"spot_{n:03d}", "title": f"Место с маяком {n}"} for n in range(1, 12)
    ]}),
]


@pytest.fixture
def content():
    return build_content(DOCS)


@pytest_asyncio.fixture
async def h(content):
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = make_session_factory(engine)
    svc = ProgressService(sessions)
    session = FakeSession()
    dp = build_dispatcher(ContentHolder(content), svc, "")
    harness = Harness(dp, Bot("123:abc", session=session), session, sessions, svc)
    yield harness
    await engine.dispose()


def last_sent(h):
    return h.session.last(SendMessage)


def sent_message_id(h):
    return [m for c, m in h.session.sent_ids if c == USER["id"]][-1]


def buttons(markup):
    return [b for row in markup.inline_keyboard for b in row]


# --- нормализация и поиск ---

def test_normalize_case_and_yo():
    assert normalize("ЁЛКА Маяк") == "елка маяк"


def test_clean_query_collapses_spaces_and_limits_length():
    assert clean_query("  а   б  ") == "а б"
    assert clean_query(None) == ""
    assert len(clean_query("x" * 500)) == 64


def test_find_ignores_case_and_yo(content):
    ids = [i.id for i, _ in find_items(content, set(), "ЕЛОЧНЫЙ")]
    assert ids == ["car_001"]
    assert [i.id for i, _ in find_items(content, set(), "ёлочный автомобиль")] == ["car_001"]


def test_find_searches_title_and_hint(content):
    assert "car_001" in [i.id for i, _ in find_items(content, set(), "маяка")]  # только в подсказке


def test_find_all_words_must_match(content):
    assert [i.id for i, _ in find_items(content, set(), "красный кабриолет")] == ["car_002"]
    assert find_items(content, set(), "красный автомобиль") == []


def test_unmarked_spoiler_is_not_found_but_marked_is(content):
    assert "car_003" not in [i.id for i, _ in find_items(content, set(), "спорткар")]
    assert "car_003" not in [i.id for i, _ in find_items(content, set(), "маяком")]  # и по подсказке тоже
    assert [i.id for i, _ in find_items(content, {"car_003"}, "спорткар")] == ["car_003"]


def test_find_empty_query_finds_nothing(content):
    assert find_items(content, set(), "   ") == []


def test_search_state_is_bounded():
    state = SearchState(limit=2)
    for n in range(3):
        state.put((1, n), SearchResult("q", (), 0))
    assert state.get((1, 0)) is None and state.get((1, 2)) is not None


def test_search_callback_fits_64_bytes():
    assert len(SearchCb(item="a" * 24).pack().encode()) <= 64
    assert SearchCb.unpack(SearchCb(item="car_001").pack()).item == "car_001"


# --- /find ---

async def test_find_without_text_shows_hint(h):
    await h.send("/find")
    assert last_sent(h).text == "Напиши /find и слово, например: /find маяк"
    assert last_sent(h).text == FIND_HINT


async def test_find_shows_results_with_marks_and_category(h):
    await h.send("/find автомобиль")
    sent = last_sent(h)
    assert "найдено 1" in sent.text
    labels = [b.text for b in buttons(sent.reply_markup)]
    assert labels[0] == "⬜ Ёлочный Автомобиль — Машины"


async def test_find_limits_to_8_and_says_so(h):
    await h.send("/find маяком")  # 11 мест; спойлер car_003 не находится
    sent = last_sent(h)
    results = [b for b in buttons(sent.reply_markup) if b.callback_data.startswith("f:")]
    assert len(results) == 8
    assert "найдено 11" in sent.text and "Показаны первые 8" in sent.text


async def test_find_nothing(h):
    await h.send("/find несуществующее")
    assert last_sent(h).text == "Ничего не найдено. Попробуй другое слово."


async def test_find_reaches_handler_not_fallback(h):
    await h.send("/find")
    assert "Нажми /start" not in last_sent(h).text


async def test_press_toggles_mark_and_keeps_results(h):
    await h.send("/find кабриолет автомобиль")  # ничего (AND)
    await h.send("/find красный")
    msg = sent_message_id(h)
    await h.press("f:car_002", message_id=msg)
    assert await h.svc.is_done(42, "car_002")
    text, data = h.screen()
    assert "найдено 1" in text and data[0] == "f:car_002"
    labels = [b.text for b in buttons(h.session.last(EditMessageText).reply_markup)]
    assert labels[0] == "✅ Красный кабриолет — Машины"
    await h.press("f:car_002", message_id=msg)  # повторное нажатие снимает отметку
    assert not await h.svc.is_done(42, "car_002")


async def test_results_stay_after_toggle_with_many_items(h):
    await h.send("/find маяком")
    msg = sent_message_id(h)
    await h.press("f:spot_003", message_id=msg)
    labels = [b.text for b in buttons(h.session.last(EditMessageText).reply_markup)]
    assert len([b for b in labels if "Место" in b]) == 8
    assert "✅ Место с маяком 3 — Места" in labels


async def test_marked_spoiler_found_and_unmarking_hides_it_again(h, content):
    await h.svc.mark(42, "car_003")
    await h.send("/find спорткар")
    msg = sent_message_id(h)
    labels = [b.text for b in buttons(last_sent(h).reply_markup)]
    assert labels[0] == "✅ Секретный спорткар — Машины"
    await h.press("f:car_003", message_id=msg)  # снять отметку
    assert not await h.svc.is_done(42, "car_003")
    labels = [b.text for b in buttons(h.session.last(EditMessageText).reply_markup)]
    assert "Секретный" not in labels[0] and "Скрыто" in labels[0]


async def test_hidden_spoiler_button_does_not_mark_or_reveal(h):
    await h.svc.mark(42, "car_003")
    await h.send("/find спорткар")
    msg = sent_message_id(h)
    await h.press("f:car_003", message_id=msg)  # снята отметка, теперь скрыт
    edits = len([c for c in h.session.calls if isinstance(c, EditMessageText)])
    await h.press("f:car_003", message_id=msg)  # нажатие на скрытый спойлер
    assert not await h.svc.is_done(42, "car_003")
    assert len([c for c in h.session.calls if isinstance(c, EditMessageText)]) == edits
    assert "спойлер" in h.session.last(AnswerCallbackQuery).text


async def test_search_button_after_restart_is_stale_and_shows_menu(h):
    await h.press("f:car_002", message_id=999)  # о такой выдаче бот не знает
    assert "Общий прогресс" in h.screen()[0]
    assert h.session.last(AnswerCallbackQuery).text == "Список обновился"


async def test_removed_item_button_is_stale(h):
    await h.send("/find красный")
    msg = sent_message_id(h)
    await h.press("f:no_such_item", message_id=msg)
    assert h.session.last(AnswerCallbackQuery).text == "Список обновился"


async def test_query_text_not_logged(h, caplog):
    with caplog.at_level(logging.DEBUG):
        await h.send("/find СекретныйЗапрос123")
        await h.send("/find красный")
    assert "СекретныйЗапрос123" not in caplog.text
    assert "красный" not in caplog.text.lower()


# --- главный экран и меню команд ---

async def test_home_has_search_button_that_shows_hint(h):
    await h.send("/start")
    labels = [b.text for b in buttons(last_sent(h).reply_markup)]
    assert "Поиск" in labels
    await h.press("m:find")
    assert last_sent(h).text == FIND_HINT


def test_bot_commands_menu():
    assert [c.command for c in COMMANDS] == ["start", "find", "privacy", "about", "delete_me"]
    for admin_only in ("stats", "reload", "broadcast", "add", "newcat", "edit", "hide", "cat", "export"):
        assert admin_only not in [c.command for c in COMMANDS]


def test_find_not_swallowed_as_admin_command():
    from app.handlers.menu import ADMIN_COMMANDS
    assert "find" not in ADMIN_COMMANDS
