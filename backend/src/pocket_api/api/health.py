"""稼働確認エンドポイント。"""

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """`/health` のレスポンス。"""

    status: str
    engine_version: str | None


def _engine_version() -> str | None:
    """Rust エンジンがビルド済みならそのバージョンを返す。未ビルドなら None。"""
    try:
        import pocket_engine_py
    except ImportError:
        return None
    return str(pocket_engine_py.engine_version())


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """API とエンジンの稼働状況を返す。"""
    return HealthResponse(status="ok", engine_version=_engine_version())
