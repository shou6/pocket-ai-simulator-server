"""FastAPI アプリケーションのエントリポイント。"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from pocket_api.api.cards import router as cards_router
from pocket_api.api.decks import router as decks_router
from pocket_api.api.health import router as health_router
from pocket_api.api.jobs import router as jobs_router
from pocket_api.api.meta import router as meta_router
from pocket_api.settings import get_settings


def create_app() -> FastAPI:
    """アプリケーションを組み立てる（テストからも呼べるようファクトリにする）。"""
    settings = get_settings()
    app = FastAPI(title="Pocket AI Simulator API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health_router)
    app.include_router(meta_router)
    app.include_router(decks_router)
    app.include_router(cards_router)
    app.include_router(jobs_router)
    return app


app = create_app()
