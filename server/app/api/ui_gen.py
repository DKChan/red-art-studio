"""ui_gen 路由（P4-L1 generate / P4-L2 extract）：提交任务 / 查询任务状态。

契约（任务书定稿）：
- POST /api/v1/ui_gen（JSON body: prompt 必填 + 档位/去背/分割可选）
    → 202 {job_id, status}（响应结构照抄 textures 线）
- POST /api/v1/ui_gen/extract（multipart: files[] 1-8 张 + payload JSON 串）
    → 202 {job_id, status}（L2：extract 模式提取重排）
- GET  /api/v1/ui_gen/{job_id} → 状态响应（复用 P1/P2/P3 结构）

与官方请求面的差异（诚实标注）：官方 generate/ui_extract 两模式归一在一个
请求字段 generation_mode 里（canonical 值 ui_extract，调研文档 06 §3.2）；本
HTTP 面用独立端点表达模式。extract 参考图数量是官方硬门禁（0 张抛错 L5198、
>8 张抛错 L5133），本路由在读文件内容前先挡（0/>8 → 422），无效请求不进管线。

quality 是契约面参数位：对齐官方 CLI 面保留取值（standard/detailed/ultimate，
官方映射 standard→low / detailed→medium / ultimate→high），但本地 provider 无
质量档，**实际不入 provider 请求**——请求快照（job.json params.quality）留档
该参数的原始取值，"未生效"事实在本 docstring 与 STATE.md 标注。

执行链：factory.get() → generate_image(size=resolution_size(...)) → decode →
（remove_background=true 时）matte 色键去背 →（split_components=true 时）alpha
连通域分割 + 门禁 → 原子写产物。extract 线零 provider 调用（确定性管线）。
prompt 只透传，服务端不做提示词改写/增强。

编码规范：本层不 import 任何具体 Provider，只依赖协议；依赖实例（store/runner）
由 main.py 的 lifespan 装配到 app.state。
"""

import logging
from typing import Annotated, Union

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field, ValidationError

