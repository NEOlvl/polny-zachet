"""Раскрытые спойлеры: только в памяти процесса, по ключу (chat_id, message_id).
После перезапуска бота всё скрыто снова — так задумано (см. ТЗ)."""
from collections import OrderedDict

MAX_MESSAGES = 2000


class SpoilerState:
    def __init__(self, limit: int = MAX_MESSAGES):
        self._limit = limit
        self._data: OrderedDict[tuple[int, int], set[str]] = OrderedDict()

    def opened(self, key: tuple[int, int]) -> set[str]:
        return set(self._data.get(key, ()))

    def is_open(self, key: tuple[int, int], item_id: str) -> bool:
        return item_id in self._data.get(key, ())

    def open(self, key: tuple[int, int], item_id: str) -> None:
        self._data.setdefault(key, set()).add(item_id)
        self._data.move_to_end(key)
        while len(self._data) > self._limit:
            self._data.popitem(last=False)

    def clear(self, key: tuple[int, int]) -> None:
        self._data.pop(key, None)
