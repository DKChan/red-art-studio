"""无缝纹理生成线四段流水线（P3-L2，调研文档 05 §3.1/§4）。

bytes→bytes 确定性纯函数（铁律 #3），provider 无关：provider 只负责出大图，
归一化/无缝化/平铺预览/检缝/等距投影全部在本模块完成，四段各自可独立测试：

1. normalize_texture —— 任意尺寸 → NEAREST 降采样到 64×64（像素纪律，禁平滑
   采样），可选调色板量化（≤32 色，默认关）；
2. self_loop         —— 复用 P2 processors.self_loop（four_way，双轴无缝化），
   不重写；
3. tiling_preview    —— 64×64 → 3×3 平铺 192×192。官方只实证 tiling_preview
   产物的存在（原版 CLI 源码 L1618/L1627 tiling_preview_path 字段），3×3 铺法
   与 192×192 尺寸是推断约定（官方未公开预览图规格），在此与 STATE.md 标注；
4. seam_report       —— 对 3×3 平铺图做相邻列/行最大通道跳变全图扫描（P2
   _max_neighbor_step 同思路），阈值 <6 与 P2 一致。检缝不过不判 failed：
   指标如实进产物（final_outputs.json seam_report），是否重生成由用户决定。

等距投影（isometric_project）：调研文档 05 §3.2 四路线中选 C「平面生成 +
投影变换」（标注推断）——材质纹理无立体装饰物，后投影不露馅，且零模型依赖；
画布尺寸取 map_layout 像素等距常量（底座菱形 128×64），中心锚定契约不变：
图片中心=逻辑格中心，四角透明；相邻瓦片按 (±64, 32) 中心偏移摆放即无缝拼接。
"""

import logging
from dataclasses import dataclass
from typing import NamedTuple

from PIL import Image

from server.app.core.map_layout import PIXEL_ISOMETRIC_DIAMOND_SIZE
from server.app.core.processors import ImageEditError, _decode, _encode, self_loop

logger = logging.getLogger(__name__)

# 正图契约尺寸（原版 texture-gen 输出严格 64×64）
_TEXTURE_SIZE = 64
# tiling_preview 铺贴次数（3×3 = 192×192，推断约定，见模块 docstring）
_PREVIEW_REPEATS = 3
# 检缝阈值：相邻像素最大通道绝对跳变 <6 视为无缝（与 P2 self_loop 验收同口径）
_SEAM_STEP_THRESHOLD = 6
# 调色板量化色数上限（像素纪律，与 P2 pixelate 同口径）
_QUANTIZE_COLORS = 32


class TexturePipelineError(ImageEditError):
    """纹理流水线输入非法（ValueError 家族，上层统一映射 422/failed）。"""


class SeamMetrics(NamedTuple):
    """检缝报告数值（jobs/models.SeamReport 是它的持久化契约形态）。"""

    horizontal_max_step: int
    vertical_max_step: int
    passed: bool


@dataclass(frozen=True)
class TexturePipelineResult:
    """一次纹理流水线的全部产物（PNG 字节 + 检缝数值）。"""

    texture_png: bytes
    tiling_preview_png: bytes
    seam_metrics: SeamMetrics
    isometric_texture_png: bytes | None = None

    def as_files(self) -> list[tuple[str, bytes, str]]:
        """产物 → 落盘清单 [(文件名, 字节, 格式)]；isometric 可选。"""
        files = [
            ("texture.png", self.texture_png, "png"),
            ("tiling_preview.png", self.tiling_preview_png, "png"),
        ]
        if self.isometric_texture_png is not None:
            files.append(("isometric_texture.png", self.isometric_texture_png, "png"))
        return files


# ---------- 通用 ----------


def _decode_checked(data: bytes) -> Image.Image:
    """字节 → RGBA 图像；无法解码抛 TexturePipelineError（而非裸 PIL 异常）。"""
    try:
        return _decode(data)
    except Exception as exc:
        raise TexturePipelineError(f"图像无法解码：{exc}") from exc


# ---------- 1) normalize ----------


def normalize_texture(data: bytes, quantize: bool = False) -> bytes:
    """任意尺寸输入 → NEAREST 采样到 64×64 PNG（像素纪律：禁平滑采样）。

    小于 64 的输入按 NEAREST 放大（「任意尺寸归一」语义；像素风放大不留灰边）。
    quantize=True 追加 FASTOCTREE 调色板量化（≤32 色，与 P2 pixelate 同口径）。
    """
    img = _decode_checked(data)
    resized = img.resize((_TEXTURE_SIZE, _TEXTURE_SIZE), Image.Resampling.NEAREST)
    if quantize:
        resized = resized.quantize(
            colors=_QUANTIZE_COLORS, method=Image.Quantize.FASTOCTREE
        ).convert("RGBA")
    return _encode(resized)


# ---------- 3) tiling_preview ----------


def tiling_preview(data: bytes, repeats: int = _PREVIEW_REPEATS) -> bytes:
    """64×64 纹理 → repeats×repeats 平铺预览 PNG（3×3 = 192×192，推断约定）。

    直接逐格 paste（零采样、零插值），平铺图上肉眼可查的缝即真实接缝。
    """
    if repeats < 1:
        raise TexturePipelineError(f"repeats 必须 ≥1：{repeats}")
    img = _decode_checked(data)
    w, h = img.size
    out = Image.new("RGBA", (w * repeats, h * repeats), (0, 0, 0, 0))
    for row in range(repeats):
        for col in range(repeats):
            out.paste(img, (col * w, row * h))
    return _encode(out)


