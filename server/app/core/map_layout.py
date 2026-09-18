"""地图瓦片几何契约（P3-L1，调研文档 05 §2.5 逐字复刻）。

本模块是纯函数 + 常量（无 I/O），锁定原版「地图就绪瓦片」的几何护城河——
数字一个都不能错（127/599/149.75/88.5 这类反直觉数字就是契约本体）。

中心锚定（全模块第一契约，调研文档 05 §5.1）：图片中心 = 逻辑格中心，
装饰高出底座的部分用透明 padding 表达（PNG 外框可大于底座且各不相同）；
客户端只按中心偏移摆放，禁止对产物做任何裁剪/缩放/拉伸/重锚。

dual-grid 格位索引有官方实证（非推断）：原版官方脚本
`~/code/red-art-studio-refs/original-skills/skills/game-assets/scripts/map-tile-layout.js`
L14-19 `DUAL_GRID_ATLAS_BY_KEY` 与 L200-210 `dualGridTileKeyAt`：
- key = 左上·1 + 左下·2 + 右上·4 + 右下·8（四角=显示格周围的 4 个地形格）；
- 图集摆放是非线性的 16 格映射表（见 DUAL_GRID_ATLAS_CELL_BY_KEY）。
本文件照抄该实证，出处见各常量注释。任务书原推断约定
（idx = tl + tr*2 + bl*4 + br*8、按 idx 顺序摆放）与官方实证不符，已弃用。
"""

from dataclasses import dataclass

# ---------- 通用 ----------

# 默认逻辑格边长（官方 map-tile-layout.js L8 DEFAULT_TILE_SIZE）
DEFAULT_TILE_SIZE_PX = 64

# footprint 常量：瓦片占用的逻辑格数（1×1 标准 / 2×2 tetraploid）
FOOTPRINT_SINGLE = 1
FOOTPRINT_TETRAPLOID = 2

# 中心锚定语义（§5.1；常量化供 API 文档/校验器引用，本模块不做图像 I/O）
CENTER_ANCHOR_SEMANTICS = (
    "图片中心=逻辑中心；装饰超出底座部分用透明 padding 表达；"
    "客户端按中心偏移摆放，禁止裁剪/缩放/重锚（校验主体包围盒+逻辑几何，而非外框尺寸）"
)

# ---------- 像素等距（§2.5 L84） ----------

# 底座菱形尺寸
PIXEL_ISOMETRIC_DIAMOND_SIZE = (128, 64)
# 两轴邻居中心偏移（斜向坐标系的两个基向量）
PIXEL_ISOMETRIC_AXIS_OFFSETS = ((64, 32), (-64, 32))
# 同行（同一屏幕水平线）中心横向间距 128、同竖线中心纵向间距 64
PIXEL_ISOMETRIC_ALIGNED_ROW_OFFSET = (128, 0)
PIXEL_ISOMETRIC_ALIGNED_COLUMN_OFFSET = (0, 64)
# tetraploid ≈ 4 个底座 = 逻辑 256×128
PIXEL_ISOMETRIC_TETRAPLOID_LOGICAL_SIZE = (256, 128)


def pixel_isometric_center(
    column: int, row: int, origin: tuple[float, float] = (0.0, 0.0)
) -> tuple[float, float]:
    """像素等距瓦片逻辑中心（官方 map-tile-layout.js L39-47 isometricCenter）。

    x = origin.x + (column − row)·64，y = origin.y + (column + row)·32。
    """
    x = origin[0] + (column - row) * (PIXEL_ISOMETRIC_DIAMOND_SIZE[0] / 2)
    y = origin[1] + (column + row) * (PIXEL_ISOMETRIC_DIAMOND_SIZE[1] / 2)
    return (x, y)


# ---------- 像素六边等距（§2.5 L86） ----------

PIXEL_HEX_SIDE_LENGTH = 64
# 同行中心步距 127 = 2×64−1：相邻六边共享一列边缘像素（反接缝技巧，§5.2；
# 按标准 128 实现会处处细缝，渲染器与导出器都要写死 127）
PIXEL_HEX_HORIZONTAL_STRIDE = 127
PIXEL_HEX_ROW_STRIDE = 64
PIXEL_HEX_ODD_ROW_OFFSET = 64
# 下行两条对角偏移 (64,64) 与 (−63,64)；跨两行竖向中心距 128
PIXEL_HEX_DOWN_RIGHT_OFFSET = (64, 64)
PIXEL_HEX_DOWN_LEFT_OFFSET = (-63, 64)
PIXEL_HEX_ALIGNED_VERTICAL_OFFSET = (0, 128)


