import time

import pytest_asyncio
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.methods import EditMessageText, SendMessage
from conftest import FIXTURE_CONTENT
from test_flow import FakeSession, Harness

from app.broadcast import send_broadcast
from app.content import ContentError, ContentHolder, load_content
from app.db import Base, make_engine, make_session_factory
from app.handlers import admin as admin_module
from app.main import build_dispatcher
from app.progress import ProgressService
from app.stats import collect_stats, format_stats

ADMIN = {"id": 1, "is_bot": False, "first_name": "A", "username": "boss"}
USER2 = {"id": 2, "is_bot": False, "first_name": "B", "username": "guest"}


@pytest_asyncio.fixture
async def h():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = make_session_factory(engine)
    svc = ProgressService(sessions)
    session = FakeSession()
    holder = ContentHolder(load_content(FIXTURE_CONTENT))
    dp = build_dispatcher(holder, svc, "", admin_ids=[1], content_dir=FIXTURE_CONTENT)
    harness = Harness(dp, Bot("123:abc", session=session), session, sessions, svc)
    harness.holder = holder
    yield harness
    await engine.dispose()


def preview_id(h, chat_id=1):
    """message_id последнего сообщения, отправленного в чат (то есть предпросмотра)."""
    return [m for c, m in h.session.sent_ids if c == chat_id][-1]


def sent_to(h, chat_id):
    return [c for c in h.session.calls if isinstance(c, SendMessage) and c.chat_id == chat_id]


# --- доступ ---

async def test_non_admin_commands_silently_ignored(h):
    await h.send("/start", USER2)
    before = len(h.session.calls)
    for cmd in ("/stats", "/reload", "/broadcast hi"):
        await h.send(cmd, USER2)
    assert len(h.session.calls) == before  # ни одного ответа


async def test_non_admin_cannot_confirm_broadcast(h):
    await h.send("/start", USER2)
    await h.send("/broadcast secret", ADMIN)
    await h.press("b:1", USER2, preview_id(h))
    assert not [c for c in sent_to(h, 2) if c.text == "secret"]


# --- /stats и source ---

async def test_stats_with_source_from_second_account(h):
    await h.send("/start", ADMIN)
    await h.send("/start test", USER2)
    await h.press("ct:test_001:0:0", USER2)
    await h.send("/stats", ADMIN)
    text = sent_to(h, 1)[-1].text
    assert "Пользователей: 2" in text and "Новых за сутки: 2" in text
    assert "Активных за сутки: 2" in text and "Активных за 7 дней: 2" in text
    assert "test: 1" in text and "без метки: 1" in text
    assert "Среднее число отметок: 0.5" in text
    assert "ТЕСТ: пример категории: 1" in text


async def test_stats_ignores_items_removed_from_content(h):
    await h.send("/start", ADMIN)
    await h.svc.mark(1, "removed_item")
    st = await collect_stats(h.sessions, h.holder.content)
    assert st.avg_marks == 0 and st.top_categories == ()


async def test_stats_empty_db(h):
    st = await collect_stats(h.sessions, h.holder.content)
    assert st.total_users == 0 and st.avg_marks == 0
    assert "Пользователей: 0" in format_stats(st)


# --- /reload ---

async def test_reload_ok(h):
    await h.send("/reload", ADMIN)
    assert "Контент обновлён" in sent_to(h, 1)[-1].text


async def test_reload_invalid_keeps_old_content(h, monkeypatch):
    old = h.holder.content

    def boom(*_):
        raise ContentError("a.yaml, пункт «x»: id уже используется <дубль>")
    monkeypatch.setattr(admin_module, "load_content", boom)
    await h.send("/reload", ADMIN)
    text = sent_to(h, 1)[-1].text
    assert "остаётся старый" in text and "a.yaml" in text
    assert h.holder.content is old


async def test_reload_swaps_content_without_restart(h, monkeypatch):
    new = load_content(FIXTURE_CONTENT)
    monkeypatch.setattr(admin_module, "load_content", lambda *_: new)
    await h.send("/reload", ADMIN)
    assert h.holder.content is new


# --- /broadcast ---

