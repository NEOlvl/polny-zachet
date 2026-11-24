"""API Mini App: авторизация и /api/summary на тестовой базе."""
import hashlib
import hmac
import json
from urllib.parse import urlencode

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.content import Category, Content, ContentHolder, Item
from app.db import Base, User, make_engine, make_session_factory
from app.progress import ProgressService
from app.webapp import build_web_app

TOKEN = "123:test-token"
NOW = 1_700_000_000.0

_CATS = (
    Category("cat_a", "Категория А", 0,
             (Item("a1", "Ёлка"),
              Item("a2", "Второй", hint="где искать", description="подробно",
                   tips="совет", photo="ph"))),
    Category("cat_b", "Категория Б", 1,
             (Item("b1", "Третий"), Item("b2", "Секретный тайник", spoiler=True))),
)
CONTENT = Content(
    _CATS,
    {i.id: i for c in _CATS for i in c.items},
    {i.id: c.id for c in _CATS for i in c.items},
)


def sign(user_id: int, token: str = TOKEN) -> str:
    pairs = {"auth_date": str(int(NOW)),
             "user": json.dumps({"id": user_id, "username": "u"}, separators=(",", ":"))}
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)


@pytest.fixture
def web_dir(tmp_path):
    d = tmp_path / "web"
    d.mkdir()
    return d


@pytest.fixture
def freeze_now(monkeypatch):
    monkeypatch.setattr("app.webauth.time.time", lambda: NOW)


@pytest_asyncio.fixture
async def svc():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield ProgressService(make_session_factory(engine))
    await engine.dispose()


@pytest_asyncio.fixture
async def client(svc, web_dir):
    app = build_web_app(ContentHolder(CONTENT), svc, TOKEN, dev_tg_id=555, web_dir=web_dir)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://t") as c:
        yield c


async def test_summary_empty_progress(client):
    r = await client.get("/api/summary")
    assert r.status_code == 200
    assert r.json() == {
        "done": 0, "total": 4, "percent": 0,
        "categories": [
            {"id": "cat_a", "title": "Категория А", "done": 0, "total": 2, "percent": 0},
            {"id": "cat_b", "title": "Категория Б", "done": 0, "total": 2, "percent": 0},
        ],
    }


async def test_summary_counts_marks(client, svc):
    await svc.mark(555, "a1")
    data = (await client.get("/api/summary")).json()
    assert data["done"] == 1 and data["percent"] == 25
    assert data["categories"][0]["done"] == 1


async def test_no_header_unauthorized(svc, web_dir):
    app = build_web_app(ContentHolder(CONTENT), svc, TOKEN, web_dir=web_dir)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://t") as c:
        assert (await c.get("/api/summary")).status_code == 401
        assert (await c.get("/api/summary",
                            headers={"Authorization": "tma garbage"})).status_code == 401


async def test_signed_header_authenticates_and_creates_user(svc, web_dir, freeze_now):
    app = build_web_app(ContentHolder(CONTENT), svc, TOKEN, web_dir=web_dir)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://t") as c:
        r = await c.get("/api/summary", headers={"Authorization": f"tma {sign(999)}"})
    assert r.status_code == 200
    async with svc.sessions() as s:
        user = await s.scalar(select(User).where(User.tg_id == 999))
    assert user is not None and user.source == "miniapp"


async def test_static_index_served(svc, web_dir):
    (web_dir / "index.html").write_text("<h1>app</h1>", encoding="utf-8")
    app = build_web_app(ContentHolder(CONTENT), svc, TOKEN, dev_tg_id=555, web_dir=web_dir)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://t") as c:
        r = await c.get("/")
    assert r.status_code == 200 and "<h1>app</h1>" in r.text


async def test_category_items(client, svc):
    await svc.mark(555, "a1")
    r = await client.get("/api/categories/cat_a")
    assert r.status_code == 200
    data = r.json()
    assert data["title"] == "Категория А" and data["done"] == 1 and data["total"] == 2
    assert data["percent"] == 50
    assert [(i["id"], i["done"]) for i in data["items"]] == [("a1", True), ("a2", False)]


async def test_category_unknown_is_404(client):
    assert (await client.get("/api/categories/nope")).status_code == 404


async def test_toggle_on_and_off(client):
    r = await client.post("/api/items/a1/toggle")
    assert r.status_code == 200 and r.json() == {"id": "a1", "done": True}
    r = await client.post("/api/items/a1/toggle")
    assert r.status_code == 200 and r.json() == {"id": "a1", "done": False}
    data = (await client.get("/api/summary")).json()
    assert data["done"] == 0


async def test_toggle_unknown_item_is_404(client):
    assert (await client.post("/api/items/nope/toggle")).status_code == 404


async def test_toggle_visible_in_category(client):
    await client.post("/api/items/b1/toggle")
    data = (await client.get("/api/categories/cat_b")).json()
    assert data["done"] == 1 and data["items"][0]["done"] is True


async def test_item_card_fields(client):
    data = (await client.get("/api/items/a2")).json()
    assert data == {
        "id": "a2", "title": "Второй", "done": False,
        "category": "Категория А", "category_id": "cat_a",
        "hint": "где искать", "description": "подробно", "tips": "совет",
        "has_photo": True, "spoiler": False,
    }


async def test_item_unknown_is_404(client):
    assert (await client.get("/api/items/nope")).status_code == 404
    assert (await client.get("/api/items/nope/photo")).status_code == 404


async def test_item_without_photo_is_404(client):
    assert (await client.get("/api/items/a1/photo")).status_code == 404


