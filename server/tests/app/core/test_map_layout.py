"""map_layout 几何契约测试：调研文档 05 §2.5 全部数字逐值断言（P3-L1 验收 1）。

数字来源：docs/features/05-maps-tiles-and-textures.md §2.5（原版契约护城河）；
dual-grid 格位表另与官方 map-tile-layout.js 原表逐项对照。
"""

import pytest

from server.app.core.map_layout import (
    DEFAULT_TILE_SIZE_PX,
    DUAL_GRID_ATLAS_CELL_BY_KEY,
    FOOTPRINT_SINGLE,
    FOOTPRINT_TETRAPLOID,
    HD_HEX_SIDE_LENGTH,
    HD_ISOMETRIC_TETRAPLOID_TOP_FACE_SIZE,
    HD_ISOMETRIC_TOP_FACE_SIZE,
    PIXEL_HEX_DOWN_LEFT_OFFSET,
    PIXEL_HEX_DOWN_RIGHT_OFFSET,
    PIXEL_HEX_HORIZONTAL_STRIDE,
    dual_grid_atlas_cell,
    dual_grid_tile_key,
    hd_hex_center,
    hd_hex_metrics,
    hd_isometric_asset_scale,
    hex_tetraploid_occupancy,
    pixel_hex_center,
    pixel_isometric_center,
)


class TestPixelIsometric:
    """像素等距：底座菱形 128×64、轴偏移 (±64,32)、同行 128/同竖线 64、tetraploid 256×128。"""

    def test_diamond_size(self) -> None:
        from server.app.core.map_layout import (
            PIXEL_ISOMETRIC_ALIGNED_COLUMN_OFFSET,
            PIXEL_ISOMETRIC_ALIGNED_ROW_OFFSET,
            PIXEL_ISOMETRIC_AXIS_OFFSETS,
            PIXEL_ISOMETRIC_DIAMOND_SIZE,
            PIXEL_ISOMETRIC_TETRAPLOID_LOGICAL_SIZE,
        )

        assert PIXEL_ISOMETRIC_DIAMOND_SIZE == (128, 64)
        assert set(PIXEL_ISOMETRIC_AXIS_OFFSETS) == {(64, 32), (-64, 32)}
        assert PIXEL_ISOMETRIC_ALIGNED_ROW_OFFSET == (128, 0)
        assert PIXEL_ISOMETRIC_ALIGNED_COLUMN_OFFSET == (0, 64)
        assert PIXEL_ISOMETRIC_TETRAPLOID_LOGICAL_SIZE == (256, 128)

    def test_center_formula(self) -> None:
        # (0,0)→(0,0)、(1,0)→(64,32)、(0,1)→(−64,32)、(1,1)→(0,64)、(2,1)→(64,96)
        assert pixel_isometric_center(0, 0) == (0.0, 0.0)
        assert pixel_isometric_center(1, 0) == (64.0, 32.0)
        assert pixel_isometric_center(0, 1) == (-64.0, 32.0)
        assert pixel_isometric_center(1, 1) == (0.0, 64.0)
        assert pixel_isometric_center(2, 1) == (64.0, 96.0)
        # 带原点偏移
        assert pixel_isometric_center(1, 0, origin=(100.0, 50.0)) == (164.0, 82.0)


class TestPixelHex:
    """像素六边：边长 64、同行步距 127、下行 (64,64)/(−63,64)、跨两行竖距 128。"""

    def test_stride_is_127_not_128(self) -> None:
        # 反直觉契约本体：2×64−1=127，相邻六边共享一列边缘像素
        assert PIXEL_HEX_HORIZONTAL_STRIDE == 127
        assert HD_HEX_SIDE_LENGTH * 0 + 2 * 64 - 1 == PIXEL_HEX_HORIZONTAL_STRIDE

    def test_offsets(self) -> None:
        assert PIXEL_HEX_DOWN_RIGHT_OFFSET == (64, 64)
        assert PIXEL_HEX_DOWN_LEFT_OFFSET == (-63, 64)

    def test_center_formula(self) -> None:
        # 偶数行无奇偏移：(3,0)→(381,0)；奇数行 +64：(0,1)→(64,64)
        assert pixel_hex_center(3, 0) == (381.0, 0.0)
        assert pixel_hex_center(0, 1) == (64.0, 64.0)
        assert pixel_hex_center(1, 1) == (191.0, 64.0)
        # 跨两行竖向中心距 128
        assert pixel_hex_center(0, 2)[1] - pixel_hex_center(0, 0)[1] == 128.0

    def test_down_offsets_match_center_deltas(self) -> None:
        """官方下行偏移 (64,64)/(−63,64) 与 center 公式的实际位移一致（§2.5 下一行 64/64）。"""
        # 从偶数行 (0,0)：右下邻 (0,1) Δ=(64,64)=downRight；左下邻 (−1,1) Δ=(−63,64)=downLeft
        assert pixel_hex_center(0, 1) == (pixel_hex_center(0, 0)[0] + 64, 64.0)
        assert pixel_hex_center(-1, 1) == (pixel_hex_center(0, 0)[0] - 63, 64.0)