def pixel_hex_center(
    column: int, row: int, origin: tuple[float, float] = (0.0, 0.0)
) -> tuple[float, float]:
    """像素六边瓦片逻辑中心（官方 L71-79 hexCenter）。

    x = origin.x + column·127 + (row 奇数 ? 64 : 0)，y = origin.y + row·64。
    """
    x = origin[0] + column * PIXEL_HEX_HORIZONTAL_STRIDE + (row % 2) * PIXEL_HEX_ODD_ROW_OFFSET
    y = origin[1] + row * PIXEL_HEX_ROW_STRIDE
    return (x, y)


# ---------- HD 等距（§2.5 L88） ----------

# 块边 372、块高 119，标准顶面 744×372 占 1 格
HD_ISOMETRIC_BLOCK_SIDE = 372
HD_ISOMETRIC_BLOCK_HEIGHT = 119
HD_ISOMETRIC_TOP_FACE_SIZE = (744, 372)
# 工作流坐标轴偏移 (±372, 186)
HD_ISOMETRIC_AXIS_OFFSETS = ((372, 186), (-372, 186))
# 产品地图编辑器归一到 128×64 逻辑格（显示偏移 (±64,32)；HD 用平滑采样禁最近邻）
HD_ISOMETRIC_EDITOR_TILE_SIZE = (128, 64)
HD_ISOMETRIC_EDITOR_DISPLAY_OFFSETS = ((64, 32), (-64, 32))
# tetraploid 边长翻倍 744 → 顶面 1488×744，占 2×2
HD_ISOMETRIC_TETRAPLOID_TOP_FACE_SIZE = (1488, 744)


def hd_isometric_asset_scale(
    image_width: float,
    footprint: int = FOOTPRINT_SINGLE,
    display_tile_size: float = DEFAULT_TILE_SIZE_PX,
) -> float:
    """单资产绘制缩放（官方 L91-101 hdIsometricAssetScale）。

    scale = display_tile_size·2 / min(image_width / footprint, 744)：
    footprint=1 → 128/min(image_width, 744)；footprint=2 → 128/min(image_width/2, 744)。
    缩放后中心对齐 footprint 中心（中心锚定）。
    """
    if image_width <= 0:
        raise ValueError(f"image_width 必须为正数：{image_width}")
    if footprint not in (FOOTPRINT_SINGLE, FOOTPRINT_TETRAPLOID):
        raise ValueError(f"footprint 必须是 1 或 2：{footprint}")
    if display_tile_size <= 0:
        raise ValueError(f"display_tile_size 必须为正数：{display_tile_size}")
    source_tile_width = min(image_width / footprint, float(HD_ISOMETRIC_TOP_FACE_SIZE[0]))
    return display_tile_size * 2 / source_tile_width


# ---------- HD 六边等距（§2.5 L90） ----------

HD_HEX_SIDE_LENGTH = 300
HD_HEX_BOTTOM_LAYER_HEIGHT = 96
# 同行步距 599 = 2×300−1；行距 354 = 1.5×300−96；下行 (300,354) / (−299,354)
HD_HEX_HORIZONTAL_STRIDE = 599
HD_HEX_ROW_STRIDE = 354
HD_HEX_DOWN_RIGHT_OFFSET = (300, 354)
HD_HEX_DOWN_LEFT_OFFSET = (-299, 354)
# 编辑器 0.25× 显示缩放（显示数字见 hd_hex_metrics）
HD_HEX_DISPLAY_SCALE = 0.25

# 六边 tetraploid 锚点行奇偶两套占据格偏移表（§2.5 L90 / §5.3：深度排序必须
# 遍历所有占据格，不能只按锚点或 column+row）
HEX_TETRAPLOID_OCCUPANCY_EVEN_ROW = ((0, 0), (1, 0), (-1, 1), (0, 1))
HEX_TETRAPLOID_OCCUPANCY_ODD_ROW = ((0, 0), (1, 0), (0, 1), (1, 1))


def hex_tetraploid_occupancy(anchor_row: int) -> tuple[tuple[int, int], ...]:
    """六边 tetraploid 按锚点行奇偶返回占据格偏移表（偶数行/奇数行两套）。"""
    if anchor_row % 2 == 0:
        return HEX_TETRAPLOID_OCCUPANCY_EVEN_ROW
    return HEX_TETRAPLOID_OCCUPANCY_ODD_ROW


