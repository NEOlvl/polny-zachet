"""Общие пути для тестов.

Тесты работают с копией тестового контента в tests/fixtures/content/, а не с папкой content/ проекта:
там владелец ведёт реальный контент, и названия, id и число пунктов в ней могут быть любыми.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_CONTENT = Path(__file__).resolve().parent / "fixtures" / "content"
PROJECT_CONTENT = ROOT / "content"  # реальный контент: тесты его только проверяют, но не читают данные из него


def _snapshot(directory: Path) -> dict[str, bytes]:
    if not directory.is_dir():
        return {}
    return {p.name: p.read_bytes() for p in sorted(directory.iterdir()) if p.is_file()}


@pytest.fixture(scope="session", autouse=True)
def project_content_is_untouched():
    """Защита: ни один тест не должен менять content/ проекта."""
    before = _snapshot(PROJECT_CONTENT)
    yield
    assert _snapshot(PROJECT_CONTENT) == before, "тесты изменили папку content/ проекта"