def tg_api(files: dict[str, list[str]]):
    """Подмена Bot API: files — file_id -> очередь file_path (каждый вызов getFile берёт следующий;
    пустая очередь = getFile отвечает ok:false)."""
    calls = {"getFile": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/getFile"):
            calls["getFile"] += 1
            paths = files.get(req.url.params.get("file_id", ""), [])
            if not paths:
                return httpx.Response(200, json={"ok": False, "description": "bad file_id"})
            path = paths.pop(0)
            return httpx.Response(200, json={"ok": True, "result": {"file_path": path}})
        if "/file/bot" in req.url.path:
            if "expired.jpg" in req.url.path:
                return httpx.Response(404)
            return httpx.Response(200, content=b"img-bytes",
                                  headers={"content-type": "image/jpeg"})
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), calls


async def test_photo_proxy_and_cache(svc, web_dir):
    http, calls = tg_api({"ph": ["photos/f.jpg"]})
    app = build_web_app(ContentHolder(CONTENT), svc, TOKEN, dev_tg_id=555,
                        web_dir=web_dir, http_client=http)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://t") as c:
        r = await c.get("/api/items/a2/photo")
        assert r.status_code == 200 and r.content == b"img-bytes"
        assert r.headers["content-type"].startswith("image/jpeg")
        await c.get("/api/items/a2/photo")  # повтор — file_path из кэша
    assert calls["getFile"] == 1


async def test_photo_refreshes_expired_path(svc, web_dir):
    http, calls = tg_api({"ph": ["expired.jpg", "fresh.jpg"]})
    app = build_web_app(ContentHolder(CONTENT), svc, TOKEN, dev_tg_id=555,
                        web_dir=web_dir, http_client=http)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://t") as c:
        r = await c.get("/api/items/a2/photo")
    assert r.status_code == 200 and calls["getFile"] == 2


async def test_photo_getfile_failure_is_404(svc, web_dir):
    http, _ = tg_api({})  # любой file_id — ok:false
    content = Content(
        (Category("cb", "CB", 0, (Item("bad1", "X", photo="bad"),)),),
        {"bad1": Item("bad1", "X", photo="bad")},
        {"bad1": "cb"},
    )
    app = build_web_app(ContentHolder(content), svc, TOKEN, dev_tg_id=555,
                        web_dir=web_dir, http_client=http)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://t") as c:
        assert (await c.get("/api/items/bad1/photo")).status_code == 404


async def test_summary_contract_for_progress_screen(client, svc):
    """Экран прогресса рендерится по /api/summary: процент общий и по каждой категории."""
    await svc.mark(555, "a1")
    data = (await client.get("/api/summary")).json()
    cat = data["categories"][0]
    assert cat == {"id": "cat_a", "title": "Категория А", "done": 1, "total": 2,
                   "percent": 50}


async def test_category_hides_unmarked_spoiler_title(client):
    data = (await client.get("/api/categories/cat_b")).json()
    by_id = {i["id"]: i for i in data["items"]}
    assert by_id["b2"]["title"] is None and by_id["b2"]["spoiler"] is True
    assert by_id["b1"]["title"] == "Третий"


async def test_category_shows_marked_spoiler_title(client, svc):
    await svc.mark(555, "b2")
    data = (await client.get("/api/categories/cat_b")).json()
    assert data["items"][1]["title"] == "Секретный тайник"


async def test_search_finds_case_and_yo_insensitive(client):
    data = (await client.get("/api/search", params={"q": "ЕЛКА"})).json()
    assert data["total"] == 1 and data["results"][0]["id"] == "a1"


async def test_search_by_hint_and_all_words_required(client):
    data = (await client.get("/api/search", params={"q": "искать второй"})).json()
    assert data["total"] == 1 and data["results"][0]["id"] == "a2"
    data = (await client.get("/api/search", params={"q": "второй блабла"})).json()
    assert data["total"] == 0


async def test_search_hides_unmarked_spoiler(client, svc):
    data = (await client.get("/api/search", params={"q": "секретный"})).json()
    assert data["total"] == 0
    await svc.mark(555, "b2")
    data = (await client.get("/api/search", params={"q": "секретный"})).json()
    assert data["total"] == 1
    assert data["results"][0] == {"id": "b2", "title": "Секретный тайник", "done": True,
                                "category": "Категория Б", "category_id": "cat_b"}
    await svc.unmark(555, "b2")
    assert (await client.get("/api/search", params={"q": "секретный"})).json()["total"] == 0


async def test_search_empty_query(client):
    assert (await client.get("/api/search", params={"q": "   "})).json()["total"] == 0
    assert (await client.get("/api/search")).json()["total"] == 0


async def test_search_limit_8(svc, web_dir):
    cats = (Category("c", "C", 0, tuple(Item(f"i{n}", f"Пункт {n}") for n in range(10))),)
    content = Content(cats, {i.id: i for c in cats for i in c.items},
                      {i.id: c.id for c in cats for i in c.items})
    app = build_web_app(ContentHolder(content), svc, TOKEN, dev_tg_id=555, web_dir=web_dir)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://t") as c:
        data = (await c.get("/api/search", params={"q": "пункт"})).json()
    assert data["total"] == 10 and len(data["results"]) == 8


async def test_existing_user_source_not_overwritten(svc, web_dir):
    await svc.touch_user(888, "old", source="friend")
    app = build_web_app(ContentHolder(CONTENT), svc, TOKEN, dev_tg_id=888, web_dir=web_dir)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://t") as c:
        await c.get("/api/summary")
    async with svc.sessions() as s:
        user = await s.scalar(select(User).where(User.tg_id == 888))
    assert user.source == "friend"