@dataclass(frozen=True)
class HDHexMetrics:
    """HD 六边一组显示缩放下的几何度量（像素）。"""

    display_scale: float
    side_length: float
    tile_width: float
    bottom_layer_height: float
    horizontal_stride: float
    row_stride: float
    odd_row_offset: float


def hd_hex_metrics(display_scale: float = HD_HEX_DISPLAY_SCALE) -> HDHexMetrics:
    """HD 六边度量（官方 L103-115 hdHexMetrics；0.25× → 边长 75/步距 149.75/行距 88.5）。

    horizontal_stride = (2·300−1)·scale；row_stride = (1.5·300−96)·scale；
    odd_row_offset = side（奇数行水平偏移一格边长）。小数偏移渲染必须平滑采样（§5.2）。
    """
    if display_scale <= 0:
        raise ValueError(f"display_scale 必须为正数：{display_scale}")
    side = HD_HEX_SIDE_LENGTH * display_scale
    return HDHexMetrics(
        display_scale=display_scale,
        side_length=side,
        tile_width=side * 2,
        bottom_layer_height=HD_HEX_BOTTOM_LAYER_HEIGHT * display_scale,
        horizontal_stride=HD_HEX_HORIZONTAL_STRIDE * display_scale,
        row_stride=HD_HEX_ROW_STRIDE * display_scale,
        odd_row_offset=side,
    )


def hd_hex_center(
    column: int,
    row: int,
    display_scale: float = HD_HEX_DISPLAY_SCALE,
    origin: tuple[float, float] = (0.0, 0.0),
) -> tuple[float, float]:
    """HD 六边瓦片逻辑中心（官方 L117-125 hdHexCenter；公式同像素六边、度量换 HD）。"""
    metrics = hd_hex_metrics(display_scale)
    x = origin[0] + column * metrics.horizontal_stride + (row % 2) * metrics.odd_row_offset
    y = origin[1] + row * metrics.row_stride
    return (x, y)


# ---------- dual-grid 图集格位契约（tileset 合成与客户端取格共用） ----------

# 256×256 的 4×4 图集，64px dual-grid 格（调研文档 05 §1.3）
DUAL_GRID_ATLAS_SIZE = (256, 256)
DUAL_GRID_CELL_SIZE = 64
DUAL_GRID_ATLAS_GRID = (4, 4)

# 图集格位表，按 key 0-15 索引，值为 (col, row)——官方实证照抄：
# map-tile-layout.js L14-19 DUAL_GRID_ATLAS_BY_KEY（demo L534 消费顺序
# [atlasX, atlasY] = 表[key]，即 (列, 行)）。
# key=0（全背景）落在格 (0,3)，key=15（全前景）落在格 (2,1)——非线性摆放是
# 官方契约本体，勿"修复"成顺序摆放。
DUAL_GRID_ATLAS_CELL_BY_KEY: tuple[tuple[int, int], ...] = (
    (0, 3), (3, 3), (0, 0), (3, 2),
    (0, 2), (1, 2), (2, 3), (3, 1),
    (1, 3), (0, 1), (3, 0), (2, 0),
    (1, 0), (2, 2), (1, 1), (2, 1),
)


def dual_grid_tile_key(
    top_left: bool, bottom_left: bool, top_right: bool, bottom_right: bool
) -> int:
    """四角地形位 → 图集 key（官方 L200-210 dualGridTileKeyAt 逐字照抄）。

    key = 左上·1 + 左下·2 + 右上·4 + 右下·8。四角指显示格中心周围 4 个
    地形格：左上=(dc−1,dr−1)、左下=(dc−1,dr)、右上=(dc,dr−1)、右下=(dc,dr)。
    注意与任务书原推断约定（tr=2/bl=4）位序相反，以官方为准。
    """
    key = 0
    if top_left:
        key += 1
    if bottom_left:
        key += 2
    if top_right:
        key += 4
    if bottom_right:
        key += 8
    return key


def dual_grid_atlas_cell(key: int) -> tuple[int, int]:
    """key → 图集格位 (col, row)（查官方表；key 越界抛 ValueError）。"""
    if not 0 <= key < len(DUAL_GRID_ATLAS_CELL_BY_KEY):
        raise ValueError(f"dual-grid key 超出取值域 [0, 15]：{key}")
    return DUAL_GRID_ATLAS_CELL_BY_KEY[key]
