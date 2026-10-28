from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

router = APIRouter(tags=["health"])


@router.get("/health/live")
def live() -> dict[str, str]:
    """存活探针：进程还在就返回 ok，不碰数据库。"""
    return {"status": "ok"}


@router.get("/health/ready")
def ready(request: Request) -> JSONResponse:
    """就绪探针：数据库能跑通一次查询才返回 200，否则 503。"""
    session = request.app.state.session_factory()
    try:
        session.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "checks": {"database": "error"}},
        )
    finally:
        session.close()
    return JSONResponse(
        status_code=200,
        content={"status": "ready", "checks": {"database": "ok"}},
    )
