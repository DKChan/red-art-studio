"""dual-grid tileset 程序化合成（P3-L1，调研文档 05 §3.3 路线 A）。

bytes→bytes 确定性纯函数：两张 64×64 RGBA 无缝纹理（A=背景材质、B=前景材质）
→ 256×256 的 4×4 图集（64px dual-grid 格）。零 AI、零外部依赖。

合成算法（路线 A）：每格由 4 角地形位双线性插值出 [0,1] 连续场 + 带种子的周期
blob 噪声 → 阈值化得前景 coverage mask（带羽化的软 alpha）。三模式：
- dual：mask 内 B、mask 外 A（过渡带有 1px 级 alpha 羽化）；
- foreground：只保留 B 的 coverage 区域（mask 外 alpha=0，镂空图集）；
- background：反向镂空（mask 内 alpha=0，保留 A）。

正确性不变量（blob 噪声幅度受限保证，测试锁定）：key=0（全背景）与 key=15
（全前景）两格的 coverage 不受噪声扰动——dual 下恒为纯 A / 纯 B。

blob 噪声的周期性是正确性要求而非装饰：dual-grid 图集格在地图上以任意密度
重复摆放，相邻显示格共享同一噪声场的相邻采样窗口，场必须跨显示格边界连续，
blob 边缘才不会在格缝处跳变。整数频率正则晶格 + smoothstep 插值天然周期。
"""

import logging
import random

from PIL import Image, ImageChops

from server.app.core.map_layout import (
    DUAL_GRID_ATLAS_SIZE,
    DUAL_GRID_CELL_SIZE,
    dual_grid_atlas_cell,
)
from server.app.core.processors import ImageEditError, _decode, _encode

logger = logging.getLogger(__name__)

# terrain_mode 取值域（与原版 CLI 源码 tileset-gen 的前台三模式一致）
_TERRAIN_MODES = frozenset({"dual", "foreground", "background"})

# blob 噪声参数：幅度相对 [0,1] 场；基础晶格 4×4（blob 直径约 64/4=16px）+ 1 细节八度
_BLOB_AMPLITUDE = 0.18
_NOISE_BASE_CELLS = 4
_NOISE_OCTAVES = 2
# coverage 阈值（场空间 0-1）
_THRESHOLD = 0.5
# seed 取值域与羽化宽度域（pydantic 层同口径，纯函数层兜底强校验）
_SEED_MAX = 2**31 - 1
_FEATHER_MIN_PX = 0.5
_FEATHER_MAX_PX = 8.0


class TilesetError(ImageEditError):
    """tileset 合成输入非法（ValueError 家族，上层统一映射 422）。

    继承 ImageEditError 以共用执行器的已知错误分支（保留异常类型名）
    与 P2 确立的「确定性处理输入非法 → 422」错误语义。
    """


# ---------- 输入校验 ----------


def _decode_texture(data: bytes, label: str) -> Image.Image:
    """字节 → RGBA 图像；无法解码抛 TilesetError（而非裸 PIL 异常）。"""
    try:
        return _decode(data)
    except Exception as exc:
        raise TilesetError(f"{label} 纹理无法解码为图像：{exc}") from exc


def _validate_inputs(
    background: Image.Image, foreground: Image.Image
) -> None:
    """入口强校验（调研文档 05 §5.6 坑 6）：非 64×64 一律拒绝，禁止静默缩放。"""
    expected = (DUAL_GRID_CELL_SIZE, DUAL_GRID_CELL_SIZE)
    for label, img in (("background", background), ("foreground", foreground)):
        if img.size != expected:
            raise TilesetError(
                f"{label} 纹理必须恰好 64×64，实际 {img.width}×{img.height}"
                "（禁止静默缩放：源纹理一条缝会被图集和整张地图放大成处处缝）"
            )


# ---------- 周期 blob 噪声 ----------


def _noise_field(seed: int, size: int) -> list[float]:
    """带种子的周期值噪声场（[−1,1]，size×size，u/v 方向按晶格数回绕）。

    每八度一个独立随机场（smoothstep 插值的正则晶格值噪声），八度频率翻倍、
    幅度减半后叠加归一。确定性完全来自 seed（random.Random 固定迭代顺序），
    同 seed 同输出。
    """
    rng = random.Random(seed)
    field = [0.0] * (size * size)
    total_amplitude = 0.0
    for octave in range(_NOISE_OCTAVES):
        cells = _NOISE_BASE_CELLS << octave
        amplitude = 1.0 / (1 << octave)
        total_amplitude += amplitude
        lattice = [rng.uniform(-1.0, 1.0) for _ in range(cells * cells)]
        for y in range(size):
            # x·cells/size 的上确界是 cells−cells/size < cells，i0/j0 天然 < cells，
            # 只有 +1 的邻点需要回绕取模
            gy = y * cells / size
            j0 = int(gy)
            fy = gy - j0
            sy = fy * fy * (3.0 - 2.0 * fy)  # smoothstep
            j1 = (j0 + 1) % cells
            row0 = j0 * cells
            row1 = j1 * cells
            base = y * size
            for x in range(size):
                gx = x * cells / size
                i0 = int(gx)
                fx = gx - i0
                sx = fx * fx * (3.0 - 2.0 * fx)
                i1 = (i0 + 1) % cells
                value = (
                    lattice[row0 + i0] * (1.0 - sx) * (1.0 - sy)
                    + lattice[row0 + i1] * sx * (1.0 - sy)
                    + lattice[row1 + i0] * (1.0 - sx) * sy
                    + lattice[row1 + i1] * sx * sy
                )
                field[base + x] += amplitude * value
    return [value / total_amplitude for value in field]


