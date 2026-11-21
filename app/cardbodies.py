"""Связка «фото-карточка -> её второе сообщение с текстом» в памяти процесса.

Если подпись карточки с фото длиннее 1024 символов, тело (описание, подсказка, советы)
уходит отдельным сообщением следом за фото. Чтобы «Закрыть» удаляла оба сообщения,
id текстового хранится по ключу (chat_id, message_id фото). После перезапуска бота
связки нет — «Закрыть» удалит только фото, текст останется: допустимо, как у спойлеров.
"""
from collections import OrderedDict

MAX_CARDS = 2000


class CardBodyState:
    def __init__(self, limit: int = MAX_CARDS):
        self._limit = limit
        self._data: OrderedDict[tuple[int, int], int] = OrderedDict()

    def put(self, key: tuple[int, int], body_message_id: int) -> None:
        self._data[key] = body_message_id
        self._data.move_to_end(key)
        while len(self._data) > self._limit:
            self._data.popitem(last=False)

    def pop(self, key: tuple[int, int]) -> int | None:
        return self._data.pop(key, None)
