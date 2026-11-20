import json
import shutil
import sqlite3
from pathlib import Path

import pytest
import pytest_asyncio
from aiogram import Bot
from aiogram.methods import EditMessageText
from alembic import command
from alembic.config import Config
from conftest import FIXTURE_CONTENT
from test_admin import ADMIN, USER2, preview_id, sent_to
from test_flow import FakeSession, Harness

from app.announce import SnapshotError, format_notification, load_snapshot, new_by_category, save_snapshot
from app.content import ContentHolder, build_content, load_content
from app.db import Base, User, make_engine, make_session_factory
from app.main import build_dispatcher
from app.progress import ProgressService

ROOT = Path(__file__).resolve().parent.parent
USER3 = {"id": 3, "is_bot": False, "first_name": "C", "username": "third"}

DOCS = [
    ("a.yaml", {"id": "tainiki", "title": "Тайники", "order": 10,
                "items": [{"id": f"t_{n}", "title": f"Т{n}"} for n in range(1, 4)]}),
    ("b.yaml", {"id": "cars", "title": "Машины", "order": 20,
                "items": [{"id": "c_1", "title": "М1"}]}),
]


# --- снимок и текст ---

def test_snapshot_roundtrip_and_missing(tmp_path):
    path = tmp_path / "data" / "announced_items.json"
    assert load_snapshot(path) is None
    save_snapshot({"b", "a"}, path)
    assert load_snapshot(path) == {"a", "b"}
    assert json.loads(path.read_text(encoding="utf-8")) == ["a", "b"]
    assert not list(path.parent.glob(".*.tmp"))


@pytest.mark.parametrize("raw", ["не json", '{"a": 1}', "[1, 2]"])
def test_corrupt_snapshot_raises(tmp_path, raw):
    path = tmp_path / "s.json"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(SnapshotError):
        load_snapshot(path)


def test_new_by_category_and_text():
    content = build_content(DOCS)
    new = new_by_category(content, {"t_1"})
    assert [(c.id, n) for c, n in new] == [("tainiki", 2), ("cars", 1)]
    text = format_notification(new)
    assert text.startswith("Новое в чек-листе: Тайники +2, Машины +1")
    assert text.endswith("Отключить уведомления: /notify_off")
    assert new_by_category(content, {"t_1", "t_2", "t_3", "c_1"}) == []
    assert new_by_category(content, {"t_1", "t_2", "t_3", "c_1", "removed_item"}) == []  # скрытые пункты не мешают


# --- миграция ---

def test_migration_adds_notify_true_for_existing_users(tmp_path, monkeypatch):
    db = tmp_path / "bot.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db.as_posix()}")
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))

    command.upgrade(cfg, "0001")
    con = sqlite3.connect(db)
    con.execute("insert into users (tg_id, username, first_seen, last_seen, source) "
                "values (1, 'old', '2026-11-01 00:00:00', '2026-11-01 00:00:00', null)")
    con.commit()
    con.close()

    command.upgrade(cfg, "head")
    con = sqlite3.connect(db)
    assert con.execute("select notify from users where tg_id = 1").fetchone() == (1,)
    con.execute("insert into users (tg_id, first_seen, last_seen) values (2, 'x', 'x')")
    assert con.execute("select notify from users where tg_id = 2").fetchone() == (1,)  # по умолчанию true
    con.close()

    command.downgrade(cfg, "0001")
    con = sqlite3.connect(db)
    assert "notify" not in [r[1] for r in con.execute("pragma table_info(users)")]
    con.close()


# --- харнесс ---

@pytest_asyncio.fixture
async def hn(tmp_path):
    cdir = tmp_path / "content"
    shutil.copytree(FIXTURE_CONTENT, cdir)
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = make_session_factory(engine)
    svc = ProgressService(sessions)
    session = FakeSession()
    holder = ContentHolder(load_content(cdir))
    snap = tmp_path / "data" / "announced_items.json"
    dp = build_dispatcher(holder, svc, "", admin_ids=[1], content_dir=cdir, snapshot_path=snap)
    harness = Harness(dp, Bot("123:abc", session=session), session, sessions, svc)
    harness.snap, harness.holder = snap, holder
    yield harness
    await engine.dispose()


def last_text(h, chat=1):
    return sent_to(h, chat)[-1].text


def messages_to(h, chat):
    return [c for c in sent_to(h, chat) if c.text.startswith("Новое в чек-листе")]


# --- /notify_off, /notify_on ---

async def test_user_can_turn_notifications_off_and_on(hn):
    await hn.send("/start", USER2)
    assert await hn.svc.get_notify(2) is True  # по умолчанию включены
    await hn.send("/notify_off", USER2)
    assert await hn.svc.get_notify(2) is False
    assert "отключены" in last_text(hn, 2) and "/notify_on" in last_text(hn, 2)
    await hn.send("/notify_on", USER2)
    assert await hn.svc.get_notify(2) is True and "включены" in last_text(hn, 2)


async def test_notify_commands_not_swallowed_by_fallback(hn):
    await hn.send("/notify_off", USER2)
    assert "Нажми /start" not in last_text(hn, 2)


async def test_notify_flag_survives_activity_and_other_users_unaffected(hn):
    await hn.send("/start", USER2)
    await hn.send("/start", USER3)
    await hn.send("/notify_off", USER2)
    await hn.press("c:test_example:0:0", USER2)
    assert await hn.svc.get_notify(2) is False and await hn.svc.get_notify(3) is True
    assert await hn.svc.notify_user_ids() == [3]