async def test_broadcast_requires_confirmation(h):
    await h.send("/start", ADMIN)
    await h.send("/start", USER2)
    await h.send("/broadcast Привет всем", ADMIN)

    preview = sent_to(h, 1)[-1]
    assert "Получателей: 2" in preview.text and "Привет всем" in preview.text
    assert [b.text for row in preview.reply_markup.inline_keyboard for b in row] == ["Отправить", "Отмена"]
    assert not [c for c in sent_to(h, 2) if c.text == "Привет всем"]  # пока ничего не ушло

    await h.press("b:1", ADMIN, preview_id(h))
    assert [c.text for c in sent_to(h, 2)][-1] == "Привет всем"
    assert [c.text for c in sent_to(h, 1)][-1] == "Привет всем"
    report = h.session.last(EditMessageText).text
    assert "Доставлено: 2" in report and "Заблокировали бота: 0" in report and "Ошибки: 0" in report


async def test_broadcast_cancel(h):
    await h.send("/start", USER2)
    await h.send("/broadcast text", ADMIN)
    await h.press("b:0", ADMIN, preview_id(h))
    assert not [c for c in sent_to(h, 2) if c.text == "text"]
    assert "отменена" in h.session.last(EditMessageText).text


async def test_broadcast_double_confirm_sends_once(h):
    await h.send("/start", USER2)
    await h.send("/broadcast once", ADMIN)
    pid = preview_id(h)
    await h.press("b:1", ADMIN, pid)
    await h.press("b:1", ADMIN, pid)
    assert len([c for c in sent_to(h, 2) if c.text == "once"]) == 1
    assert "устарела" in h.session.last(EditMessageText).text


async def test_broadcast_two_previews_each_button_sends_own_text(h):
    await h.send("/start", USER2)
    await h.send("/broadcast AAA", ADMIN)
    first = preview_id(h)
    await h.send("/broadcast BBB", ADMIN)
    second = preview_id(h)
    assert first != second

    await h.press("b:1", ADMIN, first)  # «Отправить» под первым предпросмотром
    texts = [c.text for c in sent_to(h, 2)]
    assert "AAA" in texts and "BBB" not in texts

    await h.press("b:1", ADMIN, first)  # повторное нажатие ничего не шлёт
    assert [c.text for c in sent_to(h, 2)].count("AAA") == 1
    assert "устарела" in h.session.last(EditMessageText).text

    await h.press("b:1", ADMIN, second)  # второй черновик жив и отправляет своё
    assert "BBB" in [c.text for c in sent_to(h, 2)]


async def test_broadcast_empty_text(h):
    await h.send("/broadcast", ADMIN)
    assert "Использование" in sent_to(h, 1)[-1].text


async def test_broadcast_sends_plain_text_not_html(h):
    await h.send("/start", USER2)
    await h.send("/broadcast a <b>x</b> & y", ADMIN)
    await h.press("b:1", ADMIN, preview_id(h))
    msg = [c for c in sent_to(h, 2) if "<b>x</b>" in c.text][-1]
    assert msg.parse_mode is None


# --- логика рассылки: ошибки и лимит ---

class ScriptedSession(FakeSession):
    """Для выбранных chat_id кидает заданные ошибки."""

    def __init__(self, script):
        super().__init__()
        self.script = script  # chat_id -> список исключений (по одному на попытку)
        self.times = []
        self.attempts = []

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, SendMessage):
            self.times.append(time.monotonic())
            self.attempts.append(method.chat_id)
            queue = self.script.get(method.chat_id)
            if queue:
                raise queue.pop(0)
        return await super().make_request(bot, method, timeout)


async def test_send_broadcast_handles_forbidden_retry_and_errors():
    m = SendMessage(chat_id=0, text="x")
    session = ScriptedSession({
        2: [TelegramForbiddenError(m, "blocked")],
        3: [TelegramRetryAfter(m, "flood", 0)],   # один раз ждём и повторяем — успех
        4: [TelegramBadRequest(m, "chat not found")],
    })
    bot = Bot("123:abc", session=session)
    report = await send_broadcast(bot, [1, 2, 3, 4, 5], "hi", interval=0)
    assert (report.delivered, report.blocked, report.errors) == (3, 1, 1)
    assert session.attempts.count(3) == 2  # повтор после ожидания


async def test_send_broadcast_rate_limit_20_per_second():
    session = ScriptedSession({})
    bot = Bot("123:abc", session=session)
    start = time.monotonic()
    await send_broadcast(bot, list(range(1, 11)), "hi")  # интервал по умолчанию
    assert time.monotonic() - start >= 9 / 20