from server.app.api.tilesets import _read_texture
from server.app.core.storage import JobStore
from server.app.jobs.executor import UiGenJobRunner
from server.app.jobs.models import (
    ComponentsManifest,
    FinalOutputs,
    JobStatus,
    OutputFile,
    UiExtractParams,
    UiGenParams,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/ui_gen", tags=["ui_gen"])

# extract 参考图数量硬门禁（官方 §3.2：0 张 L5198 抛错 / >8 张 L5133 抛错）
_EXTRACT_MIN_IMAGES = 1
_EXTRACT_MAX_IMAGES = 8


# ---------- 请求/响应契约 ----------


class UiGenCreateRequest(BaseModel):
    """`POST /api/v1/ui_gen` 请求体（字段与取值域见 UiGenParams docstring）。"""

    model_config = {"extra": "forbid"}

    prompt: str = Field(min_length=1, max_length=2000, description="生成提示词（只透传）")
    quality: str = Field(
        default="detailed",
        pattern=r"^(standard|detailed|ultimate)$",
        description="质量档位（契约面参数位；本地 provider 不消费）",
    )
    resolution: str = Field(
        default="2k", pattern=r"^(1k|2k)$", description="分辨率档位（官方默认 2K）"
    )
    aspect_ratio: str = Field(
        default="1:1", pattern=r"^(4:3|3:4|16:9|9:16|1:1)$", description="长宽比"
    )
    background_color: str = Field(
        default="#cccccc",
        pattern=r"^(#000000|#ffffff|#cccccc|#808080|#333333)$",
        description="matte 底色（官方五枚举）",
    )
    remove_background: bool = Field(
        default=True, description="false 时强制覆盖去背档位为 none（坑 2 优先级）"
    )
    split_components: bool = Field(
        default=True, description="false 仅跳过 components.json 生成（坑 9 假设）"
    )


class UiGenSubmitResponse(BaseModel):
    """提交成功（202）响应：任务已受理，后台执行。"""

    job_id: str
    status: JobStatus


class UiGenStatusResponse(BaseModel):
    """`GET /api/v1/ui_gen/{job_id}` 响应（与 P1/P2/P3 状态模型同构）。

    params 为 union：generate（UiGenParams）与 extract（UiExtractParams）两种
    job 形态都挂在本路由的 GET 下，按落盘 kind 区分。
    """

    job_id: str
    status: JobStatus
    params: Union[UiGenParams, UiExtractParams]
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = Field(default=None, description="失败原因（仅 failed 时非空）")
    outputs: list[OutputFile] = Field(default_factory=list, description="产物清单（succeeded 时）")
    ui_components: ComponentsManifest | None = Field(
        default=None, description="组件分割数据（succeeded 且 split_components=true 时）"
    )


# ---------- 依赖获取（实例由 lifespan 装配在 app.state） ----------


def _store(request: Request) -> JobStore:
    return request.app.state.store


def _runner(request: Request) -> UiGenJobRunner:
    return request.app.state.ui_gen_runner


# ---------- 路由 ----------


@router.post(
    "",
    response_model=UiGenSubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_ui_gen(body: UiGenCreateRequest, request: Request) -> UiGenSubmitResponse:
    """受理 UI 生成任务：落盘 pending 记录并调度后台执行，立即返回 202。"""
    record = await _runner(request).submit(
        UiGenParams(
            prompt=body.prompt,
            quality=body.quality,  # type: ignore[arg-type]  # pattern 已收敛取值域
            resolution=body.resolution,  # type: ignore[arg-type]
            aspect_ratio=body.aspect_ratio,  # type: ignore[arg-type]
            background_color=body.background_color,  # type: ignore[arg-type]
            remove_background=body.remove_background,
            split_components=body.split_components,
        )
    )
    return UiGenSubmitResponse(job_id=record.job_id, status=record.status)


@router.get(
    "/{job_id}",
    response_model=UiGenStatusResponse,
    responses={status.HTTP_404_NOT_FOUND: {"description": "job 不存在"}},
)
async def get_ui_gen(job_id: str, request: Request) -> UiGenStatusResponse:
    """查询任务当前状态；不存在返回 404，succeeded 时附产物清单与组件分割数据。

    generate 与 extract 两种 job 共用本端点（params 按落盘 kind 返回对应形态）。
    """
    store = _store(request)
    record = store.get_job(job_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"job 不存在：{job_id}"
        )

    outputs: list[OutputFile] = []
    ui_components: ComponentsManifest | None = None
    if record.status == "succeeded":
        final: FinalOutputs | None = store.load_final_outputs(job_id)
        if final is not None:
            outputs = final.outputs
            ui_components = final.ui_components

    return UiGenStatusResponse(
        job_id=record.job_id,
        status=record.status,
        params=record.params,  # type: ignore[arg-type]  # JobParams union 由本路由语义保证
        created_at=record.created_at,
        started_at=record.started_at,
        finished_at=record.finished_at,
        error=record.error,
        outputs=outputs,
        ui_components=ui_components,
    )


@router.post(
    "/extract",
    response_model=UiGenSubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_ui_extract(
    request: Request,
    payload: Annotated[
        str, Form(description="任务参数 JSON（background_color 可选；缺省自动扫描）")
    ],
    files: Annotated[
        list[UploadFile] | None, File(description="参考图 1-8 张（png/jpeg/webp）")
    ] = None,
) -> UiGenSubmitResponse:
    """受理 extract 提取重排任务：数量硬门禁 → 逐文件防线 → payload 校验 → 后台执行。

    数量防线（官方 §3.2 硬门禁：0 张 L5198 / 超 8 张 L5133 都抛错）在读文件
    内容**前**做——无效请求不进管线（官方语义：挡在 provider 前；本线确定性
    管线，同精神）。files 为 Optional 由本函数自行做 0 张检查（必填字段缺失时
    FastAPI 的 422 消息是泛化的 "Field required"，0 张是官方明确门禁，值得
    精确消息）。逐文件防线复用 tilesets 的 _read_texture（大小上限 413 / 空 /
    Content-Type / 魔数 422，与 P2/P3 上传防线同口径）。
    """
    # 1) 数量硬门禁（先于读内容：0 张/超量请求最廉价地挡下）
    count = len(files) if files else 0
    if count < _EXTRACT_MIN_IMAGES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"extract 至少需要 {_EXTRACT_MIN_IMAGES} 张参考图，实际 {count} 张",
        )
    if count > _EXTRACT_MAX_IMAGES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"extract 最多 {_EXTRACT_MAX_IMAGES} 张参考图，实际 {count} 张",
        )

    # 2) payload JSON → pydantic 契约校验（extra=forbid：未知参数 422）
    try:
        params = UiExtractParams.model_validate_json(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"payload 校验失败：{exc.error_count()} 处错误（{exc.errors()[0]['msg']}）",
        ) from exc

    # 3) 逐文件上传防线（大小/空/Content-Type/魔数，tilesets 同款）
    settings = request.app.state.settings
    images = [await _read_texture(file, settings) for file in files or []]
    logger.debug(
        "extract 受理 images=%d matte=%s", len(images), params.background_color or "auto-scan"
    )

    # 4) 受理：确定性管线零 provider 调用（同 Job 状态机语义）
    record = await _runner(request).submit_extract(params, images)
    return UiGenSubmitResponse(job_id=record.job_id, status=record.status)
