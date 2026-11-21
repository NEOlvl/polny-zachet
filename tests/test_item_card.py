"""Карточка пункта: поля description / tips / photo и её показ в боте."""
import time

import pytest
import pytest_asyncio
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import (
    AnswerCallbackQuery,
    DeleteMessage,
    EditMessageCaption,
    EditMessageText,
    SendMessage,
    SendPhoto,
)
from aiogram.types import Chat, Message, PhotoSize
from test_flow import FakeSession, Harness

from app import ui
from app.callbacks import CardCloseCb, CardToggleCb, InfoCb
from app.content import Category, ContentError, ContentHolder, Item, build_content
from app.db import Base, make_engine, make_session_factory
from app.main import build_dispatcher
from app.progress import ProgressService

USER = {"id": 42, "is_bot": False, "first_name": "T", "username": "tester"}


def cbs(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]


def labels(markup):
    return [b.text for row in markup.inline_keyboard for b in row]


def doc(items):
    return [("a.yaml", {"id": "cat", "title": "Категория <b>", "order": 10, "items": items})]


# --- валидация ---

def test_new_fields_are_optional_and_parsed():
    content = build_content(doc([
        {"id": "i1", "title": "Один"},
        {"id": "i2", "title": "Два", "description": " Где искать ", "tips": "Совет", "photo": "AgAC"},
    ]))
    one, two = content.items_by_id["i1"], content.items_by_id["i2"]
    assert (one.description, one.tips, one.photo, one.has_details) == (None, None, None, False)
    assert (two.description, two.tips, two.photo, two.has_details) == ("Где искать", "Совет", "AgAC", True)


@pytest.mark.parametrize("field,size", [("description", 1501), ("tips", 501)])
def test_too_long_field_is_rejected(field, size):
    with pytest.raises(ContentError, match=field):
        build_content(doc([{"id": "i1", "title": "Один", field: "я" * size}]))


def test_limits_are_inclusive_and_empty_is_rejected():
    build_content(doc([{"id": "i1", "title": "Один", "description": "я" * 1500, "tips": "я" * 500}]))
    with pytest.raises(ContentError, match="photo"):
        build_content(doc([{"id": "i1", "title": "Один", "photo": "  "}]))


# --- экраны ---

def test_callbacks_fit_64_bytes_and_roundtrip():
    cid = "a" * 24
    for cb in (InfoCb(item=cid, page=999999, only=True), CardToggleCb(item=cid, page=999999, only=True),
               CardCloseCb()):
        assert len(cb.pack().encode()) <= 64
    assert InfoCb.unpack(InfoCb(item="c_1", page=2, only=True).pack()) == InfoCb(item="c_1", page=2, only=True)


def test_list_has_single_card_button_per_item_with_page_and_filter():
    plain = Item("i1", "Один")
    rich = Item("i2", "Два", description="Описание")
    secret = Item("i3", "Три", description="Скрыто", spoiler=True)
    cat = Category("cat", "К", 1, (plain, rich, secret))
    _, kb = ui.category_screen(cat, {"i2"}, 0)
    assert labels(kb)[:3] == ["⬜ 1", "✅ 2", "⬜ 3"]
    assert cbs(kb)[:3] == ["i:i1:0:0", "i:i2:0:0", "i:i3:0:0"]
    assert not [d for d in cbs(kb) if d.startswith(("t:", "ct:", "r:"))]
    _, kb = ui.category_screen(cat, set(), 0, opened={"i3"}, only_undone=True)
    assert "i:i3:0:1" in cbs(kb)


def test_card_screen_text_and_buttons():
    item = Item("i1", "Пункт <1>", hint="Подсказка", description="Описание & детали", tips="Совет")
    cat = Category("cat", "Кат <b>", 1, (item,))
    text, kb, overflow = ui.card_screen(item, cat, False, page=2, only_undone=True)
    assert "<b>Пункт &lt;1&gt;</b>" in text and "Категория: Кат &lt;b&gt;" in text
    assert "⬜ не отмечено" in text and "Описание &amp; детали" in text
    assert "💡 Подсказка" in text and "📌 Советы: Совет" in text and overflow == ""
    assert labels(kb) == ["Отметить", "⚠️ Ошибка в пункте", "« К списку"]
    assert cbs(kb) == ["ct:i1:2:1", "r:i1", "c:cat:2:1"]
    text, kb, _ = ui.card_screen(item, cat, True)
    assert "✅ отмечено" in text and labels(kb)[0] == "Снять отметку"


def test_card_with_photo_has_close_button_and_overflow_goes_to_second_message():
    long_item = Item("i1", "Один", description="ъ" * 1500, tips="ы" * 500, photo="AgAC")
    cat = Category("cat", "К", 1, (long_item,))
    text, kb, overflow = ui.card_screen(long_item, cat, False, with_photo=True)
    assert len(text) <= 1024 and "ъ" not in text and overflow.startswith("ъ" * 1500)
    assert labels(kb)[-1] == "Закрыть" and cbs(kb)[-1] == "cx"
    short = Item("i2", "Два", description="Коротко", photo="AgAC")
    text, _, overflow = ui.card_screen(short, Category("cat", "К", 1, (short,)), False, with_photo=True)
    assert "Коротко" in text and overflow == ""


