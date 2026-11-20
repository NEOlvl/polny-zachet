"""Резервная копия базы и папки content/ не чаще раза в сутки с ротацией (хранятся 7 последних).

Запуск: python -m app.backup  (цикл в отдельном контейнере, см. docker-compose.yml).
Раз в час цикл проверяет, нужны ли копии: каждая создаётся, только если самой свежей такого типа нет
или ей 24 часа и больше. Перезапуски контейнера лишних копий не создают.
Файлы: bot-<дата>.db (база) и content-<дата>.tar.gz (архив content/), время в имени — UTC.
"""
import io
import logging
import sqlite3
import sys
import tarfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

log = logging.getLogger("polny_zachet.backup")

DB_PATH = Path("data/bot.db")
CONTENT_DIR = Path("content")
BACKUP_DIR = Path("data/backups")
KEEP = 7
MIN_AGE = timedelta(hours=24)
CHECK_INTERVAL_SECONDS = 60 * 60
STAMP_FORMAT = "%Y%m%d-%H%M%S"
PREFIX, SUFFIX = "bot-", ".db"
CONTENT_PREFIX, CONTENT_SUFFIX = "content-", ".tar.gz"


def _stamp_time(path: Path, prefix: str, suffix: str) -> datetime | None:
    """Время копии по имени файла (UTC); None, если имя не в нашем формате."""
    stamp = path.name[len(prefix):-len(suffix)]
    try:
        return datetime.strptime(stamp, STAMP_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None


def _latest_time(backup_dir: Path, prefix: str, suffix: str) -> datetime | None:
    times = [t for p in backup_dir.glob(f"{prefix}*{suffix}") if (t := _stamp_time(p, prefix, suffix))]
    return max(times, default=None)


def _backup_time(path: Path) -> datetime | None:
    return _stamp_time(path, PREFIX, SUFFIX)


def latest_backup_time(backup_dir: Path) -> datetime | None:
    return _latest_time(backup_dir, PREFIX, SUFFIX)


def content_files_to_archive(content_dir: Path) -> list[Path]:
    """Файлы категорий (*.yaml, *.yml); временные и служебные файлы в архив не попадают."""
    if not content_dir.is_dir():
        return []
    return sorted([*content_dir.glob("*.yaml"), *content_dir.glob("*.yml")])


def make_content_archive(content_dir: Path = CONTENT_DIR) -> bytes:
    """tar.gz с папкой content/ (в архиве пути вида content/<файл>). Пустой контент — ValueError.
    Файлы пишутся атомарно (os.replace), поэтому каждый читается целым, даже если бот правит контент."""
    files = content_files_to_archive(content_dir)
    if not files:
        raise ValueError(f"В {content_dir} нет файлов контента")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path in files:
            tar.add(path, arcname=f"content/{path.name}")
    return buf.getvalue()


def make_db_copy(db_path: Path = DB_PATH) -> bytes:
    """Целостный снимок базы одним файлом (безопасно, даже если бот в этот момент пишет)."""
    src = sqlite3.connect(db_path)
    try:
        return src.serialize()
    finally:
        src.close()


def backup_once(db_path: Path = DB_PATH, backup_dir: Path = BACKUP_DIR, keep: int = KEEP,
                now: datetime | None = None) -> Path | None:
    """Делает копию базы и удаляет старые копии сверх keep.
    Возвращает путь копии или None, если копия не нужна (есть свежая) или базы ещё нет."""
    if not db_path.exists():
        log.warning("Базы %s ещё нет, копия пропущена", db_path)
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    now = now or datetime.now(UTC)
    latest = latest_backup_time(backup_dir)
    if latest is not None and now - latest < MIN_AGE:
        log.info("Свежая копия уже есть (%s), новая не нужна", latest.strftime(STAMP_FORMAT))
        return None
    stamp = now.strftime(STAMP_FORMAT)
    target = backup_dir / f"{PREFIX}{stamp}{SUFFIX}"
    tmp = target.with_suffix(".tmp")

    # sqlite3.backup даёт целостную копию, даже если бот в этот момент пишет в базу
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(tmp)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    tmp.replace(target)

    ours = sorted(p for p in backup_dir.glob(f"{PREFIX}*{SUFFIX}") if _backup_time(p))
    old = ours[:-keep]
    for path in old:
        path.unlink()
    log.info("Копия создана: %s; удалено старых: %d", target.name, len(old))
    return target


def backup_content_once(content_dir: Path = CONTENT_DIR, backup_dir: Path = BACKUP_DIR, keep: int = KEEP,
                        now: datetime | None = None) -> Path | None:
    """Архив content/ по тем же правилам, что и копия базы: не чаще раза в сутки, хранится keep последних.
    Возвращает путь архива или None, если он не нужен (есть свежий) или контента нет."""
    if not content_files_to_archive(content_dir):
        log.warning("В %s нет файлов контента, архив пропущен", content_dir)
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    now = now or datetime.now(UTC)
    latest = _latest_time(backup_dir, CONTENT_PREFIX, CONTENT_SUFFIX)
    if latest is not None and now - latest < MIN_AGE:
        log.info("Свежий архив контента уже есть (%s), новый не нужен", latest.strftime(STAMP_FORMAT))
        return None
    target = backup_dir / f"{CONTENT_PREFIX}{now.strftime(STAMP_FORMAT)}{CONTENT_SUFFIX}"
    tmp = target.with_name(target.name + ".tmp")
    try:
        tmp.write_bytes(make_content_archive(content_dir))
        tmp.replace(target)
    finally:
        tmp.unlink(missing_ok=True)

    ours = sorted(p for p in backup_dir.glob(f"{CONTENT_PREFIX}*{CONTENT_SUFFIX}")
                  if _stamp_time(p, CONTENT_PREFIX, CONTENT_SUFFIX))
    old = ours[:-keep]
    for path in old:
        path.unlink()
    log.info("Архив контента создан: %s; удалено старых: %d", target.name, len(old))
    return target


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    while True:
        try:
            backup_once()
        except Exception:
            log.exception("Не удалось сделать копию")
        try:
            backup_content_once()
        except Exception:
            log.exception("Не удалось сделать архив контента")
        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
