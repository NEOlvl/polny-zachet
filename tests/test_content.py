from pathlib import Path

import pytest
import yaml
from conftest import FIXTURE_CONTENT, PROJECT_CONTENT

from app.content import ContentError, load_content


def cat(id="cat_a", order=1, items=None, **extra):
    d = {"id": id, "title": "Категория", "order": order,
         "items": items if items is not None else [{"id": "i_1", "title": "Пункт"}]}
    d.update(extra)
    return d


def write(tmp_path: Path, name: str, data) -> None:
    text = data if isinstance(data, str) else yaml.safe_dump(data, allow_unicode=True)
    (tmp_path / name).write_text(text, encoding="utf-8")


def test_repo_content_is_valid():
    """Реальная папка content/ проходит валидацию (защита от сломанного контента перед выкладкой).
    Названия, id и число пунктов не проверяются: они зависят от владельца."""
    content = load_content(PROJECT_CONTENT)
    assert content.categories and content.total_items >= 1


def test_fixture_content_is_valid():
    content = load_content(FIXTURE_CONTENT)
    assert list(content.items_by_id) == ["test_001", "test_002", "test_003"]


def test_loads_and_sorts_by_order(tmp_path):
    write(tmp_path, "a.yaml", cat("cat_a", order=20, items=[{"id": "a_1", "title": "A"}]))
    write(tmp_path, "b.yaml", cat("cat_b", order=10, items=[{"id": "b_1", "title": "B"}]))
    content = load_content(tmp_path)
    assert [c.id for c in content.categories] == ["cat_b", "cat_a"]
    assert content.category_of_item["a_1"] == "cat_a"
    assert content.total_items == 2


def test_optional_fields_defaults(tmp_path):
    write(tmp_path, "a.yaml", cat())
    item = load_content(tmp_path).items_by_id["i_1"]
    assert item.hint is None and item.spoiler is False


def test_duplicate_item_id_across_files(tmp_path):
    write(tmp_path, "a.yaml", cat("cat_a", items=[{"id": "dup", "title": "A"}]))
    write(tmp_path, "b.yaml", cat("cat_b", items=[{"id": "dup", "title": "B"}]))
    with pytest.raises(ContentError, match="b.yaml.*dup.*уже используется"):
        load_content(tmp_path)


def test_duplicate_item_id_in_one_file(tmp_path):
    write(tmp_path, "a.yaml", cat(items=[{"id": "x", "title": "A"}, {"id": "x", "title": "B"}]))
    with pytest.raises(ContentError, match="a.yaml.*x"):
        load_content(tmp_path)


def test_duplicate_category_id(tmp_path):
    write(tmp_path, "a.yaml", cat("same", items=[{"id": "a_1", "title": "A"}]))
    write(tmp_path, "b.yaml", cat("same", items=[{"id": "b_1", "title": "B"}]))
    with pytest.raises(ContentError, match="same"):
        load_content(tmp_path)


def test_item_id_clashes_with_category_id(tmp_path):
    write(tmp_path, "a.yaml", cat("same", items=[{"id": "same", "title": "A"}]))
    with pytest.raises(ContentError, match="same"):
        load_content(tmp_path)


@pytest.mark.parametrize("bad", ["Upper", "with-dash", "пробел", "has space", "a" * 25, ""])
def test_invalid_id(tmp_path, bad):
    write(tmp_path, "a.yaml", cat(items=[{"id": bad, "title": "A"}]))
    with pytest.raises(ContentError):
        load_content(tmp_path)


def test_id_exactly_24_chars_ok(tmp_path):
    write(tmp_path, "a.yaml", cat(items=[{"id": "a" * 24, "title": "A"}]))
    load_content(tmp_path)


@pytest.mark.parametrize("missing", ["id", "title", "order", "items"])
def test_category_required_fields(tmp_path, missing):
    data = cat()
    del data[missing]
    write(tmp_path, "a.yaml", data)
    with pytest.raises(ContentError, match="a.yaml"):
        load_content(tmp_path)


@pytest.mark.parametrize("missing", ["id", "title"])
def test_item_required_fields(tmp_path, missing):
    item = {"id": "i_1", "title": "A"}
    del item[missing]
    write(tmp_path, "a.yaml", cat(items=[item]))
    with pytest.raises(ContentError, match="a.yaml"):
        load_content(tmp_path)


def test_spoiler_must_be_bool(tmp_path):
    write(tmp_path, "a.yaml", cat(items=[{"id": "i_1", "title": "A", "spoiler": "yes please"}]))
    with pytest.raises(ContentError, match="spoiler"):
        load_content(tmp_path)


def test_order_must_be_int(tmp_path):
    write(tmp_path, "a.yaml", cat(order="first"))
    with pytest.raises(ContentError, match="order"):
        load_content(tmp_path)


def test_broken_yaml(tmp_path):
    write(tmp_path, "a.yaml", "id: [unclosed\n")
    with pytest.raises(ContentError, match="a.yaml"):
        load_content(tmp_path)


def test_empty_directory(tmp_path):
    with pytest.raises(ContentError):
        load_content(tmp_path)


def test_missing_directory(tmp_path):
    with pytest.raises(ContentError):
        load_content(tmp_path / "nope")
