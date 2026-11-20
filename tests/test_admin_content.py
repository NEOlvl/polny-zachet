import asyncio
import io
import shutil
import sqlite3
import tarfile

import pytest
import pytest_asyncio
import yaml
from aiogram import Bot
from aiogram.methods import SendDocument
from conftest import FIXTURE_CONTENT
from test_admin import ADMIN, USER2, sent_to
from test_flow import FakeSession, Harness

from app.content import ContentHolder, load_content
from app.db import Base, make_engine, make_session_factory
from app.handlers.admin import chunk_lines
from app.main import build_dispatcher
from app.progress import ProgressService


@pytest_asyncio.fixture
async def hc(tmp_path):
    """Харнесс, у которого папка контента — временная копия (команды реально пишут файлы)."""
    cdir = tmp_path / "content"
    shutil.copytree(FIXTURE_CONTENT, cdir)
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = make_session_factory(engine)
    svc = ProgressService(sessions)
    session = FakeSession()
    holder = ContentHolder(load_content(cdir))
    dp = build_dispatcher(holder, svc, "", admin_ids=[1], content_dir=cdir)
    harness = Harness(dp, Bot("123:abc", session=session), session, sessions, svc)
    harness.holder, harness.cdir = holder, cdir
    yield harness
    await engine.dispose()


def last_text(h, chat=1):
    return sent_to(h, chat)[-1].text


def file_items(h, name):
    return yaml.safe_load((h.cdir / name).read_text(encoding="utf-8"))["items"]


# --- доступ ---

@pytest.mark.parametrize("cmd", ["/add test_example | x", "/newcat a | b | c", "/edit test_001 | x",
                                 "/hide test_001", "/cat"])
async def test_non_admin_commands_get_no_reply_and_change_nothing(hc, cmd):
    await hc.send("/start", USER2)
    before = len(hc.session.calls)
    snapshot = (hc.cdir / "test_example.yaml").read_text(encoding="utf-8")
    await hc.send(cmd, USER2)
    assert len(hc.session.calls) == before
    assert (hc.cdir / "test_example.yaml").read_text(encoding="utf-8") == snapshot
    assert not list(hc.cdir.glob("a.yaml"))


# --- /add ---

async def test_add_writes_file_and_updates_live_content(hc):
    await hc.send("/add test_example | Новый пункт | Где искать | spoiler", ADMIN)
    assert last_text(hc) == "Добавлено: <code>test_004</code> в «ТЕСТ: пример категории», теперь 4 пунктов"
    assert file_items(hc, "test_example.yaml")[-1] == {
        "id": "test_004", "title": "Новый пункт", "hint": "Где искать", "spoiler": True}
    item = hc.holder.content.items_by_id["test_004"]  # без /reload
    assert item.title == "Новый пункт" and item.spoiler is True


async def test_add_without_optional_parts_and_with_dash_hint(hc):
    await hc.send("/add test_example | Первый", ADMIN)
    await hc.send("/add test_example | Второй | - | spoiler", ADMIN)
    new = file_items(hc, "test_example.yaml")[-2:]
    assert new[0] == {"id": "test_004", "title": "Первый"}
    assert new[1] == {"id": "test_005", "title": "Второй", "spoiler": True}


async def test_add_bad_format_shows_usage(hc):
    for cmd in ("/add", "/add test_example", "/add test_example |  ", "/add a | b | c | d | e",
                "/add test_example | x | y | nospoiler"):
        await hc.send(cmd, ADMIN)
        assert "Использование: /add" in last_text(hc)
    assert len(file_items(hc, "test_example.yaml")) == 3


async def test_add_unknown_category_reports_and_keeps_file(hc):
    before = (hc.cdir / "test_example.yaml").read_text(encoding="utf-8")
    await hc.send("/add nope | x", ADMIN)
    assert "Не записано" in last_text(hc) and "не найдена" in last_text(hc)
    assert (hc.cdir / "test_example.yaml").read_text(encoding="utf-8") == before


async def test_add_text_is_html_escaped_in_reply(hc):
    await hc.send("/add test_example | <b>x</b>", ADMIN)
    await hc.send("/hide nothing_here", ADMIN)
    assert "Не записано" in last_text(hc)
    await hc.send("/newcat nope | <i>T</i> | <b>x</b>", ADMIN)
    assert "&lt;i&gt;T&lt;/i&gt;" in last_text(hc)


# --- /newcat ---

async def test_newcat_creates_file_and_category_appears(hc):
    await hc.send("/newcat stunts | Трюки | Первый трюк", ADMIN)
    assert "Создана категория «Трюки»" in last_text(hc) and "stunts_001" in last_text(hc)
    assert (hc.cdir / "stunts.yaml").is_file()
    cat = hc.holder.content.category("stunts")
    assert cat.order == 20 and [i.id for i in cat.items] == ["stunts_001"]


async def test_newcat_requires_first_item(hc):
    await hc.send("/newcat stunts | Трюки", ADMIN)
    await hc.send("/newcat stunts | Трюки | ", ADMIN)
    assert "Использование: /newcat" in last_text(hc)
    assert not (hc.cdir / "stunts.yaml").exists()


# --- /edit ---

async def test_edit_changes_title_and_hint_and_clears_it(hc):
    await hc.send("/edit test_002 | Переименован | Теперь есть подсказка", ADMIN)
    assert last_text(hc) == "Изменено: <code>test_002</code> в «ТЕСТ: пример категории»"
    item = hc.holder.content.items_by_id["test_002"]
    assert (item.title, item.hint) == ("Переименован", "Теперь есть подсказка")
    await hc.send("/edit test_002 | Переименован | -", ADMIN)
    assert hc.holder.content.items_by_id["test_002"].hint is None


# --- /hide ---

