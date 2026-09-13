"""API の依存注入。"""

from collections.abc import Iterator

from sqlalchemy.orm import Session

from pocket_api.db.session import SessionLocal


def get_session() -> Iterator[Session]:
    """リクエストごとの DB セッション。テストからは差し替える。"""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
