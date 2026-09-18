"""帧序列打包线核心管线（P5-L1，调研文档 02）。

N 张等尺寸静帧 → 确定性打包（spritesheet / 动画 WebP / 动画 GIF）+ 循环检
报告，bytes→bytes 纯函数（铁律 #3），provider 无关、零 AI 零新依赖（纯
Pillow；动画编码能力 2026-09-12 预检通过：save_all 多帧 WebP/GIF 编码 +
回读 n_frames 均正常，pillow 12.3.0）。

路线收敛（任务书定稿，ADR-001/ADR-005）：调研文档 §3/§4 的视频扩散生成路线
（时序视频模型类，一律排除）——本线只做**确定性打包**；provider 逐帧生成线
在 P5-L2 复用本模块。

行为锚点（官方语义照抄，调研文档 02）：
- 帧数必须为偶数（§2.1）；帧数域取经典线 4-16 与像素线 2-16 的**并集**
  2-16（偶数）——打包线同时服务两条源，并集最宽容（推断标注）；
- 像素动画画布任一轴 >256 拒绝（坑 6；该硬约束官方语义属像素线，pixel=false
  不设画布上限——soft/HD 打包线，推断决策）；
- 动画 WebP 恒循环播放（§2.3：loop 元数据恒开，loop=0）；
- alpha 双模式（坑 8）：sharp=二值化（与 P2/P4 前景判据 α≥128 同口径）/
  soft=保留半透明；alpha_mode 缺省按 pixel 路由（None + pixel=true → sharp，
  否则 soft——官方"像素默认 sharp"的路由复刻，推断标注），显式值覆盖路由；
- 帧间主体漂移治标（坑 2）：pixel=true 时逐帧调色板统一，以首帧调色板为准
  做 color_count 色量化；
- spritesheet 必须带帧网格元数据（坑 7：Aseprite/Unity 切图约定）→
  sheet.png + spritesheet.json（frame_size/columns/rows/frame_durations/
  loop/animation_type，pydantic 契约锁定）。

loop_report（§2.5）：idle/walk/run 官方要求末帧≈首帧——首帧 vs 末帧逐像素
最大通道跳变 <6 判 passed（阈值与 P2 检缝/P3 检缝同口径）。**报告数据不判
failed**（P3-L2 SeamReport 同哲学）：指标如实进产物，是否重打包由用户决定。
"""

import io
import logging
import math
from dataclasses import dataclass, field

from PIL import Image

from server.app.core.processors import (
    ImageEditError,
    _binarize_alpha,
    _decode,
    _encode,
)

logger = logging.getLogger(__name__)

# 帧数域（偶数约束单独校验）：经典线 4-16 ∪ 像素线 2-16 的并集（推断标注）
_FRAMES_MIN = 2
_FRAMES_MAX = 16
# 像素动画画布任一轴硬上限（坑 6；仅 pixel=true 时生效，见模块 docstring）
_PIXEL_CANVAS_MAX = 256
# loop_report 阈值：首末帧逐像素最大通道跳变 <6（与 P2/P3 检缝同口径）
_LOOP_STEP_THRESHOLD = 6
# 调色板统一缺省色数（坑 2 治标；官方 CLI 示例 --color-count 32，缺省推断）
_DEFAULT_COLOR_COUNT = 32
# 动图帧时长缺省（推断：官方原版帧动画 16 帧=2 秒 → 2000/16=125ms）
_DEFAULT_DURATION_MS = 125


class AnimPipelineError(ImageEditError):
    """动画打包管线输入非法（ValueError 家族，上层统一映射 422/failed）。"""


# ---------- 通用 ----------


def _decode_checked(data: bytes) -> Image.Image:
    """字节 → RGBA 图像；无法解码抛 AnimPipelineError（而非裸 PIL 异常）。"""
    try:
        return _decode(data)
    except Exception as exc:
        raise AnimPipelineError(f"帧图像无法解码：{exc}") from exc


# ---------- 1) 帧完整性校验 ----------


