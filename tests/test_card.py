import io
import time

import pytest_asyncio
from aiogram import Bot
from aiogram.methods import AnswerCallbackQuery, GetMe, SendMessage, SendPhoto
from aiogram.types import Chat, Message, User
from conftest import FIXTURE_CONTENT
from PIL import Image
from test_flow import FakeSession, Harness

from app import ui
from app.card import ASSETS, BAR_X0, BAR_X1, BAR_Y0, BG, SIZE, fit_text, render_card, top_categories
from app.content import ContentHolder, load_content
from app.db import Base, make_engine, make_session_factory
from app.handlers import menu as menu_module
from app.main import build_dispatcher
from app.progress import CategoryProgress as C
from app.progress import ProgressService, ProgressSummary

OTHER = {"id": 43, "is_bot": False, "first_name": "O", "username": "other"}


def summary(done=47, total=120, cats=None):
    cats = cats if cats is not None else (C("a", "Тайники", 30, 40), C("b", "Машины", 5, 40))
    return ProgressSummary(done, total, tuple(cats))


def decode(png: bytes) -> Image.Image:
    return Image.open(io.BytesIO(png))


# --- картинка ---

def test_card_is_png_square():
    png = render_card(summary())
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    img = decode(png)
    assert img.format == "PNG" and img.size == (SIZE, SIZE)


def test_card_bar_reflects_percent():
    img = decode(render_card(summary(done=60, total=120))).convert("RGB")  # 50%
    y = (BAR_Y0 + 30)
    mid = BAR_X0 + (BAR_X1 - BAR_X0) // 2
    filled, empty = img.getpixel((mid - 40, y)), img.getpixel((mid + 40, y))
    assert filled != empty and filled != BG and empty != BG


def test_card_renders_edge_cases():
    render_card(summary(0, 10, [C("a", "Пусто", 0, 10)]))
    render_card(summary(10, 10, [C("a", "Всё", 10, 10)]))
    render_card(summary(0, 0, []))  # нет категорий
    render_card(summary(cats=[C("a", "Ё" * 300, 1, 2), C("b", "<b>&amp;</b> 🚗", 1, 2)]))  # длинные и странные названия


def test_top_categories_by_percent_then_done_then_order():
    cats = [C("a", "A", 1, 10), C("b", "B", 5, 10), C("c", "C", 10, 20), C("d", "D", 3, 10), C("e", "E", 0, 5)]
    # 50% и 50%: выше, где больше отмечено
    assert [c.category_id for c in top_categories(summary(cats=cats))] == ["c", "b", "d"]
    cats = [C("a", "A", 5, 10), C("b", "B", 10, 20), C("c", "C", 1, 100)]
    # равный процент: выше, где больше отмечено
    assert [c.category_id for c in top_categories(summary(cats=cats), 2)] == ["b", "a"]


def test_fit_text_truncates_with_ellipsis():
    from PIL import ImageDraw, ImageFont
    font = ImageFont.truetype(str(ASSETS / "DejaVuSans.ttf"), 36)
    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    assert fit_text(draw, "Короткое", font, 500) == "Короткое"
    out = fit_text(draw, "Очень длинное название категории " * 5, font, 300)
    assert out.endswith("…") and draw.textlength(out, font=font) <= 300


def test_font_and_license_are_in_repo():
    assert (ASSETS / "DejaVuSans.ttf").is_file() and (ASSETS / "DejaVuSans-Bold.ttf").is_file()
    assert "Bitstream" in (ASSETS / "DejaVu-LICENSE.txt").read_text(encoding="utf-8")


def test_card_has_no_brand_words():
    # слова из раздела «Название и товарные знаки» не должны попадать в текст карточки в коде
    source = (ASSETS.parent / "card.py").read_text(encoding="utf-8").lower()
    for word in ("rockstar", "take-two", "grand theft", "vice city", "leonida"):
        assert word not in source


# --- кнопка и обработчик ---

def test_progress_screen_has_share_button():
    _, kb = ui.progress_screen(summary())
    assert "m:share" in [b.callback_data for row in kb.inline_keyboard for b in row]


class CardSession(FakeSession):
    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, GetMe):
            self.calls.append(method)
            return User(id=999, is_bot=True, first_name="Бот", username="polny_zachet_bot")
        if isinstance(method, SendPhoto):
            self.calls.append(method)
            return Message(message_id=700 + len(self.calls), date=int(time.time()),
                           chat=Chat(id=method.chat_id, type="private"))
        return await super().make_request(bot, method, timeout)


@pytest_asyncio.fixture
async def h():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = make_session_factory(engine)
    svc = ProgressService(sessions)
    session = CardSession()
    dp = build_dispatcher(ContentHolder(load_content(FIXTURE_CONTENT)), svc, "", content_dir=FIXTURE_CONTENT)
    harness = Harness(dp, Bot("123:abc", session=session), session, sessions, svc)
    yield harness
    await engine.dispose()


def photos(h, chat=42):
    return [c for c in h.session.calls if isinstance(c, SendPhoto) and c.chat_id == chat]


async def test_share_sends_png_with_caption_and_link(h):
    await h.svc.mark(42, "test_001")
    await h.press("m:share")
    sent = photos(h)
    assert len(sent) == 1
    assert sent[0].photo.data[:8] == b"\x89PNG\r\n\x1a\n"
    assert "33%" in sent[0].caption and "1/3" in sent[0].caption
    assert "https://t.me/polny_zachet_bot?start=share" in sent[0].caption


async def test_share_twice_within_30_seconds_is_refused(h, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(menu_module, "monotonic", lambda: now[0])
    await h.press("m:share")
    now[0] += 10
    await h.press("m:share")
    assert len(photos(h)) == 1
    assert h.session.last(AnswerCallbackQuery).text == "Карточка уже отправлена выше"


async def test_share_again_after_cooldown_and_per_user_limit(h, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(menu_module, "monotonic", lambda: now[0])
    await h.press("m:share")
    await h.press("m:share", OTHER)  # у другого пользователя свой лимит
    assert len(photos(h)) == 1 and len(photos(h, 43)) == 1
    now[0] += 31
    await h.press("m:share")
    assert len(photos(h)) == 2


async def test_share_failure_reports_and_does_not_block_retry(h, monkeypatch):
    def boom(_summary):
        raise RuntimeError("шрифт не найден")
    monkeypatch.setattr(menu_module, "render_card", boom)
    await h.press("m:share")
    assert not photos(h)
    assert [c for c in h.session.calls if isinstance(c, SendMessage)][-1].text.startswith("Не удалось создать")
    monkeypatch.undo()
    await h.press("m:share")  # сбой не включил паузу
    assert len(photos(h)) == 1


async def test_share_generation_runs_in_thread(h, monkeypatch):
    import threading
    seen = []
    real = menu_module.render_card

    def spy(s):
        seen.append(threading.current_thread() is not threading.main_thread())
        return real(s)
    monkeypatch.setattr(menu_module, "render_card", spy)
    await h.press("m:share")
    assert seen == [True]
