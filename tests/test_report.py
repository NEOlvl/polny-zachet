import logging
import time

import pytest_asyncio
from aiogram import Bot
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from conftest import FIXTURE_CONTENT
from test_flow import USER, FakeSession, Harness

from app import ui
from app.callbacks import ReportCancelCb, ReportCb
from app.content import ContentHolder, load_content
from app.db import Base, make_engine, make_session_factory
from app.handlers import report as report_module
from app.main import build_dispatcher
from app.progress import ProgressService

ADMIN1 = 1
ADMIN2 = 5
OTHER = {"id": 43, "is_bot": False, "first_name": "O", "username": "other"}


class FailingSession(FakeSession):
    """Отправка сообщений выбранным чатам падает (админ заблокировал бота и т. п.)."""

    def __init__(self):
        super().__init__()
        self.fail_for: set[int] = set()

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, SendMessage) and method.chat_id in self.fail_for:
            self.calls.append(method)
            raise RuntimeError(f"секрет в тексте ошибки: {method.text}")
        return await super().make_request(bot, method, timeout)


@pytest_asyncio.fixture
async def h():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = make_session_factory(engine)
    svc = ProgressService(sessions)
    session = FailingSession()
    dp = build_dispatcher(ContentHolder(load_content(FIXTURE_CONTENT)), svc, "", admin_ids=[ADMIN1, ADMIN2],
                          content_dir=FIXTURE_CONTENT)
    yield Harness(dp, Bot("123:abc", session=session), session, sessions, svc)
    await engine.dispose()


def texts_to(h, chat):
    return [c.text for c in h.session.calls if isinstance(c, SendMessage) and c.chat_id == chat]


def reports_to(h, chat):
    return [t for t in texts_to(h, chat) if t.startswith("⚠️ <b>Ошибка в пункте")]


def prompt_id(h, chat=42):
    return [m for c, m in h.session.sent_ids if c == chat][-1]


def labels(markup):
    return [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row]


async def start_report(h, user=USER, item="test_001"):
    await h.press(f"r:{item}", user)


# --- кнопка на экране категории ---

def test_report_button_only_for_last_pressed_visible_item():
    from app.content import Category, Item
    cat = Category("c", "К", 1, (Item("i1", "Один"), Item("i2", "Два", spoiler=True)))
    _, kb = ui.category_screen(cat, set(), 0)
    assert not [b for b in labels(kb) if b[0].startswith("⚠️")]  # ничего ещё не нажимали
    _, kb = ui.category_screen(cat, set(), 0, focus="i1")
    assert ("⚠️ Ошибка в пункте", "r:i1") in labels(kb)
    _, kb = ui.category_screen(cat, set(), 0, focus="i2")  # спойлер скрыт — на что жаловаться нельзя
    assert not [b for b in labels(kb) if b[0].startswith("⚠️")]
    _, kb = ui.category_screen(cat, set(), 0, opened={"i2"}, focus="i2")
    assert ("⚠️ Ошибка в пункте", "r:i2") in labels(kb)
    _, kb = ui.category_screen(cat, {"i1"}, 0, focus="i1", only_undone=True)  # пункт исчез из фильтра
    assert not [b for b in labels(kb) if b[0].startswith("⚠️")]


def test_report_callbacks_fit_64_bytes():
    assert len(ReportCb(item="a" * 24).pack().encode()) <= 64
    assert ReportCb.unpack(ReportCb(item="col_001").pack()).item == "col_001"
    assert ReportCancelCb.unpack(ReportCancelCb().pack()) == ReportCancelCb()


async def test_button_appears_after_pressing_item_in_bot(h):
    await h.press("t:test_001:0:0")
    c = h.session.last(EditMessageText)
    assert ("⚠️ Ошибка в пункте", "r:test_001") in labels(c.reply_markup)


# --- основной сценарий ---

async def test_report_goes_to_all_admins_with_item_id_and_category(h):
    await start_report(h)
    prompt = [c for c in h.session.calls if isinstance(c, SendMessage)][-1]
    assert "ТЕСТ: обычный пункт" in prompt.text
    assert labels(prompt.reply_markup) == [("Отмена", "rx")]

    await h.send("Опечатка в <названии> & подсказке")
    for admin in (ADMIN1, ADMIN2):
        got = reports_to(h, admin)
        assert len(got) == 1
        assert "<code>test_001</code>" in got[0] and "ТЕСТ: обычный пункт" in got[0]
        assert "Категория: ТЕСТ: пример категории" in got[0]
        assert "Опечатка в &lt;названии&gt; &amp; подсказке" in got[0]
    assert texts_to(h, 42)[-1] == "Спасибо, проверим"


async def test_state_cleared_after_report(h):
    await start_report(h)
    await h.send("Ошибка")
    await h.send("привет")  # обычный текст снова получает подсказку про /start
    assert texts_to(h, 42)[-1] == "Нажми /start, чтобы открыть чек-лист"
    assert len(reports_to(h, ADMIN1)) == 1


