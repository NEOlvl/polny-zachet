"""FastAPI-приложение Mini App: статика web/ и JSON API с проверкой initData Telegram."""
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles

from app.content import ContentHolder
from app.progress import ProgressService, percent
from app.search import MAX_RESULTS, clean_query, find_items
from app.webauth import validate_init_data

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
DEV_SOURCE = "miniapp"
TG_API = "https://api.telegram.org"


def build_web_app(holder: ContentHolder, svc: ProgressService, bot_token: str,
                  dev_tg_id: int | None = None, web_dir: str | Path = WEB_DIR,
                  http_client: httpx.AsyncClient | None = None) -> FastAPI:
    """Собрать приложение. dev_tg_id — обход авторизации initData, только для локальной разработки."""
    client = http_client or httpx.AsyncClient(timeout=20)

    @asynccontextmanager
    async def lifespan(_app):
        yield
        if http_client is None:
            await client.aclose()

    app = FastAPI(title="Полный зачёт", docs_url=None, redoc_url=None, lifespan=lifespan)
    file_paths: dict[str, str] = {}  # file_path из getFile живёт ~час; при 404 перевыпрашиваем

    async def current_user(request: Request) -> int:
        if dev_tg_id is not None:
            tg_id, username = dev_tg_id, None
        else:
            auth = request.headers.get("authorization", "")
            if not auth.startswith("tma "):
                raise HTTPException(401, "Нет данных авторизации Mini App")
            data = validate_init_data(auth[4:], bot_token)
            if data is None:
                raise HTTPException(401, "Неверная или устаревшая подпись initData")
            tg_id, username = data["tg_id"], data["username"]
        await svc.touch_user(tg_id, username, source=DEV_SOURCE)
        return tg_id

    TgId = Annotated[int, Depends(current_user)]

    @app.get("/api/summary")
    async def summary(tg_id: TgId):
        s = await svc.summary(tg_id, holder.content)
        return {
            "done": s.done,
            "total": s.total,
            "percent": s.percent,
            "categories": [
                {"id": c.category_id, "title": c.title, "done": c.done, "total": c.total,
                 "percent": c.percent}
                for c in s.categories
            ],
        }

    @app.get("/api/categories/{cat_id}")
    async def category(cat_id: str, tg_id: TgId):
        cat = holder.content.category(cat_id)
        if cat is None:
            raise HTTPException(404, "Категория не найдена")
        done = await svc.done_ids(tg_id)
        done_count = sum(1 for i in cat.items if i.id in done)
        total = len(cat.items)
        return {
            "id": cat.id,
            "title": cat.title,
            "done": done_count,
            "total": total,
            "percent": percent(done_count, total),
            "items": [
                {"id": i.id,
                 # неотмеченный спойлер названием не светим: оно приходит при раскрытии (как в боте)
                 "title": None if i.spoiler and i.id not in done else i.title,
                 "done": i.id in done, "spoiler": i.spoiler, "has_details": i.has_details}
                for i in cat.items
            ],
        }

    @app.post("/api/items/{item_id}/toggle")
    async def toggle(item_id: str, tg_id: TgId):
        if item_id not in holder.content.items_by_id:
            raise HTTPException(404, "Пункт не найден")
        done = await svc.toggle(tg_id, item_id)
        return {"id": item_id, "done": done}

    @app.get("/api/items/{item_id}")
    async def item(item_id: str, tg_id: TgId):
        it = holder.content.items_by_id.get(item_id)
        if it is None:
            raise HTTPException(404, "Пункт не найден")
        cat = holder.content.category(holder.content.category_of_item[it.id])
        return {
            "id": it.id,
            "title": it.title,
            "done": await svc.is_done(tg_id, it.id),
            "category": cat.title if cat else "",
            "category_id": cat.id if cat else "",
            "hint": it.hint,
            "description": it.description,
            "tips": it.tips,
            "has_photo": bool(it.photo),
            "spoiler": it.spoiler,
        }

    async def _file_path(item_id: str, file_id: str) -> str | None:
        cached = file_paths.get(item_id)
        if cached is not None:
            return cached
        r = await client.get(f"{TG_API}/bot{bot_token}/getFile", params={"file_id": file_id})
        data = r.json()
        if not data.get("ok"):
            return None
        file_paths[item_id] = data["result"]["file_path"]
        return file_paths[item_id]

    @app.get("/api/items/{item_id}/photo")
    async def item_photo(item_id: str, tg_id: TgId):
        """Прокси картинки пункта: file_id -> getFile -> скачивание. Токен на клиент не отдаётся."""
        it = holder.content.items_by_id.get(item_id)
        if it is None or not it.photo:
            raise HTTPException(404, "Нет фото")
        try:
            path = await _file_path(item_id, it.photo)
            if path is None:
                raise HTTPException(404, "Фото недоступно")
            resp = await client.get(f"{TG_API}/file/bot{bot_token}/{path}")
            if resp.status_code == 404:  # file_path протух: перевыпрашиваем один раз
                file_paths.pop(item_id, None)
                path = await _file_path(item_id, it.photo)
                if path is None:
                    raise HTTPException(404, "Фото недоступно")
                resp = await client.get(f"{TG_API}/file/bot{bot_token}/{path}")
        except httpx.HTTPError:
            raise HTTPException(502, "Не удалось скачать фото") from None
        if resp.status_code >= 400:
            raise HTTPException(502, "Не удалось скачать фото")
        return Response(content=resp.content,
                        media_type=resp.headers.get("content-type", "image/jpeg"))

    @app.get("/api/search")
    async def search(tg_id: TgId, q: str = ""):
        """Поиск как /find в боте: все слова запроса в названии или подсказке, неотмеченные
        спойлеры не ищутся. Текст запроса в логи не пишется."""
        query = clean_query(q)
        if not query:
            return {"query": "", "total": 0, "results": []}
        done = await svc.done_ids(tg_id)
        found = find_items(holder.content, done, query)
        return {
            "query": query,
            "total": len(found),
            "results": [
                {"id": i.id, "title": i.title, "done": i.id in done,
                 "category": c.title, "category_id": c.id}
                for i, c in found[:MAX_RESULTS]
            ],
        }

    app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
    return app
