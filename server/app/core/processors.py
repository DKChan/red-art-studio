"""后处理三件套：去背（色键）/ 像素化 / 无缝循环（P2 Loop，调研文档 04）。

定位（铁律 #3）：全部是确定性图像处理纯函数（bytes + params → bytes），
不依赖 FastAPI / 存储 / Provider，零模型推理（去背仅色键抠像，模型分割路线被 ADR-001 排除）。

实现约束：P2 只允许新增 pillow 一个依赖（任务书约束 2），因此全部算法用纯 Pillow
原语实现（ImageChops.offset / point 查表 / paste(mask) / ImageStat），热路径都在 C 层。

产品语义（继承原版，调研文档 04 §1/§5）：
- 推荐顺序是先像素化、后去背（API 文档字符串注明，不强制）；
- 像素资产要求"完美像素"：alpha 二值化（α≥128 → 255，否则 0），不留半透明灰边（§5.1）；
- self_loop 是纯算法 offset-warp + 接缝带镜像融合（生成模型重绘接缝的混合管线不在 P2 范围）。
"""

import io
import logging
from enum import Enum

from PIL import Image, ImageChops, ImageStat

logger = logging.getLogger(__name__)

# 自动像素尺寸估计的候选范围与缺省回落值（任务书：4-64；失败回落 16）
_PIXEL_SIZE_MIN = 4
_PIXEL_SIZE_MAX = 64
_PIXEL_SIZE_FALLBACK = 16
# 粒度估计用降采样上限（估计不需要全分辨率，控制打分开销）
_ESTIMATE_MAX_SIDE = 512
# 调色板量化色数上限（调研文档 04 §3.2：像素画的关键是低色数）
_QUANTIZE_COLORS = 32
# 完美像素 alpha 二值化阈值（像素化与色键去背共用同一口径）
_ALPHA_BINARIZE_THRESHOLD = 128
# 接缝融合带宽度上限（像素）
_SEAM_BAND_MAX = 32


class ImageEditError(ValueError):
    """后处理输入非法（基类；上层统一映射 422）。"""


class PixelateError(ImageEditError):
    """像素化输入非法。"""


class SelfLoopError(ImageEditError):
    """无缝循环输入非法。"""


class Operation(str, Enum):
    """三件套操作名（API 契约定稿取值域）。"""

    PIXELATE = "pixelate"
    REMOVE_BACKGROUND = "remove_background"
    SELF_LOOP = "self_loop"


# ---------- 通用图像编解码 ----------


def _decode(data: bytes) -> Image.Image:
    """字节 → RGBA 图像（统一到 RGBA 工作空间）。"""
    img = Image.open(io.BytesIO(data))
    img.load()
    return img.convert("RGBA")


def _encode(img: Image.Image) -> bytes:
    """图像 → PNG 字节（无损保留 alpha）。"""
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _binarized_lut() -> tuple[int, ...]:
    """完美像素 alpha 查表：≥128 → 255，否则 0。"""
    return tuple(255 if v >= _ALPHA_BINARIZE_THRESHOLD else 0 for v in range(256))


_ALPHA_LUT = _binarized_lut()


def _binarize_alpha(img: Image.Image) -> Image.Image:
    """α≥128 → 255，否则 0（调研文档 04 §5.1 完美像素口径）。"""
    out = img.copy()
    out.putalpha(img.getchannel("A").point(_ALPHA_LUT))
    return out


# ---------- pixelate ----------