# ---------- 4) seam_report ----------


def _max_neighbor_step(img: Image.Image, axis: int) -> int:
    """全图扫描：相邻列（axis=0）/相邻行（axis=1）的最大通道绝对跳变。

    P2 test_processors._max_neighbor_step 的生产化版本（思路照抄）：只查首尾
    列/行的旧断言测不到藏在中央的接缝硬边，必须全图扫描。
    """
    w, h = img.size
    pixels = img.load()
    worst = 0
    if axis == 0:
        for y in range(h):
            for x in range(w - 1):
                a, b = pixels[x, y], pixels[x + 1, y]
                worst = max(worst, max(abs(a[i] - b[i]) for i in range(len(a))))
    else:
        for y in range(h - 1):
            for x in range(w):
                a, b = pixels[x, y], pixels[x, y + 1]
                worst = max(worst, max(abs(a[i] - b[i]) for i in range(len(a))))
    return worst


def seam_report(preview_data: bytes) -> SeamMetrics:
    """对 3×3 平铺图做双轴跳变扫描 → 检缝数值（阈值 <6，与 P2 一致）。

    检缝不过不判 failed：数值如实上报，是否重生成由用户决定（任务书语义）。
    """
    preview = _decode_checked(preview_data)
    horizontal = _max_neighbor_step(preview, axis=0)
    vertical = _max_neighbor_step(preview, axis=1)
    metrics = SeamMetrics(
        horizontal_max_step=horizontal,
        vertical_max_step=vertical,
        passed=horizontal < _SEAM_STEP_THRESHOLD and vertical < _SEAM_STEP_THRESHOLD,
    )
    logger.debug(
        "检缝：horizontal=%d vertical=%d passed=%s",
        metrics.horizontal_max_step,
        metrics.vertical_max_step,
        metrics.passed,
    )
    return metrics


# ---------- 等距投影（2:1 仿射） ----------


def isometric_project(texture_data: bytes) -> bytes:
    """64×64 平面无缝纹理 → 128×64 等距材质瓦片 PNG（确定性 2:1 逆仿射采样）。

    正向投影：纹理 (u,v)∈[0,1]² → 屏幕 x=(u−v)·64+64、y=(u+v)·32（菱形四顶点
    恰为画布 (64,0)/(128,32)/(64,64)/(0,32)）。本函数对每个输出像素中心做逆变换
    得 (u,v)，菱形内 NEAREST 采样源纹理（像素纪律），菱形外 alpha=0（四角透明）。
    画布尺寸取 map_layout.PIXEL_ISOMETRIC_DIAMOND_SIZE，不写魔数；源纹理无缝 ⇒
    相邻瓦片按 (±64, 32) 中心偏移摆放即无缝拼接。
    """
    texture = _decode_checked(texture_data)
    if texture.size != (_TEXTURE_SIZE, _TEXTURE_SIZE):
        raise TexturePipelineError(
            f"等距投影输入必须恰好 64×64，实际 {texture.width}×{texture.height}"
            "（禁止静默缩放）"
        )
    diamond_w, diamond_h = PIXEL_ISOMETRIC_DIAMOND_SIZE  # (128, 64)
    half_w, half_h = diamond_w / 2.0, diamond_h / 2.0
    src_w, src_h = texture.size
    src = texture.load()
    out = Image.new("RGBA", (diamond_w, diamond_h), (0, 0, 0, 0))
    dst = out.load()
    for y in range(diamond_h):
        # dy = u+v ∈ [0, 2]，dx = u−v ∈ [−1, 1]（像素中心采样）
        dy = (y + 0.5) / half_h
        for x in range(diamond_w):
            dx = (x + 0.5 - half_w) / half_w
            u = (dx + dy) / 2.0
            v = (dy - dx) / 2.0
            if 0.0 <= u < 1.0 and 0.0 <= v < 1.0:
                sx = min(int(u * src_w), src_w - 1)
                sy = min(int(v * src_h), src_h - 1)
                dst[x, y] = src[sx, sy]
    return _encode(out)


# ---------- 流水线编排 ----------


def run_texture_pipeline(
    provider_image: bytes,
    *,
    quantize: bool = False,
    isometric: bool = False,
) -> TexturePipelineResult:
    """provider 原图 → 归一化 → 双轴无缝化 → 平铺预览 + 检缝（→ 等距投影）。

    isometric=true 追加对无缝正图的 2:1 投影（先平面生成后变换，标注推断，
    见模块 docstring）。
    """
    texture = normalize_texture(provider_image, quantize=quantize)
    texture = self_loop(texture, direction="four_way")
    preview = tiling_preview(texture, repeats=_PREVIEW_REPEATS)
    metrics = seam_report(preview)
    iso = isometric_project(texture) if isometric else None
    logger.info(
        "纹理流水线完成 quantize=%s isometric=%s seam=(%d,%d,%s)",
        quantize,
        isometric,
        metrics.horizontal_max_step,
        metrics.vertical_max_step,
        metrics.passed,
    )
    return TexturePipelineResult(
        texture_png=texture,
        tiling_preview_png=preview,
        seam_metrics=metrics,
        isometric_texture_png=iso,
    )
