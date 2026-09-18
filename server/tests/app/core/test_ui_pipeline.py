"""UI 生成线核心管线测试（P4-L1 验收 1 + P4-L2 extract 提取重排）。

覆盖：分辨率档位矩阵逐值（5 长宽比 × 2 档全组合）、连通域分割（分离块 bbox
精确断言 / min_area 噪点过滤 / 角相触粘连合并 / 全透明 0 组件）、质量门禁
三规则独立断言、run_ui_pipeline 编排（matte 去背 / remove_bg=false 强制 /
split=false 仅 sheet）；L2：shelf_repack 布局精确断言 / 排序确定性 / 双坐标 /
字节级复现、run_ui_extract_pipeline 编排（显式 matte / 自动扫描 / 门禁复用 /
数量防线）。夹具全部 Pillow 代码生成。
"""

import io

import pytest
from PIL import Image

from server.app.core.ui_pipeline import (
    _GIANT_COMPONENT_RATIO,
    Component,
    UiPipelineError,
    quality_gate,
    resolution_size,
    run_ui_extract_pipeline,
    run_ui_pipeline,
    segment_components,
    shelf_repack,
)


def _fill(
    img: Image.Image,
    box: tuple[int, int, int, int],
    color: tuple[int, int, int, int],
) -> None:
    """在 img 上填充矩形 (x0, y0, x1_excl, y1_excl)（测试夹具专用）。"""
    px = img.load()
    x0, y0, x1, y1 = box
    for y in range(y0, y1):
        for x in range(x0, x1):
            px[x, y] = color


# ---------- resolution_size 档位矩阵（10 组合逐值） ----------


@pytest.mark.parametrize(
    ("resolution", "aspect", "expected"),
    [
        ("1k", "1:1", (1024, 1024)),
        ("1k", "4:3", (1024, 768)),
        ("1k", "3:4", (768, 1024)),
        ("1k", "16:9", (1024, 576)),
        ("1k", "9:16", (576, 1024)),
        ("2k", "1:1", (2048, 2048)),
        ("2k", "4:3", (2048, 1536)),
        ("2k", "3:4", (1536, 2048)),
        ("2k", "16:9", (2048, 1156)),
        ("2k", "9:16", (1156, 2048)),
    ],
)
def test_resolution_size_matrix(resolution: str, aspect: str, expected: tuple[int, int]) -> None:
    """档位映射表逐值断言（5 长宽比 × 2 档全组合；映射表为推断约定，坑 4）。"""
    assert resolution_size(resolution, aspect) == expected


@pytest.mark.parametrize(
    ("resolution", "aspect"),
    [("3k", "1:1"), ("1k", "5:4"), ("", "1:1"), ("1k", "")],
)
def test_resolution_size_rejects_unknown(resolution: str, aspect: str) -> None:
    """未知档位/长宽比组合抛 UiPipelineError（不静默回落）。"""
    with pytest.raises(UiPipelineError):
        resolution_size(resolution, aspect)


# ---------- segment_components ----------


def test_segment_three_separate_blocks_exact_bbox() -> None:
    """3 个分离色块 → 3 组件，bbox 与面积精确断言；label 顺序编号。"""
    img = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    _fill(img, (10, 10, 30, 30), (255, 0, 0, 255))  # 20×20 = 400px
    _fill(img, (50, 40, 80, 70), (0, 255, 0, 255))  # 30×30 = 900px
    _fill(img, (85, 5, 95, 15), (0, 0, 255, 255))  # 10×10 = 100px

    comps = segment_components(img)
    assert len(comps) == 3
    # 发现顺序 = 自上而下、自左而右扫描：右上小蓝块最先
    assert [(c.label, c.bbox, c.area_px) for c in comps] == [
        ("component_01", (85, 5, 10, 10), 100),
        ("component_02", (10, 10, 20, 20), 400),
        ("component_03", (50, 40, 30, 30), 900),
    ]
    assert [c.id for c in comps] == [0, 1, 2]


