"""Статистика для админа (/stats)."""
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import timedelta
from html import escape

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.content import Content
from app.db import Progress, User, utcnow
from app.textutil import pack_blocks

NO_SOURCE = "без метки"
IN_CHUNK = 500  # сколько id пунктов подставляем в один запрос IN (...)


@dataclass(frozen=True)
class SourceStats:
    source: str
    users: int
    active_7d: int
    avg_marks: float


@dataclass(frozen=True)
class Stats:
    total_users: int
    new_24h: int
    active_24h: int
    active_7d: int
    sources: tuple[tuple[str, int], ...]
    avg_marks: float
    top_categories: tuple[tuple[str, int], ...]  # (название категории, число отметок)
    # расширенная статистика
    activated: int = 0  # пользователей хотя бы с 1 отметкой
    returned_1d: tuple[int, int] = (0, 0)  # (вернулись позже чем через 1 сутки, из тех, кто пришёл ≥ 1 суток назад)
    returned_7d: tuple[int, int] = (0, 0)
    source_stats: tuple[SourceStats, ...] = ()
    notify_off: int = 0
    category_fill: tuple[tuple[str, int], ...] = ()  # (название, пользователей с ≥ 1 отметкой) в порядке контента


async def _marks_by_user(session, item_ids: list[str]) -> dict[int, int]:
    """Сколько из заданных пунктов отметил каждый пользователь (только те, у кого есть отметки)."""
    result: dict[int, int] = defaultdict(int)
    for i in range(0, len(item_ids), IN_CHUNK):
        chunk = item_ids[i:i + IN_CHUNK]
        rows = await session.execute(
            select(Progress.tg_id, func.count()).where(Progress.item_id.in_(chunk)).group_by(Progress.tg_id)
        )
        for tg_id, n in rows:
            result[tg_id] += n
    return result


async def collect_stats(sessions: async_sessionmaker, content: Content) -> Stats:
    now = utcnow()
    day, week = now - timedelta(days=1), now - timedelta(days=7)
    async with sessions() as s:
        users = (await s.execute(
            select(User.tg_id, User.source, User.first_seen, User.last_seen, User.notify)
        )).all()
        # Только пункты из текущего контента: удалённые из YAML в статистику не входят.
        # По одному запросу на категорию: в памяти не держим все отметки всех пользователей.
        per_category_users: dict[str, dict[int, int]] = {
            c.id: await _marks_by_user(s, [i.id for i in c.items]) for c in content.categories
        }

    total = len(users)
    marks_per_user: Counter[int] = Counter()
    per_category_marks: Counter[str] = Counter()
    for cat_id, by_user in per_category_users.items():
        for tg_id, n in by_user.items():
            marks_per_user[tg_id] += n
            per_category_marks[cat_id] += n
    marks = sum(marks_per_user.values())

    titles = {c.id: c.title for c in content.categories}
    top = tuple((titles[cid], n) for cid, n in per_category_marks.most_common(5))

    by_source: dict[str, list] = defaultdict(list)
    for u in users:
        by_source[u.source or NO_SOURCE].append(u)
    source_stats = tuple(sorted(
        (SourceStats(src, len(us), sum(1 for u in us if u.last_seen >= week),
                     sum(marks_per_user.get(u.tg_id, 0) for u in us) / len(us))
         for src, us in by_source.items()),
        key=lambda x: (-x.users, x.source)))

    def returned(days: int) -> tuple[int, int]:
        limit = timedelta(days=days)
        eligible = [u for u in users if u.first_seen <= now - limit]
        return sum(1 for u in eligible if u.last_seen - u.first_seen > limit), len(eligible)

    return Stats(
        total_users=total,
        new_24h=sum(1 for u in users if u.first_seen >= day),
        active_24h=sum(1 for u in users if u.last_seen >= day),
        active_7d=sum(1 for u in users if u.last_seen >= week),
        sources=tuple((x.source, x.users) for x in source_stats),
        avg_marks=marks / total if total else 0.0,
        top_categories=top,
        activated=sum(1 for u in users if marks_per_user.get(u.tg_id, 0) > 0),
        returned_1d=returned(1),
        returned_7d=returned(7),
        source_stats=source_stats,
        notify_off=sum(1 for u in users if not u.notify),
        category_fill=tuple((c.title, len(per_category_users[c.id])) for c in content.categories),
    )


def _share(part: int, whole: int) -> str:
    return f"{part * 100 // whole}% ({part} из {whole})" if whole else "—"


def stats_blocks(st: Stats) -> list[list[str]]:
    """Разделы отчёта: каждый — список строк (HTML)."""
    blocks = [[
        "<b>Статистика</b>",
        f"Пользователей: {st.total_users}",
        f"Новых за сутки: {st.new_24h}",
        f"Активных за сутки: {st.active_24h}",
        f"Активных за 7 дней: {st.active_7d}",
        f"Среднее число отметок: {st.avg_marks:.1f}",
        f"Отключили уведомления: {st.notify_off}",
    ], [
        "<b>Активация и возвраты</b>",
        f"Отметили хотя бы 1 пункт: {_share(st.activated, st.total_users)}",
        f"Вернулись через 1+ сутки: {_share(*st.returned_1d)}",
        f"Вернулись через 7+ суток: {_share(*st.returned_7d)}",
    ]]
    sources = ["<b>Источники</b>"]
    sources += [
        f"{escape(x.source)}: {x.users} польз., активных за 7 дней {x.active_7d}, отметок в среднем {x.avg_marks:.1f}"
        for x in st.source_stats
    ] or ["—"]
    blocks.append(sources)
    top = ["<b>Топ-5 категорий по отметкам</b>"]
    top += [f"{i}. {escape(title)}: {n}" for i, (title, n) in enumerate(st.top_categories, 1)] or ["—"]
    blocks.append(top)
    fill = ["<b>Заполнение по категориям</b> (пользователей хотя бы с 1 отметкой)"]
    fill += [f"{escape(title)}: {_share(n, st.total_users)}" for title, n in st.category_fill] or ["—"]
    blocks.append(fill)
    return blocks


def format_stats_parts(st: Stats) -> list[str]:
    """Отчёт, разбитый на сообщения не длиннее лимита Telegram."""
    return pack_blocks(stats_blocks(st))


def format_stats(st: Stats) -> str:
    return "\n\n".join("\n".join(block) for block in stats_blocks(st))
