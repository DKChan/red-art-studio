"""animate 生成线核心管线（P5-L2，调研文档 02）。

provider 逐帧文生图（prompt 帧序 + seed 序列）→ 复用 P5-L1 anim_pipeline
全套打包（spritesheet / 动图编码 / loop_report / 像素纪律 / 调色板统一）。

通道现实（诚实路线，ADR-001/ADR-005 约束下收敛）：无图生视频/图生图通道 →
provider 逐帧文生图。**帧间主体一致性有客观局限**（纯文生图无参考图条件，
逐帧漂移是路线 A 的固有痛点，调研文档 02 §3/坑 2），靠 prompt 模板（§2.6
纪律）+ 确定性 seed 序列 + pixel=true 时首帧调色板统一（坑 2 治标，L1 管线
pixel 分支原样执行）缓解——本模块如实标注局限，不假装修复。

prompt 纪律（§2.6 官方前置工作流 + §2.5 动作循环特性表）：
- prompt 只写动作：用户动作描述 + 帧序描述（"frame i of n" 式）+ locked
  camera + 循环要求 + 保持项（same character/silhouette/palette/hard edges）；
- 循环要求按 §2.5 表：idle/walk/run 必须循环（末帧≈首帧）；attack 回起手式；
  jump 循环可选、hit/defeated 非循环、other 无约定——后四者不加循环从句
  （§2.5 表的循环校验策略降级为 prompt 建议文案与 L1 loop_report 指标，
  不做硬校验——padding 同理不进参数面，见 AnimateParams docstring）；
- 模板原生英文组装（官方 --optimize-prompt 语义是"翻译成英文并改写为动画
  模型易懂的 prompt"，模板原生英文等价该语义；用户 prompt 原文透传嵌入，
  不做改写/增强）。

seed 序列（推断决策）：base_seed + i 线性递增——目的是确定性可复现
（pollinations seed 固定时输出 md5 一致，2026-09-07 实测）；"命名空间隔离
防相邻 seed 意外相关"属过度设计，不做。

帧尺寸决策（§2.6 无源图）：所有帧由同一 size 参数生成，天然同尺寸；provider
返回实际尺寸与请求不符时**拒绝**（诚实失败，禁静默缩放——tileset 先例；
L1 validate_frames 异尺寸校验作为打包段兜底）。

重试语义不在本层（照纹理线架构位次归执行器）：执行器在
run_anim_gen_pipeline 外层做**整帧序列级**重试（4xx 不重试 / 5xx·超时·
网络错误重试；不做帧级部分重试——非确定性 provider 下拼接重生成帧会引入
帧间风格断层），本层任一帧失败直接上抛（ProviderError 家族），半成品帧
序列无交付价值。
"""

import asyncio
import logging
from dataclasses import dataclass

from PIL import Image

from server.app.core.anim_pipeline import (
    AnimationPackResult,
    _decode_checked,
    run_anim_pack_pipeline,
)
from server.app.core.processors import ImageEditError, _encode
from server.app.providers.base import GenerateImageRequest, Provider

logger = logging.getLogger(__name__)

# 动图帧时长缺省（与 L1 同源推断：官方 16 帧=2 秒 → 125ms）
_DEFAULT_DURATION_MS = 125


class AnimGenPipelineError(ImageEditError):
    """animate 生成管线输入非法（ValueError 家族，上层统一映射 422/failed）。"""


# ---------- prompt 模板（§2.6 纪律 + §2.5 动作循环特性表） ----------

# 动作枚举 → 英文动作短语（八枚举映射；空串=other 无动作词，模板省略该段）
_ACTION_PHRASE = {
    "idle": "idle breathing",
    "walk": "walk cycle",
    "run": "run cycle",
    "jump": "jump",
    "attack": "attack swing",
    "hit": "hit reaction",
    "defeated": "defeat collapse",
    "other": "",
}

