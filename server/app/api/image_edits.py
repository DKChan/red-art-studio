"""image_edits 路由（P2 后处理三件套）：提交图片编辑任务 / 查询任务状态。

契约（任务书定稿）：
- POST /api/v1/image-edits（multipart: file + payload JSON 字符串）→ 202 {job_id, status}
- GET  /api/v1/image-edits/{job_id} → 状态响应（复用 generations 的状态模型结构）

上传防线（任务书约束 6）：Content-Type/魔数双白名单（png/jpeg/webp）、
大小上限 settings.max_upload_bytes（默认 20MB）、payload pydantic 校验，非法 422。
产品语义（调研文档 04 §1/§5）：推荐先像素化、后去背，本接口不强制顺序。

编码规范：本层不 import 任何具体 Provider，只依赖协议；依赖实例（store/runner）
由 main.py 的 lifespan 装配到 app.state。
"""

import logging
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field, ValidationError

from server.app.core.config import Settings
from server.app.core.storage import JobStore
from server.app.jobs.executor import ImageEditJobRunner
from server.app.jobs.models import (
    FinalOutputs,
    ImageEditParams,
    JobStatus,
    OutputFile,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/image-edits", tags=["image-edits"])

# 请求侧的宽松 Content-Type 白名单：浏览器/客户端对 image/png 等实际取值多样
# （image/jpg、image/pjpeg、application/octet-stream 都常见），此处只做第一道廉价
# 拦截；真正可信的判据是下方文件头魔数（内容自证，不可伪造提交表单语义）。
_ALLOWED_CONTENT_TYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/jpg",
        "image/pjpeg",
        "image/webp",
        "application/octet-stream",
    }
)

# 魔数 → 规范格式名（与 final_outputs.json 的 format 契约一致）
_MAGIC_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"RIFF", "webp"),  # webp 还需 8-12 字节为 WEBP，由 _sniff_image_format 复核
)


def _sniff_image_format(data: bytes) -> str | None:
    """文件头魔数白名单嗅探：png/jpeg/webp → 规范格式名，其余 None（非法输入）。

    只认字节不认 Content-Type：魔数校验是内容自证的最终防线。
    """
    for magic, fmt in _MAGIC_SIGNATURES:
        if data.startswith(magic):
            if fmt == "webp" and data[8:12] != b"WEBP":
                return None  # RIFF 容器但不是 WEBP
            return fmt
    return None


# ---------- 请求/响应契约 ----------


class ImageEditSubmitResponse(BaseModel):
    """提交成功（202）响应：任务已受理，后台执行。"""

    job_id: str
    status: JobStatus


class ImageEditStatusResponse(BaseModel):
    """`GET /api/v1/image-edits/{job_id}` 响应；succeeded 时含产物清单。

    结构复用 generations 的状态模型（status/outputs/error），params 换成后处理契约。
    """

    job_id: str
    status: JobStatus
    params: ImageEditParams
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = Field(default=None, description="失败原因（仅 failed 时非空）")
    outputs: list[OutputFile] = Field(default_factory=list, description="产物清单（succeeded 时）")


# ---------- 依赖获取（实例由 lifespan 装配在 app.state） ----------


def _store(request: Request) -> JobStore:
    return request.app.state.store


def _runner(request: Request) -> ImageEditJobRunner:
    return request.app.state.image_edit_runner


def _settings(request: Request) -> Settings:
    return request.app.state.settings


# ---------- 路由 ----------


@router.post(
    "",
    response_model=ImageEditSubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_image_edit(
    request: Request,
    payload: Annotated[str, Form(description="任务参数 JSON（operation + params 交叉校验）")],
    file: Annotated[UploadFile, File(description="待处理图片（PNG/JPEG/WebP，≤20MB)")],
) -> ImageEditSubmitResponse:
    """受理后处理任务：上传校验 → 落盘 pending 记录 → 后台线程池执行 → 202。

    推荐顺序是先 pixelate、后 remove_background（像素化先统一色块，色键底色
    距离判断更稳），接口不强制顺序。
    """
    settings = _settings(request)

    # 1) 大小上限（Content-Length 可伪造/缺失，以实读字节为准）
    data = await file.read()
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"文件超过大小上限：{len(data)} > {settings.max_upload_bytes} 字节",
        )
    if not data:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="上传文件为空"
        )

    # 2) Content-Type 宽松白名单（第一道拦截）+ 魔数硬校验（最终防线）
    content_type = (file.content_type or "").lower().split(";")[0].strip()
    if content_type not in _ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"不支持的 Content-Type：{content_type!r}（允许 png/jpeg/webp）",
        )
    if _sniff_image_format(data) is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="文件魔数不属于白名单格式（png/jpeg/webp）",
        )

    # 3) payload JSON → pydantic 契约校验（operation 与 params 交叉校验在模型内）
    try:
        params = ImageEditParams.model_validate_json(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"payload 校验失败：{exc.error_count()} 处错误（{exc.errors()[0]['msg']}）",
        ) from exc

    # 4) 受理：复用 Job 状态机（pending→running→succeeded|failed 语义不变）
    record = await _runner(request).submit(params, data)
    return ImageEditSubmitResponse(job_id=record.job_id, status=record.status)


@router.get(
    "/{job_id}",
    response_model=ImageEditStatusResponse,
    responses={status.HTTP_404_NOT_FOUND: {"description": "job 不存在"}},
)
async def get_image_edit(job_id: str, request: Request) -> ImageEditStatusResponse:
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

    return ImageEditStatusResponse(
        job_id=record.job_id,
        status=record.status,
        params=record.params,  # type: ignore[arg-type]  # JobParams union 由本路由语义保证
        created_at=record.created_at,
        started_at=record.started_at,
        finished_at=record.finished_at,
        error=record.error,
        outputs=outputs,
    )
