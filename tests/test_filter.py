import pytest_asyncio
from aiogram import Bot
from aiogram.methods import EditMessageText
from test_flow import FakeSession, Harness

from app import ui
from app.callbacks import CatCb, InfoCb
from app.content import Category, ContentHolder, Item, build_content
from app.db import Base, make_engine, make_session_factory
from app.main import build_dispatcher
from app.progress import ProgressService
from app.texts import SPOILER_HIDDEN


def make_cat(n=20, spoiler_ids=()):
    items = tuple(Item(f"i_{k}", f"Пункт {k}", hint=f"Подсказка {k}", spoiler=f"i_{k}" in spoiler_ids)
                  for k in range(1, n + 1))
    return Category("cat", "Категория", 1, items)


def cbs(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]


def labels(markup):
    return [b.text for row in markup.inline_keyboard for b in row]


# --- экран ---

def test_filter_button_toggles_and_resets_page():
    text, kb = ui.category_screen(make_cat(), set(), 1)
    assert "Только неотмеченные" in labels(kb) and "c:cat:0:1" in cbs(kb)
    text, kb = ui.category_screen(make_cat(), set(), 1, only_undone=True)
    assert "Показать все" in labels(kb) and "c:cat:0:0" in cbs(kb)
    assert "Фильтр: только неотмеченные" in text


def test_filter_hides_marked_items_and_renumbers():
    done = {"i_1", "i_2", "i_3"}
    text, kb = ui.category_screen(make_cat(20), done, 0, only_undone=True)
    assert "Пункт 1" not in text.replace("Пункт 10", "").replace("Пункт 11", "").replace("Пункт 12", "")
    assert "1. ⬜ Пункт 4" in text
    assert "3/20" in text  # счётчик считается по всей категории
    assert all(d.endswith(":1") for d in cbs(kb) if d.startswith("i:"))


def test_filter_paginates_by_remaining_items():
    done = {f"i_{k}" for k in range(1, 14)}  # осталось 7 из 20 — одна страница
    text, kb = ui.category_screen(make_cat(20), done, 2, only_undone=True)  # page зажимается
    assert "Страница 1/1" in text
    assert not [b for b in labels(kb) if "Вперёд" in b or "Назад" in b]
    assert len([d for d in cbs(kb) if d.startswith("i:")]) == 7


def test_filter_nav_buttons_keep_flag():
    _, kb = ui.category_screen(make_cat(30), set(), 1, only_undone=True)
    nav = [d for d in cbs(kb) if d.startswith("c:cat:") and d not in ("c:cat:0:0",)]
    assert "c:cat:0:1" in nav and "c:cat:2:1" in nav


def test_all_done_shows_message_and_button_to_remove_filter():
    cat = make_cat(3)
    text, kb = ui.category_screen(cat, {"i_1", "i_2", "i_3"}, 0, only_undone=True)
    assert "Всё собрано!" in text and "Страница" not in text
    assert "Показать все" in labels(kb) and "c:cat:0:0" in cbs(kb)
    assert not [d for d in cbs(kb) if d.startswith("i:")]


def test_without_filter_all_done_has_no_message():
    text, _ = ui.category_screen(make_cat(3), {"i_1", "i_2", "i_3"}, 0)
    assert "Всё собрано" not in text and "Фильтр" not in text


def test_filter_keeps_hidden_spoiler_hidden():
    text, _ = ui.category_screen(make_cat(3, spoiler_ids=("i_2",)), set(), 0, only_undone=True)
    assert SPOILER_HIDDEN in text and "Пункт 2" not in text


# --- callback_data ---

def test_callbacks_with_flag_fit_64_bytes_and_roundtrip():
    cid = "a" * 24
    for packed in (CatCb(cat=cid, page=999999, only=True).pack(), InfoCb(item=cid, page=999999, only=True).pack()):
        assert len(packed.encode()) <= 64
    assert CatCb.unpack(CatCb(cat="c", page=2, only=True).pack()) == CatCb(cat="c", page=2, only=True)
    assert InfoCb.unpack(InfoCb(item="i", page=1, only=False).pack()).only is False


