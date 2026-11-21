"""Сквозной сценарий через настоящий Dispatcher и подставную сессию Telegram."""
import time

import pytest_asyncio
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import Chat, Message
from conftest import FIXTURE_CONTENT
from sqlalchemy import func, select

from app.content import ContentHolder, load_content
from app.db import Base, Progress, User, make_engine, make_session_factory
from app.main import build_dispatcher
from app.progress import ProgressService

USER = {"id": 42, "is_bot": False, "first_name": "T", "username": "tester"}


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.edit_error = None
        self.sent_ids = []  # (chat_id, message_id) отправленных сообщений

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, EditMessageText) and self.edit_error:
            raise self.edit_error
        if isinstance(method, SendMessage):
            self.sent_ids.append((method.chat_id, 500 + len(self.calls)))
            return Message(message_id=500 + len(self.calls), date=int(time.time()),
                           chat=Chat(id=method.chat_id, type="private"), text=method.text)
        return True

    async def stream_content(self, *a, **kw):
        yield b""

    def last(self, kind):
        return [c for c in self.calls if isinstance(c, kind)][-1]


class Harness:
    def __init__(self, dp, bot, session, sessions, svc):
        self.dp, self.bot, self.session, self.sessions, self.svc = dp, bot, session, sessions, svc
        self.n = 0

    async def send(self, text, user=USER):
        self.n += 1
        chat = {"id": user["id"], "type": "private"}
        await self.dp.feed_raw_update(self.bot, {"update_id": self.n, "message": {
            "message_id": self.n, "date": int(time.time()), "chat": chat, "from": user, "text": text}})

    async def press(self, data, user=USER, message_id=7):
        self.n += 1
        chat = {"id": user["id"], "type": "private"}
        await self.dp.feed_raw_update(self.bot, {"update_id": self.n, "callback_query": {
            "id": str(self.n), "from": user, "chat_instance": "x", "data": data,
            "message": {"message_id": message_id, "date": int(time.time()), "chat": chat, "text": "old"}}})

    def screen(self):
        c = self.session.last(EditMessageText)
        return c.text, [b.callback_data or b.url for row in c.reply_markup.inline_keyboard for b in row]


@pytest_asyncio.fixture
async def h():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = make_session_factory(engine)
    svc = ProgressService(sessions)
    session = FakeSession()
    bot = Bot("123:abc", session=session)
    dp = build_dispatcher(ContentHolder(load_content(FIXTURE_CONTENT)), svc, "https://t.me/chan",
                          content_dir=FIXTURE_CONTENT)
    yield Harness(dp, bot, session, sessions, svc)
    await engine.dispose()


async def test_start_shows_menu_and_registers_user_with_source(h):
    await h.send("/start promo_1")
    sent = h.session.last(SendMessage)
    assert "Неофициальный фан-проект" in sent.text and "0%" in sent.text
    labels = [b.text for row in sent.reply_markup.inline_keyboard for b in row]
    assert labels == ["Чек-лист", "Мой прогресс", "Поиск", "Канал"]
    async with h.sessions() as s:
        user = await s.get(User, 42)
    assert user.source == "promo_1" and user.username == "tester"


async def test_full_walkthrough(h):
    await h.send("/start")
    await h.press("m:list")
    text, buttons = h.screen()
    assert "c:test_example:0:0" in buttons

    await h.press("c:test_example:0:0")
    text, buttons = h.screen()
    assert "ТЕСТ: обычный пункт" in text and "i:test_001:0:0" in buttons

    await h.press("i:test_001:0:0")  # нажатие на пункт открывает карточку, зачёт не меняется
    text, buttons = h.screen()
    assert "ТЕСТ: обычный пункт" in text and "💡 ТЕСТ: подсказка к пункту" in text
    assert not await h.svc.is_done(42, "test_001")
    assert "ct:test_001:0:0" in buttons and "r:test_001" in buttons

    await h.press("ct:test_001:0:0")  # отметка только из карточки
    assert await h.svc.is_done(42, "test_001") and "✅ отмечено" in h.screen()[0]
    await h.press("c:test_example:0:0")  # «К списку»
    text, buttons = h.screen()
    assert "1. ✅" in text and "i:test_002:0:0" in buttons  # остались на той же странице категории

    await h.press("i:test_003:0:0")  # спойлер: первое нажатие раскрывает, карточка не открывается
    text, _ = h.screen()
    assert "3. ⬜ ТЕСТ: пункт со спойлером" in text
    assert not await h.svc.is_done(42, "test_003")
    await h.press("i:test_003:0:0")  # второе открывает карточку
    assert "ct:test_003:0:0" in h.screen()[1]
    await h.press("ct:test_003:0:0")  # отметка в карточке
    assert await h.svc.is_done(42, "test_003")

    await h.press("m:prog")
    text, _ = h.screen()
    assert "66%" in text and "2/3" in text

    await h.press("c:test_example:0:0")  # повторный вход: отмеченный спойлер виден с названием
    text, _ = h.screen()
    assert "3. ✅ ТЕСТ: пункт со спойлером" in text and "🔒" not in text


