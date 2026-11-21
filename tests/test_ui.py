from app import ui
from app.callbacks import CatCb, DeleteCb, InfoCb, MenuCb, ReportCb, SearchCb
from app.content import Category, Item
from app.progress import CategoryProgress, ProgressSummary
from app.spoilers import SpoilerState
from app.texts import SPOILER_HIDDEN


def all_callbacks(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]


def make_cat(n=20, spoiler_ids=(), hints=True):
    items = tuple(
        Item(f"i_{k}", f"Пункт {k}", hint=f"Подсказка {k}" if hints else None, spoiler=f"i_{k}" in spoiler_ids)
        for k in range(1, n + 1)
    )
    return Category("cat", "Категория <b>", 1, items)


# --- callback_data ---

def test_longest_callback_data_fits_64_bytes():
    cid = "a" * 24
    longest = [
        CatCb(cat=cid, page=999999, only=True).pack(),
        InfoCb(item=cid, page=999999, only=True).pack(),
        MenuCb(action="progress_long").pack(),
        DeleteCb(confirm=True).pack(),
        SearchCb(item=cid).pack(),
    ]
    assert all(len(s.encode()) <= 64 for s in longest), [len(s) for s in longest]


def test_callbacks_roundtrip():
    assert InfoCb.unpack(InfoCb(item="col_001", page=2).pack()) == InfoCb(item="col_001", page=2)
    assert CatCb.unpack(CatCb(cat="c1", page=0).pack()) == CatCb(cat="c1", page=0)
    assert DeleteCb.unpack(DeleteCb(confirm=True).pack()).confirm is True
    assert DeleteCb.unpack(DeleteCb(confirm=False).pack()).confirm is False


def test_every_generated_button_fits_64_bytes():
    items = tuple(Item("i" * 24, "x") if k == 0 else Item(f"{'j' * 20}{k:04d}", "x") for k in range(40))
    cat = Category("c" * 24, "Длинное имя", 1, items)
    for page in range(5):
        _, kb = ui.category_screen(cat, set(), page)
        assert all(len(d.encode()) <= 64 for d in all_callbacks(kb))
    summary = ProgressSummary(0, 40, (CategoryProgress("c" * 24, "t", 0, 40),))
    _, kb = ui.list_screen(summary)
    assert all(len(d.encode()) <= 64 for d in all_callbacks(kb))


# --- категория ---

def test_pagination_8_per_page():
    cat = make_cat(20)
    assert ui.page_count(20) == 3 and ui.page_count(8) == 1 and ui.page_count(9) == 2
    text, kb = ui.category_screen(cat, set(), 0)
    item_buttons = [d for d in all_callbacks(kb) if d.startswith("i:")]
    assert len(item_buttons) == 8
    _, kb_last = ui.category_screen(cat, set(), 2)
    assert len([d for d in all_callbacks(kb_last) if d.startswith("i:")]) == 4


def test_nav_buttons():
    cat = make_cat(20)
    def nav(page):
        _, kb = ui.category_screen(cat, set(), page)
        return [b.text for row in kb.inline_keyboard for b in row if b.text in ("« Назад", "Вперёд »")]
    assert nav(0) == ["Вперёд »"]
    assert nav(1) == ["« Назад", "Вперёд »"]
    assert nav(2) == ["« Назад"]
    assert nav(99) == ["« Назад"]  # страница за пределами прижимается к последней


def test_no_nav_for_single_page():
    _, kb = ui.category_screen(make_cat(3), set(), 0)
    assert [b.text for row in kb.inline_keyboard for b in row if "Назад" in b.text or "Вперёд" in b.text] == []


def test_marks_and_counter():
    text, kb = ui.category_screen(make_cat(3), {"i_1"}, 0)
    assert "1. ✅ Пункт 1" in text and "2. ⬜ Пункт 2" in text
    assert "1/3" in text
    assert kb.inline_keyboard[0][0].text == "✅ 1"


