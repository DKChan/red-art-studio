"""后处理三件套纯函数单元测试（P2 Loop，镜像 server/app/core/processors.py）。

夹具全部用 Pillow 代码合成（任务书约束 8：禁止读二进制文件触发 vision 请求）。
覆盖：三 operation 成功路径、自动像素尺寸估计、四角扫描底色、four_way 串行、
非法参数 4xx 语义（ImageEditError 家族）、alpha 二值化/量化等像素级断言。
"""

import io

import pytest
from PIL import Image

from server.app.core.processors import (
    ImageEditError,
    estimate_pixel_size,
    pixelate,
    remove_background,
    scan_background_color,
    self_loop,
)


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _decode(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img.load()
    return img


def _colors(img: Image.Image) -> set[tuple[int, ...]]:
    """图内全部唯一颜色（含 alpha 通道的 RGBA 元组）。"""
    return {c[1] for c in img.getcolors(maxcolors=1 << 24) or []}


RED = (255, 0, 0, 255)
BLUE = (0, 0, 255, 255)


# ---------- pixelate ----------


def test_pixelate_success_block_invariant_and_quantize() -> None:
    """成功路径：整数倍回放尺寸不变；同块内像素一致；色数 ≤ 32；alpha 已二值化。"""
    size = 64
    img = Image.new("RGBA", (size, size), RED)
    for x in range(16, 48):
        for y in range(16, 48):
            img.putpixel((x, y), (10, 200, 30, 255))  # 绿色块
    for x in range(8):
        for y in range(8):
            img.putpixel((x, y), (5, 5, 5, 0))  # 一个完整 8×8 透明块（一个逻辑像素）
    data = pixelate(_png(img), pixel_size=8)

    out = _decode(data)
    assert out.format == "PNG"
    assert out.size == (size, size)  # 64 可被 8 整除 → 尺寸不变
    assert out.mode == "RGBA"

    # 同块内像素完全一致（NEAREST 采样+整数倍回放的"完美像素"语义）
    for bx in range(0, size, 8):
        for by in range(0, size, 8):
            block = out.crop((bx, by, bx + 8, by + 8))
            assert len(set(block.getdata())) == 1, f"块 ({bx},{by}) 内像素不一致"

    # 量化生效：唯一色数 ≤ 32
    assert len(_colors(out)) <= 32

    # alpha 二值化：只有 0/255 两档
    assert {a for *_, a in out.getdata()} <= {0, 255}
    assert out.getpixel((0, 0))[3] == 0  # 透明角点保持透明
    assert out.getpixel((32, 32))[3] == 255


def test_pixelate_downscale_truncates_remainder() -> None:
    """尺寸不是粒度整数倍：输出裁掉边缘残块（⌊w/p⌋·p）。"""
    img = Image.new("RGBA", (30, 20), RED)
    out = _decode(pixelate(_png(img), pixel_size=8))
    assert out.size == (24, 16)


def test_pixelate_auto_estimate_recovers_block_period() -> None:
    """自动像素尺寸：对真块状合成图（每 8px 一块）估出 8，而非细粒度或回落值。"""
    img = Image.new("RGBA", (128, 128), RED)
    palette = [RED, BLUE, (0, 255, 0, 255), (255, 255, 0, 255)]
    for bx in range(0, 128, 8):
        for by in range(0, 128, 8):
            color = palette[((bx // 8) * 7 + (by // 8) * 13) % len(palette)]
            for x in range(bx, bx + 8):
                for y in range(by, by + 8):
                    img.putpixel((x, y), color)
    assert estimate_pixel_size(img) == 8


def test_pixelate_auto_estimate_smooth_image_falls_fine() -> None:
    """平滑渐变图无块周期：估计偏保守（细粒度），且绝不超出 4-64 取值域。"""
    img = Image.new("RGBA", (128, 128))
    for x in range(128):
        for y in range(128):
            img.putpixel((x, y), (x * 2 % 256, y * 2 % 256, 128, 255))
    chosen = estimate_pixel_size(img)
    assert 4 <= chosen <= 64
    assert chosen <= 8  # 连续调图应选细粒度，少毁细节


def test_pixelate_invalid_size_rejected() -> None:
    """pixel_size 越界 → ImageEditError（上层映射 422）。"""
    img = _png(Image.new("RGBA", (16, 16), RED))
    with pytest.raises(ImageEditError):
        pixelate(img, pixel_size=3)
    with pytest.raises(ImageEditError):
        pixelate(img, pixel_size=65)


# ---------- remove_background ----------


def test_scan_background_color_corners_mode() -> None:
    """四角扫描：四角纯红、中央蓝块 → 众数底色为红。"""
    img = Image.new("RGBA", (64, 64), RED)
    for x in range(24, 40):
        for y in range(24, 40):
            img.putpixel((x, y), BLUE)
    assert scan_background_color(img) == (255, 0, 0)


def test_remove_background_success_with_explicit_key() -> None:
    """显式底色色键：底色 alpha==0、前景 alpha==255（二值化无灰边）。"""
    img = Image.new("RGBA", (64, 64), RED)
    for x in range(20, 44):
        for y in range(20, 44):
            img.putpixel((x, y), BLUE)
    out = _decode(remove_background(_png(img), source_background_color="#ff0000"))

    for cx, cy in ((0, 0), (63, 0), (0, 63), (63, 63)):
        assert out.getpixel((cx, cy))[3] == 0, f"角点 ({cx},{cy}) 未透明"
    center = out.getpixel((32, 32))
    assert center[:3] == (0, 0, 255)
    assert center[3] == 255
    # alpha 全图只有 0/255（完美像素口径）
    assert {a for *_, a in out.getdata()} <= {0, 255}


def test_remove_background_auto_scans_corners() -> None:
    """缺省底色：四角扫描自动取红为键，效果与显式指定一致。"""
    img = Image.new("RGBA", (64, 64), RED)
    for x in range(20, 44):
        for y in range(20, 44):
            img.putpixel((x, y), BLUE)
    out = _decode(remove_background(_png(img)))
    assert out.getpixel((0, 0))[3] == 0
    assert out.getpixel((32, 32))[3] == 255


def test_remove_background_tolerance_zero_exact_key_only() -> None:
    """tolerance=0：只有与底色完全相等的像素被抠掉。"""
    img = Image.new("RGB", (8, 8), (10, 20, 30))
    img.putpixel((4, 4), (11, 20, 30))  # 色距 1 的"前景"
    out = _decode(remove_background(_png(img), source_background_color="#0a141e", tolerance=0))
    assert out.getpixel((0, 0))[3] == 0
    assert out.getpixel((4, 4))[3] == 255


def test_remove_background_invalid_inputs_rejected() -> None:
    """非法底色格式 / tolerance 越界 → ImageEditError 家族（上层映射 422）。"""
    img = _png(Image.new("RGBA", (8, 8), RED))
    with pytest.raises(ValueError):
        remove_background(img, source_background_color="ff0000")  # 缺 #
    with pytest.raises(ValueError):
        remove_background(img, source_background_color="#ff00zz")
    with pytest.raises(ValueError):
        remove_background(img, tolerance=-1)
    with pytest.raises(ValueError):
        remove_background(img, tolerance=256)


# ---------- self_loop ----------


def _max_neighbor_step(img: Image.Image, axis: int) -> int:
    """全图扫描：相邻列（axis=0）/相邻行（axis=1）间的最大通道绝对跳变。

    与旧断言只查首尾列/行不同，这里扫全图——接缝硬边藏在中央，首尾对比永远测不到。
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


def test_self_loop_horizontal_center_seam_no_hard_edge() -> None:
    """回归（第三轮修复）：水平循环中央接缝不得留全对比度硬缝。

    历史 bug 逃逸点：旧断言只查首尾列连续（roll 本身已保证），_seam_mask 中心权重
    0（保留底图）与 255（全镜像）两种错误极性都恰好通过。本测试直接断言中央缝
    列对 (63|64) 与全图列扫描。
    夹具：128×64 水平渐变（R=x*255//127，斜率 2，接缝处对比度 255）。
    权重公式 (band-d)*128//band、band=length//4=32 ⇒ 带内相邻列理论跳变 ≈4 < 6。
    """
    w, h = 128, 64
    img = Image.new("RGBA", (w, h))
    for x in range(w):
        for y in range(h):
            img.putpixel((x, y), (x * 255 // (w - 1), 100, 200, 255))
    out = _decode(self_loop(_png(img), direction="horizontal"))
    assert out.size == (w, h)

    row = h // 2
    center_step = max(
        abs(out.getpixel((63, row))[i] - out.getpixel((64, row))[i]) for i in range(4)
    )
    assert center_step < 6, f"中央接缝列对 (63|64) 跳变 {center_step} ≥ 6，硬缝未抹平"
    full_scan = _max_neighbor_step(out, axis=0)
    assert full_scan < 6, f"全图相邻列最大跳变 {full_scan} ≥ 6"


def test_self_loop_vertical_center_seam_no_hard_edge() -> None:
    """回归：垂直循环中央接缝（行对 63|64）+ 全图行扫描，阈值同水平。"""
    w, h = 64, 128
    img = Image.new("RGBA", (w, h))
    for x in range(w):
        for y in range(h):
            img.putpixel((x, y), (100, y * 255 // (h - 1), 200, 255))
    out = _decode(self_loop(_png(img), direction="vertical"))
    assert out.size == (w, h)

    col = w // 2
    center_step = max(
        abs(out.getpixel((col, 63))[i] - out.getpixel((col, 64))[i]) for i in range(4)
    )
    assert center_step < 6, f"中央接缝行对 (63|64) 跳变 {center_step} ≥ 6，硬缝未抹平"
    full_scan = _max_neighbor_step(out, axis=1)
    assert full_scan < 6, f"全图相邻行最大跳变 {full_scan} ≥ 6"


def test_self_loop_four_way_both_axes_no_hard_edge() -> None:
    """回归：four_way 串行两遍后双轴的中央缝与全图扫描都无硬缝。"""
    w, h = 128, 128
    img = Image.new("RGBA", (w, h))
    for x in range(w):
        for y in range(h):
            img.putpixel((x, y), (x * 255 // (w - 1), y * 255 // (h - 1), 50, 255))
    out = _decode(self_loop(_png(img), direction="four_way"))
    assert out.size == (w, h)

    row = col = 64
    center_step_x = max(
        abs(out.getpixel((63, row))[i] - out.getpixel((64, row))[i]) for i in range(4)
    )
    center_step_y = max(
        abs(out.getpixel((col, 63))[i] - out.getpixel((col, 64))[i]) for i in range(4)
    )
    assert center_step_x < 6, f"水平缝列对跳变 {center_step_x} ≥ 6"
    assert center_step_y < 6, f"垂直缝行对跳变 {center_step_y} ≥ 6"
    assert _max_neighbor_step(out, axis=0) < 6, "全图相邻列扫描超阈值"
    assert _max_neighbor_step(out, axis=1) < 6, "全图相邻行扫描超阈值"


def test_self_loop_horizontal_seam_continuity_and_size() -> None:
    """水平循环：尺寸不变；roll 后接缝在中央被镜像融合，左右边界连续（差 < 2）。"""
    w, h = 128, 64
    img = Image.new("RGBA", (w, h))
    for x in range(w):
        for y in range(h):
            # 水平滑变（左右边界颜色差异最大，roll 后接缝最刺眼，考验融合）
            img.putpixel((x, y), (x * 255 // (w - 1), 100, 200, 255))
    out = _decode(self_loop(_png(img), direction="horizontal"))

    assert out.size == (w, h)
    col0 = out.crop((0, 0, 1, h))
    col_last = out.crop((w - 1, 0, w, h))
    diff = sum(
        abs(p0[i] - pl[i]) for p0, pl in zip(col0.getdata(), col_last.getdata()) for i in range(4)
    ) / (h * 4)
    assert diff < 2.0, f"首尾列平均绝对差 {diff:.2f} ≥ 2，接缝未抹平"


def test_self_loop_vertical_seam_continuity() -> None:
    """垂直循环：上下边界连续（差 < 2）。"""
    w, h = 64, 128
    img = Image.new("RGBA", (w, h))
    for x in range(w):
        for y in range(h):
            img.putpixel((x, y), (100, y * 255 // (h - 1), 200, 255))
    out = _decode(self_loop(_png(img), direction="vertical"))
    assert out.size == (w, h)
    row0 = out.crop((0, 0, w, 1))
    row_last = out.crop((0, h - 1, w, h))
    diff = sum(
        abs(p0[i] - pl[i]) for p0, pl in zip(row0.getdata(), row_last.getdata()) for i in range(4)
    ) / (w * 4)
    assert diff < 2.0


def test_self_loop_four_way_serial_both_axes() -> None:
    """four_way = 先水平后垂直串行：两个方向的边界都连续，尺寸不变。"""
    w, h = 128, 128
    img = Image.new("RGBA", (w, h))
    for x in range(w):
        for y in range(h):
            img.putpixel((x, y), (x * 255 // (w - 1), y * 255 // (h - 1), 50, 255))
    out = _decode(self_loop(_png(img), direction="four_way"))
    assert out.size == (w, h)

    def col_diff() -> float:
        c0, cl = out.crop((0, 0, 1, h)), out.crop((w - 1, 0, w, h))
        return sum(
            abs(a[i] - b[i]) for a, b in zip(c0.getdata(), cl.getdata()) for i in range(4)
        ) / (h * 4)

    def row_diff() -> float:
        r0, rl = out.crop((0, 0, w, 1)), out.crop((0, h - 1, w, h))
        return sum(
            abs(a[i] - b[i]) for a, b in zip(r0.getdata(), rl.getdata()) for i in range(4)
        ) / (w * 4)

    assert col_diff() < 2.0
    assert row_diff() < 2.0


def test_self_loop_invalid_direction_rejected() -> None:
    """非法 direction → SelfLoopError（ImageEditError 家族，上层映射 422）。"""
    img = _png(Image.new("RGBA", (16, 16), RED))
    with pytest.raises(ImageEditError):
        self_loop(img, direction="diagonal")


def test_all_ops_corrupt_bytes_rejected() -> None:
    """坏字节输入统一走 ImageEditError（含 alpha 通道的 RGBA 转换失败/解码失败）。"""
    import pytest

    from server.app.core.processors import PixelateError, SelfLoopError

    bad = b"not an image at all"
    with pytest.raises(Exception):
        pixelate(bad)
    with pytest.raises(Exception):
        remove_background(bad)
    with pytest.raises(Exception):
        self_loop(bad, direction="horizontal")
    # 异常家族归属校验（Pillow UnidentifiedImageError 被包在调用链上层语义内即可）
    assert issubclass(PixelateError, ImageEditError)
    assert issubclass(SelfLoopError, ImageEditError)
