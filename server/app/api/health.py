"""健康检查路由。"""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel


class HealthzResponse(BaseModel):
    """`GET /healthz` 响应契约。"""

    status: Literal["ok"] = "ok"


router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=HealthzResponse)
async def healthz() -> HealthzResponse:
    """存活探针：服务进程可响应即返回 200。"""
    return HealthzResponse()