def test_every_generated_button_with_filter_fits_64_bytes():
    cat = Category("c" * 24, "T", 1, tuple(Item(f"{'j' * 20}{k:04d}", "x") for k in range(40)))
    for only in (False, True):
        for page in range(6):
            _, kb = ui.category_screen(cat, set(), page, only_undone=only)
            assert all(len(d.encode()) <= 64 for d in cbs(kb))


# --- сценарий в боте ---

@pytest_asyncio.fixture
async def h():
    docs = [("a.yaml", {"id": "cat", "title": "Категория", "order": 10, "items": [
        {"id": f"i_{k}", "title": f"Пункт {k}", "hint": f"Подсказка {k}",
         **({"spoiler": True} if k == 10 else {})} for k in range(1, 11)]})]
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = make_session_factory(engine)
    svc = ProgressService(sessions)
    session = FakeSession()
    dp = build_dispatcher(ContentHolder(build_content(docs)), svc, "")
    harness = Harness(dp, Bot("123:abc", session=session), session, sessions, svc)
    yield harness
    await engine.dispose()


def screen(h):
    c = h.session.last(EditMessageText)
    return c.text, cbs(c.reply_markup)


async def test_marking_in_filter_mode_removes_item_and_keeps_filter(h):
    await h.press("c:cat:0:1")
    text, data = screen(h)
    assert "Фильтр: только неотмеченные" in text and "i:i_1:0:1" in data

    await h.press("i:i_1:0:1")  # из списка открывается карточка, зачёт не меняется
    assert not await h.svc.is_done(42, "i_1")
    await h.press("ct:i_1:0:1")  # отметка в карточке
    assert await h.svc.is_done(42, "i_1")
    await h.press("c:cat:0:1")  # «К списку»: фильтр сохранился
    text, data = screen(h)
    assert "Пункт 1\n" not in text and "1. ⬜ Пункт 2" in text
    assert "i:i_1:0:1" not in data and "i:i_2:0:1" in data
    assert "1/10" in text and "Фильтр" in text


async def test_page_is_clamped_when_last_item_of_page_is_marked(h):
    for k in range(1, 8):  # из 10 пунктов отмечаем 7: останется 3 — одна страница
        await h.svc.mark(42, f"i_{k}")
    await h.press("c:cat:0:1")
    assert "Страница 1/1" in screen(h)[0]
    # карточка со старым номером страницы: «К списку» не ломается, страница зажимается
    await h.press("i:i_8:1:1")
    await h.press("ct:i_8:1:1")
    assert await h.svc.is_done(42, "i_8")
    await h.press("c:cat:1:1")
    text, data = screen(h)
    assert "Страница 1/1" in text
    assert [d for d in data if d.startswith("i:")] == ["i:i_9:0:1", "i:i_10:0:1"]


async def test_marking_last_unmarked_item_shows_all_done(h):
    for k in range(1, 10):
        await h.svc.mark(42, f"i_{k}")
    await h.press("c:cat:0:1")
    await h.press("i:i_10:0:1")  # спойлер: первое нажатие раскрывает, пункт остаётся
    assert "Пункт 10" in screen(h)[0] and not await h.svc.is_done(42, "i_10")
    await h.press("i:i_10:0:1")  # второе открывает карточку
    await h.press("ct:i_10:0:1")  # отметка в карточке
    await h.press("c:cat:0:1")  # «К списку»: список опустел
    text, data = screen(h)
    assert "Всё собрано!" in text and "c:cat:0:0" in data


async def test_turning_filter_off_shows_everything_again(h):
    await h.svc.mark(42, "i_1")
    await h.press("c:cat:0:1")
    assert "i:i_1:0:1" not in screen(h)[1]
    await h.press("c:cat:0:0")
    text, data = screen(h)
    assert "Фильтр" not in text and "i:i_1:0:0" in data and "1. ✅ Пункт 1" in text


async def test_unfiltered_flow_unchanged_marked_item_stays(h):
    await h.press("c:cat:0:0")
    await h.press("i:i_1:0:0")
    await h.press("ct:i_1:0:0")
    await h.press("c:cat:0:0")
    text, data = screen(h)
    assert "1. ✅ Пункт 1" in text and "i:i_1:0:0" in data


async def test_old_format_button_is_stale_not_crash(h):
    # кнопки из сообщений, отправленных до появления фильтра, без флага
    await h.press("c:cat:0")
    assert "Общий прогресс" in screen(h)[0]
