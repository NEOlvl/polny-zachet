"""Поиск по пунктам: нормализация запроса, выдача, состояние выдачи в памяти процесса."""
from collections import OrderedDict
from dataclasses import dataclass

from app.content import Category, Content, Item

MAX_RESULTS = 8
MAX_QUERY_LEN = 64
MAX_SEARCHES = 2000


def normalize(text: str) -> str:
    """Без учёта регистра, «ё» = «е»."""
    return text.casefold().replace("ё", "е")


def clean_query(raw: str | None) -> str:
    return " ".join((raw or "").split())[:MAX_QUERY_LEN]


def find_items(content: Content, done: set[str], query: str) -> list[tuple[Item, Category]]:
    """Все подходящие пункты в порядке контента. Слова запроса должны все встретиться в названии
    или подсказке. Неотмеченные спойлеры не ищутся вовсе: иначе поиск раскрыл бы название."""
    tokens = normalize(query).split()
    if not tokens:
        return []
    found = []
    for category in content.categories:
        for item in category.items:
            if item.spoiler and item.id not in done:
                continue
            haystack = normalize(f"{item.title} {item.hint or ''}")
            if all(t in haystack for t in tokens):
                found.append((item, category))
    return found


@dataclass(frozen=True)
class SearchResult:
    query: str
    item_ids: tuple[str, ...]  # показанные пункты (до MAX_RESULTS)
    total: int  # сколько нашлось всего


class SearchState:
    """Выдача поиска по ключу (chat_id, message_id): кнопки под ней переключают отметки,
    а выдача остаётся прежней. После перезапуска бота старые кнопки считаются устаревшими."""

    def __init__(self, limit: int = MAX_SEARCHES):
        self._limit = limit
        self._data: OrderedDict[tuple[int, int], SearchResult] = OrderedDict()

    def get(self, key: tuple[int, int]) -> SearchResult | None:
        return self._data.get(key)

    def put(self, key: tuple[int, int], result: SearchResult) -> None:
        self._data[key] = result
        self._data.move_to_end(key)
        while len(self._data) > self._limit:
            self._data.popitem(last=False)