def test_photo_card_caption_has_source_credit():
    item = Item("i1", "Один", description="Описание", photo="AgAC")
    cat = Category("cat", "К", 1, (item,))
    text, _, overflow = ui.card_screen(item, cat, False, with_photo=True)
    assert text.endswith("Фото взято с сайта POWERPYX.COM") and overflow == ""
    text, _, _ = ui.card_screen(item, cat, False)  # без фото приписки нет
    assert "POWERPYX" not in text
    long_item = Item("i2", "Два", description="ъ" * 1500, photo="AgAC")
    text, _, overflow = ui.card_screen(long_item, cat, False, with_photo=True)
    assert "POWERPYX" in text and "ъ" not in text and len(text) <= 1024
    assert overflow.startswith("ъ" * 1500)


def test_photo_only_card_caption_is_header_without_placeholder():
    item = Item("i1", "Только фото", photo="AgAC")
    cat = Category("cat", "К", 1, (item,))
    text, kb, overflow = ui.card_screen(item, cat, False, with_photo=True)
    assert "<b>Только фото</b>" in text and "Категория: К" in text and "Статус:" in text
    assert "Описание пока не добавлено." not in text and overflow == ""
    assert labels(kb)[-1] == "Закрыть" and cbs(kb)[-1] == "cx"


# --- бот ---

class PhotoSession(FakeSession):
    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, SendPhoto):
            self.calls.append(method)
            return Message(message_id=900 + len(self.calls), date=int(time.time()),
                           chat=Chat(id=method.chat_id, type="private"),
                           photo=[PhotoSize(file_id="x", file_unique_id="y", width=1, height=1)])
        return await super().make_request(bot, method, timeout)


class CardHarness(Harness):
    async def press(self, data, user=USER, message_id=7, photo=False, reply_to=None):
        self.n += 1
        chat = {"id": user["id"], "type": "private"}
        msg = {"message_id": message_id, "date": int(time.time()), "chat": chat}
        if photo:
            msg["photo"] = [{"file_id": "x", "file_unique_id": "y", "width": 1, "height": 1}]
        else:
            msg["text"] = "old"
        if reply_to:
            msg["reply_to_message"] = {"message_id": reply_to, "date": int(time.time()), "chat": chat,
                                       "text": "list"}
        await self.dp.feed_raw_update(self.bot, {"update_id": self.n, "callback_query": {
            "id": str(self.n), "from": user, "chat_instance": "x", "data": data, "message": msg}})


@pytest_asyncio.fixture
async def h():
    docs = doc([
        {"id": "i1", "title": "Простой", "hint": "Подсказка"},
        {"id": "i2", "title": "С описанием", "description": "Где искать", "tips": "Совет"},
        {"id": "i3", "title": "С фото", "description": "Описание", "photo": "AgAC"},
        {"id": "i4", "title": "Секретный", "description": "Тайна", "spoiler": True},
        {"id": "i5", "title": "Длинное с фото", "description": "ъ" * 1500, "photo": "AgAC"},
    ])
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = make_session_factory(engine)
    svc = ProgressService(sessions)
    session = PhotoSession()
    dp = build_dispatcher(ContentHolder(build_content(docs)), svc, "")
    yield CardHarness(dp, Bot("123:abc", session=session), session, sessions, svc)
    await engine.dispose()


def edited(h):
    c = h.session.last(EditMessageText)
    return c.text, cbs(c.reply_markup)


async def test_list_has_one_button_per_item_and_marking_is_not_in_list(h):
    await h.press("c:cat:0:0")
    text, data = edited(h)
    item_buttons = [d for d in data if d.startswith(("i:", "t:", "ct:"))]
    assert item_buttons == ["i:i1:0:0", "i:i2:0:0", "i:i3:0:0", "i:i4:0:0", "i:i5:0:0"]  # по одной кнопке на пункт
    assert "r:i1" not in data and "💡" not in text  # ошибка и подсказка только в карточке
    await h.press("i:i2:0:0")  # нажатие открывает карточку и не отмечает пункт
    assert not await h.svc.is_done(42, "i2") and "ct:i2:0:0" in edited(h)[1]


async def test_open_card_without_photo_edits_message_and_toggle_works(h):
    await h.press("i:i2:0:0")
    text, data = edited(h)
    assert "С описанием" in text and "Где искать" in text and "📌 Советы: Совет" in text
    assert "ct:i2:0:0" in data and "c:cat:0:0" in data
    await h.press("ct:i2:0:0")
    text, _ = edited(h)
    assert await h.svc.is_done(42, "i2") and "✅ отмечено" in text
    await h.press("ct:i2:0:0")
    assert not await h.svc.is_done(42, "i2") and "⬜ не отмечено" in edited(h)[0]
    await h.press("c:cat:0:0")  # «К списку»
    assert "1. ⬜ Простой" in edited(h)[0]


