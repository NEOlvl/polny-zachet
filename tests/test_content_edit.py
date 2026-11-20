import shutil

import pytest
import yaml
from conftest import FIXTURE_CONTENT

from app.content import ContentError, load_content
from app.content_edit import ContentEditor, _dump, _yaml, generate_item_id

CAT = """# Комментарий над категорией: должен пережить правку
id: col
title: "Коллекционки"
order: 20
items:
  # комментарий внутри списка
  - id: col_001
    title: "Первый"
    hint: "Подсказка"
  - id: col_014
    title: "Четырнадцатый"
    spoiler: true
"""


@pytest.fixture
def cdir(tmp_path):
    d = tmp_path / "content"
    d.mkdir()
    shutil.copy(FIXTURE_CONTENT / "test_example.yaml", d / "test_example.yaml")
    (d / "col.yaml").write_text(CAT, encoding="utf-8")
    return d


def items(d, name="col.yaml"):
    return yaml.safe_load((d / name).read_text(encoding="utf-8"))["items"]


# --- генерация id ---

def test_generate_id_continues_numbering_from_max():
    assert generate_item_id("col", ["col_001", "col_014"], set()) == "col_015"


def test_generate_id_keeps_zero_padding_width():
    assert generate_item_id("c", ["c_0009"], set()) == "c_0010"


def test_generate_id_skips_ids_taken_elsewhere():
    assert generate_item_id("col", ["col_001"], {"col_002", "col_003"}) == "col_004"


def test_generate_id_prefix_from_category_when_no_numbers():
    assert generate_item_id("maya", [], set()) == "maya_001"
    assert generate_item_id("maya", ["abc"], set()) == "maya_001"


def test_generate_id_long_category_id_fits_limit():
    new = generate_item_id("a" * 24, [], set())
    assert len(new) <= 24 and new.endswith("_001")


# --- round-trip ---

def test_roundtrip_dump_is_identical():
    doc = _yaml().load(CAT)
    assert _dump(doc) == CAT


def test_roundtrip_of_repo_test_file():
    text = (FIXTURE_CONTENT / "test_example.yaml").read_text(encoding="utf-8")
    assert _dump(_yaml().load(text)).replace("\r\n", "\n") == text.replace("\r\n", "\n")


# --- /add ---

def test_add_item_appends_and_keeps_comments(cdir):
    r = ContentEditor(cdir).add_item("col", "Новый пункт", "Где искать", True)
    assert (r.item_id, r.category_title, r.count) == ("col_015", "Коллекционки", 3)
    text = (cdir / "col.yaml").read_text(encoding="utf-8")
    assert "# Комментарий над категорией" in text and "# комментарий внутри списка" in text
    last = items(cdir)[-1]
    assert last == {"id": "col_015", "title": "Новый пункт", "hint": "Где искать", "spoiler": True}
    assert load_content(cdir).total_items == 6  # 3 тестовых + 3


def test_add_item_without_hint_and_spoiler(cdir):
    ContentEditor(cdir).add_item("col", "Просто пункт")
    assert items(cdir)[-1] == {"id": "col_015", "title": "Просто пункт"}


def test_add_item_unknown_category(cdir):
    with pytest.raises(ContentError, match="не найдена"):
        ContentEditor(cdir).add_item("nope", "x")


def test_add_item_special_characters_survive(cdir):
    title = 'Пункт: "в кавычках" # не комментарий, ё'
    ContentEditor(cdir).add_item("col", title, "- подсказка: с двоеточием")
    assert items(cdir)[-1]["title"] == title
    assert items(cdir)[-1]["hint"] == "- подсказка: с двоеточием"


def test_add_item_id_unique_globally(cdir):
    # у другой категории уже занят col_015 — генератор его пропускает
    other = cdir / "other.yaml"
    other.write_text('id: other\ntitle: "O"\norder: 30\nitems:\n  - id: col_015\n    title: "X"\n',
                     encoding="utf-8")
    assert ContentEditor(cdir).add_item("col", "y").item_id == "col_016"


def test_invalid_write_leaves_file_untouched(cdir):
    before = (cdir / "col.yaml").read_text(encoding="utf-8")
    with pytest.raises(ContentError):
        ContentEditor(cdir).add_item("col", "   ")  # пустое название не проходит валидацию
    assert (cdir / "col.yaml").read_text(encoding="utf-8") == before
    assert not list(cdir.glob(".*.tmp"))


