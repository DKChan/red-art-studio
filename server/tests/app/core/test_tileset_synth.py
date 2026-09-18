"""tileset_synth 合成器测试（P3-L1 验收 1）：三模式格位断言 + 镂空方向 + 确定性 + 强校验。

夹具一律 Pillow 代码生成（禁 Read 二进制）。A/B 纹理用整幅唯一纯色
（A=#102030 红、B=#0000ff 蓝风格），mask 内外按 alpha/色值断言。
"""

import io

import pytest
from PIL import Image

from server.app.core.tileset_synth import (
    _coverage_mask,
    _noise_field,
    _validate_inputs,
    synthesize_tileset,
)

# 夹具色：A（背景）与 B（前景）必须是同码位不相交的纯色
_A_RGB = (16, 32, 48)
_B_RGB = (200, 40, 240)
_ATLAS = 256
_CELL = 64


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _texture(rgb: tuple[int, int, int]) -> bytes:
    """64×64 纯色无缝纹理（RGBA，alpha=255）。"""
    return _png(Image.new("RGBA", (_CELL, _CELL), (*rgb, 255)))


def _atlas(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img.load()
    return img.convert("RGBA")


def _cell(img: Image.Image, key: int) -> Image.Image:
    from server.app.core.map_layout import dual_grid_atlas_cell

    col, row = dual_grid_atlas_cell(key)
    return img.crop((col * _CELL, row * _CELL, (col + 1) * _CELL, (row + 1) * _CELL))


def _avg_alpha(img: Image.Image) -> float:
    hist = img.getchannel("A").histogram()
    total = sum(i * count for i, count in enumerate(hist)) / (_CELL * _CELL)
    return total


def _center_px(img: Image.Image) -> tuple[int, int, int, int]:
    return img.getpixel((_CELL // 2, _CELL // 2))  # type: ignore[return-value]


class TestValidation:
    """入口强校验：非 64×64 拒绝（禁止静默缩放，§5.6 坑 6）。"""

    def test_wrong_size_rejected(self) -> None:
        for wrong in [(63, 64), (64, 63), (128, 128), (32, 32)]:
            with pytest.raises(ValueError, match="64×64"):
                synthesize_tileset(
                    _texture(_A_RGB), _png(Image.new("RGBA", wrong, (0, 0, 0, 255))),
                    "dual",
                )

    def test_wrong_size_on_foreground_argument_rejected(self) -> None:
        with pytest.raises(ValueError, match="foreground"):
            synthesize_tileset(
                _texture(_A_RGB), _png(Image.new("RGBA", (65, 64), (0, 0, 0, 255))),
                "foreground",
            )

    def test_direct_validate_helper(self) -> None:
        good = Image.new("RGBA", (64, 64))
        _validate_inputs(good, good)  # 不抛
        with pytest.raises(ValueError, match="background"):
            _validate_inputs(Image.new("RGBA", (10, 10)), good)

    def test_undecodable_bytes_rejected(self) -> None:
        with pytest.raises(ValueError, match="background"):
            synthesize_tileset(b"not an image", _texture(_B_RGB), "dual")

    def test_terrain_mode_rejected(self) -> None:
        with pytest.raises(ValueError, match="terrain_mode"):
            synthesize_tileset(_texture(_A_RGB), _texture(_B_RGB), "interleaved")

    def test_seed_range_rejected(self) -> None:
        with pytest.raises(ValueError, match="seed"):
            synthesize_tileset(_texture(_A_RGB), _texture(_B_RGB), "dual", seed=-1)
        with pytest.raises(ValueError, match="seed"):
            synthesize_tileset(_texture(_A_RGB), _texture(_B_RGB), "dual", seed=2**32)

    def test_feather_range_rejected(self) -> None:
        with pytest.raises(ValueError, match="feather"):
            synthesize_tileset(_texture(_A_RGB), _texture(_B_RGB), "dual", feather_width=0.0)
        with pytest.raises(ValueError, match="feather"):
            synthesize_tileset(_texture(_A_RGB), _texture(_B_RGB), "dual", feather_width=9.0)


class TestDeterminism:
    """同 seed 确定性复现；不同 seed 噪声不同。"""

    def test_same_seed_identical_bytes(self) -> None:
        a = _texture(_A_RGB)
        b = _texture(_B_RGB)
        one = synthesize_tileset(a, b, "dual", seed=42)
        two = synthesize_tileset(a, b, "dual", seed=42)
        assert one == two

    def test_different_seed_changes_edge_cells_only(self) -> None:
        a = _texture(_A_RGB)
        b = _texture(_B_RGB)
        s1 = _atlas(synthesize_tileset(a, b, "dual", seed=1))
        s2 = _atlas(synthesize_tileset(a, b, "dual", seed=2))
        # 纯色对：噪声只扰动过渡格的中心像素（key 0/15 恒定），逐格找差异
        differing = [
            key for key in range(16)
            if _cell(s1, key).tobytes() != _cell(s2, key).tobytes()
        ]
        assert differing, "两个 seed 的图集完全相同，blob 噪声未生效"
        assert 0 not in differing and 15 not in differing, "key 0/15 不应受噪声扰动"

    def test_feather_zero_slightly_sharper_than_large(self) -> None:
        a = _texture(_A_RGB)
        b = _texture(_B_RGB)
        small = _atlas(synthesize_tileset(a, b, "dual", seed=7, feather_width=0.5))
        big = _atlas(synthesize_tileset(a, b, "dual", seed=7, feather_width=8.0))
        # dual 模式 A/B 都不透明，羽化体现在 RGB 颜色混合上：羽化越宽，
        # 过渡格（key=1）中"既非纯 A 也非纯 B"的混合像素越多
        def _blend_count(img: Image.Image) -> int:
            return sum(
                1 for p in _cell(img, 1).getdata() if p[:3] not in (_A_RGB, _B_RGB)
            )

        assert _blend_count(big) > _blend_count(small) >= 1


class TestDualMode:
    """dual 模式：mask 内 B、mask 外 A；key0==纯 A、key15==纯 B、过渡格同时含 A/B。"""

    def test_atlas_geometry(self) -> None:
        atlas = _atlas(synthesize_tileset(_texture(_A_RGB), _texture(_B_RGB), "dual"))
        assert atlas.size == (_ATLAS, _ATLAS)

    def test_key0_is_pure_background(self) -> None:
        atlas = _atlas(synthesize_tileset(_texture(_A_RGB), _texture(_B_RGB), "dual"))
        cell = _cell(atlas, 0)
        colors = set(cell.getdata())
        assert colors == {(*_A_RGB, 255)}, "key0 应为纯 A（全背景，coverage=0）"

    def test_key15_is_pure_foreground(self) -> None:
        atlas = _atlas(synthesize_tileset(_texture(_A_RGB), _texture(_B_RGB), "dual"))
        cell = _cell(atlas, 15)
        colors = set(cell.getdata())
        assert colors == {(*_B_RGB, 255)}, "key15 应为纯 B（全前景，coverage=1）"

    def test_transition_cells_contain_both_terrains(self) -> None:
        atlas = _atlas(synthesize_tileset(_texture(_A_RGB), _texture(_B_RGB), "dual"))
        for key in range(1, 15):
            colors = set(_cell(atlas, key).getdata())
            a_present = (*_A_RGB, 255) in colors
            b_present = (*_B_RGB, 255) in colors
            # 过渡格必须同时含 A 与 B（coverage 0<field<1 的区域各在一侧）
            assert a_present and b_present, f"key={key} 过渡格缺少 A/B 之一：{colors}"

    def test_corner_bit_layout(self) -> None:
        """四角位形正确：key=1（只有左上角是前景）格内左上区域是 B、右下区域是 A。"""
        atlas = _atlas(synthesize_tileset(_texture(_A_RGB), _texture(_B_RGB), "dual"))
        cell = _cell(atlas, 1)
        # 左上角 (8,8) 在前景 coverage 内（tl=1 的双线性场在 (0.125,0.125) 处≈0.77>0.5）
        assert cell.getpixel((8, 8)) == (*_B_RGB, 255)
        # 右下角 (56,56) 在背景区（场≈0.23<0.5）
        assert cell.getpixel((56, 56)) == (*_A_RGB, 255)

    def test_single_texture_color_clash_detected_via_key0(self) -> None:
        """A=B 纯色退化输入：key0/key15 断言失去分辨力时的行为仍确定（同色合成）。"""
        same = _texture((5, 5, 5))
        atlas = _atlas(synthesize_tileset(same, same, "dual", seed=3))
        assert set(_cell(atlas, 0).getdata()) == {(5, 5, 5, 255)}


class TestForegroundMode:
    """foreground 镂空：mask 外 alpha=0；key0 全透明、key15 纯 B、过渡格部分透明。"""

    def test_key0_fully_transparent(self) -> None:
        atlas = _atlas(
            synthesize_tileset(_texture(_A_RGB), _texture(_B_RGB), "foreground")
        )
        assert _avg_alpha(_cell(atlas, 0)) == 0.0
        # 镂空区 RGB 保留 B 色（putalpha 语义）：客户端平滑采样时 bleed 的是
        # 相邻前景色而非黑边，游戏美术正确实践；断言只看 alpha
        assert set(p[3] for p in _cell(atlas, 0).getdata()) == {0}

    def test_key15_is_pure_foreground(self) -> None:
        atlas = _atlas(
            synthesize_tileset(_texture(_A_RGB), _texture(_B_RGB), "foreground")
        )
        cell = _cell(atlas, 15)
        assert set(cell.getdata()) == {(*_B_RGB, 255)}

    def test_transition_cell_partially_cut(self) -> None:
        atlas = _atlas(
            synthesize_tileset(_texture(_A_RGB), _texture(_B_RGB), "foreground")
        )
        cell = _cell(atlas, 7)
        alphas = [p[3] for p in cell.getdata()]
        transparent = sum(1 for v in alphas if v == 0)
        opaque = sum(1 for v in alphas if v == 255)
        assert transparent > 0 and opaque > 0, "过渡格应同时含镂空区与 B 覆盖区"
        # 镂空区绝不能泄漏 A 色（mask 外 alpha=0）
        assert all(p[3] == 0 for p in cell.getdata() if p[:3] == _A_RGB)

    def test_background_texture_never_leaks(self) -> None:
        atlas = _atlas(
            synthesize_tileset(_texture(_A_RGB), _texture(_B_RGB), "foreground")
        )
        # 全图集任何不透明像素都不该是 A 色
        for p in atlas.getdata():
            assert not (p[3] > 0 and p[:3] == _A_RGB)


class TestBackgroundMode:
    """background 反向镂空：mask 内 alpha=0；key0 纯 A、key15 全透明。"""

    def test_key0_is_pure_background(self) -> None:
        atlas = _atlas(
            synthesize_tileset(_texture(_A_RGB), _texture(_B_RGB), "background")
        )
        assert set(_cell(atlas, 0).getdata()) == {(*_A_RGB, 255)}

    def test_key15_fully_transparent(self) -> None:
        atlas = _atlas(
            synthesize_tileset(_texture(_A_RGB), _texture(_B_RGB), "background")
        )
        assert _avg_alpha(_cell(atlas, 15)) == 0.0
        # RGB 保留 A 色（putalpha 镂空语义，见 foreground 侧注释），alpha 恒 0
        assert set(p[3] for p in _cell(atlas, 15).getdata()) == {0}

    def test_foreground_color_never_leaks(self) -> None:
        atlas = _atlas(
            synthesize_tileset(_texture(_A_RGB), _texture(_B_RGB), "background")
        )
        for p in atlas.getdata():
            assert not (p[3] > 0 and p[:3] == _B_RGB)


class TestMaskInternals:
    """coverage mask 内部行为：全 0/全 1 key 不受噪声扰动（正确性不变量的根）。"""

    def test_key0_and_15_masks_binary_constant(self) -> None:
        for seed in (0, 1, 12345):
            noise = _noise_field(seed, _CELL)
            mask0 = list(_coverage_mask(0, noise, 1.0 / 64).getdata())
            mask15 = list(_coverage_mask(15, noise, 1.0 / 64).getdata())
            # key0：场 ∈ [−0.18, 0.18]，阈值带 [0.492, 0.508] → 恒 0
            # key15：场 ∈ [0.82, 1.18]，恒 255（噪声幅度 _BLOB_AMPLITUDE=0.18 保证）
            assert set(mask0) == {0}
            assert set(mask15) == {255}

    def test_mask_gradient_direction(self) -> None:
        """key=2（只有左下角是前景）：左下内部恒 255、右上内部恒 0。

        双线性场在右下角像素处 br=0 → 0，故不能断言整行；按区域断言方向。
        """
        noise = _noise_field(9, _CELL)
        mask = _coverage_mask(2, noise, 1.0 / 64)
        px = mask.load()
        # 区域取到"含噪声仍确定"的深度（场 v·(1−u) 距阈值 > 噪声幅度 0.18）
        for x in range(2, 10):
            for y in range(56, 64):
                assert px[x, y] == 255, f"左下角深处 ({x},{y}) 应全前景"
        for x in range(44, 62):
            for y in range(2, 20):
                assert px[x, y] == 0, f"右上角深处 ({x},{y}) 应全背景"


class TestCellPacking:
    """图集打包与官方格位表一致（合成产物层面再证一遍）。"""

    def test_only_expected_cells_are_pure_in_dual(self) -> None:
        atlas = _atlas(synthesize_tileset(_texture(_A_RGB), _texture(_B_RGB), "dual"))
        pure_a_cells = [
            key for key in range(16) if set(_cell(atlas, key).getdata()) == {(*_A_RGB, 255)}
        ]
        pure_b_cells = [
            key for key in range(16) if set(_cell(atlas, key).getdata()) == {(*_B_RGB, 255)}
        ]
        assert pure_a_cells == [0]
        assert pure_b_cells == [15]
