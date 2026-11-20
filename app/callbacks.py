"""Все callback_data бота. Лимит Telegram — 64 байта."""
from aiogram.filters.callback_data import CallbackData


class MenuCb(CallbackData, prefix="m"):
    action: str  # home | list | prog | find | share


class CatCb(CallbackData, prefix="c"):
    cat: str
    page: int = 0
    only: bool = False  # фильтр «только неотмеченные»; в базе не хранится


class ItemCb(CallbackData, prefix="t"):
    item: str
    page: int = 0
    only: bool = False


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
