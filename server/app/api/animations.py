"""animations 路由（P5-L1 帧序列打包线）：提交任务 / 查询任务状态。

契约（任务书定稿）：
- POST /api/v1/animations（multipart: files[] 2-16 张等尺寸静帧 + payload JSON 串）
    → 202 {job_id, status}
- GET  /api/v1/animations/{job_id} → 状态响应（复用 P1-P4 状态模型结构）

帧数硬门禁在读文件内容**前**（0/1 张、>16 张、奇数张 → 422）——无效请求不进
管线（P4-L2 extract 数量门禁同模式）。官方帧数域是经典线 4-16 / 像素线 2-16，
本线取并集 2-16 且必须偶数（官方 §2.1 偶数约束；并集为推断决策，见
AnimationPackParams docstring）。

**动画格式输入拒绝（422，任务书标注决策）**：多帧 WebP/GIF 是"动画格式输入"，
静帧打包线收它语义不清——首帧截取是静默丢信息，宁可显式拒绝。检测在解码层：
PIL n_frames>1 即动画输入（零依赖，Pillow 本就是唯一图像库）。

上传防线照抄 tilesets `_read_texture`（大小上限 413 / 空 / Content-Type 白名单
422 / 魔数 422）。执行链零 provider 调用（确定性管线，AnimationPackJobRunner
线程池模式，重试语义不适用）。

编码规范：本层不 import 任何具体 Provider，只依赖协议；依赖实例（store/runner）
由 main.py 的 lifespan 装配到 app.state。
"""

import logging
from io import BytesIO
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from PIL import Image
from pydantic import BaseModel, Field, ValidationError

from server.app.api.tilesets import _read_texture
from server.app.core.anim_pipeline import _FRAMES_MAX, _FRAMES_MIN
from server.app.core.storage import JobStore
from server.app.jobs.executor import AnimationPackJobRunner
from server.app.jobs.models import (
    AnimationPackParams,
    AnimPackReport,
    FinalOutputs,
    JobStatus,
    OutputFile,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/animations", tags=["animations"])


# ---------- 请求/响应契约 ----------


class AnimationSubmitResponse(BaseModel):
    """提交成功（202）响应：任务已受理，后台执行。"""

    job_id: str
    status: JobStatus


class AnimationStatusResponse(BaseModel):
    """`GET /api/v1/animations/{job_id}` 响应（与 P1-P4 状态模型同构）。

    anim_report：打包报告（succeeded 时随响应回显，与 final_outputs.json 一致）。
    """

    job_id: str
    status: JobStatus
    params: AnimationPackParams
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = Field(default=None, description="失败原因（仅 failed 时非空）")
    outputs: list[OutputFile] = Field(default_factory=list, description="产物清单（succeeded 时）")
    anim_report: AnimPackReport | None = Field(
        default=None, description="打包报告（succeeded 时；与 final_outputs.json 一致）"
    )


# ---------- 依赖获取（实例由 lifespan 装配在 app.state） ----------


def _store(request: Request) -> JobStore:
    return request.app.state.store


def _runner(request: Request) -> AnimationPackJobRunner:
    return request.app.state.anim_runner


# ---------- 输入防线 ----------


def _ensure_static_image(data: bytes, filename: str) -> None:
    """拒绝动画格式输入（多帧 WebP/GIF → 422；任务书标注决策）。

    静帧线收动画输入语义不清：首帧截取是静默丢信息，显式拒绝让用户自行拆帧
    后重提。PIL n_frames>1 即动画输入（APNG 同语义）。
    """
    try:
        with Image.open(BytesIO(data)) as img:
            if getattr(img, "n_frames", 1) > 1:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"检测到动画格式输入（n_frames>1）：{filename!r}——"
                        "静帧打包线只收静帧，请先用拆帧工具导出静帧序列"
                    ),
                )
    except HTTPException:
        raise
    except Exception:
        pass  # 解码失败留给管线的 _decode_checked 归一为 AnimPipelineError


def _frames_gate(count: int) -> None:
    """帧数硬门禁（读文件内容前）：数量与奇偶校验，精确报文。"""
    if count < _FRAMES_MIN:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"动画至少需要 {_FRAMES_MIN} 帧，实际 {count} 张",
        )
    if count > _FRAMES_MAX:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"动画最多 {_FRAMES_MAX} 帧，实际 {count} 张",
        )
    if count % 2 != 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"帧数必须为偶数（官方约束），实际 {count} 张",
        )


# ---------- 路由 ----------


@router.post(
    "",
    response_model=AnimationSubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_animation(
    request: Request,
    payload: Annotated[
        str, Form(description="任务参数 JSON（output_format/animation_type/pixel 等，全可选）")
    ],
    files: Annotated[
        list[UploadFile] | None,
        File(description=f"静帧序列 {_FRAMES_MIN}-{_FRAMES_MAX} 张（png/jpeg/webp，等尺寸）"),
    ] = None,
) -> AnimationSubmitResponse:
    """受理动画打包任务：帧数门禁 → payload 校验 → 逐文件防线 → 动画输入拒绝 → 后台执行。

    防线顺序（廉价优先）：数量门禁（不读内容）→ payload 契约 → 逐文件大小/
    魔数 → 动画格式检测（需解码头部，最后做）。
    """
    # 1) 帧数硬门禁（先于读内容：无效请求最廉价地挡下；奇偶约束官方 §2.1）
    count = len(files) if files else 0
    _frames_gate(count)

    # 2) payload JSON → pydantic 契约校验（extra=forbid：未知参数 422）
    try:
        params = AnimationPackParams.model_validate_json(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"payload 校验失败：{exc.error_count()} 处错误（{exc.errors()[0]['msg']}）",
        ) from exc

    # 3) 逐文件上传防线（大小/空/Content-Type/魔数，tilesets 同款）
    settings = request.app.state.settings
    images: list[bytes] = []
    for file in files or []:
        data = await _read_texture(file, settings)
        _ensure_static_image(data, file.filename or "")
        images.append(data)
    logger.debug(
        "动画打包受理 frames=%d format=%s pixel=%s",
        len(images),
        params.output_format,
        params.pixel,
    )

    # 4) 受理：确定性管线零 provider 调用（同 Job 状态机语义）
    record = await _runner(request).submit(params, images)
    return AnimationSubmitResponse(job_id=record.job_id, status=record.status)


@router.get(
    "/{job_id}",
    response_model=AnimationStatusResponse,
    responses={status.HTTP_404_NOT_FOUND: {"description": "job 不存在"}},
)
async def get_animation(job_id: str, request: Request) -> AnimationStatusResponse:
    """查询任务当前状态；不存在返回 404，succeeded 时附产物清单与打包报告。"""
    store = _store(request)
    record = store.get_job(job_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"job 不存在：{job_id}"
        )

    outputs: list[OutputFile] = []
    anim_report: AnimPackReport | None = None
    if record.status == "succeeded":
        final: FinalOutputs | None = store.load_final_outputs(job_id)
        if final is not None:
            outputs = final.outputs
            anim_report = final.anim_report

    return AnimationStatusResponse(
        job_id=record.job_id,
        status=record.status,
        params=record.params,  # type: ignore[arg-type]  # JobParams union 由本路由语义保证
        created_at=record.created_at,
        started_at=record.started_at,
        finished_at=record.finished_at,
        error=record.error,
        outputs=outputs,
        anim_report=anim_report,
    )
