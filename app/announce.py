"""Уведомления о новых пунктах: снимок уже объявленных id и текст уведомления."""
import json
import os
from pathlib import Path

from app.content import Category, Content

SNAPSHOT_PATH = Path("data/announced_items.json")


class SnapshotError(Exception):
    """Файл снимка повреждён. Текст сообщения — для админа."""


def load_snapshot(path: Path = SNAPSHOT_PATH) -> set[str] | None:
    """id пунктов, о которых уже сообщали. None — снимка ещё нет."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SnapshotError(f"Не удалось прочитать {path}: {e}") from e
    if not isinstance(data, list) or not all(isinstance(i, str) for i in data):
        raise SnapshotError(f"{path}: ожидался JSON-список id пунктов")
    return set(data)


def save_snapshot(ids, path: Path = SNAPSHOT_PATH) -> None:
    """Атомарная запись: временный файл + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(sorted(ids), f, ensure_ascii=False, indent=0)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def new_by_category(content: Content, snapshot: set[str]) -> list[tuple[Category, int]]:
    """Категории (в порядке контента) и число пунктов, которых нет в снимке."""
    result = []
    for category in content.categories:
        count = sum(1 for i in category.items if i.id not in snapshot)
        if count:
            result.append((category, count))
    return result


def format_notification(new: list[tuple[Category, int]]) -> str:
    parts = ", ".join(f"{category.title} +{count}" for category, count in new)
    return f"Новое в чек-листе: {parts}\n\nОтключить уведомления: /notify_off"