def test_segment_min_area_filters_noise() -> None:
    """min_area 噪点过滤：小连通域被丢弃，大块保留且 label 连续重编号。"""
    img = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    _fill(img, (0, 0, 10, 10), (255, 255, 255, 255))  # 100px：恰好过缺省阈值
    _fill(img, (50, 50, 53, 53), (255, 255, 255, 255))  # 9px：噪点
    _fill(img, (80, 80, 100, 90), (255, 255, 255, 255))  # 200px

    comps = segment_components(img, min_area=10)
    assert len(comps) == 2
    # 噪点不占编号：label 连续（component_01/02 而非 01/03）
    assert [c.label for c in comps] == ["component_01", "component_02"]
    assert comps[0].bbox == (0, 0, 10, 10)
    assert comps[1].bbox == (80, 80, 20, 10)


def test_segment_diagonal_touch_merges() -> None:
    """角相触的两个色块被判**合并**（8-连通判据的语义断言：相触即粘连）。"""
    img = Image.new("RGBA", (50, 50), (0, 0, 0, 0))
    _fill(img, (5, 5, 25, 25), (255, 0, 0, 255))
    _fill(img, (25, 25, 45, 45), (0, 0, 255, 255))  # 仅 (25,25) 与红块角相触

    comps = segment_components(img)
    assert len(comps) == 1
    assert comps[0].bbox == (5, 5, 40, 40)
    assert comps[0].area_px == 400 + 400


def test_segment_gap_of_one_pixel_keeps_separate() -> None:
    """1px 间隔（非相触）的两个色块保持分离（对照角相触用例）。"""
    img = Image.new("RGBA", (50, 50), (0, 0, 0, 0))
    _fill(img, (5, 5, 25, 25), (255, 0, 0, 255))
    _fill(img, (26, 26, 46, 46), (0, 0, 255, 255))  # (26,26) 与红块 (25,25) 间隔 1px

    assert len(segment_components(img)) == 2


def test_segment_fully_transparent_zero_components() -> None:
    """全透明图 → 0 组件。"""
    img = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
    assert segment_components(img) == []


def test_segment_alpha_threshold_binary() -> None:
    """前景判据 alpha≥128：α=127 不算前景、α=128 算（与 P2 二值化口径一致）。"""
    img = Image.new("RGBA", (30, 10), (0, 0, 0, 0))
    px = img.load()
    for x in range(10):
        px[x, 5] = (255, 0, 0, 127)
    for x in range(20, 30):
        px[x, 5] = (255, 0, 0, 128)
    comps = segment_components(img, min_area=1)
    assert len(comps) == 1
    assert comps[0].bbox == (20, 5, 10, 1)


def test_segment_rejects_non_rgba_and_bad_min_area() -> None:
    """非 RGBA 输入 / min_area<1 抛 UiPipelineError。"""
    with pytest.raises(UiPipelineError):
        segment_components(Image.new("RGB", (10, 10)))
    with pytest.raises(UiPipelineError):
        segment_components(Image.new("RGBA", (10, 10)), min_area=0)


def test_segment_single_giant_blob_bbox() -> None:
    """单块充满画布的图 → 1 组件且 bbox=全画布（门禁巨型粘连的前置场景）。"""
    img = Image.new("RGBA", (64, 64), (255, 0, 0, 255))
    comps = segment_components(img, min_area=1)
    assert len(comps) == 1
    assert comps[0].bbox == (0, 0, 64, 64)
    assert comps[0].area_px == 64 * 64


# ---------- quality_gate（三规则独立断言） ----------


def _comp(bbox: tuple[int, int, int, int], area: int | None = None) -> Component:
    x, y, w, h = bbox
    return Component(id=0, label="component_00", bbox=bbox, area_px=area or w * h)


def test_gate_rule1_zero_components_fails() -> None:
    """规则 ①：组件数 0 → passed=False。"""
    report = quality_gate([], 100, 100)
    assert report.component_count == 0
    assert report.passed is False


