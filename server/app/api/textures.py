"""textures 路由（P3-L2）：提交无缝纹理生成任务 / 查询任务状态。

契约（任务书定稿）：
- POST /api/v1/textures（JSON body: prompt 必填 + 可选 quantize/isometric）
    → 202 {job_id, status}（响应结构照抄 P1 generations）
- GET  /api/v1/textures/{job_id} → 状态响应（复用 P1/P2 结构）

执行链：factory.get() → provider 生成 1024×1024（4xx/5xx 重试语义见执行器）
→ NEAREST 归一 64×64 → self_loop → tiling_preview + seam_report →（可选等距
投影）→ 原子写产物。prompt 只透传，服务端不做提示词改写/增强。

编码规范：本层不 import 任何具体 Provider，只依赖协议；依赖实例（store/runner）
由 main.py 的 lifespan 装配到 app.state。
"""

import logging

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from server.app.core.storage import JobStore
from server.app.jobs.executor import TextureJobRunner
from server.app.jobs.models import (
    FinalOutputs,
    JobStatus,
    OutputFile,
    SeamReport,
    TextureParams,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/textures", tags=["textures"])


# ---------- 请求/响应契约 ----------


class TextureCreateRequest(BaseModel):
    """`POST /api/v1/textures` 请求体。"""

    model_config = {"extra": "forbid"}

    prompt: str = Field(min_length=1, max_length=2000, description="生成提示词（只透传）")
    quantize: bool = Field(default=False, description="归一化后是否做调色板量化（≤32 色）")
    isometric: bool = Field(
        default=False, description="是否追加等距投影（64×64 平面 → 128×64 瓦片）"
    )


class TextureSubmitResponse(BaseModel):
    """提交成功（202）响应：任务已受理，后台执行。"""

    job_id: str
    status: JobStatus


class TextureStatusResponse(BaseModel):
    """`GET /api/v1/textures/{job_id}` 响应（与 generations 状态模型同构）。"""

    job_id: str
    status: JobStatus
    params: TextureParams
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = Field(default=None, description="失败原因（仅 failed 时非空）")
    outputs: list[OutputFile] = Field(default_factory=list, description="产物清单（succeeded 时）")
    seam_report: SeamReport | None = Field(
        default=None, description="检缝报告（succeeded 且为纹理任务时）"
    )


# ---------- 依赖获取（实例由 lifespan 装配在 app.state） ----------


def _store(request: Request) -> JobStore:
    return request.app.state.store


def _runner(request: Request) -> TextureJobRunner:
    return request.app.state.texture_runner


# ---------- 路由 ----------


@router.post(
    "",
    response_model=TextureSubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_texture(
    body: TextureCreateRequest, request: Request
) -> TextureSubmitResponse:
    """受理无缝纹理生成任务：落盘 pending 记录并调度后台执行，立即返回 202。"""
    record = await _runner(request).submit(
        TextureParams(
            prompt=body.prompt, quantize=body.quantize, isometric=body.isometric
        )
    )
    return TextureSubmitResponse(job_id=record.job_id, status=record.status)


@router.get(
    "/{job_id}",
    response_model=TextureStatusResponse,
    responses={status.HTTP_404_NOT_FOUND: {"description": "job 不存在"}},
)
async def get_texture(job_id: str, request: Request) -> TextureStatusResponse:
    """查询任务当前状态；不存在返回 404，succeeded 时附产物清单与检缝报告。"""
    store = _store(request)
    record = store.get_job(job_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"job 不存在：{job_id}"
        )

    outputs: list[OutputFile] = []
    seam_report: SeamReport | None = None
    if record.status == "succeeded":
        final: FinalOutputs | None = store.load_final_outputs(job_id)
        if final is not None:
            outputs = final.outputs
            seam_report = final.seam_report

    return TextureStatusResponse(
        job_id=record.job_id,
        status=record.status,
        params=record.params,  # type: ignore[arg-type]  # JobParams union 由本路由语义保证
        created_at=record.created_at,
        started_at=record.started_at,
        finished_at=record.finished_at,
        error=record.error,
        outputs=outputs,
        seam_report=seam_report,
    )
