"""任务状态机与执行器。

状态流转：pending → running → succeeded | failed（各能力线共用同一状态机语义）。
执行器只依赖 JobStore / ProviderFactory 协议（编码规范：不见具体实现）；
生成线（P1 generations / P3-L2 textures）经 Provider 协议外呼，确定性处理线
（P2 后处理 / P3 tileset / P3-L2 流水线）在线程池里跑纯函数——CPU 秒级同步
操作，不需要轮询外部服务。
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

from server.app.core.anim_gen_pipeline import AnimGenResult, run_anim_gen_pipeline
from server.app.core.anim_pipeline import run_anim_pack_pipeline
from server.app.core.imaging import image_dimensions
from server.app.core.processors import (
    ImageEditError,
    Operation,
    pixelate,
    remove_background,
    self_loop,
)
from server.app.core.storage import JobStore
from server.app.core.texture_pipeline import run_texture_pipeline
from server.app.core.tileset_synth import synthesize_tileset
from server.app.core.ui_pipeline import resolution_size, run_ui_extract_pipeline, run_ui_pipeline
from server.app.jobs.models import (
    AnimateParams,
    AnimateReport,
    AnimationPackParams,
    AnimPackReport,
    ComponentEntry,
    ComponentsManifest,
    FinalOutputs,
    GenerationParams,
    ImageEditParams,
    JobRecord,
    LoopCheckReport,
    OutputFile,
    OverlapEntry,
    QualityGateReportModel,
    SeamReport,
    SpritesheetMetaModel,
    TextureParams,
    TilesetParams,
    UiExtractParams,
    UiGenParams,
    extension_for_format,
    utc_now_iso,
)
from server.app.providers.base import (
    GenerateImageRequest,
    Provider,
    ProviderError,
    ProviderFactory,
    ProviderRequestError,
    ProviderResponseError,
    ProviderTimeoutError,
)

logger = logging.getLogger(__name__)

# 后处理缺省参数（契约层 Optional 字段在此落地为算法层的缺省行为）
_DEFAULT_TOLERANCE = 32
# 后处理输出固定 PNG（保留 alpha；三件套语义都面向透明/像素资产）
_EDIT_OUTPUT_FORMAT = "png"
# 纹理线生成尺寸（provider 出大图，服务端归一到 64×64；见 texture_pipeline）
_TEXTURE_SOURCE_SIZE = "1024x1024"
# 纹理线可重试错误的最大重试次数（首次 + 2 次重试；pollinations 偶发 5xx/超时）
_TEXTURE_MAX_RETRIES = 2
# UI 生成线可重试错误的最大重试次数（与纹理线同语义：首次 + 2 次重试）
_UI_GEN_MAX_RETRIES = 2


class _JobRunnerBase:
    """共用任务状态机：登记 pending → 后台推进 running → succeeded|failed。

    两条能力线（生成/后处理）的执行器都继承本类，状态机推进与产物落盘
    骨架（_drive_job）只写一遍；差异只在"产出图像字节"这一步。
    """

    def __init__(self, store: JobStore) -> None:
        self._store = store
        # 持有强引用，避免事件循环对后台任务的弱引用导致任务被 GC（asyncio 文档要求）
        self._background: set[asyncio.Task[None]] = set()

    def _submit_record(
        self,
        params: GenerationParams
        | ImageEditParams
        | TextureParams
        | TilesetParams
        | UiGenParams
        | UiExtractParams
        | AnimationPackParams
        | AnimateParams,
    ) -> JobRecord:
        """登记 pending 任务记录（同步落盘），返回记录供后台执行体使用。"""
        record = JobRecord(
            job_id=uuid.uuid4().hex,
            params=params,  # type: ignore[arg-type]  # union 落盘由 pydantic 校验
            created_at=utc_now_iso(),
            updated_at=utc_now_iso(),
        )
        self._store.save_job(record)
        return record

    def _spawn(
        self, coro_factory: Callable[[], Coroutine[Any, Any, None]]
    ) -> None:
        """调度后台任务并持强引用（asyncio 对后台任务仅持弱引用，文档要求）。"""
        task = asyncio.create_task(coro_factory())
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _drive_job(
        self,
        record: JobRecord,
        produce: Callable[[], Awaitable[list[tuple[str, bytes, str]]]],
        seam_report_provider: Callable[[], SeamReport | None] | None = None,
        components_provider: Callable[[], ComponentsManifest | None] | None = None,
        anim_report_provider: Callable[[], AnimPackReport | None] | None = None,
        animate_report_provider: Callable[[], AnimateReport | None] | None = None,
    ) -> None:
        """后台执行体骨架：状态机推进 + 产物落盘 + 终态落盘。

        produce() 返回 awaitable，await 后得 [(文件名, 图像字节, 格式)]，由各执行器
        提供；任何异常归入 failed 分支，绝不让任务悬在 running。
        seam_report_provider / components_provider / anim_report_provider /
        animate_report_provider 在 produce() 成功后调用（惰性求值），返回非
        None 时随 final_outputs.json 交付（纹理线检缝 / UI 线组件分割数据 /
        动画打包线报告 / animate 生成线报告——数值都在 produce 内部产生，传值
        会在 produce 运行前求值而恒为空，P3-L2 实测教训）。
        """
        job_id = record.job_id
        try:
            record.status = "running"
            record.started_at = utc_now_iso()
            record.updated_at = utc_now_iso()
            self._store.save_job(record)

            outputs: list[OutputFile] = []
            for filename, data, fmt in await produce():
                width, height = image_dimensions(data, fmt)
                outputs.append(
                    OutputFile(filename=filename, width=width, height=height, format=fmt)
                )
                self._store.save_artifact(job_id, filename, data)

            final = FinalOutputs(job_id=job_id, created_at=utc_now_iso(), outputs=outputs)
            if seam_report_provider is not None:
                final.seam_report = seam_report_provider()
            if components_provider is not None:
                final.ui_components = components_provider()
            if anim_report_provider is not None:
                final.anim_report = anim_report_provider()
            if animate_report_provider is not None:
                final.animate_report = animate_report_provider()
            self._store.save_final_outputs(final)

            record.status = "succeeded"
            record.finished_at = utc_now_iso()
            record.updated_at = utc_now_iso()
            self._store.save_job(record)
            logger.info("任务成功 job_id=%s outputs=%d", job_id, len(outputs))
        except (ProviderError, ImageEditError) as exc:
            # 已知错误分支：保留异常类型名，便于区分重试策略
            self._fail(record, f"{type(exc).__name__}: {exc}")
        except Exception as exc:
            # 执行器兜底：任何意外异常都不得让任务悬在 running
            self._fail(record, f"unexpected: {type(exc).__name__}: {exc}")

    def _fail(self, record: JobRecord, message: str) -> None:
        """推进到 failed 终态并落盘（同步方法：无 await 点，不会被打断）。"""
        record.status = "failed"
        record.error = message
        record.finished_at = utc_now_iso()
        record.updated_at = utc_now_iso()
        self._store.save_job(record)
        logger.warning("任务失败 job_id=%s：%.500s", record.job_id, message)


class GenerationJobRunner(_JobRunnerBase):
    """生成任务执行器：调用 Provider 外部推理 API → 落盘产物 → 推进状态机。"""

    async def submit(self, provider: Provider, params: GenerationParams) -> JobRecord:
        """登记 pending 任务（同步落盘）并调度后台执行，立即返回。"""
        record = self._submit_record(params)
        self._spawn(lambda: self._run(provider, record))
        logger.info("任务已受理 job_id=%s provider=%s", record.job_id, params.provider)
        return record

    async def _run(self, provider: Provider, record: JobRecord) -> None:
        """生成线执行体：Provider 外呼由各 Provider 自己处理阻塞（任务书语义不变）。"""

        async def produce() -> list[tuple[str, bytes, str]]:
            result = await provider.generate_image(
                GenerateImageRequest(
                    prompt=record.params.prompt,
                    size=record.params.size,
                    n=record.params.n,
                )
            )
            return [
                (
                    f"{idx:03d}{extension_for_format(image.format)}",
                    image.data,
                    image.format,
                )
                for idx, image in enumerate(result.images)
            ]

        await self._drive_job(record, produce)


class ImageEditJobRunner(_JobRunnerBase):
    """后处理任务执行器（P2）：CPU 秒级纯函数在线程池跑完落盘，状态机语义不变。"""

    async def submit(self, params: ImageEditParams, image: bytes) -> JobRecord:
        """登记 pending 任务并调度线程池执行，立即返回（复用 202+轮询模式）。"""
        record = self._submit_record(params)
        self._spawn(lambda: self._run(params, image, record))
        logger.info(
            "后处理任务已受理 job_id=%s operation=%s", record.job_id, params.operation
        )
        return record

    async def _run(
        self, params: ImageEditParams, image: bytes, record: JobRecord
    ) -> None:
        """后处理执行体：纯 CPU 处理丢进线程池（不阻塞事件循环），
        状态机推进与产物落盘复用 _drive_job 骨架。
        """

        async def produce() -> list[tuple[str, bytes, str]]:
            result = await asyncio.to_thread(process_image_edit, params, image)
            return [("000.png", result, _EDIT_OUTPUT_FORMAT)]

        await self._drive_job(record, produce)


def process_image_edit(params: ImageEditParams, image: bytes) -> bytes:
    """按 params 分派三件套纯函数（Optional 缺省参数在此落地）。"""
    if params.operation == Operation.PIXELATE.value:
        return pixelate(image, pixel_size=params.pixel_size)
    if params.operation == Operation.REMOVE_BACKGROUND.value:
        return remove_background(
            image,
            source_background_color=params.source_background_color,
            tolerance=params.tolerance if params.tolerance is not None else _DEFAULT_TOLERANCE,
        )
    if params.operation == Operation.SELF_LOOP.value:
        # direction 契约层已保证非 None（self_loop 必填校验）
        assert params.direction is not None
        return self_loop(image, direction=params.direction)
    raise ImageEditError(f"未知 operation：{params.operation!r}")


class TilesetJobRunner(_JobRunnerBase):
    """tileset 合成任务执行器（P3-L1）：确定性纯函数在线程池跑完落盘。

    与 ImageEditJobRunner 同款线程池模式（CPU 秒级同步操作，不阻塞事件循环），
    状态机语义不变；差异只在输入是两张纹理、产出是 256×256 图集。
    """

    async def submit(
        self, params: TilesetParams, background: bytes, foreground: bytes
    ) -> JobRecord:
        """登记 pending 任务并调度线程池执行，立即返回（复用 202+轮询模式）。"""
        record = self._submit_record(params)
        self._spawn(lambda: self._run(params, background, foreground, record))
        logger.info(
            "tileset 任务已受理 job_id=%s terrain_mode=%s", record.job_id, params.terrain_mode
        )
        return record

    async def _run(
        self,
        params: TilesetParams,
        background: bytes,
        foreground: bytes,
        record: JobRecord,
    ) -> None:
        async def produce() -> list[tuple[str, bytes, str]]:
            result = await asyncio.to_thread(
                process_tileset, params, background, foreground
            )
            return [("000.png", result, _EDIT_OUTPUT_FORMAT)]

        await self._drive_job(record, produce)


def process_tileset(
    params: TilesetParams, background: bytes, foreground: bytes
) -> bytes:
    """按 params 调 tileset 合成纯函数（两模式输入参数固定语义：A=背景/B=前景）。"""
    try:
        return synthesize_tileset(
            background_texture=background,
            foreground_texture=foreground,
            terrain_mode=params.terrain_mode,
            seed=params.seed,
            feather_width=params.feather_width,
        )
    except ImageEditError:
        raise
    except ValueError as exc:
        # 纯函数层的裸 ValueError 归一为已知错误分支（保留类型名语义）
        raise ImageEditError(str(exc)) from exc


class TextureJobRunner(_JobRunnerBase):
    """无缝纹理生成任务执行器（P3-L2）：Provider 外呼（带重试）→ 线程池跑
    确定性流水线 → 落盘产物。

    重试语义：4xx（参数/凭证问题）立即 failed 不可重试；5xx/超时/网络错误
    可重试，最多再试 _TEXTURE_MAX_RETRIES 次（pollinations 无 key 档偶发
    5xx/超时，任务书验收条款）。
    """

    def __init__(self, store: JobStore, factory: ProviderFactory) -> None:
        super().__init__(store)
        self._factory = factory

    async def submit(self, params: TextureParams) -> JobRecord:
        """登记 pending 任务（同步落盘）并调度后台执行，立即返回。"""
        record = self._submit_record(params)
        self._spawn(lambda: self._run(params, record))
        logger.info(
            "纹理任务已受理 job_id=%s quantize=%s isometric=%s",
            record.job_id,
            params.quantize,
            params.isometric,
        )
        return record

    async def _generate_with_retry(self, params: TextureParams) -> bytes:
        """Provider 外呼 + 可重试分支：4xx 立即失败；5xx/超时重试至上限。"""
        provider = self._factory.get()
        last_error: Exception | None = None
        for attempt in range(1 + _TEXTURE_MAX_RETRIES):
            try:
                result = await provider.generate_image(
                    GenerateImageRequest(
                        prompt=params.prompt, size=_TEXTURE_SOURCE_SIZE, n=1
                    )
                )
                if not result.images:
                    raise ProviderResponseError("Provider 返回空图像列表")
                return result.images[0].data
            except ProviderRequestError:
                raise  # 4xx：参数/凭证问题，重试无效
            except (ProviderResponseError, ProviderTimeoutError, ProviderError) as exc:
                last_error = exc
                logger.warning(
                    "Provider 调用失败（第 %d/%d 次）：%s",
                    attempt + 1,
                    1 + _TEXTURE_MAX_RETRIES,
                    exc,
                )
        assert last_error is not None
        raise last_error

    async def _run(self, params: TextureParams, record: JobRecord) -> None:
        # 检缝数值在 produce 内部产生；_drive_job 在 produce 成功后经本闭包惰性取值
        metrics_holder: list[SeamReport] = []

        def _seam_report() -> SeamReport | None:
            return metrics_holder[0] if metrics_holder else None

        async def produce() -> list[tuple[str, bytes, str]]:
            raw = await self._generate_with_retry(params)
            # 确定性流水线是 CPU 秒级同步操作，丢线程池（不阻塞事件循环）
            result = await asyncio.to_thread(
                run_texture_pipeline,
                raw,
                quantize=params.quantize,
                isometric=params.isometric,
            )
            metrics_holder.append(
                SeamReport(
                    horizontal_max_step=result.seam_metrics.horizontal_max_step,
                    vertical_max_step=result.seam_metrics.vertical_max_step,
                    passed=result.seam_metrics.passed,
                )
            )
            return result.as_files()

        await self._drive_job(record, produce, seam_report_provider=_seam_report)


class AnimationPackJobRunner(_JobRunnerBase):
    """动画打包任务执行器（P5-L1）：确定性管线在线程池跑完落盘，零 provider 调用。

    与 UiGenJobRunner.submit_extract 同款模式：确定性纯函数（帧校验 → alpha
    路由 → 调色板统一 → 打包 + 循环检），无外呼可重试故重试语义不适用；管线
    非法输入归 ImageEditError 家族 → failed，任务终态不悬空。
    """

    async def submit(
        self, params: AnimationPackParams, frames: list[bytes]
    ) -> JobRecord:
        """登记 pending 任务并调度线程池执行，立即返回（复用 202+轮询模式）。"""
        record = self._submit_record(params)
        self._spawn(lambda: self._run(params, frames, record))
        logger.info(
            "动画打包任务已受理 job_id=%s format=%s frames=%d pixel=%s",
            record.job_id,
            params.output_format,
            len(frames),
            params.pixel,
        )
        return record

    async def _run(
        self, params: AnimationPackParams, frames: list[bytes], record: JobRecord
    ) -> None:
        # 打包报告数值在 produce 内部产生；_drive_job 在 produce 成功后经本闭包
        # 惰性取值（P3-L2 教训复用；None 时不写 final_outputs.anim_report 键）
        report_holder: list[AnimPackReport] = []

        def _anim_report() -> AnimPackReport | None:
            return report_holder[0] if report_holder else None

        async def produce() -> list[tuple[str, bytes, str]]:
            # 确定性管线是 CPU 秒级同步操作，丢线程池（不阻塞事件循环）
            result = await asyncio.to_thread(
                run_anim_pack_pipeline,
                frames,
                output_format=params.output_format,
                pixel=params.pixel,
                alpha_mode=params.alpha_mode,
                color_count=params.color_count,
                duration_ms=params.duration_ms,
                animation_type=params.animation_type,
            )
            report_holder.append(
                AnimPackReport(
                    frame_count=result.frame_count,
                    frame_size=result.frame_size,
                    columns=result.sheet_meta.columns if result.sheet_meta else None,
                    rows=result.sheet_meta.rows if result.sheet_meta else None,
                    duration_ms=params.duration_ms,
                    animation_type=params.animation_type,
                    pixel=params.pixel,
                    alpha_mode=result.alpha_mode,
                    loop_report=LoopCheckReport(
                        first_last_max_step=result.loop_metrics.first_last_max_step,
                        passed=result.loop_metrics.passed,
                    ),
                    sheet_meta=SpritesheetMetaModel(
                        frame_size=result.sheet_meta.frame_size,
                        columns=result.sheet_meta.columns,
                        rows=result.sheet_meta.rows,
                        frame_durations=list(result.sheet_meta.frame_durations),
                        loop=result.sheet_meta.loop,
                        animation_type=result.sheet_meta.animation_type,
                    )
                    if result.sheet_meta
                    else None,
                )
            )
            files: list[tuple[str, bytes, str]] = list(result.files)
            if params.output_format == "spritesheet":
                # 切图元数据也是产物（坑 7：无帧网格元数据的 sheet 引擎侧不可用）；
                # Content-Type json 映射 P4 已有先例
                files.append(
                    (
                        "spritesheet.json",
                        SpritesheetMetaModel(
                            frame_size=result.sheet_meta.frame_size,  # type: ignore[union-attr]  # spritesheet 分支 meta 恒非 None
                            columns=result.sheet_meta.columns,  # type: ignore[union-attr]
                            rows=result.sheet_meta.rows,  # type: ignore[union-attr]
                            frame_durations=list(result.sheet_meta.frame_durations),  # type: ignore[union-attr]
                            loop=result.sheet_meta.loop,  # type: ignore[union-attr]
                            animation_type=result.sheet_meta.animation_type,  # type: ignore[union-attr]
                        ).model_dump_json(indent=2).encode("utf-8"),
                        "json",
                    )
                )
            return files

        await self._drive_job(record, produce, anim_report_provider=_anim_report)


class UiGenJobRunner(_JobRunnerBase):
    """UI 生成任务执行器（P4-L1）：Provider 外呼（带重试）→ 线程池跑确定性
    管线（matte 色键去背 + alpha 连通域组件分割 + 门禁）→ 落盘产物。

    重试语义与 TextureJobRunner 相同：4xx（参数/凭证问题）立即 failed 不可重试；
    5xx/超时/网络错误可重试，最多再试 _UI_GEN_MAX_RETRIES 次（pollinations 无
    key 档偶发 5xx/超时，任务书验收条款）。
    """

    def __init__(self, store: JobStore, factory: ProviderFactory) -> None:
        super().__init__(store)
        self._factory = factory

    async def submit(self, params: UiGenParams) -> JobRecord:
        """登记 pending 任务（同步落盘）并调度后台执行，立即返回。"""
        record = self._submit_record(params)
        self._spawn(lambda: self._run(params, record))
        logger.info(
            "UI 生成任务已受理 job_id=%s resolution=%s aspect_ratio=%s remove_bg=%s split=%s",
            record.job_id,
            params.resolution,
            params.aspect_ratio,
            params.remove_background,
            params.split_components,
        )
        return record

    async def _generate_with_retry(self, params: UiGenParams, size: str) -> bytes:
        """Provider 外呼 + 可重试分支：4xx 立即失败；5xx/超时重试至上限。

        size 由 resolution 档位映射（resolution_size，推断约定）；prompt 只透传。
        quality 契约面参数位**不进 provider 请求**（本地 provider 无质量档，
        见 UiGenParams docstring）。
        """
        provider = self._factory.get()
        last_error: Exception | None = None
        for attempt in range(1 + _UI_GEN_MAX_RETRIES):
            try:
                result = await provider.generate_image(
                    GenerateImageRequest(prompt=params.prompt, size=size, n=1)
                )
                if not result.images:
                    raise ProviderResponseError("Provider 返回空图像列表")
                return result.images[0].data
            except ProviderRequestError:
                raise  # 4xx：参数/凭证问题，重试无效
            except (ProviderResponseError, ProviderTimeoutError, ProviderError) as exc:
                last_error = exc
                logger.warning(
                    "Provider 调用失败（第 %d/%d 次）：%s",
                    attempt + 1,
                    1 + _UI_GEN_MAX_RETRIES,
                    exc,
                )
        assert last_error is not None
        raise last_error

    async def _run(self, params: UiGenParams, record: JobRecord) -> None:
        # 组件分割数据在 produce 内部产生；_drive_job 在 produce 成功后经本闭包
        # 惰性取值（None 时不写 final_outputs.ui_components 键，序列化省略）
        manifest_holder: list[ComponentsManifest] = []

        def _components() -> ComponentsManifest | None:
            return manifest_holder[0] if manifest_holder else None

        async def produce() -> list[tuple[str, bytes, str]]:
            # 坑 2 优先级关系（官方 L5181 语义）：remove_background=false 时强制
            # 覆盖去背档位为 none——本服务只有色键一档，等价表达为 remove_bg=False
            effective_remove_bg = params.remove_background
            size = resolution_size(params.resolution, params.aspect_ratio)
            raw = await self._generate_with_retry(params, f"{size[0]}x{size[1]}")
            # 确定性管线是 CPU 秒级同步操作，丢线程池（不阻塞事件循环）
            result = await asyncio.to_thread(
                run_ui_pipeline,
                raw,
                matte_color=params.background_color,
                remove_bg=effective_remove_bg,
                split=params.split_components,
            )
            if result.has_components:
                manifest_holder.append(
                    ComponentsManifest(
                        components=[
                            ComponentEntry(
                                id=c.id, label=c.label, bbox=c.bbox, area_px=c.area_px
                            )
                            for c in result.components
                        ],
                        gate=QualityGateReportModel(
                            component_count=result.gate.component_count,
                            passed=result.gate.passed,
                            giant_components=list(result.gate.giant_components),
                            overlaps=[
                                OverlapEntry(
                                    a_label=p.a_label,
                                    b_label=p.b_label,
                                    ratio=p.ratio,
                                )
                                for p in result.gate.overlaps
                            ],
                        ),
                        actual_size=result.actual_size,
                    )
                )
            files: list[tuple[str, bytes, str]] = [("sheet.png", result.sheet_png, "png")]
            if manifest_holder:
                # components.json 也是产物（进 final_outputs 清单才可经 artifacts
                # 端点取回）；format="json" 非图像格式，image_dimensions 对未知
                # 格式返回 (0,0)——JSON 无像素尺寸，0 值即诚实语义，实际尺寸在
                # manifest.actual_size 里
                files.append(
                    (
                        "components.json",
                        manifest_holder[0].model_dump_json(indent=2).encode("utf-8"),
                        "json",
                    )
                )
            return files

        await self._drive_job(record, produce, components_provider=_components)

    async def submit_extract(
        self, params: UiExtractParams, images: list[bytes]
    ) -> JobRecord:
        """受理 extract 任务（P4-L2）：登记 pending 并调度线程池执行，立即返回。

        与 submit 的差异：extract 是**确定性管线**（色键去背 + alpha 连通域 +
        shelf 装箱），零 provider 调用——线程池模式同 TilesetJobRunner，重试
        语义自然不适用（无外呼可重试；管线非法输入归 ImageEditError 家族 →
        failed，与 P2/P3 确定性线同分支）。
        """
        record = self._submit_record(params)
        self._spawn(lambda: self._run_extract(params, images, record))
        logger.info(
            "UI 提取任务已受理 job_id=%s images=%d matte=%s",
            record.job_id,
            len(images),
            params.background_color or "auto-scan",
        )
        return record

    async def _run_extract(
        self, params: UiExtractParams, images: list[bytes], record: JobRecord
    ) -> None:
        # 组件分割数据在 produce 内部产生；经闭包惰性取值（P3-L2 教训复用）
        manifest_holder: list[ComponentsManifest] = []

        def _components() -> ComponentsManifest | None:
            return manifest_holder[0] if manifest_holder else None

        async def produce() -> list[tuple[str, bytes, str]]:
            # 确定性管线是 CPU 秒级同步操作，丢线程池（不阻塞事件循环）
            result = await asyncio.to_thread(
                run_ui_extract_pipeline,
                images,
                matte_color=params.background_color,
            )
            manifest_holder.append(
                ComponentsManifest(
                    components=[
                        ComponentEntry(
                            id=i,
                            label=c.label,
                            bbox=c.bbox,
                            area_px=c.area_px,
                            source_index=c.source_index,
                            source_bbox=c.source_bbox,
                        )
                        for i, c in enumerate(result.components)
                    ],
                    gate=QualityGateReportModel(
                        component_count=result.gate.component_count,
                        passed=result.gate.passed,
                        giant_components=list(result.gate.giant_components),
                        overlaps=[
                            OverlapEntry(
                                a_label=p.a_label,
                                b_label=p.b_label,
                                ratio=p.ratio,
                            )
                            for p in result.gate.overlaps
                        ],
                    ),
                    actual_size=result.actual_size,
                )
            )
            # extract 无 provider 参与：无重试语义；产物形态与 generate 线一致
            #（一张透明聚合表 + components.json，坑 1 硬契约：不导出单组件裁剪）
            return [
                ("sheet.png", result.sheet_png, "png"),
                (
                    "components.json",
                    manifest_holder[0].model_dump_json(indent=2).encode("utf-8"),
                    "json",
                ),
            ]

        await self._drive_job(record, produce, components_provider=_components)


class AnimateJobRunner(_JobRunnerBase):
    """animate 生成任务执行器（P5-L2）：provider 逐帧文生图（带重试）→ 线程池
    跑 L1 打包管线 → 落盘产物。

    重试语义照纹理线（4xx 不重试 / 5xx·超时·网络错误重试至上限），但作用于
    **整帧序列级别**：任一帧耗尽重试即整单 failed，不做帧级部分重试——非确定
    性 provider 下拼接重生成帧会引入帧间风格断层（确定性 seed 序列下整串重试
    已完成帧原样复现，语义等价且实现简单）。
    """

    # 与纹理线同语义：首次 + 2 次重试（pollinations 无 key 档偶发 5xx/超时）
    _ANIMATE_MAX_RETRIES = _TEXTURE_MAX_RETRIES

    def __init__(self, store: JobStore, factory: ProviderFactory) -> None:
        super().__init__(store)
        self._factory = factory

    async def submit(self, params: AnimateParams) -> JobRecord:
        """登记 pending 任务（同步落盘）并调度后台执行，立即返回。"""
        record = self._submit_record(params)
        self._spawn(lambda: self._run(params, record))
        logger.info(
            "animate 任务已受理 job_id=%s frames=%d size=%s type=%s seed=%d",
            record.job_id,
            params.frame_count,
            params.size,
            params.animation_type,
            params.seed,
        )
        return record

    async def _run(self, params: AnimateParams, record: JobRecord) -> None:
        # 生成报告数值在 produce 内部产生；_drive_job 在 produce 成功后经本闭包
        # 惰性取值（P3-L2 教训复用；None 时不写 final_outputs.animate_report 键）
        report_holder: list[AnimateReport] = []

        def _animate_report() -> AnimateReport | None:
            return report_holder[0] if report_holder else None

        async def produce() -> list[tuple[str, bytes, str]]:
            result = await self._generate_with_retry(params)
            report_holder.append(_build_animate_report(params, result))
            files: list[tuple[str, bytes, str]] = list(result.pack.files)
            if params.output_format == "spritesheet":
                # 切图元数据也是产物（坑 7），形态与 L1 打包线完全一致
                meta = result.pack.sheet_meta
                assert meta is not None  # spritesheet 分支 L1 管线恒产出 meta
                files.append(
                    (
                        "spritesheet.json",
                        SpritesheetMetaModel(
                            frame_size=meta.frame_size,
                            columns=meta.columns,
                            rows=meta.rows,
                            frame_durations=list(meta.frame_durations),
                            loop=meta.loop,
                            animation_type=meta.animation_type,
                        ).model_dump_json(indent=2).encode("utf-8"),
                        "json",
                    )
                )
            return files

        await self._drive_job(record, produce, animate_report_provider=_animate_report)

    async def _generate_with_retry(self, params: AnimateParams) -> AnimGenResult:
        """整帧序列级 Provider 外呼 + 可重试分支：4xx 立即失败；5xx/超时重试至上限。

        重试时整个序列从头重生成（确定性 seed 序列下已完成帧原样复现）；管线
        非法输入（AnimGenPipelineError/AnimPipelineError，ValueError 家族）不在
        可重试集合，直接上抛归 failed（_drive_job 的 ImageEditError 分支）。
        """
        provider = self._factory.get()
        last_error: Exception | None = None
        for attempt in range(1 + self._ANIMATE_MAX_RETRIES):
            try:
                return await run_anim_gen_pipeline(
                    provider,
                    prompt=params.prompt,
                    animation_type=params.animation_type,
                    frame_count=params.frame_count,
                    size=params.size,
                    base_seed=params.seed,
                    output_format=params.output_format,
                    pixel=params.pixel,
                    alpha_mode=params.alpha_mode,
                    color_count=params.color_count,
                    duration_ms=params.duration_ms,
                )
            except ProviderRequestError:
                raise  # 4xx：参数/凭证问题，重试无效
            except (ProviderResponseError, ProviderTimeoutError, ProviderError) as exc:
                last_error = exc
                logger.warning(
                    "animate 帧序列生成失败（第 %d/%d 次）：%s",
                    attempt + 1,
                    1 + self._ANIMATE_MAX_RETRIES,
                    exc,
                )
        assert last_error is not None
        raise last_error


def _build_animate_report(params: AnimateParams, result: AnimGenResult) -> AnimateReport:
    """生成结果 → 报告契约（pack 报告 + 生成元数据；数值都在 produce 路径内产生）。"""
    pack = result.pack
    return AnimateReport(
        frame_count=params.frame_count,
        size=result.requested_size,
        seeds=result.seeds,
        frame_prompts=result.frame_prompts,
        animation_type=params.animation_type,
        pixel=params.pixel,
        alpha_mode=pack.alpha_mode,
        pack=AnimPackReport(
            frame_count=pack.frame_count,
            frame_size=pack.frame_size,
            columns=pack.sheet_meta.columns if pack.sheet_meta else None,
            rows=pack.sheet_meta.rows if pack.sheet_meta else None,
            duration_ms=params.duration_ms,
            animation_type=params.animation_type,
            pixel=params.pixel,
            alpha_mode=pack.alpha_mode,
            loop_report=LoopCheckReport(
                first_last_max_step=pack.loop_metrics.first_last_max_step,
                passed=pack.loop_metrics.passed,
            ),
            sheet_meta=SpritesheetMetaModel(
                frame_size=pack.sheet_meta.frame_size,
                columns=pack.sheet_meta.columns,
                rows=pack.sheet_meta.rows,
                frame_durations=list(pack.sheet_meta.frame_durations),
                loop=pack.sheet_meta.loop,
                animation_type=pack.sheet_meta.animation_type,
            )
            if pack.sheet_meta
            else None,
        ),
    )
