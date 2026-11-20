"""Логика прогресса: отметки, проценты, удаление данных пользователя."""
import re
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.content import Content
from app.db import Progress, User, utcnow

SOURCE_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def normalize_source(raw: str | None) -> str | None:
    """Метка из ?start=<метка>: обрезаем до 32 символов, невалидная -> None."""
    if not raw:
        return None
    raw = raw[:32]
    return raw if SOURCE_RE.fullmatch(raw) else None


def percent(done: int, total: int) -> int:
    """Целый процент, округление вниз: 100% только при полном завершении."""
    return done * 100 // total if total else 0


@dataclass(frozen=True)
class CategoryProgress:
    category_id: str
    title: str
    done: int
    total: int

    @property
    def percent(self) -> int:
        return percent(self.done, self.total)


@dataclass(frozen=True)
class ProgressSummary:
    done: int
    total: int
    categories: tuple[CategoryProgress, ...]

    @property
    def percent(self) -> int:
        return percent(self.done, self.total)


class ProgressService:
    def __init__(self, sessions: async_sessionmaker):
        self._sessions = sessions

    @property
    def sessions(self) -> async_sessionmaker:
        return self._sessions

    # --- пользователи ---

    async def touch_user(self, tg_id: int, username: str | None, source: str | None = None) -> bool:
        """Создаёт пользователя или обновляет last_seen/username.
        source записывается один раз при создании. Возвращает True, если пользователь новый."""
        now = utcnow()
        async with self._sessions() as s:
            user = await s.get(User, tg_id)
            created = user is None
            if created:
                s.add(User(tg_id=tg_id, username=username, first_seen=now, last_seen=now,
                           source=normalize_source(source)))
            else:
                user.last_seen = now
                user.username = username
            await s.commit()
            return created

    async def all_user_ids(self) -> list[int]:
        async with self._sessions() as s:
            return list(await s.scalars(select(User.tg_id).order_by(User.tg_id)))

    async def notify_user_ids(self) -> list[int]:
        """Получатели рассылок и уведомлений: только те, у кого notify = true."""
        async with self._sessions() as s:
            return list(await s.scalars(
                select(User.tg_id).where(User.notify.is_(True)).order_by(User.tg_id)))

    async def set_notify(self, tg_id: int, value: bool) -> None:
        async with self._sessions() as s:
            user = await s.get(User, tg_id)
            if user is not None:
                user.notify = value
                await s.commit()

    async def get_notify(self, tg_id: int) -> bool:
        async with self._sessions() as s:
            user = await s.get(User, tg_id)
            return True if user is None else bool(user.notify)

    async def delete_user(self, tg_id: int) -> None:
        """Удаляет запись пользователя и весь его прогресс."""
        async with self._sessions() as s:
            await s.execute(delete(Progress).where(Progress.tg_id == tg_id))
            await s.execute(delete(User).where(User.tg_id == tg_id))
            await s.commit()

    # --- отметки ---

    async def is_done(self, tg_id: int, item_id: str) -> bool:
        async with self._sessions() as s:
            return await s.get(Progress, (tg_id, item_id)) is not None

    async def done_ids(self, tg_id: int) -> set[str]:
        async with self._sessions() as s:
            rows = await s.scalars(select(Progress.item_id).where(Progress.tg_id == tg_id))
            return set(rows)

    async def mark(self, tg_id: int, item_id: str) -> None:
        async with self._sessions() as s:
            s.add(Progress(tg_id=tg_id, item_id=item_id, done_at=utcnow()))
            try:
                await s.commit()
            except IntegrityError:  # уже отмечено (в том числе параллельным запросом)
                await s.rollback()

    async def unmark(self, tg_id: int, item_id: str) -> None:
        async with self._sessions() as s:
            await s.execute(delete(Progress).where(Progress.tg_id == tg_id, Progress.item_id == item_id))
            await s.commit()

    async def toggle(self, tg_id: int, item_id: str) -> bool:
        """Переключает отметку. Возвращает новое состояние: True — отмечено."""
        if await self.is_done(tg_id, item_id):
            await self.unmark(tg_id, item_id)
            return False
        await self.mark(tg_id, item_id)
        return True

    # --- проценты ---

    async def summary(self, tg_id: int, content: Content) -> ProgressSummary:
        """Считает только пункты, которые есть в текущем контенте:
        удалённые из YAML записи в базе остаются, но в проценты не входят."""
        done = await self.done_ids(tg_id)
        cats = tuple(
            CategoryProgress(c.id, c.title, sum(1 for i in c.items if i.id in done), len(c.items))
            for c in content.categories
        )
        return ProgressSummary(sum(c.done for c in cats), sum(c.total for c in cats), cats)