def validate_frames(frames: list[Image.Image], *, pixel: bool) -> None:
    """帧完整性校验（入口防线）：帧数域 / 偶数 / 同尺寸 / 像素画布上限。

    - 帧数 2-16 且为偶数（经典线 4-16 与像素线 2-16 的并集，推断标注）；
    - 所有帧同尺寸，禁静默缩放（tileset 先例：尺寸不一致是输入错误不是
      "顺手缩放"的机会）；
    - pixel=true 时画布任一轴 >256 拒绝（坑 6）；pixel=false 不设上限
      （soft/HD 打包线，推断决策：256 硬约束官方语义属像素线）。
    """
    count = len(frames)
    if count < _FRAMES_MIN:
        raise AnimPipelineError(
            f"动画至少需要 {_FRAMES_MIN} 帧，实际 {count} 帧"
        )
    if count > _FRAMES_MAX:
        raise AnimPipelineError(
            f"动画最多 {_FRAMES_MAX} 帧，实际 {count} 帧"
            "（帧数域为经典线 4-16 与像素线 2-16 的并集）"
        )
    if count % 2 != 0:
        raise AnimPipelineError(f"帧数必须为偶数（官方 §2.1），实际 {count} 帧")

    size = frames[0].size
    if size[0] < 1 or size[1] < 1:
        raise AnimPipelineError(f"帧尺寸非法：{size[0]}x{size[1]}")
    for idx, frame in enumerate(frames):
        if frame.size != size:
            raise AnimPipelineError(
                f"帧尺寸不一致（禁静默缩放）：第 0 帧 {size[0]}x{size[1]}，"
                f"第 {idx} 帧 {frame.size[0]}x{frame.size[1]}"
            )

    if pixel and max(size) > _PIXEL_CANVAS_MAX:
        raise AnimPipelineError(
            f"像素动画画布任一轴不得超过 {_PIXEL_CANVAS_MAX}px（坑 6 硬校验），"
            f"实际 {size[0]}x{size[1]}"
        )


# ---------- 2) alpha 双模式（坑 8） ----------


def resolve_alpha_mode(alpha_mode: str | None, pixel: bool) -> str:
    """alpha_mode 路由：显式值直通；None 时按 pixel 路由（pixel→sharp/否则 soft）。

    官方语义是"像素默认 sharp"（坑 8：半透明像素在引擎里发糊）；None →
    pixel=true 解析为 sharp、否则 soft 是对该路由的复刻（推断标注）。
    """
    if alpha_mode is not None:
        if alpha_mode not in ("soft", "sharp"):
            raise AnimPipelineError(f"alpha_mode 必须是 soft/sharp：{alpha_mode!r}")
        return alpha_mode
    return "sharp" if pixel else "soft"


def apply_alpha_mode(img: Image.Image, mode: str) -> Image.Image:
    """按模式处理 alpha：sharp=α≥128 二值化（完美像素口径）；soft=原样保留。"""
    if mode == "sharp":
        return _binarize_alpha(img)
    if mode == "soft":
        return img
    raise AnimPipelineError(f"未知 alpha 模式：{mode!r}")


# ---------- 3) 调色板统一（坑 2 治标：以首帧调色板为准量化） ----------


def _used_palette_colors(img: Image.Image, color_count: int) -> list[tuple[int, int, int]]:
    """提取首帧**可见像素**的实际用色作为统一调色板（≤color_count 时全取）。

    - 只统计 alpha>0 的像素：全透明像素的 RGB 无意义（Pillow 透明填充通常是
      黑），混进调色板会把异色吸向黑（2026-09-12 管线测试实证）；pixel=true
      时 alpha 已先经 sharp 二值化，alpha>0 即完全可见；
    - 首帧色数 ≤ color_count：全量取用（按出现频次降序），首帧自身零损失；
    - 首帧色数 > color_count：FASTOCTREE 量化到 color_count（与 P2 pixelate
      同方法），统计量化色在可见像素上的频次降序取基准；
    - 首帧全透明抛 AnimPipelineError（无可见内容可提取，如实报错）。
    """
    src_px = img.load()

    def _visible() -> list[tuple[int, int, int]]:
        visible: list[tuple[int, int, int]] = []
        for y in range(img.height):
            for x in range(img.width):
                if src_px[x, y][3] > 0:
                    visible.append((x, y))
        return visible

    if not 2 <= color_count <= 64:
        raise AnimPipelineError(f"color_count 超出取值域 [2, 64]：{color_count}")

    positions = _visible()
    if not positions:
        raise AnimPipelineError("首帧无可见像素，无法提取统一调色板")
    rgb_counts: dict[tuple[int, int, int], int] = {}
    for x, y in positions:
        r, g, b = src_px[x, y][:3]
        rgb_counts[(r, g, b)] = rgb_counts.get((r, g, b), 0) + 1
    if len(rgb_counts) <= color_count:
        return sorted(rgb_counts, key=lambda c: -rgb_counts[c])

    # 色数超出 color_count：量化后按可见像素上的频次降序取实际用色
    quantized = img.convert("RGB").quantize(colors=color_count, method=Image.Quantize.FASTOCTREE)
    quantized.load()
    palette = quantized.getpalette() or []
    q_px = quantized.load()
    idx_counts: dict[int, int] = {}
    for x, y in positions:
        idx = q_px[x, y]
        idx_counts[idx] = idx_counts.get(idx, 0) + 1
    return [
        tuple(palette[idx * 3 : idx * 3 + 3])
        for idx, _ in sorted(idx_counts.items(), key=lambda item: -item[1])
    ]