def test_html_is_escaped():
    text, _ = ui.category_screen(make_cat(1), set(), 0)
    assert "&lt;b&gt;" in text and "<b>Категория <b>" not in text


def test_one_button_per_item_opens_card_with_status_label():
    cat = make_cat(10)
    _, kb = ui.category_screen(cat, {"i_2"}, 0, only_undone=False)
    buttons = [b for row in kb.inline_keyboard for b in row if b.callback_data and b.callback_data.startswith("i:")]
    assert [b.text for b in buttons] == ["⬜ 1", "✅ 2", "⬜ 3", "⬜ 4", "⬜ 5", "⬜ 6", "⬜ 7", "⬜ 8"]
    assert [b.callback_data for b in buttons][:2] == ["i:i_1:0:0", "i:i_2:0:0"]
    data = all_callbacks(kb)
    assert not [d for d in data if d.startswith(("t:", "ct:", "r:"))]  # ни отметки, ни «ошибки» в списке
    assert not [d for d in data if d.startswith("i:")][8:]


def test_list_has_no_hint_and_no_report_button():
    text, kb = ui.category_screen(make_cat(3), set(), 0)
    assert "💡" not in text and "Подсказка" not in text
    assert not [b for row in kb.inline_keyboard for b in row if b.text.startswith("⚠️")]
    assert ReportCb(item="i_1").pack() not in all_callbacks(kb)


def test_spoiler_hidden_then_opened():
    cat = make_cat(3, spoiler_ids={"i_2"})
    text, kb = ui.category_screen(cat, set(), 0)
    assert f"2. ⬜ {SPOILER_HIDDEN}" in text
    assert "Пункт 2" not in text and "Подсказка 2" not in text  # ни название, ни hint не спойлерятся
    assert "i:i_2:0:0" in all_callbacks(kb)  # кнопка та же: скрытый спойлер сначала раскрывается
    text, _ = ui.category_screen(cat, set(), 0, opened={"i_2"})
    assert "2. ⬜ Пункт 2" in text


# --- прочие экраны ---

def make_summary():
    return ProgressSummary(3, 10, (CategoryProgress("a", "Альфа", 3, 4), CategoryProgress("b", "Бета", 0, 6)))


def test_progress_screen():
    text, _ = ui.progress_screen(make_summary())
    assert "30%" in text and "███░░░░░░░" in text
    assert "Альфа: 3/4 (75%)" in text and "Бета: 0/6 (0%)" in text


def test_bar_edges():
    assert ui.bar(0) == "░" * 10 and ui.bar(100) == "█" * 10


def test_list_screen_counters():
    _, kb = ui.list_screen(make_summary())
    assert kb.inline_keyboard[0][0].text == "Альфа — 3/4"


def test_home_channel_button_only_when_url_set():
    _, kb = ui.home_screen(make_summary(), "")
    assert all(b.url is None for row in kb.inline_keyboard for b in row)
    text, kb = ui.home_screen(make_summary(), "https://t.me/example")
    assert any(b.url == "https://t.me/example" and b.text == "Канал" for row in kb.inline_keyboard for b in row)
    assert "30%" in text and "Неофициальный фан-проект" in text


# --- спойлеры в памяти ---

def test_spoiler_state():
    s = SpoilerState(limit=2)
    s.open((1, 1), "a")
    assert s.is_open((1, 1), "a") and not s.is_open((1, 2), "a")
    s.clear((1, 1))
    assert s.opened((1, 1)) == set()
    for key in ((1, 1), (1, 2), (1, 3)):
        s.open(key, "a")
    assert s.opened((1, 1)) == set()  # вытеснено самое старое


def test_done_spoiler_shows_title_without_opening():
    cat = make_cat(3, spoiler_ids={"i_2"})
    text, _ = ui.category_screen(cat, {"i_2"}, 0)
    assert "2. ✅ Пункт 2" in text and SPOILER_HIDDEN not in text
    text, _ = ui.category_screen(cat, set(), 0)  # неотмеченный — по-прежнему скрыт
    assert f"2. ⬜ {SPOILER_HIDDEN}" in text