async def test_privacy_mentions_notification_setting(hn):
    await hn.send("/privacy", USER2)
    assert "настройка уведомлений" in last_text(hn, 2) and "/notify_off" in last_text(hn, 2)


# --- /broadcast только для notify = true ---

async def test_broadcast_skips_users_with_notifications_off(hn):
    await hn.send("/start", ADMIN)
    await hn.send("/start", USER2)
    await hn.send("/start", USER3)
    await hn.send("/notify_off", USER2)
    await hn.send("/broadcast Привет", ADMIN)
    assert "Получателей: 2" in last_text(hn, 1)
    await hn.press("b:1", ADMIN, preview_id(hn))
    assert [c.text for c in sent_to(hn, 3)][-1] == "Привет"
    assert "Привет" not in [c.text for c in sent_to(hn, 2)]


# --- /notify_new ---

async def test_first_call_creates_snapshot_without_broadcast(hn):
    await hn.send("/start", USER2)
    await hn.send("/notify_new", ADMIN)
    assert last_text(hn) == "Снимок создан, рассылать нечего"
    assert load_snapshot(hn.snap) == {"test_001", "test_002", "test_003"}
    assert not messages_to(hn, 2)


async def test_no_new_items_nothing_to_send(hn):
    await hn.send("/notify_new", ADMIN)
    await hn.send("/notify_new", ADMIN)
    assert last_text(hn) == "Новых пунктов нет, рассылать нечего"


async def prepare_new_items(hn):
    await hn.send("/start", ADMIN)
    await hn.send("/start", USER2)
    await hn.send("/start", USER3)
    await hn.send("/notify_new", ADMIN)  # снимок
    await hn.send("/add test_example | Новый один", ADMIN)
    await hn.send("/add test_example | Новый два", ADMIN)
    await hn.send("/newcat cars | Машины | Первая машина", ADMIN)


async def test_notify_new_preview_then_send_to_subscribers_only(hn):
    await prepare_new_items(hn)
    await hn.send("/notify_new", ADMIN)
    preview = last_text(hn)
    assert "Получателей: 3" in preview
    assert "Новое в чек-листе: ТЕСТ: пример категории +2, Машины +1" in preview
    assert "Отключить уведомления: /notify_off" in preview
    assert not messages_to(hn, 2)  # до подтверждения ничего не ушло

    await hn.send("/notify_off", USER3)  # отписался до отправки
    await hn.press("b:1", ADMIN, preview_id(hn))
    got = messages_to(hn, 2)
    assert len(got) == 1 and not messages_to(hn, 3)
    markup = got[0].reply_markup
    assert [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row] == [
        ("Открыть чек-лист", "m:list")]
    assert "Доставлено: 2" in hn.session.last(EditMessageText).text  # админ и USER2
    assert load_snapshot(hn.snap) >= {"test_004", "test_005", "cars_001"}

    await hn.send("/notify_new", ADMIN)
    assert last_text(hn) == "Новых пунктов нет, рассылать нечего"


async def test_open_checklist_button_works_in_notification(hn):
    await hn.send("/start", USER2)
    await hn.press("m:list", USER2, message_id=777)
    assert "Чек-лист" in hn.session.last(EditMessageText).text


async def test_cancel_keeps_snapshot_and_repeats_next_time(hn):
    await prepare_new_items(hn)
    before = load_snapshot(hn.snap)
    await hn.send("/notify_new", ADMIN)
    await hn.press("b:0", ADMIN, preview_id(hn))
    assert hn.session.last(EditMessageText).text == "Рассылка отменена."
    assert load_snapshot(hn.snap) == before and not messages_to(hn, 2)
    await hn.send("/notify_new", ADMIN)
    assert "Новое в чек-листе: ТЕСТ: пример категории +2" in last_text(hn)


async def test_double_press_sends_once(hn):
    await prepare_new_items(hn)
    await hn.send("/notify_new", ADMIN)
    pid = preview_id(hn)
    await hn.press("b:1", ADMIN, pid)
    await hn.press("b:1", ADMIN, pid)
    assert len(messages_to(hn, 2)) == 1
    assert "уже обработана" in hn.session.last(EditMessageText).text


async def test_items_added_after_preview_are_announced_next_time(hn):
    await prepare_new_items(hn)
    await hn.send("/notify_new", ADMIN)
    pid = preview_id(hn)
    await hn.send("/add test_example | Появился после предпросмотра", ADMIN)
    await hn.press("b:1", ADMIN, pid)
    await hn.send("/notify_new", ADMIN)
    assert "Новое в чек-листе: ТЕСТ: пример категории +1" in last_text(hn)


async def test_corrupt_snapshot_reported_and_left_alone(hn):
    hn.snap.parent.mkdir(parents=True)
    hn.snap.write_text("{сломано", encoding="utf-8")
    await hn.send("/notify_new", ADMIN)
    assert "Не удалось прочитать" in last_text(hn)
    assert hn.snap.read_text(encoding="utf-8") == "{сломано"


async def test_notify_new_is_admin_only(hn):
    await hn.send("/start", USER2)
    before = len(hn.session.calls)
    await hn.send("/notify_new", USER2)
    assert len(hn.session.calls) == before and not hn.snap.exists()


async def test_user_model_default_notify_true(hn):
    async with hn.sessions() as s:
        s.add(User(tg_id=99, first_seen=__import__("datetime").datetime(2026, 1, 1),
                   last_seen=__import__("datetime").datetime(2026, 1, 1)))
        await s.commit()
        assert (await s.get(User, 99)).notify is True
