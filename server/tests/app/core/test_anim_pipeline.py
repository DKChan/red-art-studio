"""动画打包线核心管线测试（P5-L1）。

夹具全部 Pillow 代码生成（禁真实外呼、禁读二进制文件）；断言全部走数值取证
（尺寸/n_frames/loop/像素值），不读图。
"""

import io
import math

import pytest
from PIL import Image

from server.app.core.anim_pipeline import (
    AnimationPackResult,
    AnimPipelineError,
    apply_alpha_mode,
    encode_animation_gif,
    encode_animation_webp,
    loop_report,
    pack_spritesheet,
    resolve_alpha_mode,
    run_anim_pack_pipeline,
    unify_palette,
    validate_frames,
)


def _rgba(data: tuple[int, int, int, int], size: tuple[int, int] = (16, 16)) -> Image.Image:
    """纯色 RGBA 帧夹具。"""
    return Image.new("RGBA", size, data)


def _encode_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _decode(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img.load()
    return img.convert("RGBA")


# ---------- 帧完整性校验 ----------


@pytest.mark.parametrize("count", [0, 1, 3, 5, 15, 17])
def test_validate_frames_bad_counts(count: int) -> None:
    """0/1 张（下限）、17 张（上限）、3/5/15 张（奇数）全拒绝。"""
    frames = [_rgba((255, 0, 0, 255))] * count
    with pytest.raises(AnimPipelineError):
        validate_frames(frames, pixel=False)


@pytest.mark.parametrize("count", [2, 16])
def test_validate_frames_boundary_ok(count: int) -> None:
    """2/16 帧边界合法（偶数帧数域 2-16）。"""
    validate_frames([_rgba((255, 0, 0, 255))] * count, pixel=False)


def test_validate_frames_mixed_sizes_rejected() -> None:
    """异尺寸帧拒绝（禁静默缩放）。"""
    with pytest.raises(AnimPipelineError, match="尺寸不一致"):
        validate_frames(
            [_rgba((0, 0, 0, 255), (16, 16)), _rgba((0, 0, 0, 255), (32, 32))], pixel=False
        )


def test_validate_frames_pixel_canvas_257_rejected_256_ok() -> None:
    """pixel=true 画布 257 拒绝 / 256 通过（坑 6 硬校验逐值断言）。"""
    ok_frame = _rgba((0, 0, 0, 255), (256, 256))
    validate_frames([ok_frame, ok_frame], pixel=True)
    big_frame = _rgba((0, 0, 0, 255), (257, 128))
    with pytest.raises(AnimPipelineError, match="256"):
        validate_frames([big_frame, big_frame], pixel=True)
    tall_frame = _rgba((0, 0, 0, 255), (128, 257))
    with pytest.raises(AnimPipelineError, match="256"):
        validate_frames([tall_frame, tall_frame], pixel=True)


def test_validate_frames_non_pixel_canvas_unlimited() -> None:
    """pixel=false 不设画布上限（soft/HD 打包线；256 硬约束官方语义属像素线）。"""
    big = _rgba((0, 0, 0, 255), (512, 512))
    validate_frames([big, big], pixel=False)


# ---------- alpha 路由（坑 8） ----------


@pytest.mark.parametrize(
    ("alpha_mode", "pixel", "expected"),
    [
        (None, True, "sharp"),
        (None, False, "soft"),
        ("sharp", False, "sharp"),
        ("soft", True, "soft"),
        ("sharp", True, "sharp"),
        ("soft", False, "soft"),
    ],
)
def test_resolve_alpha_mode_routing(alpha_mode: str | None, pixel: bool, expected: str) -> None:
    """None+pixel→sharp / None 无 pixel→soft / 显式值覆盖路由。"""
    assert resolve_alpha_mode(alpha_mode, pixel) == expected


def test_apply_alpha_mode_sharp_binarizes() -> None:
    """sharp=α≥128 二值化：80→0、128→255（与 P2 前景判据同口径）。"""
    img = _rgba((255, 0, 0, 255))
    px = img.load()
    px[0, 0] = (255, 0, 0, 80)
    px[1, 1] = (255, 0, 0, 128)
    out = apply_alpha_mode(img, "sharp")
    out_px = out.load()
    assert out_px[0, 0][3] == 0
    assert out_px[1, 1][3] == 255
    assert out_px[5, 5][3] == 255  # 原本不透明的像素不动


def test_apply_alpha_mode_soft_preserves_semitransparent() -> None:
    """soft=保留原 alpha：半透明像素 α 原样保留。"""
    img = _rgba((0, 255, 0, 128))
    out = apply_alpha_mode(img, "soft")
    assert out.load()[3, 3][3] == 128


# ---------- 调色板统一（坑 2 治标） ----------


def _two_color_frame() -> Image.Image:
    """红蓝 2 色首帧夹具（左半红右半蓝，全不透明）。"""
    img = _rgba((0, 255, 0, 255))
    px = img.load()
    for y in range(16):
        for x in range(16):
            px[x, y] = (255, 0, 0, 255) if x < 8 else (0, 0, 255, 255)
    return img


def test_unify_palette_all_frames_within_reference_palette() -> None:
    """统一后各帧颜色集 ⊆ 首帧调色板（验收断言逐字落地）。"""
    first = _two_color_frame()
    off = _rgba((10, 250, 5, 255))  # 首帧调色板外的绿
    unified = unify_palette([first, off, off], 32)
    first_colors = {
        unified[0].load()[x, y][:3]
        for x in range(16)
        for y in range(16)
        if unified[0].load()[x, y][3] > 0
    }
    for frame in unified[1:]:
        colors = {
            frame.load()[x, y][:3]
            for x in range(16)
            for y in range(16)
            if frame.load()[x, y][3] > 0
        }
        assert colors <= first_colors
    # 首帧自身零损失（色数 ≤ color_count 时全量取用）
    assert first_colors == {(255, 0, 0), (0, 0, 255)}


def test_unify_palette_invalid_color_count() -> None:
    """color_count 出域（1/65）拒绝。"""
    first = _two_color_frame()
    with pytest.raises(AnimPipelineError):
        unify_palette([first, first], 1)
    with pytest.raises(AnimPipelineError):
        unify_palette([first, first], 65)


def test_unify_palette_preserves_alpha() -> None:
    """调色板映射只动 RGB，alpha 原样回贴。"""
    img = _rgba((255, 0, 0, 60))
    unified = unify_palette([img, img], 32)
    assert unified[0].load()[3, 3] == (255, 0, 0, 60)


# ---------- loop_report ----------


def test_loop_report_identical_frames_passed() -> None:
    """首末帧全同 → step=0、passed=True；数值与独立计算一致。"""
    frame = _rgba((120, 60, 30, 255))
    metrics = loop_report([frame, frame, frame])
    assert metrics.first_last_max_step == 0
    assert metrics.passed is True


def test_loop_report_value_matches_independent_calculation() -> None:
    """构造已知跳变的序列，报告数值 == 手工计算的最大通道差。"""
    first = _rgba((10, 10, 10, 255))
    mid = _rgba((50, 10, 10, 255))
    # 末帧与首帧最大通道差：|200-10|=190（R 通道）
    last = _rgba((200, 10, 10, 255))
    metrics = loop_report([first, mid, last])
    assert metrics.first_last_max_step == 190
    assert metrics.passed is False
    # 差 5 <6 → passed
    near = _rgba((15, 10, 10, 255))
    metrics_ok = loop_report([first, mid, near])
    assert metrics_ok.first_last_max_step == 5
    assert metrics_ok.passed is True


# ---------- spritesheet 装箱 ----------


def _distinct_frame(idx: int, size: tuple[int, int] = (10, 10)) -> Image.Image:
    """第 idx 帧填充唯一颜色（用于逐格坐标断言）。"""
    img = Image.new("RGBA", size, (idx * 30 % 256, idx * 60 % 256, 100, 255))
    return img


def test_pack_spritesheet_three_frames_2x2_layout() -> None:
    """3 帧 → cols=2、rows=2 布局逐格坐标断言 + 末行空位透明格。"""
    frames = [_distinct_frame(i) for i in range(3)]
    sheet, meta = pack_spritesheet(frames, 125, "idle")
    assert meta.frame_size == (10, 10)
    assert meta.columns == 2
    assert meta.rows == 2
    assert sheet.size == (20, 20)
    px = sheet.load()
    # 帧按提交序行优先排布：(0,0)=帧0 (10,0)=帧1 (0,10)=帧2
    for idx, (ox, oy) in enumerate([(0, 0), (10, 0), (0, 10)]):
        expected = (idx * 30 % 256, idx * 60 % 256, 100, 255)
        assert px[ox + 3, oy + 4] == expected, f"帧 {idx} 逐格坐标断言失败"
    # 末行空位 (10,10) 全透明
    assert px[10, 10] == (0, 0, 0, 0)
    assert px[19, 19] == (0, 0, 0, 0)


def test_pack_spritesheet_deterministic_bytes() -> None:
    """同输入两次装箱字节级一致（确定性锁定）。"""
    frames = [_distinct_frame(i) for i in range(6)]
    sheet_a, meta_a = pack_spritesheet(frames, 125, "walk")
    sheet_b, meta_b = pack_spritesheet(frames, 125, "walk")
    buf_a, buf_b = io.BytesIO(), io.BytesIO()
    sheet_a.save(buf_a, format="PNG")
    sheet_b.save(buf_b, format="PNG")
    assert buf_a.getvalue() == buf_b.getvalue()
    assert meta_a == meta_b


@pytest.mark.parametrize(
    ("count", "cols", "rows"),
    [(2, 2, 1), (4, 2, 2), (8, 3, 3), (16, 4, 4), (6, 3, 2)],
)
def test_pack_spritesheet_grid_math(count: int, cols: int, rows: int) -> None:
    """cols=⌈√n⌉、rows=⌈n/cols⌉ 网格数学逐值断言。"""
    frames = [_distinct_frame(i) for i in range(count)]
    _, meta = pack_spritesheet(frames, 100, "other")
    assert meta.columns == cols
    assert meta.rows == rows
    assert len(meta.frame_durations) == count
    assert all(d == 100 for d in meta.frame_durations)
    assert meta.loop is True  # 官方 §2.3：恒循环元数据


# ---------- 动图编码 ----------


def test_encode_animation_webp_roundtrip() -> None:
    """WebP 回读 n_frames=帧数、loop=0、duration 回读一致（seek+load 后读 info）。"""
    frames = [_distinct_frame(i) for i in range(4)]
    data = encode_animation_webp(frames, 250)
    back = Image.open(io.BytesIO(data))
    assert getattr(back, "n_frames", 1) == 4
    assert back.info.get("loop") == 0
    durations = []
    for i in range(back.n_frames):
        back.seek(i)
        back.load()
        durations.append(back.info.get("duration"))
    assert durations == [250] * 4


def test_encode_animation_webp_lossless_color_fidelity() -> None:
    """无损 WebP 颜色保真：回读像素与源帧一致（含 alpha）。"""
    frames = [_distinct_frame(i) for i in range(4)]
    data = encode_animation_webp(frames, 125)
    back = Image.open(io.BytesIO(data))
    for i in range(4):
        back.seek(i)
        back.load()
        assert back.convert("RGBA").getpixel((5, 5)) == (i * 30 % 256, i * 60 % 256, 100, 255)


def test_encode_animation_gif_roundtrip() -> None:
    """GIF 回读 n_frames/loop=0；时长落 10ms 栅格（125→120，格式规范）。"""
    frames = [_distinct_frame(i) for i in range(4)]
    data = encode_animation_gif(frames, 125)
    back = Image.open(io.BytesIO(data))
    assert getattr(back, "n_frames", 1) == 4
    assert back.info.get("loop") == 0
    back.seek(1)
    back.load()
    # GIF 时长单位 10ms：125ms 编码为 12.5 → 取整 12 → 读回 120（预检实证）
    assert back.info.get("duration") == 120


# ---------- 编排 ----------


def test_run_pipeline_webp_end_to_end() -> None:
    """webp 编排全链路：帧数/尺寸/alpha 回显/报告数值/产物清单。"""
    frames = [_distinct_frame(i) for i in range(4)]
    result = run_anim_pack_pipeline(
        [_encode_png(f) for f in frames],
        output_format="webp",
        duration_ms=200,
        animation_type="idle",
    )
    assert isinstance(result, AnimationPackResult)
    assert result.frame_count == 4
    assert result.frame_size == (10, 10)
    assert result.alpha_mode == "soft"  # pixel=false 缺省路由
    # 首帧 (0,0,100) vs 末帧 (90,180,100)：G 通道差 180（独立计算一致）
    assert result.loop_metrics.first_last_max_step == 180
    assert result.loop_metrics.passed is False
    assert result.files == [("animation.webp", result.files[0][1], "webp")]
    assert result.sheet_meta is None


def test_run_pipeline_pixel_sharp_and_palette() -> None:
    """pixel=true：alpha 路由 sharp 生效；loop_report 对处理后帧算（含 alpha 通道）。"""
    # 次帧 α=100 <128 被 sharp 二值化为全透明 → 首末帧 alpha 通道差 255（如实数值）
    first = _rgba((255, 0, 0, 255))
    second = _rgba((0, 255, 0, 100))
    result = run_anim_pack_pipeline(
        [_encode_png(first), _encode_png(second)],
        output_format="gif",
        pixel=True,
        alpha_mode=None,
    )
    assert result.alpha_mode == "sharp"
    assert result.loop_metrics.first_last_max_step == 255
    assert result.loop_metrics.passed is False

    # 对照：两帧全可见、次帧异色绿 → 统一基准取首帧 {红}，绿被映射为红，
    # 处理后首末帧 RGB 全同（坑 2 治标生效）、跳变 0
    first_opaque = _rgba((255, 0, 0, 255))
    second_opaque = _rgba((0, 255, 0, 255))
    result_ok = run_anim_pack_pipeline(
        [_encode_png(first_opaque), _encode_png(second_opaque)],
        output_format="gif",
        pixel=True,
        alpha_mode=None,
    )
    assert result_ok.loop_metrics.first_last_max_step == 0
    assert result_ok.loop_metrics.passed is True


def test_run_pipeline_spritesheet_meta_and_files() -> None:
    """spritesheet 编排：sheet.png + sheet_meta 填充。"""
    frames = [_distinct_frame(i) for i in range(4)]
    result = run_anim_pack_pipeline(
        [_encode_png(f) for f in frames],
        output_format="spritesheet",
        animation_type="attack",
    )
    assert result.files[0][0] == "sheet.png"
    assert result.sheet_meta is not None
    assert result.sheet_meta.animation_type == "attack"
    assert result.sheet_meta.columns == 2
    assert math.ceil(4 / 2) == result.sheet_meta.rows


def test_run_pipeline_unknown_format_rejected() -> None:
    """output_format 出域（mp4）拒绝。"""
    with pytest.raises(AnimPipelineError):
        run_anim_pack_pipeline(
            [_encode_png(_rgba((0, 0, 0, 255)))] * 2, output_format="mp4"
        )


def test_run_pipeline_undecodable_frame_rejected() -> None:
    """坏帧字节 → AnimPipelineError（不悬空、不裸抛 PIL 异常）。"""
    good = _encode_png(_rgba((0, 0, 0, 255)))
    with pytest.raises(AnimPipelineError, match="无法解码"):
        run_anim_pack_pipeline([good, b"not-an-image"], output_format="webp")