async def test_hide_removes_item_but_keeps_progress(hc):
    await hc.send("/start", ADMIN)
    await hc.press("t:test_001:0:0", ADMIN)
    await hc.send("/hide test_001", ADMIN)
    assert "Удалено из списка: <code>test_001</code>" in last_text(hc) and "осталось 2" in last_text(hc)
    assert "test_001" not in hc.holder.content.items_by_id
    assert [i["id"] for i in file_items(hc, "test_example.yaml")] == ["test_002", "test_003"]
    assert "test_001" in await hc.svc.done_ids(1)  # запись прогресса в базе осталась


async def test_hide_last_item_refused(hc):
    await hc.send("/hide test_001", ADMIN)
    await hc.send("/hide test_002", ADMIN)
    await hc.send("/hide test_003", ADMIN)
    assert "последний пункт" in last_text(hc)
    assert [i["id"] for i in file_items(hc, "test_example.yaml")] == ["test_003"]


# --- /cat ---

async def test_cat_lists_categories_with_totals(hc):
    await hc.send("/cat", ADMIN)
    text = last_text(hc)
    assert "<code>test_example</code> — ТЕСТ: пример категории: 3 пунктов, с подсказкой 2" in text
    assert "Итого: категорий 1, пунктов 3, с подсказкой 2" in text


async def test_cat_with_id_lists_items(hc):
    await hc.send("/cat test_example", ADMIN)
    text = last_text(hc)
    assert "<code>test_001</code> — ТЕСТ: обычный пункт 💬" in text
    assert "<code>test_003</code> — ТЕСТ: пункт со спойлером 🔒 💬" in text
    assert "<code>test_002</code> — ТЕСТ: пункт без подсказки" in text
    await hc.send("/cat nope", ADMIN)
    assert "не найдена" in last_text(hc)


async def test_cat_long_list_split_into_several_messages(hc):
    for n in range(1, 120):
        await hc.send(f"/add test_example | {'Длинное название пункта ' * 3}{n}", ADMIN)
    before = len(sent_to(hc, 1))
    await hc.send("/cat test_example", ADMIN)
    parts = sent_to(hc, 1)[before:]
    assert len(parts) >= 2 and all(len(p.text) <= 4096 for p in parts)
    joined = "\n".join(p.text for p in parts)
    assert "test_122" in joined and "test_001" in joined


def test_chunk_lines_limits_and_keeps_all_lines():
    lines = [f"строка {n} " + "x" * 90 for n in range(200)]
    chunks = chunk_lines(lines, limit=1000)
    assert all(len(c) <= 1000 for c in chunks) and len(chunks) > 1
    assert "\n".join(chunks).split("\n") == lines
    assert chunk_lines(["a" * 5000], limit=100) == ["a" * 100]


# --- блокировка ---

async def test_concurrent_adds_are_serialized_and_get_distinct_ids(hc):
    await asyncio.gather(*(hc.send(f"/add test_example | Параллельный {n}", ADMIN) for n in range(8)))
    ids = [i["id"] for i in file_items(hc, "test_example.yaml")]
    assert len(ids) == 11 and len(set(ids)) == 11
    assert hc.holder.content.total_items == 11


# --- /export ---


def make_sqlite(path):
    con = sqlite3.connect(path)
    con.execute("create table t (x integer)")
    con.execute("insert into t values (7)")
    con.commit()
    con.close()


@pytest_asyncio.fixture
async def hx(tmp_path):
    cdir = tmp_path / "content"
    shutil.copytree(FIXTURE_CONTENT, cdir)
    db = tmp_path / "bot.db"
    make_sqlite(db)
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = make_session_factory(engine)
    svc = ProgressService(sessions)
    session = FakeSession()
    dp = build_dispatcher(ContentHolder(load_content(cdir)), svc, "", admin_ids=[1],
                          content_dir=cdir, db_path=db)
    harness = Harness(dp, Bot("123:abc", session=session), session, sessions, svc)
    harness.cdir = cdir
    yield harness
    await engine.dispose()


def docs(h):
    return [c for c in h.session.calls if isinstance(c, SendDocument)]


async def test_export_sends_content_archive_and_db_copy(hx):
    await hx.send("/export", ADMIN)
    sent = docs(hx)
    assert len(sent) == 2 and all(d.chat_id == 1 for d in sent)
    by_ext = {d.document.filename.split("-")[0]: d.document for d in sent}
    assert set(by_ext) == {"content", "bot"}
    with tarfile.open(fileobj=io.BytesIO(by_ext["content"].data), mode="r:gz") as tar:
        assert tar.getnames() == ["content/test_example.yaml"]
    db_file = hx.cdir.parent / "exported.db"
    db_file.write_bytes(by_ext["bot"].data)
    assert sqlite3.connect(db_file).execute("select x from t").fetchone() == (7,)


async def test_export_includes_content_written_by_add(hx):
    await hx.send("/add test_example | Новый для выгрузки", ADMIN)
    await hx.send("/export", ADMIN)
    archive = next(d.document for d in docs(hx) if d.document.filename.startswith("content-"))
    with tarfile.open(fileobj=io.BytesIO(archive.data), mode="r:gz") as tar:
        assert "Новый для выгрузки" in tar.extractfile("content/test_example.yaml").read().decode("utf-8")


async def test_export_not_available_to_non_admin(hx):
    await hx.send("/start", USER2)
    before = len(hx.session.calls)
    await hx.send("/export", USER2)
    assert len(hx.session.calls) == before and not docs(hx)


async def test_export_error_is_reported_not_raised(hx):
    (hx.cdir / "test_example.yaml").unlink()
    await hx.send("/export", ADMIN)
    assert "Не удалось подготовить выгрузку" in sent_to(hx, 1)[-1].text and not docs(hx)