def test_gate_rule2_giant_component_flagged_not_failed() -> None:
    """规则 ②：单组件面积占比超阈值 → giant_components 置 flag。

    门禁只出报告数据不判失败——巨型组件本身不改变 passed 之外的任何终态语义
    （job 仍 succeeded 由执行器语义保证，此处断言报告如实记录）。
    """
    # 占画布 50%（> 0.4 阈值）的单组件
    comps = [_comp((0, 0, 50, 100), area=50 * 100)]
    report = quality_gate(comps, 100, 100)
    assert report.giant_components == ("component_00",)
    assert report.passed is False  # 巨型即 passed=False（三规则之一未过）
    # 阈值边界：恰好在阈值上的不算巨型（> 严格大于）
    boundary_area = int(100 * 100 * _GIANT_COMPONENT_RATIO)
    ok = quality_gate(
        [_comp((0, 0, 100, 100), area=boundary_area)], 100, 100
    )
    assert ok.giant_components == ()


def test_gate_rule2_healthy_layout_passes() -> None:
    """健康的整表布局（多个中等组件）→ passed=True。"""
    img_comps = [
        _comp((0, 0, 30, 30)),
        _comp((40, 0, 30, 30)),
        _comp((0, 40, 30, 30)),
    ]
    report = quality_gate(img_comps, 100, 100)
    assert report.passed is True
    assert report.giant_components == ()
    assert report.overlaps == ()


def test_gate_rule3_bbox_overlap_detected_with_ratio() -> None:
    """规则 ③：bbox 两两重叠 → 重叠对列出，比率按较小组件面积归一。"""
    comps = [
        _comp((0, 0, 20, 20)),  # 400px
        _comp((10, 10, 20, 20)),  # 交叠 10×10=100px → 100/400=0.25
        _comp((50, 50, 10, 10)),  # 独立块
    ]
    report = quality_gate(comps, 100, 100)
    assert len(report.overlaps) == 1
    pair = report.overlaps[0]
    assert {pair.a_label, pair.b_label} == {"component_00", "component_00"}
    assert pair.ratio == pytest.approx(0.25)
    assert report.passed is False


def test_gate_rule3_touching_bbox_not_reported() -> None:
    """贴边相触（交集为 0）不报重叠：斜置元素外接矩形相触是布局常态。"""
    comps = [_comp((0, 0, 10, 10)), _comp((10, 0, 10, 10))]
    report = quality_gate(comps, 100, 100)
    assert report.overlaps == ()
    assert report.passed is True


# ---------- run_ui_pipeline 编排 ----------


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_pipeline_matte_keying_and_split() -> None:
    """灰 matte 底 + 两色块 → 去背 2 组件、角落透明、actual_size 正确。"""
    img = Image.new("RGB", (200, 200), (204, 204, 204))
    _fill(img, (20, 20, 60, 60), (200, 30, 30))
    _fill(img, (100, 100, 160, 150), (30, 30, 200))
    result = run_ui_pipeline(
        _png(img), matte_color="#cccccc", remove_bg=True, split=True
    )
    assert result.actual_size == (200, 200)
    assert [c.label for c in result.components] == ["component_01", "component_02"]
    assert result.gate.passed is True

    sheet = Image.open(io.BytesIO(result.sheet_png))
    assert sheet.mode == "RGBA"
    px = sheet.load()
    assert px[0, 0][3] == 0  # matte 变透明
    assert px[40, 40][3] == 255  # 色块保留


def test_pipeline_remove_bg_false_forces_none() -> None:
    """remove_background=false → 强制不去背：matte 被当作一个巨型前景组件。

    坑 2 优先级关系的行为断言：关掉去背后整个画布（含 matte）保留，alpha 连通
    域把整张图分成 1 个组件且 gate 巨型告警——「档位 none」的可观测语义。
    """
    img = Image.new("RGB", (100, 100), (204, 204, 204))
    _fill(img, (20, 20, 40, 40), (200, 30, 30))
    result = run_ui_pipeline(
        _png(img), matte_color="#cccccc", remove_bg=False, split=True
    )
    sheet = Image.open(io.BytesIO(result.sheet_png))
    px = sheet.load()
    assert px[0, 0][3] == 255  # matte 原样保留（未去背）
    assert len(result.components) == 1
    assert result.gate.giant_components == ("component_01",)
    assert result.gate.passed is False