async def test_plain_text_without_report_button_is_not_a_report(h):
    await h.send("Ошибка где-то")
    assert not reports_to(h, ADMIN1)


async def test_cancel_button(h):
    await start_report(h)
    await h.press("rx", USER, prompt_id(h))
    assert h.session.last(EditMessageText).text == "Отменено."
    await h.send("текст после отмены")
    assert not reports_to(h, ADMIN1) and texts_to(h, 42)[-1].startswith("Нажми /start")


async def test_command_cancels_waiting_and_runs(h):
    await start_report(h)
    await h.send("/privacy")
    assert "Telegram ID" in texts_to(h, 42)[-1]
    await h.send("а это уже не отчёт")
    assert not reports_to(h, ADMIN1)


async def test_non_text_message_keeps_waiting(h):
    await start_report(h)
    h.n += 1
    await h.dp.feed_raw_update(h.bot, {"update_id": h.n, "message": {
        "message_id": h.n, "date": int(time.time()), "chat": {"id": 42, "type": "private"},
        "from": USER, "dice": {"emoji": "🎲", "value": 3}}})
    assert texts_to(h, 42)[-1] == "Опиши ошибку текстом или нажми «Отмена»."
    await h.send("теперь текст")
    assert len(reports_to(h, ADMIN1)) == 1


async def test_too_long_text_asks_to_shorten_and_keeps_waiting(h):
    await start_report(h)
    await h.send("я" * 1001)
    assert "максимум 1000" in texts_to(h, 42)[-1] and not reports_to(h, ADMIN1)
    await h.send("я" * 1000)
    assert len(reports_to(h, ADMIN1)) == 1


async def test_stale_item_button(h):
    await h.press("r:nope")
    assert h.session.last(AnswerCallbackQuery).text == "Список обновился"
    assert "Общий прогресс" in h.session.last(EditMessageText).text


async def test_waiting_state_is_per_user(h):
    await start_report(h)
    await h.send("привет", OTHER)  # у другого пользователя ожидания нет
    assert not reports_to(h, ADMIN1)
    assert texts_to(h, 43)[-1].startswith("Нажми /start")


# --- лимит и устаревание ---

async def test_limit_three_reports_per_hour(h, monkeypatch):
    now = [5000.0]
    monkeypatch.setattr(report_module, "monotonic", lambda: now[0])
    for n in range(3):
        await start_report(h)
        await h.send(f"ошибка {n}")
        now[0] += 60
    assert len(reports_to(h, ADMIN1)) == 3

    before = len([c for c in h.session.calls if isinstance(c, SendMessage)])
    await start_report(h)  # четвёртая за час
    alert = h.session.last(AnswerCallbackQuery)
    assert "не больше 3" in alert.text and alert.show_alert
    assert len([c for c in h.session.calls if isinstance(c, SendMessage)]) == before  # вопрос не задан

    now[0] += 3600  # час прошёл
    await start_report(h)
    await h.send("снова можно")
    assert len(reports_to(h, ADMIN1)) == 4


async def test_limit_is_per_user(h, monkeypatch):
    for n in range(3):
        await start_report(h)
        await h.send(f"ошибка {n}")
    await start_report(h, OTHER)
    await h.send("я другой", OTHER)
    assert len(reports_to(h, ADMIN1)) == 4


async def test_waiting_expires_after_ttl(h, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(report_module, "monotonic", lambda: now[0])
    await start_report(h)
    now[0] += 11 * 60
    await h.send("давно забытое")
    assert not reports_to(h, ADMIN1) and texts_to(h, 42)[-1].startswith("Нажми /start")


# --- сбои доставки и логи ---

async def test_one_admin_failing_still_thanks_user(h):
    h.session.fail_for = {ADMIN1}
    await start_report(h)
    await h.send("ошибка")
    assert len([t for t in texts_to(h, ADMIN2) if t.startswith("⚠️")]) == 1
    assert texts_to(h, 42)[-1] == "Спасибо, проверим"


async def test_all_admins_failing_reports_failure_and_does_not_count(h):
    h.session.fail_for = {ADMIN1, ADMIN2}
    for n in range(4):  # неудачные попытки лимит не расходуют
        await start_report(h)
        await h.send(f"ошибка {n}")
        assert texts_to(h, 42)[-1] == "Не удалось отправить сообщение. Попробуй позже."
    h.session.fail_for = set()
    await start_report(h)
    await h.send("наконец")
    assert texts_to(h, 42)[-1] == "Спасибо, проверим"


async def test_report_text_never_logged(h, caplog):
    h.session.fail_for = {ADMIN1}  # сбой отправки тоже пишет в лог, но без текста
    with caplog.at_level(logging.DEBUG):
        await start_report(h)
        await h.send("СекретноеОписание777")
    assert "СекретноеОписание777" not in caplog.text


# --- /privacy ---

async def test_privacy_mentions_admin_receives_report_text(h):
    await h.send("/privacy")
    text = texts_to(h, 42)[-1]
    assert "текст твоего сообщения получит администратор" in text
    assert 4 <= len(text.split("\n")) <= 6
