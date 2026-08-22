"""FastAPI 入口：API 路由 + 前端静态产物伺服（SPA fallback，刷新不 404，PRD §16）。"""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from .core.db import engine, Base, init_db
from .core.config import ROOT, settings
from .workers.worker import Worker

STATIC_DIR = Path(settings.static_dir) if settings.static_dir else ROOT / "frontend" / "dist"

worker = Worker(concurrency=2)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(worker.loop())
    yield
    worker.stop()
    task.cancel()


app = FastAPI(title="消防监督检查 AI 辅助系统", lifespan=lifespan)

from .api import auth, inspections, evidence, knowledge, templates, documents, assistant, admin  # noqa: E402

for r in (auth.router, inspections.router, evidence.router, knowledge.router,
          templates.router, documents.router, assistant.router, admin.router):
    app.include_router(r)


@app.exception_handler(Exception)
async def on_error(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": str(exc)[:500]})


@app.get("/api/health")
def health():
    return {"ok": True, "ai": {g: settings.ai_available(g) for g in ("asr", "llm", "vl", "embed")}}


# ---- SPA：/api 之外的路径全部回落 index.html ----
@app.get("/{path:path}")
def spa(path: str):
    if STATIC_DIR.exists():
        f = STATIC_DIR / path
        if path and f.is_file():
            return FileResponse(str(f))
        return FileResponse(str(STATIC_DIR / "index.html"))
    return JSONResponse({"detail": "前端未构建（frontend/dist 不存在）"}, status_code=404)
