import io
import sqlite3
import tarfile
from datetime import UTC, datetime, timedelta

import pytest

from app.backup import backup_content_once, backup_once, make_content_archive, make_db_copy


def make_db(path):
    con = sqlite3.connect(path)
    con.execute("create table t (x integer)")
    con.execute("insert into t values (42)")
    con.commit()
    con.close()


def test_backup_is_a_valid_copy(tmp_path):
    db = tmp_path / "bot.db"
    make_db(db)
    target = backup_once(db, tmp_path / "b")
    assert sqlite3.connect(target).execute("select x from t").fetchone() == (42,)


def test_keeps_only_last_7(tmp_path):
    db = tmp_path / "bot.db"
    make_db(db)
    start = datetime(2026, 11, 1, 3, 0, tzinfo=UTC)
    for day in range(10):
        backup_once(db, tmp_path / "b", now=start + timedelta(days=day))
    names = sorted(p.name for p in (tmp_path / "b").iterdir())
    assert len(names) == 7
    assert names[0] == "bot-20261104-030000.db" and names[-1] == "bot-20261110-030000.db"


def test_no_database_yet_is_not_an_error(tmp_path):
    assert backup_once(tmp_path / "missing.db", tmp_path / "b") is None


def test_no_leftover_tmp_files(tmp_path):
    db = tmp_path / "bot.db"
    make_db(db)
    backup_once(db, tmp_path / "b")
    assert not list((tmp_path / "b").glob("*.tmp"))


def test_fresh_backup_exists_no_new_one(tmp_path):
    db = tmp_path / "bot.db"
    make_db(db)
    t0 = datetime(2026, 11, 17, 3, 0, tzinfo=UTC)
    assert backup_once(db, tmp_path / "b", now=t0) is not None
    assert backup_once(db, tmp_path / "b", now=t0 + timedelta(hours=23, minutes=59)) is None
    assert len(list((tmp_path / "b").iterdir())) == 1


def test_backup_older_than_24h_creates_new_one(tmp_path):
    db = tmp_path / "bot.db"
    make_db(db)
    t0 = datetime(2026, 11, 17, 3, 0, tzinfo=UTC)
    backup_once(db, tmp_path / "b", now=t0)
    assert backup_once(db, tmp_path / "b", now=t0 + timedelta(hours=25)) is not None
    assert len(list((tmp_path / "b").iterdir())) == 2


def test_restarts_do_not_push_out_old_backups(tmp_path):
    db = tmp_path / "bot.db"
    make_db(db)
    start = datetime(2026, 11, 1, 3, 0, tzinfo=UTC)
    for day in range(7):
        backup_once(db, tmp_path / "b", now=start + timedelta(days=day))
    for restart in range(1, 20):  # много перезапусков в один день
        backup_once(db, tmp_path / "b", now=start + timedelta(days=6, minutes=restart))
    assert len(list((tmp_path / "b").iterdir())) == 7
    assert (tmp_path / "b" / "bot-20261101-030000.db").exists()


def test_foreign_files_in_backup_dir_are_ignored(tmp_path):
    db = tmp_path / "bot.db"
    make_db(db)
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "bot-garbage.db").write_text("x")
    assert backup_once(db, tmp_path / "b") is not None


def test_foreign_file_does_not_displace_real_backups(tmp_path):
    db = tmp_path / "bot.db"
    make_db(db)
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "bot-garbage.db").write_text("x")
    start = datetime(2026, 11, 1, 3, 0, tzinfo=UTC)
    for day in range(9):
        backup_once(db, tmp_path / "b", now=start + timedelta(days=day))
    real = [p for p in (tmp_path / "b").iterdir() if p.name != "bot-garbage.db"]
    assert len(real) == 7 and (tmp_path / "b" / "bot-garbage.db").exists()


# --- архив content/ ---


@pytest.fixture
def content(tmp_path):
    d = tmp_path / "content"
    d.mkdir()
    (d / "a.yaml").write_bytes('id: a\ntitle: "Ё"\n'.encode())  # write_bytes: без CRLF на Windows
    (d / "b.yml").write_bytes(b"id: b\n")
    (d / ".a.yaml.tmp").write_text("недописано", encoding="utf-8")
    (d / ".gitkeep").write_text("")
    return d


