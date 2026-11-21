"""Все callback_data бота. Лимит Telegram — 64 байта."""
from aiogram.filters.callback_data import CallbackData


class MenuCb(CallbackData, prefix="m"):
    action: str  # home | list | prog | find | share


class CatCb(CallbackData, prefix="c"):
    cat: str
    page: int = 0
    only: bool = False  # фильтр «только неотмеченные»; в базе не хранится


class InfoCb(CallbackData, prefix="i"):
    """Открыть карточку пункта (page и only нужны, чтобы вернуться на тот же экран списка).
    Для скрытого спойлера в списке то же нажатие сначала раскрывает его."""
    item: str
    page: int = 0
    only: bool = False


class CardToggleCb(CallbackData, prefix="ct"):
    """Отметить / снять отметку из карточки."""
    item: str
    page: int = 0
    only: bool = False


class CardCloseCb(CallbackData, prefix="cx"):
    """Закрыть карточку с фото (удалить сообщение)."""


class SearchCb(CallbackData, prefix="f"):
    item: str


class ReportCb(CallbackData, prefix="r"):
    item: str


class ReportCancelCb(CallbackData, prefix="rx"):
    pass


class DeleteCb(CallbackData, prefix="d"):
    confirm: bool


class BroadcastCb(CallbackData, prefix="b"):
    confirm: bool