def test_pipeline_split_false_delivers_sheet_only() -> None:
    """split_components=false → 仅跳过组件数据交付：components 空、gate 0 组件，
    聚合表照常交付（坑 9 假设的实现口径）。"""
    img = Image.new("RGB", (80, 80), (204, 204, 204))
    _fill(img, (10, 10, 40, 40), (30, 200, 30))
    result = run_ui_pipeline(
        _png(img), matte_color="#cccccc", remove_bg=True, split=False
    )
    assert result.has_components is False
    assert result.components == []
    assert result.gate.component_count == 0
    # 聚合表照常交付且已去背
    sheet = Image.open(io.BytesIO(result.sheet_png))
    px = sheet.load()
    assert px[0, 0][3] == 0
    assert px[20, 20][3] == 255


# ---------- P4-L2：shelf_repack（布局精确断言 / 排序 / 双坐标 / 确定性） ----------


def _sheet_with(
    blocks: list[tuple[tuple[int, int, int, int], tuple[int, int, int, int]]],
    size: tuple[int, int] = (200, 200),
) -> Image.Image:
    """透明底 RGBA 图 + 若干不透明矩形块 [(bbox, color)]（extract 源图夹具）。"""
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    for box, color in blocks:
        _fill(img, box, color)
    return img


def _rgba_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_shelf_repack_layout_exact() -> None:
    """单源图 3 块 → 面积降序行装箱，画布/组件坐标逐值断言。

    块：A 60×40（2400）/ B 30×30（900）/ C 20×10（200）。
    行 1（目标 1024 足宽）：A(8,8) → cursor 76；B(76,8) → cursor 114；
    C(114,8) → cursor 142。max_right = 142-8=134 → 画布宽 142；
    content_h = 8+40=48 → 取整到 8 倍数 = 48。
    """
    sheet = _sheet_with(
        [
            ((10, 10, 70, 50), (255, 0, 0, 255)),  # A：60×40 @ (10,10)
            ((10, 100, 40, 130), (0, 255, 0, 255)),  # B：30×30 @ (10,100)
            ((100, 10, 120, 20), (0, 0, 255, 255)),  # C：20×10 @ (100,10)
        ]
    )
    comps = segment_components(sheet, min_area=1)
    assert len(comps) == 3
    canvas, repacked = shelf_repack([sheet], [comps])
    assert canvas.size == (142, 48)
    assert [(r.label, r.source_index, r.source_bbox, r.bbox, r.area_px) for r in repacked] == [
        ("component_01", 0, (10, 10, 60, 40), (8, 8, 60, 40), 2400),
        ("component_02", 0, (10, 100, 30, 30), (76, 8, 30, 30), 900),
        ("component_03", 0, (100, 10, 20, 10), (114, 8, 20, 10), 200),
    ]
    # 像素级忠实：A 块原色原样出现在聚合表 (8,8) 起
    px = canvas.load()
    assert px[8, 8] == (255, 0, 0, 255)
    assert px[105, 8] == (0, 255, 0, 255)  # B @ 表内 (76+29, 8+8)
    assert px[0, 0][3] == 0  # 画布角落透明


def test_shelf_repack_groups_by_source_then_area_desc() -> None:
    """排序语义：source_index 为主键（分组），图内面积降序为次键。

    源图 0 的小块必须排在源图 1 的大块之前（分组优先于面积）。
    """
    s0 = _sheet_with([((0, 0, 10, 10), (255, 0, 0, 255))])  # 100px 小块
    s1 = _sheet_with([((0, 0, 50, 50), (0, 255, 0, 255))])  # 2500px 大块
    c0 = segment_components(s0, min_area=1)
    c1 = segment_components(s1, min_area=1)
    _, repacked = shelf_repack([s0, s1], [c0, c1])
    assert [r.source_index for r in repacked] == [0, 1]
    assert repacked[0].area_px == 100  # 源图 0 的小块在前
    assert repacked[1].area_px == 2500