# 循环要求按 §2.5 表（仅"必须循环"与"回起手式"的动作给从句；jump 循环可选、
# hit/defeated 非循环、other 无约定 → 无从句）
_LOOP_CLAUSE = {
    "idle": "seamless loop, last frame identical to the first frame",
    "walk": "seamless loop, last frame identical to the first frame",
    "run": "seamless loop, last frame identical to the first frame",
    "attack": "action returns to the starting pose",
}

# 保持项（§2.6：prompt 里写明保持不变的东西——剪影/调色板/硬边）
_KEEP_CLAUSE = "same character design, same silhouette, same color palette, hard edges"


def build_frame_prompt(
    animation_type: str, user_prompt: str, frame_index: int, frame_count: int
) -> str:
    """组装第 frame_index 帧的生成 prompt（纯函数，独立可测）。

    帧序号对 prompt 用自然计数（1 起："frame 1 of 8"——对生成模型更直白）；
    seed 序列才是 0 基。用户 prompt 原文透传嵌入（不做改写）。
    """
    if animation_type not in _ACTION_PHRASE:
        raise AnimGenPipelineError(f"未知动作类型：{animation_type!r}")
    parts = [user_prompt.strip(), f"frame {frame_index + 1} of {frame_count}"]
    phrase = _ACTION_PHRASE[animation_type]
    if phrase:
        parts.append(f"{phrase} animation")
    parts.append("locked camera, fixed framing")
    loop_clause = _LOOP_CLAUSE.get(animation_type, "")
    if loop_clause:
        parts.append(loop_clause)
    parts.append(_KEEP_CLAUSE)
    return ", ".join(part for part in parts if part)


def build_frame_seeds(base_seed: int, frame_count: int) -> list[int]:
    """确定性 seed 序列：base_seed + i 线性递增（推断决策，见模块 docstring）。"""
    return [base_seed + i for i in range(frame_count)]


def _frame_prompts(animation_type: str, user_prompt: str, frame_count: int) -> list[str]:
    """逐帧 prompt 列表（生成与报告元数据共用同一来源，保证一致）。"""
    return [
        build_frame_prompt(animation_type, user_prompt, i, frame_count)
        for i in range(frame_count)
    ]


def parse_size(size: str) -> tuple[int, int]:
    """"宽x高" → (width, height)；格式非法抛 AnimGenPipelineError。

    取值域（64-2048）与 pixel 画布上限（≤256）由契约层校验（422 精确报文）；
    本层只解析格式。
    """
    try:
        width_str, height_str = size.lower().split("x", 1)
        return int(width_str), int(height_str)
    except ValueError as exc:
        raise AnimGenPipelineError(f"size 格式非法（应为 宽x高）：{size!r}") from exc


# ---------- 逐帧生成（串行纪律） ----------


async def generate_frames(
    provider: Provider,
    *,
    prompt: str,
    animation_type: str,
    frame_count: int,
    size: str,
    base_seed: int = 0,
) -> list[Image.Image]:
    """逐帧调用 provider 文生图（**串行 await，i=0..n-1**），返回解码后的帧序列。

    - 串行纪律：pollinations 无 key 档并发受限（与队列模式一致），逐帧顺序
      生成，不并发；
    - 每帧 bytes 经 L1 `_decode_checked` 解码（anim_pipeline 私有函数，任务书
      授权原样复用：RGBA 归一 + 解码失败归 AnimPipelineError，ValueError
      家族 → 执行器 failed 不悬空）；
    - 实际尺寸与请求不符时拒绝（诚实失败，禁静默缩放——见模块 docstring）；
    - 任一帧失败（ProviderError 家族）直接上抛整单失败，本层不做重试（见
      模块 docstring：整帧序列级重试归执行器）。
    """
    expected = parse_size(size)
    prompts = _frame_prompts(animation_type, prompt, frame_count)
    seeds = build_frame_seeds(base_seed, frame_count)

    frames: list[Image.Image] = []
    for i in range(frame_count):
        result = await provider.generate_image(
            GenerateImageRequest(prompt=prompts[i], size=size, n=1, seed=seeds[i])
        )
        if not result.images:
            raise AnimGenPipelineError(f"第 {i + 1} 帧生成返回空图像列表")
        frame = _decode_checked(result.images[0].data)
        if frame.size != expected:
            raise AnimGenPipelineError(
                f"第 {i + 1} 帧实际尺寸与请求不符：{frame.size[0]}x{frame.size[1]} "
                f"vs {expected[0]}x{expected[1]}（诚实失败，禁静默缩放）"
            )
        frames.append(frame)
        logger.debug("第 %d/%d 帧生成完成 seed=%d", i + 1, frame_count, seeds[i])
    return frames


