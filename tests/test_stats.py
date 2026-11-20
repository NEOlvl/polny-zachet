from datetime import timedelta

import pytest_asyncio
from aiogram import Bot
from test_admin import ADMIN, sent_to
from test_flow import FakeSession, Harness

from app.content import ContentHolder, build_content
from app.db import Base, Progress, User, make_engine, make_session_factory, utcnow
from app.main import build_dispatcher
from app.progress import ProgressService
from app.stats import collect_stats, format_stats, format_stats_parts
from app.textutil import pack_blocks

CONTENT = build_content([
    ("a.yaml", {"id": "cat_a", "title": "Тайники <A>", "order": 10,
                "items": [{"id": "a1", "title": "A1"}, {"id": "a2", "title": "A2"}]}),
    ("b.yaml", {"id": "cat_b", "title": "Машины", "order": 20, "items": [{"id": "b1", "title": "B1"}]}),
    ("c.yaml", {"id": "cat_c", "title": "Пустая", "order": 30, "items": [{"id": "c1", "title": "C1"}]}),
])


@pytest_asyncio.fixture
async def sessions():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield make_session_factory(engine)
    await engine.dispose()


async def add_user(sessions, tg_id, first_ago, last_ago, source=None, notify=True, marks=()):
    now = utcnow()
    async with sessions() as s:
        s.add(User(tg_id=tg_id, first_seen=now - first_ago, last_seen=now - last_ago,
                   source=source, notify=notify))
        for item_id in marks:
            s.add(Progress(tg_id=tg_id, item_id=item_id, done_at=now))
        await s.commit()


H, D = timedelta(hours=1), timedelta(days=1)


async def populate(sessions):
    await add_user(sessions, 1, 10 * D, H, "a", marks=["a1", "a2", "gone_item"])  # вернулся (10 суток)
    await add_user(sessions, 2, 3 * D, 3 * D - H, "a", notify=False)  # не вернулся, без отметок
    await add_user(sessions, 3, 2 * D, H, "b", marks=["b1"])  # вернулся через 1+ сутки
    await add_user(sessions, 4, H, timedelta(0), None)  # пришёл час назад


async def test_activation_returns_notify_and_category_fill(sessions):
    await populate(sessions)
    st = await collect_stats(sessions, CONTENT)
    assert st.total_users == 4
    assert st.activated == 2  # пользователи 1 и 3
    assert st.returned_1d == (2, 3)  # пришли ≥ 1 суток назад: 1, 2, 3; вернулись 1 и 3
    assert st.returned_7d == (1, 1)  # пришёл ≥ 7 суток назад только пользователь 1
    assert st.notify_off == 1
    assert dict(st.category_fill) == {"Тайники <A>": 1, "Машины": 1, "Пустая": 0}
    assert [t for t, _ in st.category_fill] == ["Тайники <A>", "Машины", "Пустая"]  # порядок контента


async def test_per_source_users_active_and_average_marks(sessions):
    await populate(sessions)
    st = await collect_stats(sessions, CONTENT)
    by = {x.source: x for x in st.source_stats}
    assert (by["a"].users, by["a"].active_7d, by["a"].avg_marks) == (2, 2, 1.0)  # (2 + 0) / 2; gone_item не в счёте
    assert (by["b"].users, by["b"].active_7d, by["b"].avg_marks) == (1, 1, 1.0)
    assert (by["без метки"].users, by["без метки"].avg_marks) == (1, 0.0)
    assert [x.source for x in st.source_stats] == ["a", "b", "без метки"]  # по числу пользователей


async def test_old_metrics_unchanged(sessions):
    await populate(sessions)
    st = await collect_stats(sessions, CONTENT)
    assert (st.new_24h, st.active_24h, st.active_7d) == (1, 3, 4)
    assert st.avg_marks == 3 / 4  # a1, a2, b1; удалённый пункт не считается
    assert st.top_categories == (("Тайники <A>", 2), ("Машины", 1))
    assert st.sources == (("a", 2), ("b", 1), ("без метки", 1))


async def test_active_7d_boundary_per_source(sessions):
    await add_user(sessions, 1, 20 * D, 8 * D, "x")  # давно не заходил
    await add_user(sessions, 2, 20 * D, 6 * D, "x")
    st = await collect_stats(sessions, CONTENT)
    assert st.source_stats[0].active_7d == 1


