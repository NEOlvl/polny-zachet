"""Загрузка и валидация контента игры из YAML-файлов в content/."""
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ID_RE = re.compile(r"^[a-z0-9_]{1,24}$")
MAX_DESCRIPTION = 1500
MAX_TIPS = 500


class ContentError(Exception):
    """Контент не прошёл валидацию. Текст сообщения — для человека."""


@dataclass(frozen=True)
class Item:
    id: str
    title: str
    hint: str | None = None
    spoiler: bool = False
    description: str | None = None
    tips: str | None = None
    photo: str | None = None

    @property
    def has_details(self) -> bool:
        return bool(self.description or self.tips or self.photo)


@dataclass(frozen=True)
class Category:
    id: str
    title: str
    order: int
    items: tuple[Item, ...] = ()


@dataclass(frozen=True)
class Content:
    categories: tuple[Category, ...] = ()
    items_by_id: dict[str, Item] = field(default_factory=dict)
    category_of_item: dict[str, str] = field(default_factory=dict)

    @property
    def total_items(self) -> int:
        return len(self.items_by_id)

    def category(self, category_id: str) -> Category | None:
        return next((c for c in self.categories if c.id == category_id), None)


def _check_id(value, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContentError(f"{where}: поле id обязательно и должно быть строкой")
    if not ID_RE.fullmatch(value):
        raise ContentError(
            f"{where}: id «{value}» недопустим (только a-z, 0-9, _, длина до 24 символов)"
        )
    return value


def _check_text(data: dict, key: str, where: str, required: bool = True, max_len: int | None = None) -> str | None:
    value = data.get(key)
    if value is None:
        if required:
            raise ContentError(f"{where}: обязательное поле {key} отсутствует")
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContentError(f"{where}: поле {key} должно быть непустой строкой")
    if max_len is not None and len(value.strip()) > max_len:
        raise ContentError(f"{where}: поле {key} длиннее {max_len} символов")
    return value.strip()


def _parse_data(fname: str, data, seen_ids: dict[str, str]) -> tuple[Category, dict[str, str]]:
    """Проверить уже прочитанное содержимое файла категории. fname нужен только для сообщений."""
    if not isinstance(data, dict):
        raise ContentError(f"{fname}: ожидался словарь с полями id, title, order, items")

    cat_id = _check_id(data.get("id"), f"{fname}, категория")
    if cat_id in seen_ids:
        raise ContentError(f"{fname}, категория: id «{cat_id}» уже используется ({seen_ids[cat_id]})")
    seen_ids[cat_id] = f"{fname}, категория"

    title = _check_text(data, "title", f"{fname}, категория «{cat_id}»")
    order = data.get("order")
    if isinstance(order, bool) or not isinstance(order, int):
        raise ContentError(f"{fname}, категория «{cat_id}»: поле order обязательно и должно быть целым числом")

    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ContentError(f"{fname}, категория «{cat_id}»: items должен быть непустым списком")

    items: list[Item] = []
    for idx, raw in enumerate(raw_items, start=1):
        where = f"{fname}, пункт №{idx}"
        if not isinstance(raw, dict):
            raise ContentError(f"{where}: ожидался словарь с полями id, title")
        item_id = _check_id(raw.get("id"), where)
        where = f"{fname}, пункт «{item_id}»"
        if item_id in seen_ids:
            raise ContentError(f"{where}: id уже используется ({seen_ids[item_id]})")
        seen_ids[item_id] = where
        item_title = _check_text(raw, "title", where)
        hint = _check_text(raw, "hint", where, required=False)
        spoiler = raw.get("spoiler", False)
        if not isinstance(spoiler, bool):
            raise ContentError(f"{where}: spoiler должен быть true или false")
        description = _check_text(raw, "description", where, required=False, max_len=MAX_DESCRIPTION)
        tips = _check_text(raw, "tips", where, required=False, max_len=MAX_TIPS)
        photo = _check_text(raw, "photo", where, required=False)
        items.append(Item(id=item_id, title=item_title, hint=hint, spoiler=spoiler,
                          description=description, tips=tips, photo=photo))

    return Category(id=cat_id, title=title, order=order, items=tuple(items)), seen_ids


def build_content(documents: list[tuple[str, object]]) -> Content:
    """Провалидировать набор документов (имя файла, данные) целиком и собрать Content."""
    seen_ids: dict[str, str] = {}
    categories: list[Category] = []
    for fname, data in documents:
        category, seen_ids = _parse_data(fname, data, seen_ids)
        categories.append(category)

    categories.sort(key=lambda c: (c.order, c.id))
    items_by_id = {i.id: i for c in categories for i in c.items}
    category_of_item = {i.id: c.id for c in categories for i in c.items}
    return Content(tuple(categories), items_by_id, category_of_item)


def content_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        raise ContentError(f"Папка с контентом не найдена: {directory}")
    files = sorted([*directory.glob("*.yaml"), *directory.glob("*.yml")])
    if not files:
        raise ContentError(f"В папке {directory} нет ни одного файла категории (*.yaml)")
    return files


def load_content(directory: str | Path = "content") -> Content:
    documents = []
    for path in content_files(Path(directory)):
        try:
            documents.append((path.name, yaml.safe_load(path.read_text(encoding="utf-8"))))
        except yaml.YAMLError as e:
            raise ContentError(f"{path.name}: некорректный YAML: {e}") from e
    return build_content(documents)


class ContentHolder:
    """Текущий контент. Держим в объекте, чтобы /reload мог подменить его на лету."""

    def __init__(self, content: Content):
        self.content = content