# ---------- 打包（复用 L1 全套）与编排 ----------


@dataclass(frozen=True)
class AnimGenResult:
    """一次 animate 生成管线的全部产物：L1 打包结果 + 生成元数据（报告数据源）。"""

    pack: AnimationPackResult
    requested_size: tuple[int, int]
    frame_prompts: list[str]
    seeds: list[int]


def pack_generated_frames(
    frames: list[Image.Image],
    *,
    output_format: str,
    requested_size: tuple[int, int],
    frame_prompts: list[str],
    seeds: list[int],
    pixel: bool = False,
    alpha_mode: str | None = None,
    color_count: int | None = None,
    duration_ms: int = _DEFAULT_DURATION_MS,
    animation_type: str = "other",
) -> AnimGenResult:
    """生成帧 → L1 打包管线全套（帧校验/像素纪律/调色板统一/动图或 sheet + 循环检）。

    坑 2 治标（首帧调色板统一）由 L1 管线的 pixel 分支原样执行，本层不重复
    实现。帧 Image → PNG bytes 是复用 L1 bytes 契约的无损过桥（PNG 无损，
    确定性不破坏）；CPU 密集（量化/编码），由编排层丢线程池执行。
    """
    images = [_encode(frame) for frame in frames]
    pack = run_anim_pack_pipeline(
        images,
        output_format=output_format,
        pixel=pixel,
        alpha_mode=alpha_mode,
        color_count=color_count,
        duration_ms=duration_ms,
        animation_type=animation_type,
    )
    logger.info(
        "animate 打包完成 format=%s frames=%d size=%dx%d",
        output_format,
        pack.frame_count,
        pack.frame_size[0],
        pack.frame_size[1],
    )
    return AnimGenResult(
        pack=pack,
        requested_size=requested_size,
        frame_prompts=frame_prompts,
        seeds=seeds,
    )


async def run_anim_gen_pipeline(
    provider: Provider,
    *,
    prompt: str,
    animation_type: str,
    frame_count: int,
    size: str,
    base_seed: int = 0,
    output_format: str = "webp",
    pixel: bool = False,
    alpha_mode: str | None = None,
    color_count: int | None = None,
    duration_ms: int = _DEFAULT_DURATION_MS,
) -> AnimGenResult:
    """animate 管线编排：逐帧生成（I/O，原生 async）→ 打包（CPU，线程池）。

    执行器在**本函数外层**做整帧序列级重试（见模块 docstring）：重试时整个
    序列从头重生成（确定性 seed 下已完成帧原样复现），打包段只会在生成段
    全部成功后执行一次（打包错误非传输错误、不在可重试集合）。
    """
    frames = await generate_frames(
        provider,
        prompt=prompt,
        animation_type=animation_type,
        frame_count=frame_count,
        size=size,
        base_seed=base_seed,
    )
    return await asyncio.to_thread(
        pack_generated_frames,
        frames,
        output_format=output_format,
        requested_size=parse_size(size),
        frame_prompts=_frame_prompts(animation_type, prompt, frame_count),
        seeds=build_frame_seeds(base_seed, frame_count),
        pixel=pixel,
        alpha_mode=alpha_mode,
        color_count=color_count,
        duration_ms=duration_ms,
        animation_type=animation_type,
    )