async def test_retention_requires_strictly_more_than_period(sessions):
    # last_seen ровно через сутки после first_seen — «не позже, чем на сутки», не считается возвратом
    await add_user(sessions, 1, 5 * D, 4 * D, "x")  # delta = 1 сутки ровно
    await add_user(sessions, 2, 5 * D, 4 * D - H, "x")  # delta = 1 сутки + 1 час
    st = await collect_stats(sessions, CONTENT)
    assert st.returned_1d == (1, 2)


async def test_empty_database(sessions):
    st = await collect_stats(sessions, CONTENT)
    assert (st.total_users, st.activated, st.returned_1d, st.returned_7d, st.notify_off) == (0, 0, (0, 0), (0, 0), 0)
    assert st.source_stats == ()
    text = format_stats(st)
    assert "Пользователей: 0" in text and "Отметили хотя бы 1 пункт: —" in text
    assert "Вернулись через 1+ сутки: —" in text
    assert "<b>Источники</b>\n—" in text


async def test_format_contains_all_new_sections_and_escapes_html(sessions):
    await populate(sessions)
    text = format_stats(await collect_stats(sessions, CONTENT))
    assert "Отметили хотя бы 1 пункт: 50% (2 из 4)" in text
    assert "Вернулись через 1+ сутки: 66% (2 из 3)" in text
    assert "Вернулись через 7+ суток: 100% (1 из 1)" in text
    assert "Отключили уведомления: 1" in text
    assert "a: 2 польз., активных за 7 дней 2, отметок в среднем 1.0" in text
    assert "Тайники &lt;A&gt;: 25% (1 из 4)" in text and "Пустая: 0% (0 из 4)" in text
    assert "<A>" not in text


# --- несколько сообщений ---

def test_pack_blocks_keeps_sections_together_and_splits_by_limit():
    blocks = [["Заголовок 1", "а" * 100], ["Заголовок 2", "б" * 100], ["Заголовок 3", "в" * 100]]
    assert len(pack_blocks(blocks, limit=10_000)) == 1
    parts = pack_blocks(blocks, limit=250)
    assert len(parts) == 2 and all(len(p) <= 250 for p in parts)
    assert parts[0].startswith("Заголовок 1") and "Заголовок 2" in parts[0] and parts[1].startswith("Заголовок 3")


def test_pack_blocks_splits_oversized_block_by_lines():
    block = [f"строка {n} " + "x" * 90 for n in range(100)]
    parts = pack_blocks([["Раньше"], block], limit=1000)
    assert all(len(p) <= 1000 for p in parts) and parts[0] == "Раньше"
    assert "\n".join(parts[1:]).split("\n") == block


async def test_long_report_is_split_into_several_messages(sessions):
    many = build_content([(f"c{n}.yaml", {"id": f"cat_{n}", "title": f"Категория номер {n} " + "я" * 40,
                                          "order": n, "items": [{"id": f"i_{n}", "title": "x"}]})
                          for n in range(1, 151)])
    await add_user(sessions, 1, 2 * D, H, "src", marks=[f"i_{n}" for n in range(1, 151)])
    parts = format_stats_parts(await collect_stats(sessions, many))
    assert len(parts) >= 2 and all(len(p) <= 4096 for p in parts)
    joined = "\n".join(parts)
    assert "Категория номер 150" in joined and "Категория номер 1 " in joined


@pytest_asyncio.fixture
async def h_many():
    many = build_content([(f"c{n}.yaml", {"id": f"cat_{n}", "title": f"Категория номер {n} " + "я" * 40,
                                          "order": n, "items": [{"id": f"i_{n}", "title": "x"}]})
                          for n in range(1, 151)])
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = make_session_factory(engine)
    svc = ProgressService(sessions)
    session = FakeSession()
    dp = build_dispatcher(ContentHolder(many), svc, "", admin_ids=[1])
    harness = Harness(dp, Bot("123:abc", session=session), session, sessions, svc)
    yield harness
    await engine.dispose()


async def test_stats_command_sends_several_messages(h_many):
    await h_many.send("/start", ADMIN)
    await h_many.send("/stats", ADMIN)
    parts = [m.text for m in sent_to(h_many, 1)][1:]  # без ответа на /start
    assert len(parts) >= 2 and all(len(p) <= 4096 for p in parts)
    assert parts[0].startswith("<b>Статистика</b>")