def test_too_long_generated_id_is_rejected(cdir):
    (cdir / "col.yaml").write_text(
        'id: col\ntitle: "K"\norder: 20\nitems:\n  - id: "abcdefghijklmnopqrstu_99"\n    title: "x"\n',
        encoding="utf-8")
    before = (cdir / "col.yaml").read_text(encoding="utf-8")
    with pytest.raises(ContentError, match="id"):
        ContentEditor(cdir).add_item("col", "y")  # abcdefghijklmnopqrstu_100 — 28 символов
    assert (cdir / "col.yaml").read_text(encoding="utf-8") == before


# --- /newcat ---

def test_new_category_creates_file_with_next_order(cdir):
    r = ContentEditor(cdir).new_category("stunts", "Трюки", "Первый трюк")
    assert (r.category_id, r.item_id) == ("stunts", "stunts_001")
    data = yaml.safe_load((cdir / "stunts.yaml").read_text(encoding="utf-8"))
    assert data["order"] == 30  # максимум был 20
    assert data["items"] == [{"id": "stunts_001", "title": "Первый трюк"}]
    assert load_content(cdir).category("stunts") is not None


@pytest.mark.parametrize("bad", ["../evil", "Bad Id", "a/b", "x" * 25, ""])
def test_new_category_rejects_bad_id_and_writes_nothing(cdir, bad):
    before = sorted(p.name for p in cdir.parent.rglob("*"))
    with pytest.raises(ContentError):
        ContentEditor(cdir).new_category(bad, "T", "I")
    assert sorted(p.name for p in cdir.parent.rglob("*")) == before


def test_new_category_duplicate_id_or_file(cdir):
    with pytest.raises(ContentError, match="уже существует"):
        ContentEditor(cdir).new_category("col", "T", "I")
    with pytest.raises(ContentError):  # id категории совпадает с id чужого пункта
        ContentEditor(cdir).new_category("col_001", "T", "I")
    assert not (cdir / "col_001.yaml").exists()


# --- /edit ---

def test_edit_title_and_hint(cdir):
    ContentEditor(cdir).edit_item("col_001", "Переименован", "Новая подсказка")
    assert items(cdir)[0] == {"id": "col_001", "title": "Переименован", "hint": "Новая подсказка"}


def test_edit_adds_hint_after_title_and_keeps_order(cdir):
    ContentEditor(cdir).edit_item("col_014", "Новое имя", "Появилась подсказка")
    text = (cdir / "col.yaml").read_text(encoding="utf-8")
    block = text[text.index("col_014"):]
    assert block.index("title") < block.index("hint") < block.index("spoiler")
    assert items(cdir)[1]["spoiler"] is True


def test_edit_clear_hint(cdir):
    ContentEditor(cdir).edit_item("col_001", "Первый", clear_hint=True)
    assert items(cdir)[0] == {"id": "col_001", "title": "Первый"}


def test_edit_without_hint_keeps_existing_one(cdir):
    ContentEditor(cdir).edit_item("col_001", "Другое имя")
    assert items(cdir)[0]["hint"] == "Подсказка"


def test_edit_unknown_item(cdir):
    with pytest.raises(ContentError, match="не найден"):
        ContentEditor(cdir).edit_item("nope", "x")


# --- /hide ---

def test_hide_removes_item(cdir):
    r = ContentEditor(cdir).hide_item("col_001")
    assert (r.title, r.count) == ("Первый", 1)
    assert [i["id"] for i in items(cdir)] == ["col_014"]


def test_hide_last_item_refused(cdir):
    ContentEditor(cdir).hide_item("col_001")
    before = (cdir / "col.yaml").read_text(encoding="utf-8")
    with pytest.raises(ContentError, match="последний пункт"):
        ContentEditor(cdir).hide_item("col_014")
    assert (cdir / "col.yaml").read_text(encoding="utf-8") == before


def test_numbering_continues_after_hide_of_last(cdir):
    # col_014 скрыт — новый пункт всё равно получает следующий номер от оставшихся (col_001 -> col_002)
    ContentEditor(cdir).hide_item("col_014")
    assert ContentEditor(cdir).add_item("col", "z").item_id == "col_002"


def test_existing_invalid_file_blocks_write(cdir):
    (cdir / "bad.yaml").write_text("id: bad\ntitle: x\n", encoding="utf-8")  # нет order и items
    before = (cdir / "col.yaml").read_text(encoding="utf-8")
    with pytest.raises(ContentError, match="bad.yaml"):
        ContentEditor(cdir).add_item("col", "x")
    assert (cdir / "col.yaml").read_text(encoding="utf-8") == before
