import os
import sqlite3
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select

from app.content import Category, Content, Item
from app.db import Base, Progress, User, make_engine, make_session_factory
from app.progress import ProgressService, normalize_source, percent

ROOT = Path(__file__).resolve().parent.parent


def make_content(*cats: tuple[str, list[str]]) -> Content:
    categories = tuple(
        Category(cid, cid.upper(), i, tuple(Item(x, x) for x in ids))
        for i, (cid, ids) in enumerate(cats)
    )
    return Content(
        categories,
        {i.id: i for c in categories for i in c.items},
        {i.id: c.id for c in categories for i in c.items},
    )


@pytest_asyncio.fixture
async def sessions():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield make_session_factory(engine)
    await engine.dispose()


@pytest.fixture
def svc(sessions):
    return ProgressService(sessions)


CONTENT = make_content(("cat_a", ["a1", "a2", "a3"]), ("cat_b", ["b1"]))


async def test_toggle_on_off(svc):
    assert await svc.toggle(1, "a1") is True
    assert await svc.is_done(1, "a1")
    assert await svc.toggle(1, "a1") is False
    assert not await svc.is_done(1, "a1")


async def test_mark_is_idempotent(svc):
    await svc.mark(1, "a1")
    await svc.mark(1, "a1")
    assert await svc.done_ids(1) == {"a1"}


async def test_unmark_missing_is_noop(svc):
    await svc.unmark(1, "a1")
    assert await svc.done_ids(1) == set()


async def test_progress_is_per_user(svc):
    await svc.mark(1, "a1")
    await svc.mark(2, "b1")
    assert await svc.done_ids(1) == {"a1"}
    assert await svc.done_ids(2) == {"b1"}


async def test_summary_counts_and_percent(svc):
    await svc.mark(1, "a1")
    await svc.mark(1, "b1")
    s = await svc.summary(1, CONTENT)
    assert (s.done, s.total, s.percent) == (2, 4, 50)
    by_id = {c.category_id: c for c in s.categories}
    assert (by_id["cat_a"].done, by_id["cat_a"].total) == (1, 3)
    assert by_id["cat_b"].percent == 100


async def test_summary_empty_user(svc):
    s = await svc.summary(1, CONTENT)
    assert (s.done, s.total, s.percent) == (0, 4, 0)


async def test_removed_item_not_counted_but_kept_in_db(svc, sessions):
    await svc.mark(1, "a1")
    await svc.mark(1, "gone")  # пункт удалён из YAML
    s = await svc.summary(1, CONTENT)
    assert s.done == 1 and s.total == 4
    async with sessions() as session:  # запись в базе не тронута
        assert await session.scalar(select(func.count()).select_from(Progress)) == 2


async def test_added_item_does_not_break_progress(svc):
    await svc.mark(1, "a1")
    bigger = make_content(("cat_a", ["a1", "a2", "a3", "new_item"]), ("cat_b", ["b1"]))
    s = await svc.summary(1, bigger)
    assert (s.done, s.total, s.percent) == (1, 5, 20)


def test_percent_rounds_down():
    assert percent(1, 3) == 33
    assert percent(2, 3) == 66
    assert percent(99, 100) == 99
    assert percent(3, 3) == 100
    assert percent(0, 0) == 0


async def test_empty_content(svc):
    s = await svc.summary(1, make_content())
    assert (s.done, s.total, s.percent) == (0, 0, 0)


async def test_delete_user_removes_everything(svc, sessions):
    await svc.touch_user(1, "vlad", "src")
    await svc.touch_user(2, "other")
    await svc.mark(1, "a1")
    await svc.mark(2, "a1")
    await svc.delete_user(1)
    async with sessions() as s:
        assert await s.get(User, 1) is None
        assert await s.get(User, 2) is not None
    assert await svc.done_ids(1) == set()
    assert await svc.done_ids(2) == {"a1"}


async def test_touch_user_creates_then_updates(svc, sessions):
    assert await svc.touch_user(1, "old", "post_1") is True
    assert await svc.touch_user(1, "new", "other_source") is False
    async with sessions() as s:
        u = await s.get(User, 1)
    assert u.username == "new"
    assert u.source == "post_1"  # source пишется один раз
    assert u.last_seen >= u.first_seen


@pytest.mark.parametrize("raw,expected", [
    (None, None), ("", None), ("test", "test"), ("a-b_C9", "a-b_C9"),
    ("bad source", None), ("кириллица", None), ("x" * 50, "x" * 32),
])
def test_normalize_source(raw, expected):
    assert normalize_source(raw) == expected


def test_alembic_migration_matches_models(tmp_path):
    db = tmp_path / "m.db"
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db}"
    try:
        cfg = Config(str(ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(ROOT / "migrations"))
        command.upgrade(cfg, "head")
    finally:
        del os.environ["DATABASE_URL"]
    con = sqlite3.connect(db)
    for table in Base.metadata.sorted_tables:
        cols = {r[1] for r in con.execute(f"PRAGMA table_info({table.name})")}
        assert cols == {c.name for c in table.columns}, table.name
    con.close()