def _reference_palette_image(colors: list[tuple[int, int, int]]) -> Image.Image:
    """用给定颜色构造紧凑调色板图（quantize(palette=...) 的引用对象，P 模式）。

    空余槽位用**末位真实色**填充而非留默认黑：Pillow 调色板映射对全部 256 个
    槽位做最近邻搜索，黑色空槽会把异色吸到假黑（2026-09-12 预检实证：纯绿帧
    对红蓝调色板映射被黑槽吸走）——末色填充保证映射结果永远落在首帧真实色上。
    """
    ref = Image.new("P", (1, 1))
    flat: list[int] = []
    for color in colors:
        flat.extend(color)
    flat.extend(list(colors[-1]) * (256 - len(colors)))
    ref.putpalette(flat)
    return ref


def unify_palette(frames: list[Image.Image], color_count: int) -> list[Image.Image]:
    """逐帧调色板统一（坑 2 治标方案逐字落地）：以首帧调色板为准量化。

    对每帧 RGB 通道做定调色板映射（dither 关闭——抖动会引入调色板外的混合色），
    alpha 通道原样回贴。统一后各帧可见像素颜色集 ⊆ 首帧调色板（测试锁定）。
    """
    reference = _reference_palette_image(_used_palette_colors(frames[0], color_count))

    unified: list[Image.Image] = []
    for frame in frames:
        mapped = (
            frame.convert("RGB")
            .quantize(palette=reference, dither=Image.Dither.NONE)
            .convert("RGBA")
        )
        mapped.putalpha(frame.getchannel("A"))
        unified.append(mapped)
    logger.debug("调色板统一完成 color_count=%d", color_count)
    return unified


# ---------- 4) loop_report（报告数据，不判 failed） ----------


@dataclass(frozen=True)
class LoopCheckMetrics:
    """循环检数值（jobs/models.LoopCheckReport 是它的持久化契约形态）。"""

    first_last_max_step: int
    passed: bool


def loop_report(frames: list[Image.Image]) -> LoopCheckMetrics:
    """首帧 vs 末帧逐像素最大通道跳变（RGBA 全通道，思路同 P2 _max_neighbor_step）。

    idle/walk/run 官方要求末帧≈首帧（§2.5）；<6 判 passed（与 P2/P3 检缝同
    阈值）。**不判 failed**：指标如实进产物，是否重打包由用户决定。
    """
    if len(frames) < 2:
        raise AnimPipelineError("loop_report 至少需要 2 帧")
    first, last = frames[0], frames[-1]
    if first.size != last.size:
        raise AnimPipelineError(
            f"首末帧尺寸不一致：{first.size} vs {last.size}"
        )
    first_px, last_px = first.load(), last.load()
    worst = 0
    for y in range(first.height):
        for x in range(first.width):
            a, b = first_px[x, y], last_px[x, y]
            step = max(abs(a[i] - b[i]) for i in range(4))
            if step > worst:
                worst = step
    metrics = LoopCheckMetrics(
        first_last_max_step=worst, passed=worst < _LOOP_STEP_THRESHOLD
    )
    logger.debug(
        "循环检：first_last_max_step=%d passed=%s", metrics.first_last_max_step, metrics.passed
    )
    return metrics


# ---------- 5) spritesheet 装箱（坑 7：带引擎切图元数据） ----------


@dataclass(frozen=True)
class SheetMeta:
    """帧网格元数据（spritesheet.json 的数据源；pydantic 契约在 jobs/models）。"""

    frame_size: tuple[int, int]
    columns: int
    rows: int
    frame_durations: tuple[int, ...]
    animation_type: str
    loop: bool = True  # 官方 §2.3：最终 WebP 恒循环，元数据如实标注