class TestHDIsometric:
    """HD 等距：块边 372/块高 119/顶面 744×372、轴偏移 (±372,186)、tetraploid 1488×744。"""

    def test_constants(self) -> None:
        from server.app.core.map_layout import (
            HD_ISOMETRIC_AXIS_OFFSETS,
            HD_ISOMETRIC_BLOCK_HEIGHT,
            HD_ISOMETRIC_BLOCK_SIDE,
            HD_ISOMETRIC_EDITOR_DISPLAY_OFFSETS,
            HD_ISOMETRIC_EDITOR_TILE_SIZE,
        )

        assert HD_ISOMETRIC_BLOCK_SIDE == 372
        assert HD_ISOMETRIC_BLOCK_HEIGHT == 119
        assert HD_ISOMETRIC_TOP_FACE_SIZE == (744, 372)
        assert set(HD_ISOMETRIC_AXIS_OFFSETS) == {(372, 186), (-372, 186)}
        assert HD_ISOMETRIC_EDITOR_TILE_SIZE == (128, 64)
        assert set(HD_ISOMETRIC_EDITOR_DISPLAY_OFFSETS) == {(64, 32), (-64, 32)}
        assert HD_ISOMETRIC_TETRAPLOID_TOP_FACE_SIZE == (1488, 744)

    def test_asset_scale(self) -> None:
        # 单资产：128/min(image_width, 744)
        assert hd_isometric_asset_scale(296) == pytest.approx(128 / 296)
        assert hd_isometric_asset_scale(1488) == pytest.approx(128 / 744)
        assert hd_isometric_asset_scale(10000) == pytest.approx(128 / 744)
        # 2×2 资产：128/min(image_width/2, 744)
        assert hd_isometric_asset_scale(296, footprint=2) == pytest.approx(128 / 148)
        assert hd_isometric_asset_scale(1488, footprint=2) == pytest.approx(128 / 744)
        # display_tile_size 可覆盖（编辑器 128 逻辑格语义 = 默认 64·2）
        assert hd_isometric_asset_scale(744, display_tile_size=128) == pytest.approx(256 / 744)
        # footprints 常量
        assert FOOTPRINT_SINGLE == 1 and FOOTPRINT_TETRAPLOID == 2

    def test_asset_scale_rejects_invalid(self) -> None:
        with pytest.raises(ValueError):
            hd_isometric_asset_scale(0)
        with pytest.raises(ValueError):
            hd_isometric_asset_scale(-744.0)
        with pytest.raises(ValueError):
            hd_isometric_asset_scale(744, footprint=3)
        with pytest.raises(ValueError):
            hd_isometric_asset_scale(744, display_tile_size=0)


