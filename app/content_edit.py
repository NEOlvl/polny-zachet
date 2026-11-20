"""Правка YAML-контента из админ-команд: добавить/изменить/скрыть пункт, создать категорию.

Файлы читаются и пишутся через ruamel.yaml в режиме round-trip (комментарии и порядок полей
сохраняются). Перед записью весь контент проверяется в памяти тем же валидатором, что и при старте;
при ошибке файл не меняется. Запись атомарная: временный файл + os.replace.
Блокировку между командами (asyncio.Lock) держит вызывающий код.
"""
import io
import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError as RuamelError
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

from app.content import ID_RE, ContentError, build_content, content_files

_NUM_TAIL = re.compile(r"^(.*?)(\d+)$")


@dataclass(frozen=True)
class ItemAdded:
    item_id: str
    category_title: str
    count: int  # пунктов в категории после добавления


@dataclass(frozen=True)
class CategoryCreated:
    category_id: str
    title: str
    item_id: str


@dataclass(frozen=True)
class ItemEdited:
    item_id: str
    category_title: str


@dataclass(frozen=True)
class ItemHidden:
    item_id: str
    title: str
    category_title: str
    count: int  # пунктов в категории после удаления


def generate_item_id(category_id: str, existing_ids: list[str], taken: set[str]) -> str:
    """Следующий id пункта: префикс и сквозная нумерация берутся из уже существующих id категории
    (col_014 -> col_015). Нет подходящих — префикс от id категории. Результат не входит в `taken`."""
    prefix: str | None = None
    for item_id in existing_ids:
        m = _NUM_TAIL.match(item_id)
        if m:
            prefix = m.group(1)  # берём префикс последнего пункта с числом на конце
    if prefix is None:
        prefix = (category_id[:20].rstrip("_") or "item") + "_"

    numbers: list[int] = []
    widths: list[int] = []
    for item_id in existing_ids:
        m = _NUM_TAIL.match(item_id)
        if m and m.group(1) == prefix:
            numbers.append(int(m.group(2)))
            widths.append(len(m.group(2)))
    width = max(widths, default=3)
    n = max(numbers, default=0) + 1
    candidate = f"{prefix}{n:0{width}d}"
    while candidate in taken:
        n += 1
        candidate = f"{prefix}{n:0{width}d}"
    return candidate


def _yaml() -> YAML:
    y = YAML()  # round-trip
    y.preserve_quotes = True
    y.allow_unicode = True
    y.width = 4096  # длинные подсказки не переносим на новые строки
    y.indent(mapping=2, sequence=4, offset=2)  # как в content/*.yaml: «  - id: ...»
    return y


def _dump(doc) -> str:
    buf = io.StringIO()
    _yaml().dump(doc, buf)
    return buf.getvalue()


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp")  # не *.yaml: загрузчик такие файлы не видит
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _dq(value: str) -> DoubleQuotedScalarString:
    return DoubleQuotedScalarString(value)