def pack_spritesheet(
    frames: list[Image.Image], duration_ms: int, animation_type: str
) -> tuple[Image.Image, SheetMeta]:
    """确定性网格装箱：cols=⌈√n⌉、rows=⌈n/cols⌉，帧按提交序逐格排布。

    - 排布按提交序逐格（行优先）填充，末行空位保持全透明；
    - paste 不带 mask：网格格位互不重叠，整块直拷贝才能完整保留 soft 模式的
      半透明 alpha（带 alpha mask 会把半透明像素对透明底做混合，α 值被平方
      压低）；输出 cols*fw × rows*fh RGBA PNG；
    - 全确定性 → 同输入两次运行字节级一致（测试锁定）。
    """
    count = len(frames)
    columns = math.isqrt(count)
    if columns * columns < count:
        columns += 1
    rows = -(-count // columns)
    frame_w, frame_h = frames[0].size

    sheet = Image.new("RGBA", (columns * frame_w, rows * frame_h), (0, 0, 0, 0))
    for idx, frame in enumerate(frames):
        sheet.paste(frame, ((idx % columns) * frame_w, (idx // columns) * frame_h))

    meta = SheetMeta(
        frame_size=(frame_w, frame_h),
        columns=columns,
        rows=rows,
        frame_durations=tuple([duration_ms] * count),
        animation_type=animation_type,
    )
    logger.info(
        "spritesheet 装箱：%d 帧 → %dx%d 网格（%dx%d 画布）",
        count,
        columns,
        rows,
        sheet.width,
        sheet.height,
    )
    return sheet, meta


# ---------- 6) 动图编码 ----------


def encode_animation_webp(frames: list[Image.Image], duration_ms: int) -> bytes:
    """动画 WebP（无损）：loop=0 恒循环（官方 §2.3），主交付格式（RGBA 全保真）。"""
    buf = io.BytesIO()
    frames[0].save(
        buf,
        format="WEBP",
        save_all=True,
        append_images=frames[1:],
        duration=duration_ms,
        loop=0,
        lossless=True,
    )
    return buf.getvalue()


def encode_animation_gif(frames: list[Image.Image], duration_ms: int) -> bytes:
    """动画 GIF（兼容交付）：**GIF 仅 1-bit 透明**（格式局限，诚实标注）——
    半透明像素落盘时被二值化，主交付是 WebP；disposal=2 逐帧还原防止残影。"""
    buf = io.BytesIO()
    frames[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=duration_ms,
        loop=0,
        disposal=2,
        optimize=False,
    )
    return buf.getvalue()


# ---------- 编排 ----------


@dataclass(frozen=True)
class AnimationPackResult:
    """一次动画打包管线的全部产物：落盘清单 + 报告数值（spritesheet 线附网格元数据）。"""

    frame_count: int
    frame_size: tuple[int, int]
    alpha_mode: str  # 路由解析后的实际值（回显进报告）
    loop_metrics: LoopCheckMetrics
    files: list[tuple[str, bytes, str]] = field(default_factory=list)
    sheet_meta: SheetMeta | None = None


def run_anim_pack_pipeline(
    images: list[bytes],
    *,
    output_format: str,
    pixel: bool = False,
    alpha_mode: str | None = None,
    color_count: int | None = None,
    duration_ms: int = _DEFAULT_DURATION_MS,
    animation_type: str = "other",
) -> AnimationPackResult:
    """N 张静帧 → 帧完整性校验 →（pixel 纪律）→ 打包 + 循环检。

    - output_format：webp | gif | spritesheet（单选，官方 CLI 语义；三种各要
      一个 job）；
    - pixel=true 时按序执行像素纪律：alpha 路由处理 → 调色板统一（坑 2 治标，
      color_count 缺省 32）；pixel=false 仅执行显式指定的 alpha 模式（None →
      soft），不做量化与画布限制；
    - loop_report 对**处理后**的帧计算（描述交付物本身）。
    """
    if output_format not in ("webp", "gif", "spritesheet"):
        raise AnimPipelineError(
            f"output_format 必须是 webp/gif/spritesheet：{output_format!r}"
        )
    if not images:
        raise AnimPipelineError("帧序列为空")

    frames = [_decode_checked(data) for data in images]
    validate_frames(frames, pixel=pixel)

    resolved_alpha = resolve_alpha_mode(alpha_mode, pixel)
    frames = [apply_alpha_mode(frame, resolved_alpha) for frame in frames]
    if pixel:
        effective_color_count = (
            color_count if color_count is not None else _DEFAULT_COLOR_COUNT
        )
        frames = unify_palette(frames, effective_color_count)

    metrics = loop_report(frames)

    files: list[tuple[str, bytes, str]] = []
    sheet_meta: SheetMeta | None = None
    if output_format == "webp":
        files.append(
            ("animation.webp", encode_animation_webp(frames, duration_ms), "webp")
        )
    elif output_format == "gif":
        files.append(("animation.gif", encode_animation_gif(frames, duration_ms), "gif"))
    else:
        sheet, sheet_meta = pack_spritesheet(frames, duration_ms, animation_type)
        files.append(("sheet.png", _encode(sheet), "png"))

    logger.info(
        "动画打包完成 format=%s frames=%d size=%dx%d pixel=%s alpha=%s loop_step=%d",
        output_format,
        len(frames),
        frames[0].width,
        frames[0].height,
        pixel,
        resolved_alpha,
        metrics.first_last_max_step,
    )
    return AnimationPackResult(
        frame_count=len(frames),
        frame_size=frames[0].size,
        alpha_mode=resolved_alpha,
        loop_metrics=metrics,
        files=files,
        sheet_meta=sheet_meta,
    )