async def test_card_with_photo_is_new_message_replying_to_list(h):
    await h.press("i:i3:0:0")
    sent = h.session.last(SendPhoto)
    assert sent.photo == "AgAC" and sent.reply_to_message_id == 7 and "Описание" in sent.caption
    assert "cx" in cbs(sent.reply_markup)
    assert not [c for c in h.session.calls if isinstance(c, EditMessageText)]  # список не тронут


async def test_photo_card_toggle_edits_caption_and_refreshes_list(h):
    await h.press("ct:i3:0:0", message_id=900, photo=True, reply_to=7)
    assert await h.svc.is_done(42, "i3")
    assert "✅ отмечено" in h.session.last(EditMessageCaption).caption
    assert "3. ✅ С фото" in edited(h)[0]  # список (сообщение 7) перерисован
    assert h.session.last(EditMessageText).message_id == 7


async def test_photo_card_close_deletes_message(h):
    await h.press("cx", message_id=900, photo=True)
    assert h.session.last(DeleteMessage).message_id == 900


def _photo_and_body_ids(h):
    """(id фото-карточки, id сообщения с телом) по записанным вызовам сессии."""
    calls = h.session.calls
    photo_idx = next(i for i, c in enumerate(calls) if isinstance(c, SendPhoto))
    body_idx = next(i for i, c in enumerate(calls) if isinstance(c, SendMessage))
    return 901 + photo_idx, 501 + body_idx  # сессия нумерует 900+/500+ по порядку вызова


async def test_long_photo_card_close_deletes_body_message_too(h):
    await h.press("i:i5:0:0")  # подпись не влезает в 1024: тело ушло вторым сообщением
    photo_id, body_id = _photo_and_body_ids(h)
    await h.press("cx", message_id=photo_id, photo=True)
    deleted = {c.message_id for c in h.session.calls if isinstance(c, DeleteMessage)}
    assert deleted == {photo_id, body_id}


async def test_long_photo_card_close_after_toggle_still_deletes_body(h):
    await h.press("i:i5:0:0")
    photo_id, body_id = _photo_and_body_ids(h)
    await h.press("ct:i5:0:0", message_id=photo_id, photo=True, reply_to=7)  # клавиатура перерисована
    await h.press("cx", message_id=photo_id, photo=True)
    deleted = {c.message_id for c in h.session.calls if isinstance(c, DeleteMessage)}
    assert deleted == {photo_id, body_id}


async def test_hidden_spoiler_first_press_reveals_second_opens_card(h):
    await h.press("c:cat:0:0")
    assert "🔒" in edited(h)[0]
    await h.press("i:i4:0:0")  # первое нажатие: раскрывается название, карточка не открыта
    text, data = edited(h)
    assert "4. ⬜ Секретный" in text and "Тайна" not in text and "ct:i4:0:0" not in data
    assert not await h.svc.is_done(42, "i4")
    await h.press("i:i4:0:0")  # второе открывает карточку
    text, data = edited(h)
    assert "Тайна" in text and "ct:i4:0:0" in data and not await h.svc.is_done(42, "i4")


async def test_marked_spoiler_opens_card_at_once(h):
    await h.svc.mark(42, "i4")
    await h.press("i:i4:0:0")
    assert "Тайна" in edited(h)[0]


async def test_hidden_spoiler_is_not_opened_from_search_page(h):
    await h.press("i:i4:-1:0")
    assert "спойлер" in h.session.last(AnswerCallbackQuery).text
    assert not [c for c in h.session.calls if isinstance(c, (SendMessage, EditMessageText))]


async def test_marking_only_in_card_and_list_refreshes_after_back(h):
    await h.press("ct:i1:0:0")
    assert await h.svc.is_done(42, "i1")
    await h.press("c:cat:0:0")  # «К списку»
    assert "1. ✅ Простой" in edited(h)[0]


async def test_stale_item_does_not_crash(h):
    for data in ("i:gone:0:0", "ct:gone:0:0"):
        await h.press(data)
        assert h.session.last(AnswerCallbackQuery).text == "Список обновился"
        assert "Общий прогресс" in edited(h)[0]


async def test_card_without_details_shows_placeholder(h):
    await h.press("i:i1:0:0")
    text, data = edited(h)
    assert "Простой" in text and "Описание пока не добавлено." in text and "ct:i1:0:0" in data


async def test_card_from_search_is_new_message_with_close(h):
    await h.press("i:i2:-1:0")
    sent = h.session.last(SendMessage)
    assert "Где искать" in sent.text and sent.reply_to_message_id == 7
    assert "cx" in cbs(sent.reply_markup) and not [c for c in h.session.calls if isinstance(c, EditMessageText)]


async def test_broken_photo_falls_back_to_text_card(h):
    original = h.session.make_request

    async def failing(bot, method, timeout=None):
        if isinstance(method, SendPhoto):
            raise TelegramBadRequest(method=method, message="wrong file identifier")
        return await original(bot, method, timeout)

    h.session.make_request = failing
    await h.press("i:i3:0:0")
    assert "Описание" in edited(h)[0] and not [c for c in h.session.calls if isinstance(c, SendMessage)]