def names_in(path_or_bytes):
    data = path_or_bytes if isinstance(path_or_bytes, bytes) else path_or_bytes.read_bytes()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        return sorted(tar.getnames())


def test_content_archive_has_only_category_files(content):
    assert names_in(make_content_archive(content)) == ["content/a.yaml", "content/b.yml"]
    with tarfile.open(fileobj=io.BytesIO(make_content_archive(content)), mode="r:gz") as tar:
        assert tar.extractfile("content/a.yaml").read().decode("utf-8") == 'id: a\ntitle: "Ё"\n'


def test_content_archive_without_files_is_error(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError):
        make_content_archive(tmp_path / "empty")
    with pytest.raises(ValueError):
        make_content_archive(tmp_path / "missing")


def test_content_backup_created_next_to_db_copy_and_named_by_date(tmp_path, content):
    t0 = datetime(2026, 11, 17, 3, 0, tzinfo=UTC)
    target = backup_content_once(content, tmp_path / "b", now=t0)
    assert target.name == "content-20261117-030000.tar.gz"
    assert names_in(target) == ["content/a.yaml", "content/b.yml"]
    assert not list((tmp_path / "b").glob("*.tmp"))


def test_content_backup_once_a_day(tmp_path, content):
    t0 = datetime(2026, 11, 17, 3, 0, tzinfo=UTC)
    assert backup_content_once(content, tmp_path / "b", now=t0) is not None
    assert backup_content_once(content, tmp_path / "b", now=t0 + timedelta(hours=23, minutes=59)) is None
    assert backup_content_once(content, tmp_path / "b", now=t0 + timedelta(hours=25)) is not None
    assert len(list((tmp_path / "b").iterdir())) == 2


def test_content_backup_keeps_last_7_independently_of_db_copies(tmp_path, content):
    db = tmp_path / "bot.db"
    make_db(db)
    start = datetime(2026, 11, 1, 3, 0, tzinfo=UTC)
    for day in range(10):
        now = start + timedelta(days=day)
        backup_once(db, tmp_path / "b", now=now)
        backup_content_once(content, tmp_path / "b", now=now)
    names = sorted(p.name for p in (tmp_path / "b").iterdir())
    assert len([n for n in names if n.startswith("content-")]) == 7
    assert len([n for n in names if n.startswith("bot-")]) == 7
    assert "content-20261104-030000.tar.gz" in names and "content-20261103-030000.tar.gz" not in names


def test_content_backup_works_when_db_copy_is_already_fresh(tmp_path, content):
    # после обновления базовая копия сегодня уже есть, а архива контента ещё нет — он создаётся
    db = tmp_path / "bot.db"
    make_db(db)
    t0 = datetime(2026, 11, 17, 3, 0, tzinfo=UTC)
    backup_once(db, tmp_path / "b", now=t0)
    assert backup_content_once(content, tmp_path / "b", now=t0 + timedelta(hours=1)) is not None


def test_content_backup_without_content_is_not_an_error(tmp_path):
    assert backup_content_once(tmp_path / "missing", tmp_path / "b") is None


def test_foreign_content_files_ignored_by_rotation(tmp_path, content):
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "content-garbage.tar.gz").write_text("x")
    start = datetime(2026, 11, 1, 3, 0, tzinfo=UTC)
    for day in range(9):
        backup_content_once(content, tmp_path / "b", now=start + timedelta(days=day))
    real = [p for p in (tmp_path / "b").iterdir() if p.name != "content-garbage.tar.gz"]
    assert len(real) == 7 and (tmp_path / "b" / "content-garbage.tar.gz").exists()


def test_make_db_copy_is_valid_sqlite(tmp_path):
    db = tmp_path / "bot.db"
    make_db(db)
    out = tmp_path / "copy.db"
    out.write_bytes(make_db_copy(db))
    assert sqlite3.connect(out).execute("select x from t").fetchone() == (42,)