class ContentEditor:
    def __init__(self, directory: str | Path = "content"):
        self.directory = Path(directory)

    # --- чтение ---

    def _scan(self) -> list[tuple[Path, str, object]]:
        scanned = []
        for path in content_files(self.directory):
            text = path.read_text(encoding="utf-8")
            try:
                data = yaml.safe_load(text)
            except yaml.YAMLError as e:
                raise ContentError(f"{path.name}: некорректный YAML: {e}") from e
            scanned.append((path, text, data))
        return scanned

    @staticmethod
    def _taken_ids(scanned) -> set[str]:
        taken: set[str] = set()
        for _, _, data in scanned:
            if isinstance(data, dict):
                taken.add(str(data.get("id")))
                for item in data.get("items") or []:
                    if isinstance(item, dict):
                        taken.add(str(item.get("id")))
        return taken

    @staticmethod
    def _find_category(scanned, category_id: str):
        for path, text, data in scanned:
            if isinstance(data, dict) and data.get("id") == category_id:
                return path, text, data
        raise ContentError(f"Категория «{category_id}» не найдена (список: /cat)")

    @staticmethod
    def _find_item(scanned, item_id: str):
        for path, text, data in scanned:
            if isinstance(data, dict):
                for item in data.get("items") or []:
                    if isinstance(item, dict) and item.get("id") == item_id:
                        return path, text, data
        raise ContentError(f"Пункт «{item_id}» не найден (список: /cat <категория>)")

    @staticmethod
    def _load_rt(path: Path, text: str) -> CommentedMap:
        try:
            doc = _yaml().load(text)
        except RuamelError as e:
            raise ContentError(f"{path.name}: некорректный YAML: {e}") from e
        if not isinstance(doc, CommentedMap) or not isinstance(doc.get("items"), CommentedSeq):
            raise ContentError(f"{path.name}: неожиданная структура файла, правка отменена")
        return doc

    # --- запись ---

    def _commit(self, scanned, path: Path, doc) -> None:
        """Проверить контент целиком с новым вариантом файла и только потом записать его."""
        new_text = _dump(doc)
        new_data = yaml.safe_load(new_text)
        documents = [(p.name, new_data if p == path else data) for p, _, data in scanned]
        if all(p != path for p, _, _ in scanned):
            documents.append((path.name, new_data))
        build_content(documents)  # ContentError при любой проблеме; файл при этом не тронут
        _atomic_write(path, new_text)

    # --- операции ---

    def add_item(self, category_id: str, title: str, hint: str | None = None,
                 spoiler: bool = False) -> ItemAdded:
        scanned = self._scan()
        path, text, data = self._find_category(scanned, category_id)
        doc = self._load_rt(path, text)
        existing = [str(i.get("id")) for i in data["items"] if isinstance(i, dict)]
        item_id = generate_item_id(category_id, existing, self._taken_ids(scanned))

        item = CommentedMap()
        item["id"] = item_id
        item["title"] = _dq(title)
        if hint:
            item["hint"] = _dq(hint)
        if spoiler:
            item["spoiler"] = True
        doc["items"].append(item)
        self._commit(scanned, path, doc)
        return ItemAdded(item_id, str(data["title"]), len(doc["items"]))

    def new_category(self, category_id: str, title: str, first_item_title: str) -> CategoryCreated:
        if not ID_RE.fullmatch(category_id):
            raise ContentError(
                f"id категории «{category_id}» недопустим (только a-z, 0-9, _, длина до 24 символов)")
        scanned = self._scan()
        path = self.directory / f"{category_id}.yaml"
        if path.exists() or path.with_suffix(".yml").exists():
            raise ContentError(f"Файл {path.name} уже существует")
        orders = [d["order"] for _, _, d in scanned
                  if isinstance(d, dict) and isinstance(d.get("order"), int) and not isinstance(d["order"], bool)]
        item_id = generate_item_id(category_id, [], self._taken_ids(scanned) | {category_id})

        item = CommentedMap()
        item["id"] = item_id
        item["title"] = _dq(first_item_title)
        doc = CommentedMap()
        doc["id"] = category_id
        doc["title"] = _dq(title)
        doc["order"] = max(orders, default=0) + 10
        doc["items"] = CommentedSeq([item])
        self._commit(scanned, path, doc)
        return CategoryCreated(category_id, title, item_id)

    def edit_item(self, item_id: str, title: str, hint: str | None = None,
                  clear_hint: bool = False) -> ItemEdited:
        """Изменить название; hint=None и clear_hint=False — подсказка остаётся прежней."""
        scanned = self._scan()
        path, text, data = self._find_item(scanned, item_id)
        doc = self._load_rt(path, text)
        item = next(i for i in doc["items"] if i.get("id") == item_id)
        item["title"] = _dq(title)
        if clear_hint:
            item.pop("hint", None)
        elif hint:
            if "hint" in item:
                item["hint"] = _dq(hint)
            else:
                item.insert(list(item).index("title") + 1, "hint", _dq(hint))
        self._commit(scanned, path, doc)
        return ItemEdited(item_id, str(data["title"]))

    def hide_item(self, item_id: str) -> ItemHidden:
        scanned = self._scan()
        path, text, data = self._find_item(scanned, item_id)
        doc = self._load_rt(path, text)
        if len(doc["items"]) == 1:
            raise ContentError(
                f"«{item_id}» — последний пункт категории «{data['title']}»: категория без пунктов "
                "не проходит проверку. Сначала добавь другой пункт (/add)")
        idx = next(n for n, i in enumerate(doc["items"]) if i.get("id") == item_id)
        title = str(doc["items"][idx].get("title"))
        del doc["items"][idx]
        self._commit(scanned, path, doc)
        return ItemHidden(item_id, title, str(data["title"]), len(doc["items"]))
