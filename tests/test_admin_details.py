import time

import pytest
from test_admin import ADMIN, USER2
from test_admin_content import file_items, hc, last_text  # noqa: F401

from app.content import MAX_DESCRIPTION, MAX_TIPS
from app.handlers import admin as admin_module


def item(h, item_id="test_001"):
    return next(i for i in file_items(h, "test_example.yaml") if i["id"] == item_id)


async def send_photo(h, user=ADMIN, sizes=("small", "big")):
    h.n += 1
    await h.dp.feed_raw_update(h.bot, {"update_id": h.n, "message": {
        "message_id": h.n, "date": int(time.time()), "chat": {"id": user["id"], "type": "private"},
        "from": user, "photo": [{"file_id": s, "file_unique_id": f"u{s}", "width": 10 * (n + 1), "height": 10}
                                for n, s in enumerate(sizes)]}})


@pytest.mark.parametrize("cmd", ["/desc test_001 | x", "/tips test_001 | x", "/photo test_001"])
async def test_non_admin_ignored(hc, cmd):  # noqa: F811
    await hc.send("/start", USER2)
    before = len(hc.session.calls)
    snapshot = (hc.cdir / "test_example.yaml").read_text(encoding="utf-8")
    await hc.send(cmd, USER2)
    assert len(hc.session.calls) == before
    assert (hc.cdir / "test_example.yaml").read_text(encoding="utf-8") == snapshot


async def test_desc_and_tips_set_replace_and_delete(hc):  # noqa: F811
    await hc.send("/desc test_001 | Подробно: у маяка | с чертой", ADMIN)
    assert "описание сохранено" in last_text(hc)
    assert item(hc)["description"] == "Подробно: у маяка | с чертой"
    assert hc.holder.content.items_by_id["test_001"].description == "Подробно: у маяка | с чертой"
    await hc.send("/tips test_001 | Совет", ADMIN)
    await hc.send("/desc test_001 | Новое", ADMIN)
    assert item(hc)["description"] == "Новое" and item(hc)["tips"] == "Совет"
    await hc.send("/desc test_001 | -", ADMIN)
    assert "описание удалено" in last_text(hc)
    assert "description" not in item(hc) and item(hc)["tips"] == "Совет"


async def test_desc_limits_and_errors_leave_file_unchanged(hc):  # noqa: F811
    snapshot = (hc.cdir / "test_example.yaml").read_text(encoding="utf-8")
    await hc.send("/desc test_001 | " + "а" * (MAX_DESCRIPTION + 1), ADMIN)
    assert "Не записано" in last_text(hc)
    await hc.send("/tips test_001 | " + "а" * (MAX_TIPS + 1), ADMIN)
    assert "Не записано" in last_text(hc)
    await hc.send("/desc nope | x", ADMIN)
    assert "не найден" in last_text(hc)
    await hc.send("/desc test_001", ADMIN)
    assert last_text(hc).startswith("Использование: /desc")
    await hc.send("/tips test_001 | ", ADMIN)
    assert last_text(hc).startswith("Использование: /tips")
    assert (hc.cdir / "test_example.yaml").read_text(encoding="utf-8") == snapshot
    await hc.send("/desc test_001 | " + "а" * MAX_DESCRIPTION, ADMIN)
    assert "описание сохранено" in last_text(hc)


async def test_photo_flow_takes_largest_size(hc):  # noqa: F811
    await hc.send("/photo test_002", ADMIN)
    assert "Пришли картинку" in last_text(hc)
    await send_photo(hc)
    assert "картинка сохранена" in last_text(hc)
    assert item(hc, "test_002")["photo"] == "big"
    assert hc.holder.content.items_by_id["test_002"].photo == "big"


async def test_photo_without_waiting_is_ignored(hc):  # noqa: F811
    before = len(hc.session.calls)
    await send_photo(hc)
    assert len(hc.session.calls) == before
    assert "photo" not in item(hc, "test_002")


async def test_photo_delete_and_bad_args(hc):  # noqa: F811
    await hc.send("/photo test_002", ADMIN)
    await send_photo(hc)
    await hc.send("/photo test_002 | -", ADMIN)
    assert "картинка удалена" in last_text(hc)
    assert "photo" not in item(hc, "test_002")
    await hc.send("/photo nope", ADMIN)
    assert "не найден" in last_text(hc)
    await hc.send("/photo test_002 | что-то", ADMIN)
    assert last_text(hc).startswith("Использование: /photo")


async def test_photo_waiting_reasks_on_text_and_cancels_on_command(hc):  # noqa: F811
    await hc.send("/photo test_002", ADMIN)
    await hc.send("просто текст", ADMIN)
    assert "Жду именно фото" in last_text(hc)
    await hc.send("/cat", ADMIN)
    assert "Итого" in last_text(hc)
    await send_photo(hc)
    assert "photo" not in item(hc, "test_002")


async def test_photo_waiting_expires(hc, monkeypatch):  # noqa: F811
    await hc.send("/photo test_002", ADMIN)
    monkeypatch.setattr(admin_module, "PHOTO_WAIT_TTL", -1.0)
    await send_photo(hc)
    assert "устарело" in last_text(hc)
    assert "photo" not in item(hc, "test_002")


async def test_cat_marks_description_and_photo(hc):  # noqa: F811
    await hc.send("/desc test_001 | Описание", ADMIN)
    await hc.send("/photo test_002", ADMIN)
    await send_photo(hc)
    await hc.send("/cat test_example", ADMIN)
    lines = last_text(hc).splitlines()
    line1 = next(line for line in lines if "test_001" in line)
    line2 = next(line for line in lines if "test_002" in line)
    assert "📝" in line1 and "🖼" not in line1
    assert "🖼" in line2 and "📝" not in line2