async def test_toggle_off(h):
    await h.press("ct:test_001:0:0")
    await h.press("ct:test_001:0:0")
    assert not await h.svc.is_done(42, "test_001")


async def test_stale_button_does_not_crash_and_shows_menu(h):
    await h.press("i:removed_item:0:0")
    assert h.session.last(AnswerCallbackQuery).text == "Список обновился"
    text, _ = h.screen()
    assert "Общий прогресс" in text
    await h.press("t:test_001:0:0")  # кнопка отметки из списка старого формата
    assert h.session.last(AnswerCallbackQuery).text == "Список обновился"
    assert not await h.svc.is_done(42, "test_001")
    await h.press("c:removed_cat:0:0")
    assert h.session.last(AnswerCallbackQuery).text == "Список обновился"
    await h.press("garbage")
    assert h.session.last(AnswerCallbackQuery).text == "Список обновился"


async def test_message_not_modified_is_suppressed(h, caplog):
    h.session.edit_error = TelegramBadRequest(
        method=EditMessageText(text="x"), message="Bad Request: message is not modified")
    await h.press("m:prog")
    assert not [r for r in caplog.records if r.levelname == "ERROR"]


async def test_other_edit_errors_are_logged_not_raised(h, caplog):
    h.session.edit_error = TelegramBadRequest(method=EditMessageText(text="x"), message="Bad Request: boom")
    await h.press("m:prog")  # глобальный обработчик глушит и логирует
    assert [r for r in caplog.records if r.levelname == "ERROR"]


async def test_privacy_and_about(h):
    await h.send("/privacy")
    t = h.session.last(SendMessage).text
    assert "Telegram ID" in t and "/delete_me" in t
    await h.send("/about")
    assert "Неофициальный фан-проект" in h.session.last(SendMessage).text


async def test_delete_me_flow(h):
    await h.press("ct:test_001:0:0")
    await h.send("/delete_me")
    sent = h.session.last(SendMessage)
    assert [b.text for row in sent.reply_markup.inline_keyboard for b in row] == ["Да, удалить", "Отмена"]

    await h.press("d:0")  # отмена — данные на месте
    assert await h.svc.is_done(42, "test_001")

    await h.press("d:1")
    async with h.sessions() as s:
        assert await s.get(User, 42) is None
        assert await s.scalar(select(func.count()).select_from(Progress)) == 0
    assert "удалены" in h.session.last(EditMessageText).text


async def test_last_seen_updates_on_every_action(h):
    await h.send("/start")
    async with h.sessions() as s:
        first = (await s.get(User, 42)).last_seen
    time.sleep(0.01)
    await h.press("m:list")
    async with h.sessions() as s:
        assert (await s.get(User, 42)).last_seen > first


OTHER = {"id": 99, "is_bot": False, "first_name": "O", "username": "other"}


async def test_plain_text_gets_hint(h):
    await h.send("привет")
    assert h.session.last(SendMessage).text == "Нажми /start, чтобы открыть чек-лист"


async def test_unknown_command_gets_hint(h):
    await h.send("/foo")
    assert h.session.last(SendMessage).text == "Нажми /start, чтобы открыть чек-лист"


async def test_admin_commands_from_non_admin_get_no_reply(h):
    await h.send("/start", OTHER)
    before = len(h.session.calls)
    for cmd in ("/stats", "/reload", "/broadcast hi"):
        await h.send(cmd, OTHER)
    assert len(h.session.calls) == before


async def test_fallback_does_not_intercept_known_commands(h):
    await h.send("/privacy")
    assert "Telegram ID" in h.session.last(SendMessage).text
    await h.send("/start")
    assert "Неофициальный фан-проект" in h.session.last(SendMessage).text


async def test_marked_spoiler_visible_on_new_page_and_opens_card_at_once(h):
    await h.press("i:test_003:0:0")  # раскрыть
    await h.press("i:test_003:0:0")  # карточка
    await h.press("ct:test_003:0:0")  # отметить
    assert await h.svc.is_done(42, "test_003")

    await h.press("c:test_example:0:0")  # «новая страница»: раскрытые сброшены
    text, _ = h.screen()
    assert "3. ✅ ТЕСТ: пункт со спойлером" in text

    await h.press("i:test_003:0:0")  # отмеченный спойлер виден: карточка открывается сразу
    text, data = h.screen()
    assert "Статус: ✅ отмечено" in text and "ct:test_003:0:0" in data
    assert await h.svc.is_done(42, "test_003")


async def test_unmarked_spoiler_needs_reveal_before_card(h):
    await h.press("i:test_003:0:0")
    assert not await h.svc.is_done(42, "test_003")
    assert "3. ⬜ ТЕСТ: пункт со спойлером" in h.screen()[0]
    await h.press("c:test_example:0:0")
    text, _ = h.screen()
    assert "🔒" in text  # после смены страницы снова скрыт
