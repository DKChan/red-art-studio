"""generations 路由：提交生成任务 / 查询任务状态。

编码规范：本层不 import 任何具体 Provider，只依赖 providers/base.py 的协议；
依赖实例（factory/store/runner）由 main.py 的 lifespan 装配到 app.state。
"""

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from server.app.core.config import ProviderName
from server.app.core.storage import JobStore
from server.app.jobs.executor import GenerationJobRunner
from server.app.jobs.models import (
    FinalOutputs,
    GenerationParams,
    JobStatus,
    OutputFile,
)
from server.app.providers.base import ProviderFactory

router = APIRouter(prefix="/api/v1/generations", tags=["generations"])


# ---------- 请求/响应契约 ----------


class GenerationCreateRequest(BaseModel):
    """`POST /api/v1/generations` 请求体。"""

    prompt: str = Field(min_length=1, description="生成提示词")
    size: str = Field(default="1024x1024", pattern=r"^\d+x\d+$", description="宽x高")
    n: int = Field(default=1, ge=1, le=10, description="生成张数")
    # 可选覆盖配置中的 Provider；取值域与配置一致，非法值由 pydantic 自动 422
    provider: ProviderName | None = Field(default=None, description="覆盖配置的推理后端")


class GenerationSubmitResponse(BaseModel):
    """提交成功（202）响应：任务已受理，后台执行。"""

    job_id: str
    status: JobStatus


class GenerationStatusResponse(BaseModel):
    """`GET /api/v1/generations/{job_id}` 响应；succeeded 时含产物清单。"""

    job_id: str
    status: JobStatus
    params: GenerationParams
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = Field(default=None, description="失败原因（仅 failed 时非空）")
    outputs: list[OutputFile] = Field(default_factory=list, description="产物清单（succeeded 时）")


# ---------- 依赖获取（实例由 lifespan 装配在 app.state；对 Protocol 不可 isinstance） ----------


def _factory(request: Request) -> ProviderFactory:
    return request.app.state.factory


def _store(request: Request) -> JobStore:
    return request.app.state.store


def _runner(request: Request) -> GenerationJobRunner:
    return request.app.state.runner


# ---------- 路由 ----------


@router.post(
    "",
    response_model=GenerationSubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_generation(
    body: GenerationCreateRequest, request: Request
) -> GenerationSubmitResponse:
    """受理生成任务：落盘 pending 记录并调度后台执行，立即返回 202。"""
    factory = _factory(request)
    provider_name = body.provider or factory.default_name
    try:
        provider = factory.get(provider_name)
    except ValueError as exc:
        # 配置指向尚未实现的 Provider（如 L4 前的 comfyui）→ 参数层面拒绝
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    record = await _runner(request).submit(
        provider,
        GenerationParams(
            prompt=body.prompt, size=body.size, n=body.n, provider=provider_name
        ),
    )
    return GenerationSubmitResponse(job_id=record.job_id, status=record.status)


@router.get(
    "/{job_id}",
    response_model=GenerationStatusResponse,
    responses={status.HTTP_404_NOT_FOUND: {"description": "job 不存在"}},
)
async def get_generation(job_id: str, request: Request) -> GenerationStatusResponse:
    """查询任务当前状态；不存在返回 404，succeeded 时附产物清单。"""
    store = _store(request)
    record = store.get_job(job_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"job 不存在：{job_id}"
        )

    outputs: list[OutputFile] = []
    if record.status == "succeeded":
        final: FinalOutputs | None = store.load_final_outputs(job_id)
        if final is not None:
            outputs = final.outputs

    return GenerationStatusResponse(
        job_id=record.job_id,
        status=record.status,
        params=record.params,
        created_at=record.created_at,
        started_at=record.started_at,
        finished_at=record.finished_at,
        error=record.error,
        outputs=outputs,
    )