def test_shelf_repack_label_globally_renumbered() -> None:
    """多源图 label 全局重编号：两图各有 1 组件 → component_01/02（不重名）。"""
    s0 = _sheet_with([((0, 0, 20, 20), (255, 0, 0, 255))])
    s1 = _sheet_with([((0, 0, 20, 20), (0, 255, 0, 255))])
    _, repacked = shelf_repack(
        [s0, s1], [segment_components(s0, min_area=1), segment_components(s1, min_area=1)]
    )
    assert [r.label for r in repacked] == ["component_01", "component_02"]


def test_shelf_repack_deterministic_bytes() -> None:
    """同输入两次运行字节级一致（确定性装箱，验收条款）。"""
    sheet = _sheet_with(
        [
            ((5, 5, 65, 45), (255, 0, 0, 255)),
            ((5, 60, 45, 100), (0, 255, 0, 255)),
            ((80, 5, 110, 35), (0, 0, 255, 255)),
        ]
    )
    comps = segment_components(sheet, min_area=1)
    canvas1, repacked1 = shelf_repack([sheet], [comps])
    canvas2, repacked2 = shelf_repack([sheet], [comps])
    assert repacked1 == repacked2
    buf1, buf2 = io.BytesIO(), io.BytesIO()
    canvas1.save(buf1, format="PNG")
    canvas2.save(buf2, format="PNG")
    assert buf1.getvalue() == buf2.getvalue()


def test_shelf_repack_components_not_touching() -> None:
    """装箱保证组件间 ≥padding：任意两组件 bbox 无交且不贴边（观感分离）。"""
    sheet = _sheet_with(
        [
            ((0, 0, 30, 30), (255, 0, 0, 255)),
            ((40, 0, 70, 30), (0, 255, 0, 255)),
            ((0, 40, 30, 70), (0, 0, 255, 255)),
        ]
    )
    comps = segment_components(sheet, min_area=1)
    canvas, repacked = shelf_repack([sheet], [comps])
    assert len(repacked) == 3
    for i in range(len(repacked)):
        for j in range(i + 1, len(repacked)):
            ax, ay, aw, ah = repacked[i].bbox
            bx, by, bw, bh = repacked[j].bbox
            # 交叠或贴边（间隔 <padding）都算装箱失败
            assert ax + aw + 8 <= bx or bx + bw + 8 <= ax or ay + ah + 8 <= by or by + bh + 8 <= ay
    assert canvas.mode == "RGBA"


def test_shelf_repack_oversize_component_gets_own_row() -> None:
    """超行宽组件独占一行（行首组件永不触发换行），画布随之变宽。"""
    wide = _sheet_with([((0, 0, 200, 20), (255, 0, 0, 255))], size=(200, 20))  # 200 宽
    small = _sheet_with([((0, 0, 10, 10), (0, 255, 0, 255))])
    canvas, repacked = shelf_repack(
        [wide, small],
        [segment_components(wide, min_area=1), segment_components(small, min_area=1)],
        target_row_width=128,
    )
    assert [r.bbox for r in repacked] == [(8, 8, 200, 20), (8, 36, 10, 10)]
    # 画布宽由第一行决定：8 + 200 = 208 内容右缘 + 8 padding = 216
    assert canvas.size == (216, 48)


def test_shelf_repack_rejects_empty_and_mismatched() -> None:
    """空输入 / 长度不等 / 全零组件 → UiPipelineError。"""
    sheet = _sheet_with([((0, 0, 10, 10), (255, 0, 0, 255))])
    comps = segment_components(sheet, min_area=1)
    with pytest.raises(UiPipelineError):
        shelf_repack([], [])
    with pytest.raises(UiPipelineError):
        shelf_repack([sheet], [])
    with pytest.raises(UiPipelineError):
        shelf_repack([sheet, sheet], [comps])
    with pytest.raises(UiPipelineError):
        shelf_repack([sheet], [[]])


# ---------- P4-L2：run_ui_extract_pipeline（编排） ----------