def estimate_pixel_size(img: Image.Image) -> int:
    """自动估计最佳像素粒度（4-64 候选打分；估计算法内部失败回落 16）。

    算法（调研文档 04 §3.2「块周期估计」）：对每个候选粒度 s，检验"把图按 s×s
    分块、每块压成平均色"的重建误差——块周期与 s 对齐时误差趋近 0。得分近乎最优
    的候选里取**最粗**的粒度：对真块状源图，所有细于真粒度的候选都同样无损，
    最粗者恰是真粒度；对连续调图（照片/渐变）则退回细粒度（保守，少毁细节）。
    已知失败模式与原版文档一致（§5.3 极简图误判过大 / 极复杂图过小），
    产品层留人工 review，本函数不静默承诺完美。
    """
    try:
        w, h = img.size
        if w == 0 or h == 0:
            raise PixelateError("图像尺寸为 0，无法估计像素粒度")
        # 估计用降采样图（保持比例）；粒度是比例结论，与绝对尺寸无关
        scale = min(1.0, _ESTIMATE_MAX_SIDE / max(w, h))
        if scale < 1.0:
            img = img.resize(
                (max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.BILINEAR
            )
        rgb = img.convert("RGB")
        candidates = range(_PIXEL_SIZE_MIN, _PIXEL_SIZE_MAX + 1)
        scores = [(_score_block_size(rgb, size), size) for size in candidates]
        best_score = max(score for score, _ in scores)
        # 近乎最优（相对 0.95）的候选里取最粗粒度（见上：块周期对齐判据）
        threshold = best_score * 0.95
        chosen = _PIXEL_SIZE_MIN
        for score, size in scores:
            if score >= threshold:
                chosen = size
        logger.debug(
            "自动像素粒度估计 size=%d（best_score=%.4f）", chosen, best_score
        )
        return chosen
    except Exception as exc:  # 打分过程的任何意外（退化输入等）都走回落，不让任务失败
        logger.warning("自动像素粒度估计失败，回落 %d：%s", _PIXEL_SIZE_FALLBACK, exc)
        return _PIXEL_SIZE_FALLBACK


def _score_block_size(rgb: Image.Image, size: int) -> float:
    """单个候选粒度的打分：分块平均色重建原图的保真度（1=无损，0=全丢）。"""
    w, h = rgb.size
    small_w, small_h = w // size, h // size  # 边缘残块丢弃
    if small_w == 0 or small_h == 0:
        return -1.0
    # BOX 降采样 = 每块取平均色；NEAREST 放回 = 用块平均色重建原图
    small = rgb.resize((small_w, small_h), Image.Resampling.BOX)
    rebuilt = small.resize((small_w * size, small_h * size), Image.Resampling.NEAREST)
    err = sum(
        ImageStat.Stat(
            ImageChops.difference(rgb.crop((0, 0, rebuilt.width, rebuilt.height)), rebuilt)
        ).mean
    ) / 3.0  # 三通道平均绝对差（0-255）
    return _exp_neg(err / 8.0)


def _exp_neg(x: float) -> float:
    """e^(-x)，x 异常时按饱和处理（防止 float 溢出影响打分）。"""
    if x > 50.0:
        return 0.0
    return pow(2.718281828459045, -x)


def pixelate(data: bytes, pixel_size: int | None = None) -> bytes:
    """像素化：NEAREST 降采样 → 调色板量化（≤32 色）→ alpha 二值化 → 整数倍回放，
    输出完美像素 PNG（每个逻辑像素是 pixel_size×pixel_size 实心方块）。

    pixel_size=None 走自动估计（estimate_pixel_size；估计算法内部失败回落 16）。
    尺寸不足一个粒度的边缘残块被裁掉（输出宽高 = ⌊w/p⌋·p × ⌊h/p⌋·p）。
    """
    img = _decode(data)
    if pixel_size is None:
        pixel_size = estimate_pixel_size(img)
    if not _PIXEL_SIZE_MIN <= pixel_size <= _PIXEL_SIZE_MAX:
        raise PixelateError(
            f"pixel_size 超出取值域 [{_PIXEL_SIZE_MIN}, {_PIXEL_SIZE_MAX}]：{pixel_size}"
        )

    # 1) NEAREST 降采样到逻辑像素网格（边缘残块裁掉，保证回放是整数倍）
    small_w, small_h = max(1, img.width // pixel_size), max(1, img.height // pixel_size)
    small = img.resize((small_w, small_h), Image.Resampling.NEAREST)

    # 2) 调色板量化（≤32 色）；FASTOCTREE 支持 RGBA（libimagequant 未随 Pillow 打包）
    quantized = small.quantize(colors=_QUANTIZE_COLORS, method=Image.Quantize.FASTOCTREE)

    # 3) alpha 二值化 → 完美像素；4) NEAREST 整数倍放回
    out = _binarize_alpha(quantized.convert("RGBA"))
    out = out.resize((small_w * pixel_size, small_h * pixel_size), Image.Resampling.NEAREST)
    return _encode(out)


# ---------- remove_background（色键抠像） ----------


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    """'#RRGGBB' → (r, g, b)；# 前缀必须携带（与 API pydantic pattern 同一口径），
    非法格式抛 ValueError（上层映射 422）。"""
    text = color.strip()
    if not text.startswith("#"):
        raise ValueError(f"颜色格式必须是 #RRGGBB：{color!r}")
    text = text[1:]
    if len(text) != 6:
        raise ValueError(f"颜色格式必须是 #RRGGBB：{color!r}")
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError as exc:
        raise ValueError(f"颜色格式必须是 #RRGGBB：{color!r}") from exc


def scan_background_color(img: Image.Image) -> tuple[int, int, int]:
    """四角扫描取众数底色：四角各取 min(w,h)//8（下限 1）见方的小窗，
    窗内全部像素计入计数（getcolors，C 层），跨角累加后取出现次数最多的颜色。
    """
    w, h = img.size
    win = max(1, min(w, h) // 8)
    boxes = [
        (0, 0, win, win),
        (w - win, 0, w, win),
        (0, h - win, win, h),
        (w - win, h - win, w, h),
    ]
    counts: dict[tuple[int, int, int], int] = {}
    for box in boxes:
        region = img.convert("RGB").crop(box)
        for count, color in region.getcolors(maxcolors=win * win) or []:
            counts[color] = counts.get(color, 0) + count
    if not counts:
        raise ImageEditError("四角扫描未取到任何像素")
    color = max(counts, key=counts.get)  # type: ignore[arg-type]
    logger.debug("四角扫描底色 #%02x%02x%02x（%d 像素）", *color, counts[color])
    return (color[0], color[1], color[2])


def _tolerance_lut(tolerance: int) -> tuple[int, ...]:
    """色距 → alpha 查表：≤tol 全透明、≥2×tol 全不透明，中间线性过渡（防硬边）。"""
    lut: list[int] = []
    for v in range(256):
        if tolerance <= 0:
            lut.append(0 if v == 0 else 255)
        elif v <= tolerance:
            lut.append(0)
        elif v >= 2 * tolerance:
            lut.append(255)
        else:
            lut.append((v - tolerance) * 255 // tolerance)
    return tuple(lut)


def remove_background(
    data: bytes,
    source_background_color: str | None = None,
    tolerance: int = 32,
) -> bytes:
    """色键抠像：底色色距 → alpha 渐变 → alpha 二值化。不做模型分割（铁律）。

    source_background_color 缺省时四角扫描取众数底色（scan_background_color）。
    色距取 RGB 各通道绝对差的最大值（切比雪夫距离，值域恰为 0-255，与 tolerance
    语义一致）；tolerance 是 0-255 的色距阈值。输出经 alpha 二值化，不留半透明灰边
    （调研文档 04 §5.2）。
    """
    if not 0 <= tolerance <= 255:
        raise ValueError(f"tolerance 超出取值域 [0, 255]：{tolerance}")
    img = _decode(data)
    if source_background_color is not None:
        bg = _hex_to_rgb(source_background_color)
    else:
        bg = scan_background_color(img)

    # 逐通道绝对差（ImageChops.difference，C 层）→ 通道最大值 = 色距灰度图
    solid = Image.new("RGBA", img.size, (*bg, 255))
    diff = ImageChops.difference(img, solid)
    dist = ImageChops.lighter(
        ImageChops.lighter(diff.getchannel("R"), diff.getchannel("G")), diff.getchannel("B")
    )

    out = img.copy()
    out.putalpha(dist.point(_tolerance_lut(tolerance)))
    return _encode(_binarize_alpha(out))


# ---------- self_loop（offset-warp + 接缝带镜像融合） ----------

# axis → (roll 偏移轴, 镜像 transpose, 融合带所在维)
_AXIS_HORIZONTAL = 0  # 水平循环：接缝是竖线，沿 x 卷动
_AXIS_VERTICAL = 1  # 垂直循环：接缝是横线，沿 y 卷动


def _seam_mask(length: int, band: int, horizontal: bool, other: int) -> Image.Image:
    """接缝融合权重图（Pillow paste(mask) 语义：255=完全取粘贴图（镜像），0=保留底图（rolled））。

    权重按「到接缝列对 (seam-1 | seam) 的距离」计算：d = min(|v-seam|, |v-(seam-1)|)，
    d < band 时 (band-d)*128//band，否则 0——接缝列对两侧 128 各半，随距离线性衰减，带外 0。

    为什么中心必须是 128（各半平均）：roll 后接缝两侧恰好互换值
    （mirror[seam-1] = rolled[seam]、mirror[seam] = rolled[seam-1]）。
    中心 0（历史 bug：原样保留底图）把 rolled 的全对比度硬缝原样留下；中心 255（全镜像）
    把硬缝原样镜像保留——两种极性都必然留下同幅度跳变，唯一能抹平缝的权重是
    中心 128 各半平均，把两侧值压成相等。
    """
    seam = length // 2

    def _weight(v: int) -> int:
        d = min(abs(v - seam), abs(v - (seam - 1)))
        return (band - d) * 128 // band if d < band else 0

    profile = [_weight(v) for v in range(length)]
    line = Image.new("L", (length, 1) if horizontal else (1, length))
    line.putdata(profile)
    size = (length, other) if horizontal else (other, length)
    return line.resize(size, Image.Resampling.NEAREST)


def self_loop(data: bytes, direction: str) -> bytes:
    """无缝循环：roll 半宽/半高把接缝移到中央 + 接缝带镜像融合，输出可平铺、尺寸不变。

    four_way = 先水平后垂直串行两遍（第二遍基于第一遍输出重新计算接缝带，
    调研文档 04 §5.5：避免水平接缝修好后垂直的又坏）。
    """
    passes = {
        "horizontal": (_AXIS_HORIZONTAL,),
        "vertical": (_AXIS_VERTICAL,),
        "four_way": (_AXIS_HORIZONTAL, _AXIS_VERTICAL),
    }
    if direction not in passes:
        raise SelfLoopError(f"direction 必须是 horizontal/vertical/four_way：{direction!r}")

    img = _decode(data)
    for axis in passes[direction]:
        length = img.width if axis == _AXIS_HORIZONTAL else img.height
        if length < 4:
            name = "水平" if axis == _AXIS_HORIZONTAL else "垂直"
            raise SelfLoopError(f"图像沿{name}方向尺寸过小（<4px），无法做无缝化")
        # 1) 环形滚动半程：原图的拼接缝（边界）移到中央（ImageChops.offset 即 roll）
        shift = (length // 2, 0) if axis == _AXIS_HORIZONTAL else (0, length // 2)
        rolled = ImageChops.offset(img, *shift)
        # 2) 镜像：沿接缝翻转。镜像与 rolled 在接缝处逐像素对称连续
        mirror = rolled.transpose(
            Image.Transpose.FLIP_LEFT_RIGHT
            if axis == _AXIS_HORIZONTAL
            else Image.Transpose.FLIP_TOP_BOTTOM
        )
        # 3) 接缝带内按线性权重融合（paste mask 逐通道线性混合，含 alpha）。
        # band ≤ length//2 保证接缝列对的权重可达 128；带内相邻列权重差 ≤128/band，
        # 实测渐变图全图列跳变需 <6（验收阈值），故 band 下限 2、上限放宽到 length//4
        # （128/band < 6 ⇒ band > 21，仅极短图会触碰下限）
        band = max(2, min(length // 4, _SEAM_BAND_MAX))
        # 融合带的另一维：水平接缝沿 y 全高；垂直时 rolled 高度≠宽度的非方图
        # 直接拿 height 当 mask 宽会尺寸不匹配（历史 bug）
        other = rolled.height if axis == _AXIS_HORIZONTAL else rolled.width
        mask = _seam_mask(length, band, axis == _AXIS_HORIZONTAL, other)
        blended = rolled.copy()
        blended.paste(mirror, (0, 0), mask)
        img = blended
    return _encode(img)
