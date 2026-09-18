"""tilesets 路由（P3-L1）：提交 dual-grid tileset 合成任务 / 查询任务状态。

契约（任务书定稿）：
- POST /api/v1/tilesets（multipart: background + foreground 纹理 + payload JSON 字符串）
    → 202 {job_id, status}
- GET  /api/v1/tilesets/{job_id} → 状态响应（复用 image-edits/generations 的结构）

上传防线照抄 P2（image_edits 路由）：Content-Type/魔数双白名单（png/jpeg/webp）、
大小上限 settings.max_upload_bytes（默认 20MB，两文件分别限制）、payload pydantic
校验（extra=forbid）→ 非法 422；超过上限 413。
模式-纹理必填矩阵（原版 CLI 源码 submit_tileset_generator 语义照抄）：
foreground 只带前景、background 只带背景、dual 两者都要（多带/少带都是 422）。

编码规范：本层不 import 任何具体 Provider，只依赖协议；依赖实例（store/runner）
由 main.py 的 lifespan 装配到 app.state。
"""

import logging
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field, ValidationError

from server.app.api.image_edits import _sniff_image_format
from server.app.core.config import Settings
from server.app.core.storage import JobStore
from server.app.jobs.executor import TilesetJobRunner
from server.app.jobs.models import (
    FinalOutputs,
    JobStatus,
    OutputFile,
    TilesetParams,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/tilesets", tags=["tilesets"])

# 请求侧宽松 Content-Type 白名单（第一道廉价拦截；最终防线是魔数，与 P2 同口径）
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


class TilesetSubmitResponse(BaseModel):
    """提交成功（202）响应：任务已受理，后台执行。"""

    job_id: str
    status: JobStatus


class TilesetStatusResponse(BaseModel):
    """`GET /api/v1/tilesets/{job_id}` 响应（与 image-edits 状态模型同构）。"""

    job_id: str
    status: JobStatus
    params: TilesetParams
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = Field(default=None, description="失败原因（仅 failed 时非空）")
    outputs: list[OutputFile] = Field(default_factory=list, description="产物清单（succeeded 时）")


# ---------- 依赖获取（实例由 lifespan 装配在 app.state） ----------


def _store(request: Request) -> JobStore:
    return request.app.state.store


def _runner(request: Request) -> TilesetJobRunner:
    return request.app.state.tileset_runner


def _settings(request: Request) -> Settings:
    return request.app.state.settings


# ---------- 上传校验（两纹理共用 P2 同款防线） ----------


async def _read_texture(
    file: UploadFile, settings: Settings
) -> bytes:
    """单纹理上传防线：大小上限 413 / 空 422 / Content-Type 白名单 422 / 魔数 422。"""
    data = await file.read()
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"文件超过大小上限：{len(data)} > {settings.max_upload_bytes} 字节",
        )
    if not data:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"上传文件为空：{file.filename!r}",
        )
    content_type = (file.content_type or "").lower().split(";")[0].strip()
    if content_type not in _ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"不支持的 Content-Type：{content_type!r}（允许 png/jpeg/webp）",
        )
    if _sniff_image_format(data) is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"文件魔数不属于白名单格式（png/jpeg/webp）：{file.filename!r}",
        )
    return data


# ---------- 路由 ----------


@router.post(
    "",
    response_model=TilesetSubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_tileset(
    request: Request,
    payload: Annotated[str, Form(description="任务参数 JSON（terrain_mode + 可选 seed/feather）")],
    background: Annotated[UploadFile, File(description="背景材质纹理（必须 64×64）")],
    foreground: Annotated[UploadFile, File(description="前景材质纹理（必须 64×64）")],
) -> TilesetSubmitResponse:
    """受理 tileset 合成任务：双纹理上传校验 → 模式-纹理矩阵 → 落盘 pending → 后台执行。

    确定性纯函数合成（零 AI）：两张 64×64 无缝纹理 → 256×256 的 4×4 dual-grid 图集。
    """
    settings = _settings(request)

    # 1) 双纹理上传防线（大小/空/Content-Type/魔数）
    background_data = await _read_texture(background, settings)
    foreground_data = await _read_texture(foreground, settings)

    # 2) payload JSON → pydantic 契约校验
    try:
        params = TilesetParams.model_validate_json(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"payload 校验失败：{exc.error_count()} 处错误（{exc.errors()[0]['msg']}）",
        ) from exc

    # 3) 模式-纹理语义：HTTP 形态下两槽位必填（照抄 P2 上传模式），单地形模式
    # （foreground/background）下另一张仅过上传防线、不参与合成——合成器按模式
    # 取材，镂空图集不含另一侧颜色（测试锁定）。与原版 CLI 的 exact-match
    # （多传纹理直接报错）是有意偏差：HTTP 表单没有"省略文件槽位"的干净语义，
    # 强校验（64×64/魔数）对两张都生效比省一张更防呆。决策记录见 STATE.md。
    logger.debug(
        "tileset 受理 mode=%s bg=%dB fg=%dB",
        params.terrain_mode,
        len(background_data),
        len(foreground_data),
    )

    # 4) 受理：复用 Job 状态机（pending→running→succeeded|failed 语义不变）
    record = await _runner(request).submit(params, background_data, foreground_data)
    return TilesetSubmitResponse(job_id=record.job_id, status=record.status)


@router.get(
    "/{job_id}",
    response_model=TilesetStatusResponse,
    responses={status.HTTP_404_NOT_FOUND: {"description": "job 不存在"}},
)
async def get_tileset(job_id: str, request: Request) -> TilesetStatusResponse:
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

    return TilesetStatusResponse(
        job_id=record.job_id,
        status=record.status,
        params=record.params,  # type: ignore[arg-type]  # JobParams union 由本路由语义保证
        created_at=record.created_at,
        started_at=record.started_at,
        finished_at=record.finished_at,
        error=record.error,
        outputs=outputs,
    )
