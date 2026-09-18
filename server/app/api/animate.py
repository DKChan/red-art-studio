"""animate 路由（P5-L2 生成线）：提交任务 / 查询任务状态。

契约（任务书定稿）：
- POST /api/v1/animate（**JSON body**——本线无文件上传，纯参数任务，照
  generations/textures 路由模式）→ 202 {job_id, status}
- GET  /api/v1/animate/{job_id} → 状态响应（复用 P1-P5 状态模型结构，
  animate_report 回显）

全部参数校验在契约模型（AnimateParams，extra=forbid）：prompt 长度/八枚举/
帧数域 4-16 偶数/尺寸域 64-2048/pixel 画布 ≤256/color_count 交叉——非法值
FastAPI/pydantic 统一 422 精确报文，路由层零重复校验。

执行链：AnimateJobRunner（provider 逐帧文生图，整帧序列级重试）→ L1 打包
管线 → 产物落盘。帧间主体一致性有客观局限（纯文生图无参考图条件），报告
如实交付不判 failed（loop_report，L1 同哲学）。

编码规范：本层不 import 任何具体 Provider，只依赖协议；依赖实例
（store/runner）由 main.py 的 lifespan 装配到 app.state。
"""

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from server.app.core.storage import JobStore
from server.app.jobs.executor import AnimateJobRunner
from server.app.jobs.models import (
    AnimateParams,
    AnimateReport,
    FinalOutputs,
    JobStatus,
    OutputFile,
)

router = APIRouter(prefix="/api/v1/animate", tags=["animate"])


# ---------- 请求/响应契约 ----------


class AnimateSubmitResponse(BaseModel):
    """提交成功（202）响应：任务已受理，后台执行。"""

    job_id: str
    status: JobStatus


class AnimateStatusResponse(BaseModel):
    """`GET /api/v1/animate/{job_id}` 响应（与 P1-P5 状态模型同构）。

    animate_report：生成报告（succeeded 时随响应回显，与 final_outputs.json
    一致；pack 键内嵌 L1 打包报告全量）。
    """

    job_id: str
    status: JobStatus
    params: AnimateParams
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = Field(default=None, description="失败原因（仅 failed 时非空）")
    outputs: list[OutputFile] = Field(default_factory=list, description="产物清单（succeeded 时）")
    animate_report: AnimateReport | None = Field(
        default=None, description="生成报告（succeeded 时；与 final_outputs.json 一致）"
    )


# ---------- 依赖获取（实例由 lifespan 装配在 app.state） ----------


def _store(request: Request) -> JobStore:
    return request.app.state.store


def _runner(request: Request) -> AnimateJobRunner:
    return request.app.state.animate_runner


# ---------- 路由 ----------


@router.post(
    "",
    response_model=AnimateSubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_animate(body: AnimateParams, request: Request) -> AnimateSubmitResponse:
    """受理 animate 生成任务：契约校验（模型内）→ 落盘 pending → 后台执行。"""
    record = await _runner(request).submit(body)
    return AnimateSubmitResponse(job_id=record.job_id, status=record.status)


@router.get(
    "/{job_id}",
    response_model=AnimateStatusResponse,
    responses={status.HTTP_404_NOT_FOUND: {"description": "job 不存在"}},
)
async def get_animate(job_id: str, request: Request) -> AnimateStatusResponse:
    """查询任务当前状态；不存在返回 404，succeeded 时附产物清单与生成报告。"""
    store = _store(request)
    record = store.get_job(job_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"job 不存在：{job_id}"
        )

    outputs: list[OutputFile] = []
    animate_report: AnimateReport | None = None
    if record.status == "succeeded":
        final: FinalOutputs | None = store.load_final_outputs(job_id)
        if final is not None:
            outputs = final.outputs
            animate_report = final.animate_report

    return AnimateStatusResponse(
        job_id=record.job_id,
        status=record.status,
        params=record.params,  # type: ignore[arg-type]  # JobParams union 由本路由语义保证
        created_at=record.created_at,
        started_at=record.started_at,
        finished_at=record.finished_at,
        error=record.error,
        outputs=outputs,
        animate_report=animate_report,
    )