class TestHDHex:
    """HD 六边：边长 300、层高 96、步距 599、行距 354、0.25× 显示 75/149.75/88.5/偏移 75。"""

    def test_base_constants(self) -> None:
        from server.app.core.map_layout import (  # noqa: F401  # 见下行断言
            HD_HEX_BOTTOM_LAYER_HEIGHT,
            HD_HEX_DISPLAY_SCALE,
            HD_HEX_DOWN_LEFT_OFFSET,
            HD_HEX_DOWN_RIGHT_OFFSET,
            HD_HEX_HORIZONTAL_STRIDE,
            HD_HEX_ROW_STRIDE,
        )

        assert HD_HEX_SIDE_LENGTH == 300

        assert HD_HEX_BOTTOM_LAYER_HEIGHT == 96
        assert HD_HEX_HORIZONTAL_STRIDE == 599  # 2×300−1
        assert HD_HEX_ROW_STRIDE == 354  # 1.5×300−96
        assert HD_HEX_DOWN_RIGHT_OFFSET == (300, 354)
        assert HD_HEX_DOWN_LEFT_OFFSET == (-299, 354)
        assert HD_HEX_DISPLAY_SCALE == 0.25

    def test_metrics_at_display_scale(self) -> None:
        m = hd_hex_metrics(0.25)
        assert m.side_length == pytest.approx(75)
        assert m.tile_width == pytest.approx(150)
        assert m.bottom_layer_height == pytest.approx(24)
        assert m.horizontal_stride == pytest.approx(149.75)
        assert m.row_stride == pytest.approx(88.5)
        assert m.odd_row_offset == pytest.approx(75)

    def test_center_formula(self) -> None:
        # (0,0)→(0,0)；(1,0)→(149.75,0)；奇数行 (0,1)→(75, 88.5)；(1,1)→(224.75, 88.5)
        assert hd_hex_center(0, 0) == (0.0, 0.0)
        assert hd_hex_center(1, 0) == pytest.approx((149.75, 0.0))
        assert hd_hex_center(0, 1) == pytest.approx((75.0, 88.5))
        assert hd_hex_center(1, 1) == pytest.approx((224.75, 88.5))


class TestHexTetraploidOccupancy:
    """六边 tetraploid 锚点行奇偶两套占据格偏移表（§2.5/§5.3）。"""

    def test_even_row_table(self) -> None:
        assert hex_tetraploid_occupancy(0) == ((0, 0), (1, 0), (-1, 1), (0, 1))
        assert hex_tetraploid_occupancy(2) == ((0, 0), (1, 0), (-1, 1), (0, 1))

    def test_odd_row_table(self) -> None:
        assert hex_tetraploid_occupancy(1) == ((0, 0), (1, 0), (0, 1), (1, 1))
        assert hex_tetraploid_occupancy(-1) == ((0, 0), (1, 0), (0, 1), (1, 1))


class TestDualGridKeyContract:
    """dual-grid 格位契约：官方 map-tile-layout.js L14-19/L200-210 照抄实证。"""

    def test_key_bit_weights(self) -> None:
        # 官方位权：左上·1 + 左下·2 + 右上·4 + 右下·8（与任务书原推断 tr=2/bl=4 相反）
        assert dual_grid_tile_key(True, False, False, False) == 1
        assert dual_grid_tile_key(False, True, False, False) == 2
        assert dual_grid_tile_key(False, False, True, False) == 4
        assert dual_grid_tile_key(False, False, False, True) == 8
        assert dual_grid_tile_key(True, True, True, True) == 15
        assert dual_grid_tile_key(False, False, False, False) == 0

    def test_atlas_table_matches_official_source(self) -> None:
        # 官方原表逐项对照（map-tile-layout.js L14-19，[col, row]）
        official = [
            [0, 3], [3, 3], [0, 0], [3, 2],
            [0, 2], [1, 2], [2, 3], [3, 1],
            [1, 3], [0, 1], [3, 0], [2, 0],
            [1, 0], [2, 2], [1, 1], [2, 1],
        ]
        assert list(DUAL_GRID_ATLAS_CELL_BY_KEY) == [tuple(c) for c in official]
        # 16 格恰为 4×4 的双射（每格恰好出现一次）
        assert sorted(DUAL_GRID_ATLAS_CELL_BY_KEY) == [(c, r) for c in range(4) for r in range(4)]

    def test_atlas_cell_lookup(self) -> None:
        assert dual_grid_atlas_cell(0) == (0, 3)  # 全背景 → 左下角格
        assert dual_grid_atlas_cell(15) == (2, 1)  # 全前景 → (2,1) 格
        assert dual_grid_atlas_cell(1) == (3, 3)
        with pytest.raises(ValueError):
            dual_grid_atlas_cell(16)
        with pytest.raises(ValueError):
            dual_grid_atlas_cell(-1)

    def test_default_tile_size(self) -> None:
        assert DEFAULT_TILE_SIZE_PX == 64