def _matte_ui_png(
    matte: tuple[int, int, int],
    blocks: list[tuple[tuple[int, int, int, int], tuple[int, int, int]]],
    size: tuple[int, int] = (120, 120),
) -> bytes:
    """纯色 matte 底 + 若干不透明色块的不透明 UI 图（extract 参考图夹具）。"""
    img = Image.new("RGB", size, matte)
    for box, color in blocks:
        _fill(img, box, (*color, 255))
    return _rgba_png(img.convert("RGBA"))


def test_extract_pipeline_explicit_matte_end_to_end() -> None:
    """显式 matte：两图同底色各 1 块 → 2 组件重排，双坐标 + 去背 + 门禁全链断言。

    显式 matte 是**全请求单一色**（官方 background_color 参数语义）：所有参考图
    用同一色键，不逐图匹配。
    """
    img1 = _matte_ui_png((204, 204, 204), [((10, 10, 60, 50), (200, 30, 30))])
    img2 = _matte_ui_png((204, 204, 204), [((20, 20, 70, 60), (30, 30, 200))])
    result = run_ui_extract_pipeline([img1, img2], matte_color="#cccccc")

    assert result.actual_size[0] > 0 and result.actual_size[1] > 0
    assert [c.source_index for c in result.components] == [0, 1]
    # 双坐标：源 bbox 与表内 bbox 尺寸一致（抠图保真）
    for c in result.components:
        assert c.bbox[2:] == c.source_bbox[2:]
    sheet = Image.open(io.BytesIO(result.sheet_png))
    px = sheet.load()
    for c in result.components:
        assert px[c.bbox[0], c.bbox[1]][3] == 255  # 组件本体不透明
    assert px[0, 0][3] == 0  # 画布空角透明
    # 门禁复用：2 组件无巨型无重叠 → passed
    assert result.gate.component_count == 2
    assert result.gate.passed is True


def test_extract_pipeline_auto_scan_background() -> None:
    """不传 matte → 逐图四角扫描自动取底色（非官方五枚举的底色也能去背）。

    两块夹具（单块会占重排画布 40%+ 触发巨型告警——重排画布内容贴合，
    巨型阈值按 generate 画布标定在 extract 下偏保守，宁多勿漏）。
    """
    img = _matte_ui_png(
        (18, 52, 86),
        [((10, 10, 50, 50), (200, 30, 30)), ((60, 10, 100, 50), (30, 30, 200))],
    )
    result = run_ui_extract_pipeline([img])  # matte_color=None
    assert len(result.components) == 2
    assert result.gate.component_count == 2
    assert result.gate.passed is True
    sheet = Image.open(io.BytesIO(result.sheet_png))
    assert sheet.getextrema()[3][0] == 0  # 底色被抠掉（存在透明像素）


def test_extract_pipeline_zero_components_all_images() -> None:
    """全部源图纯色无组件 → UiPipelineError（无产物可交付）。"""
    img = _matte_ui_png((204, 204, 204), [])
    with pytest.raises(UiPipelineError):
        run_ui_extract_pipeline([img])


def test_extract_pipeline_image_count_guard() -> None:
    """0 张 / 9 张 → UiPipelineError（管线层双防；路由层防线在路由测试）。"""
    img = _matte_ui_png((204, 204, 204), [((10, 10, 30, 30), (200, 30, 30))])
    with pytest.raises(UiPipelineError):
        run_ui_extract_pipeline([])
    with pytest.raises(UiPipelineError):
        run_ui_extract_pipeline([img] * 9)


def test_extract_pipeline_gate_giant_reported_not_failed() -> None:
    """matte 不匹配 → 整图成 1 巨型组件：门禁如实告警，job 语义不受影响。

    「指标如实交付」哲学在 extract 下的对应用例：色键没抠干净时不抛错，
    giant_components 如实记录（用户决定是否换 matte 重提）。
    """
    img = _matte_ui_png((204, 204, 204), [((10, 10, 60, 60), (200, 30, 30))])
    result = run_ui_extract_pipeline([img], matte_color="#000000")  # matte 与实际底不符
    assert len(result.components) == 1
    assert result.gate.giant_components == ("component_01",)
    assert result.gate.passed is False
