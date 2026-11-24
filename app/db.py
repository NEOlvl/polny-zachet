"""Подключение к SQLite и модели."""
from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, DateTime, String, event, true
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

DEFAULT_DB_URL = "sqlite+aiosqlite:///data/bot.db"


def utcnow() -> datetime:
    """Текущее UTC-время без tzinfo (SQLite всё равно не хранит пояс)."""
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(String(64))
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    source: Mapped[str | None] = mapped_column(String(32))
    # уведомления о новых пунктах и рассылки; /notify_off и /notify_on
    notify: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())


class Progress(Base):
    """Одна строка = один отмеченный пункт. Уникальная пара (tg_id, item_id) — это составной PK."""

    __tablename__ = "progress"

    tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    item_id: Mapped[str] = mapped_column(String(24), primary_key=True)
    done_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


def make_engine(url: str = DEFAULT_DB_URL) -> AsyncEngine:
    engine = create_async_engine(url)
    if url.startswith("sqlite"):
        # WAL и ожидание блокировки: с Mini App в базу пишут и бот, и веб-обработчики
        @event.listens_for(engine.sync_engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()
    return engine


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)