def _coverage_mask(key: int, noise: list[float], band: float) -> Image.Image:
    """key 的四角地形位 → 连续场 → 阈值化 + 羽化 → L 模 coverage alpha 图。

    官方位序（map-tile-layout.js dualGridTileKeyAt）：tl=1 / bl=2 / tr=4 / br=8。
    场 = 四角位双线性插值（u 从左到右、v 从上到下，像素中心采样）+ blob 噪声；
    alpha 在阈值 ±band/2 内线性羽化（band 为场单位，≈ 羽化像素宽/64——场沿
    过渡法向的梯度量级为 1/64 每像素）。
    """
    tl, bl, tr, br = bool(key & 1), bool(key & 2), bool(key & 4), bool(key & 8)
    size = DUAL_GRID_CELL_SIZE
    lo = _THRESHOLD - band / 2.0
    data: list[int] = []
    for y in range(size):
        v = (y + 0.5) / size
        row_noise = y * size
        for x in range(size):
            u = (x + 0.5) / size
            field = (
                (1.0 - u) * (1.0 - v) * tl
                + u * (1.0 - v) * tr
                + (1.0 - u) * v * bl
                + u * v * br
            )
            field += _BLOB_AMPLITUDE * noise[row_noise + x]
            alpha = (field - lo) / band
            if alpha <= 0.0:
                data.append(0)
            elif alpha >= 1.0:
                data.append(255)
            else:
                data.append(round(alpha * 255))
    mask = Image.new("L", (size, size))
    mask.putdata(data)
    return mask


def _compose_cell(
    terrain_mode: str,
    background: Image.Image,
    foreground: Image.Image,
    mask: Image.Image,
) -> Image.Image:
    """按模式合成单格 64×64 RGBA。"""
    if terrain_mode == "dual":
        # mask 内 B、mask 外 A；paste(mask) 逐通道线性混合（羽化过渡带）
        cell = background.copy()
        cell.paste(foreground, (0, 0), mask)
        return cell
    if terrain_mode == "foreground":
        # 镂空图集：只保留 B 的 coverage 区域（mask 外 alpha=0）
        cell = foreground.copy()
        cell.putalpha(ImageChops.multiply(foreground.getchannel("A"), mask))
        return cell
    # background：反向镂空（mask 内 alpha=0，保留 A）
    cell = background.copy()
    cell.putalpha(ImageChops.multiply(background.getchannel("A"), ImageChops.invert(mask)))
    return cell


def synthesize_tileset(
    background_texture: bytes,
    foreground_texture: bytes,
    terrain_mode: str,
    seed: int = 0,
    feather_width: float = 1.0,
) -> bytes:
    """两张 64×64 无缝纹理 → 256×256 dual-grid 4×4 图集 PNG（确定性 bytes→bytes）。

    - terrain_mode：dual（B 覆盖 A）/ foreground（B 镂空）/ background（A 反向镂空）；
    - seed：blob 噪声种子，同 seed 同输出；
    - feather_width：边缘羽化宽度（px，0.5-8），控制过渡带软 alpha 宽度。

    格位摆放：官方 DUAL_GRID_ATLAS_CELL_BY_KEY（非线性是契约本体，见 map_layout）。
    """
    if terrain_mode not in _TERRAIN_MODES:
        raise TilesetError(
            f"terrain_mode 必须是 dual/foreground/background：{terrain_mode!r}"
        )
    if not 0 <= seed <= _SEED_MAX:
        raise TilesetError(f"seed 超出取值域 [0, {_SEED_MAX}]：{seed}")
    if not _FEATHER_MIN_PX <= feather_width <= _FEATHER_MAX_PX:
        raise TilesetError(
            f"feather_width 超出取值域 [{_FEATHER_MIN_PX}, {_FEATHER_MAX_PX}] px："
            f"{feather_width}"
        )

    background = _decode_texture(background_texture, "background")
    foreground = _decode_texture(foreground_texture, "foreground")
    _validate_inputs(background, foreground)

    noise = _noise_field(seed, DUAL_GRID_CELL_SIZE)
    band = feather_width / DUAL_GRID_CELL_SIZE
    atlas = Image.new("RGBA", DUAL_GRID_ATLAS_SIZE, (0, 0, 0, 0))
    for key in range(16):
        mask = _coverage_mask(key, noise, band)
        cell = _compose_cell(terrain_mode, background, foreground, mask)
        col, row = dual_grid_atlas_cell(key)
        atlas.paste(cell, (col * DUAL_GRID_CELL_SIZE, row * DUAL_GRID_CELL_SIZE))
    logger.debug(
        "tileset 合成完成 mode=%s seed=%d feather=%.2f", terrain_mode, seed, feather_width
    )
    return _encode(atlas)
