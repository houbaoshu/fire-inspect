from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import get_settings
from .database import create_all
from .routers import assistant, auth, documents, facts, inspections, knowledge, materials, templates
from .security import verify_csrf


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    if settings.env in {"development", "test"}:
        create_all()
    yield


app = FastAPI(
    title="消防监督检查 AI 辅助系统",
    version="0.1.0",
    description="所有 AI 提取和生成内容均为候选或初稿，最终确认由执法人员完成。",
    lifespan=lifespan,
)
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def csrf_middleware(request, call_next):
    try:
        verify_csrf(request)
    except HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return await call_next(request)


@app.get("/health", tags=["system"])
def health() -> dict:
    return {
        "status": "ok",
        "ai_provider": settings.ai_provider,
        "task_mode": "eager" if settings.task_always_eager else "worker",
        "storage": settings.storage_backend,
    }


for api_router in (
    auth.router,
    auth.users_router,
    inspections.router,
    materials.router,
    facts.router,
    knowledge.router,
    templates.router,
    documents.router,
    assistant.router,
):
    app.include_router(api_router, prefix="/api/v1")
