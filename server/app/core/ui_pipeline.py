"""UI 生成线核心管线（P4-L1，调研文档 06 §1.2/§2/§4.1）。

bytes→(PNG bytes, 组件分割数据) 确定性纯函数（铁律 #3），provider 无关：provider
只负责在 matte 底色上出整表，色键去背（复用 P2 processors.remove_background，
只读不重写）与 alpha 连通域组件分割全部在本模块完成：

1. resolution_size   —— 官方"分辨率档位"（1K/2K × 五长宽比）→ 本地固定像素
   映射（**推断约定**：官方只承诺档位不披露精确像素矩阵，调研文档 06 §5-4；
   实际输出尺寸写进 components.json actual_size，消费方以元数据为准）；
2. remove_background —— matte 色 = background_color 参数值（调研文档 06 §5-3
   推断：五枚举的意义是给去背留 matte）；
3. segment_components —— 对去背后 RGBA 的 alpha 通道做连通域分析。**纯
   Pillow + stdlib 实现，零新依赖**（环境无科学计算库；语义分割类本地模型
   依赖属调研文档 §4.2 路线 B/C，ADR-001 排除/后移）。连通判据选 **8-连通**：
   UI 资产的斜边/抗锯齿残点常以角接触相连，4-连通会把"视觉上一块"碎成多个
   组件；代价是元素角相触即粘连（路线 A 的已知局限，如实接受）；
4. quality_gate      —— 调研文档 06 §5-7 自动门禁（组件数>0 / 巨型粘连告警 /
   bbox 两两重叠）。结果为报告数据**不判 failed**——与 P3-L2 检缝同哲学：
   指标如实进产物，是否重生成由用户决定。

组件 label 为 component_01 起顺序编号，**无语义标签**（语义分割属路线 B/C，
已后移——诚实标注，不编造）。

extract 模式（P4-L2，调研文档 06 §1.1/§4.3）：1-8 张参考图 → 逐图色键去背 →
复用 segment_components 分割 → shelf_repack 确定性重排成一张可复用观感聚合表。
**重排是确定性装箱**（路线收敛：生成式重排依赖图生图通道，ADR-001 排除）——
像素级忠实（从源图按 bbox 抠组件含 alpha 原样贴入）、零风格漂移，同输入两次
运行字节级一致。
"""

import logging
from dataclasses import dataclass

from PIL import Image

from server.app.core.processors import (
    _ALPHA_BINARIZE_THRESHOLD,
    ImageEditError,
    _decode,
    _encode,
    remove_background,
    scan_background_color,
)

logger = logging.getLogger(__name__)

# 色键容差（0-255 色距，切比雪夫口径，与 P2 processors 同语义）：matte 是服务端
# 控制的纯色底，生成内容与它的色距普遍很大，32 与 P2 remove_background 缺省一致
_KEY_TOLERANCE = 32
# min_area 噪点过滤缺省值（**推断约定**，官方未披露）：约 1K 画布万分之一的像素
# 团（100px）以下视为去背残渣；先取绝对值保持语义简单可测
_DEFAULT_MIN_AREA = 100
# 巨型粘连告警阈值：单组件面积占画布比例（**推断约定**，调研文档 06 §5-7 只说
# "超阈值告警"未给数值）。取 0.4：整表布局下单个组件吞掉四成以上画布，通常是
# 去背失败或元素粘连的信号；不取 0.6（任务书举例值）是因为合法的整幅大面板
# （如全屏对话框底）可占五成上下，0.4 与 0.6 之间取保守端，告警宁多勿漏
_GIANT_COMPONENT_RATIO = 0.4


class UiPipelineError(ImageEditError):
    """UI 流水线输入非法（ValueError 家族，上层统一映射 422/failed）。"""


# ---------- 1) 分辨率档位（推断约定，见模块 docstring） ----------

# 1K/2K × 五长宽比 → 像素：短边 1024/2048，宽高比换算（16:9 高取整到偶数）。
# 档位语义是"服务分辨率档"非精确承诺，映射表为本服务自定契约（坑 4）
_RESOLUTION_SIZES: dict[tuple[str, str], tuple[int, int]] = {
    ("1k", "1:1"): (1024, 1024),
    ("1k", "4:3"): (1024, 768),
    ("1k", "3:4"): (768, 1024),
    ("1k", "16:9"): (1024, 576),
    ("1k", "9:16"): (576, 1024),
    ("2k", "1:1"): (2048, 2048),
    ("2k", "4:3"): (2048, 1536),
    ("2k", "3:4"): (1536, 2048),
    ("2k", "16:9"): (2048, 1156),
    ("2k", "9:16"): (1156, 2048),
}


def resolution_size(resolution: str, aspect_ratio: str) -> tuple[int, int]:
    """分辨率档位 × 长宽比 → (宽, 高)。未知组合抛 UiPipelineError。

    映射表是本地固定像素契约（推断约定：官方只承诺档位不披露精确矩阵）；
    实际输出尺寸必须写进 components.json 的 actual_size，消费方以元数据为准。
    """
    key = (resolution.lower(), aspect_ratio)
    if key not in _RESOLUTION_SIZES:
        raise UiPipelineError(
            f"未知分辨率档位组合：resolution={resolution!r} aspect_ratio={aspect_ratio!r}"
        )
    return _RESOLUTION_SIZES[key]


# ---------- 3) alpha 连通域组件分割 ----------


@dataclass(frozen=True)
class Component:
    """单个组件的分割数据（components.json 的元素；schema 为自定，官方未披露）。"""

    id: int
    label: str
    bbox: tuple[int, int, int, int]  # (x, y, w, h)
    area_px: int


def segment_components(img: Image.Image, min_area: int = _DEFAULT_MIN_AREA) -> list[Component]:
    """对 RGBA 图的 alpha 通道做 8-连通域分析 → 组件列表。

    - 连通判据：**8-连通**（角接触算同一组件）——UI 资产的斜边/抗锯齿残点常以
      角接触相连，4-连通会把"视觉上一块"碎成多个组件；代价：相触色块被判合并
      （粘连），这是路线 A 的已知语义，如实接受；
    - 前景判据：alpha ≥128（与 P2 完美像素二值化阈值同口径）；
    - min_area：小于该像素数的连通域视为噪点丢弃（去背残渣过滤）；
    - label 为 component_01 起顺序编号（发现顺序 = 自上而下、自左而右扫描），
      **无语义标签**——语义分割属路线 B/C 已后移，不编造。
    """
    if img.mode != "RGBA":
        raise UiPipelineError(f"segment_components 输入必须是 RGBA，实际 {img.mode}")
    if min_area < 1:
        raise UiPipelineError(f"min_area 必须 ≥1：{min_area}")

    # 二值化 alpha 后整体取字节（C 层拷贝，逐像素访问比 load() 快一个量级）
    alpha_bin = img.getchannel("A").point(lambda v: 255 if v >= _ALPHA_BINARIZE_THRESHOLD else 0)
    bbox = alpha_bin.getbbox()
    if bbox is None:
        return []  # 全透明：0 组件
    w, h = img.size
    data = alpha_bin.tobytes()

    # visited：-2=未访问前景 / -1=背景 / ≥0=已归属组件号
    labels = [-2 if data[i] else -1 for i in range(w * h)]
    components: list[Component] = []
    for start in range(w * h):
        if labels[start] != -2:
            continue
        comp_id = len(components)
        labels[start] = comp_id
        queue = [start]
        head = 0
        min_x = max_x = start % w
        min_y = max_y = start // w
        area = 0
        while head < len(queue):
            idx = queue[head]
            head += 1
            area += 1
            cx, cy = idx % w, idx // w
            if cx < min_x:
                min_x = cx
            elif cx > max_x:
                max_x = cx
            if cy < min_y:
                min_y = cy
            elif cy > max_y:
                max_y = cy
            # 8-邻域（含对角）：8-连通判据，见 docstring
            y_lo = cy > 0
            y_hi = cy < h - 1
            x_lo = cx > 0
            x_hi = cx < w - 1
            if y_lo:
                row = idx - w
                if x_lo and labels[row - 1] == -2:
                    labels[row - 1] = comp_id
                    queue.append(row - 1)
                if labels[row] == -2:
                    labels[row] = comp_id
                    queue.append(row)
                if x_hi and labels[row + 1] == -2:
                    labels[row + 1] = comp_id
                    queue.append(row + 1)
            if x_lo and labels[idx - 1] == -2:
                labels[idx - 1] = comp_id
                queue.append(idx - 1)
            if x_hi and labels[idx + 1] == -2:
                labels[idx + 1] = comp_id
                queue.append(idx + 1)
            if y_hi:
                row = idx + w
                if x_lo and labels[row - 1] == -2:
                    labels[row - 1] = comp_id
                    queue.append(row - 1)
                if labels[row] == -2:
                    labels[row] = comp_id
                    queue.append(row)
                if x_hi and labels[row + 1] == -2:
                    labels[row + 1] = comp_id
                    queue.append(row + 1)
        if area >= min_area:
            components.append(
                Component(
                    id=comp_id,
                    label=f"component_{len(components) + 1:02d}",
                    bbox=(min_x, min_y, max_x - min_x + 1, max_y - min_y + 1),
                    area_px=area,
                )
            )
        # 未过 min_area 的连通域不占编号（label 连续性优先于物理块计数）

    logger.debug("组件分割：%d 个组件（min_area=%d）", len(components), min_area)
    return components


# ---------- 4) 质量门禁（报告数据，不判 failed） ----------


@dataclass(frozen=True)
class OverlapPair:
    """一对 bbox 重叠的组件（重叠率按较小组件的面积归一，0-1]。"""

    a_label: str
    b_label: str
    ratio: float


@dataclass(frozen=True)
class QualityGateReport:
    """调研文档 06 §5-7 三规则的门禁报告（随 components.json 交付）。

    passed 语义：三规则全过（组件数>0 且无巨型粘连且无重叠对）。**报告数据
    不判 job failed**——pollinations 等通道出图带杂色背景导致粘连属常态，
    指标如实进产物，是否重生成由用户决定（与 P3-L2 检缝同哲学）。
    """

    component_count: int
    passed: bool
    giant_components: tuple[str, ...] = ()
    overlaps: tuple[OverlapPair, ...] = ()


def quality_gate(
    components: list[Component], canvas_w: int, canvas_h: int
) -> QualityGateReport:
    """对分割结果跑 §5-7 三规则：组件数>0 / 巨型粘连告警 / bbox 两两重叠。

    - 规则 ①：组件数 >0（全透明或全被噪点过滤 → 组件数 0，passed=False）；
    - 规则 ②：单组件面积占画布 > _GIANT_COMPONENT_RATIO 记入 giant_components
      （标签列表），置 flag 不失败；
    - 规则 ③：bbox 两两求交，交叠面积 / 较小组件 bbox 面积 > 0 记入 overlaps
      （组件 bbox 允许相触——斜置元素的外接矩形天然交叠，只有真交叠才报）。

    结果为报告数据**不判 failed**，见 QualityGateReport docstring。
    """
    giant: list[str] = []
    canvas_area = canvas_w * canvas_h
    for comp in components:
        if canvas_area > 0 and comp.area_px / canvas_area > _GIANT_COMPONENT_RATIO:
            giant.append(comp.label)

    overlaps: list[OverlapPair] = []
    for i in range(len(components)):
        for j in range(i + 1, len(components)):
            a, b = components[i], components[j]
            ax, ay, aw, ah = a.bbox
            bx, by, bw, bh = b.bbox
            inter_w = min(ax + aw, bx + bw) - max(ax, bx)
            inter_h = min(ay + ah, by + bh) - max(ay, by)
            if inter_w <= 0 or inter_h <= 0:
                continue  # 不相交（贴边相触 inter=0 不报）
            inter_area = inter_w * inter_h
            smaller = min(aw * ah, bw * bh)
            ratio = inter_area / smaller if smaller > 0 else 1.0
            overlaps.append(OverlapPair(a_label=a.label, b_label=b.label, ratio=ratio))

    passed = len(components) > 0 and not giant and not overlaps
    report = QualityGateReport(
        component_count=len(components),
        passed=passed,
        giant_components=tuple(giant),
        overlaps=tuple(overlaps),
    )
    logger.info(
        "质量门禁：count=%d giant=%s overlaps=%d passed=%s",
        report.component_count,
        list(report.giant_components),
        len(report.overlaps),
        report.passed,
    )
    return report


# ---------- 编排 ----------


@dataclass(frozen=True)
class UiPipelineResult:
    """一次 UI 生成管线的全部产物：透明聚合表 PNG + 组件分割数据 + 门禁报告。"""

    sheet_png: bytes
    components: list[Component]
    gate: QualityGateReport
    actual_size: tuple[int, int]

    @property
    def has_components(self) -> bool:
        """split_components=false 时组件数据为空（仅跳过分割交付）。"""
        return bool(self.components)


def run_ui_pipeline(
    provider_image: bytes,
    *,
    matte_color: str,
    remove_bg: bool = True,
    split: bool = True,
) -> UiPipelineResult:
    """provider 原图 →（可选色键去背）→（可选组件分割 + 门禁）。

    - remove_bg=True：以 matte_color 为色键去背（复用 P2 processors，色距阈值
      _KEY_TOLERANCE）；remove_bg=False 跳过去背（坑 2 优先级由执行器保证，
      本函数只看最终 bool）；
    - split=True：对去背后 RGBA 做 alpha 连通域分割 + 门禁；split=False 仅跳过
      分割数据交付（components 空、gate 记 0 组件），聚合表照常交付（坑 9
      假设的实现口径，标注：官方关闭分割后的实际效果未披露）。
    """
    img = _decode(provider_image)
    if remove_bg:
        img = _decode(remove_background(provider_image, matte_color, _KEY_TOLERANCE))
    actual_size = (img.width, img.height)

    if split:
        components = segment_components(img)
        gate = quality_gate(components, img.width, img.height)
    else:
        components = []
        gate = quality_gate([], img.width, img.height)

    return UiPipelineResult(
        sheet_png=_encode(img),
        components=components,
        gate=gate,
        actual_size=actual_size,
    )


# ---------- 5) extract 模式：确定性 shelf 装箱重排（P4-L2） ----------

# 组件间距 / 行高对齐粒度 / 行宽目标（**推断约定**，官方未披露）：
# - padding=8：像素资产整倍数间距，同表相邻组件视觉分离又不浪费画布；
# - 行高向上取整到 8 的倍数：整表保持像素栅格观感（reusable-looking 的确定性
#   近似，调研文档 06 §4.3）；
# - 行宽目标 1024：超过即换行——1K 档长边口径，超宽组件独占一行（画布随之
#   变宽）；shelf 是"固定行宽、高度自然增长"的经典装箱（Wikipedia: Shelf
#   packing），行宽必须有个确定性目标否则无从换行
_REPACK_PADDING = 8
_REPACK_ROW_ALIGN = 8
_REPACK_TARGET_ROW_WIDTH = 1024


@dataclass(frozen=True)
class RepackedComponent:
    """组件在聚合表内的安置结果：源坐标（source_index + 源 bbox）与表内 bbox 双坐标。

    双坐标是 extract 交付契约（任务书 L2 要点）：消费方可回溯源图位置，也可
    直接按聚合表坐标裁用——但仍**不导出单组件裁剪文件**（§1.2 坑 1 硬契约）。
    label 在重排后按安置顺序**全局重编号**（component_01 起）：多张源图的
    分割 label 各自从 01 起会重名，门禁报告只引 label，全局唯一才无歧义；
    溯源靠 source_index + 源 bbox（比 label 更精确），原 label 不进交付。
    """

    source_index: int
    label: str
    source_bbox: tuple[int, int, int, int]  # 源图内 (x, y, w, h)
    bbox: tuple[int, int, int, int]  # 聚合表内 (x, y, w, h)
    area_px: int


@dataclass(frozen=True)
class UiExtractResult:
    """一次 extract 提取重排管线的全部产物（对应 generate 线的 UiPipelineResult）。"""

    sheet_png: bytes
    components: list[RepackedComponent]
    gate: QualityGateReport
    actual_size: tuple[int, int]  # 聚合表实际像素尺寸（装箱取整后的画布）


def shelf_repack(
    sheets: list[Image.Image],
    grouped: list[list[Component]],
    padding: int = _REPACK_PADDING,
    target_row_width: int = _REPACK_TARGET_ROW_WIDTH,
) -> tuple[Image.Image, list[RepackedComponent]]:
    """确定性 shelf 装箱：把各源图的组件按 bbox 抠出（含 alpha）重排进一张新画布。

    布局规则（全确定性 → 同输入两次运行字节级一致，可测试断言）：
    - 排序：按 (source_index, -area_px)——先按来源图分组（同图元素就近，观感
      连贯），图内按面积降序（shelf 装箱经典做法：大件先放减少空腔）；
    - 装箱：逐组件顺序扫描；当前行已有组件且 cursor_x + 宽 > target_row_width
      则换行（行首组件永不触发换行——超宽组件独占一行，画布随之变宽）；
      行高 = 行内最高组件；组件 y 与行顶对齐（顶对齐 shelf）；
    - 画布：宽 = 全部行最右内容边 + padding、高 = 末行底边向上取整到
      _REPACK_ROW_ALIGN 的倍数（取整只补余量，不改已安置坐标）；
    - 抠图：从源图 crop bbox 原样贴入（含 alpha；paste 必须以自身 alpha 为
      mask，否则透明区会覆盖底画布）——像素级忠实，零重渲染（§4.3 确定性路线）。

    grouped[i] 是 sheets[i] 的组件列表（i = source_index）。空输入/等长校验
    失败/全部源图零组件抛 UiPipelineError。
    """
    if not sheets or len(sheets) != len(grouped):
        raise UiPipelineError(
            f"sheets 与 grouped 必须非空且等长：{len(sheets)} vs {len(grouped)}"
        )

    # 展平 + 确定性排序（见 docstring：分组为主键、面积降序为次键）
    flat: list[tuple[int, Component]] = [
        (src, comp) for src, comps in enumerate(grouped) for comp in comps
    ]
    if not flat:
        raise UiPipelineError("所有源图均未提取到组件，无法重排")
    flat.sort(key=lambda item: (item[0], -item[1].area_px))

    # shelf 装箱：逐组件扫描，行宽超目标且行非空则换行
    placed: list[tuple[int, Component, tuple[int, int, int, int]]] = []
    cursor_x, row_top, row_height = padding, padding, 0
    max_right = padding  # 全部行最右内容边（含行首 padding 偏移基线）
    for src, comp in flat:
        _, _, cw, ch = comp.bbox
        if cursor_x > padding and cursor_x + cw > target_row_width:
            # 换行：行顶推进 (行高 + padding)，列游标复位
            row_top += row_height + padding
            cursor_x, row_height = padding, 0
        placed.append((src, comp, (cursor_x, row_top, cw, ch)))
        cursor_x += cw + padding
        row_height = max(row_height, ch)
        max_right = max(max_right, cursor_x - padding)

    canvas_w = max_right + padding
    content_h = row_top + row_height
    canvas_h = -(-content_h // _REPACK_ROW_ALIGN) * _REPACK_ROW_ALIGN  # 取整到 8 倍数

    canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    repacked: list[RepackedComponent] = []
    for src, comp, (x, y, w, h) in placed:
        sx, sy = comp.bbox[0], comp.bbox[1]
        sprite = sheets[src].crop((sx, sy, sx + w, sy + h))
        # paste 不带 mask 会把 sprite 的透明区当不透明覆盖画布——以自身 alpha
        # 为 mask 才能像素级忠实保留组件形状
        canvas.paste(sprite, (x, y), sprite)
        repacked.append(
            RepackedComponent(
                source_index=src,
                label=f"component_{len(repacked) + 1:02d}",
                source_bbox=comp.bbox,
                bbox=(x, y, w, h),
                area_px=comp.area_px,
            )
        )

    logger.info(
        "shelf 重排：%d 组件 → %dx%d 画布（padding=%d row_width<=%d）",
        len(repacked),
        canvas_w,
        canvas_h,
        padding,
        target_row_width,
    )
    return canvas, repacked


def run_ui_extract_pipeline(
    images: list[bytes],
    *,
    matte_color: str | None = None,
) -> UiExtractResult:
    """extract 编排：逐图去背 → 复用 L1 分割 → 确定性装箱重排 + 门禁。

    - images：1-8 张参考图字节（数量防线在路由层，本函数双防直调）；
    - matte_color：显式 matte 底色（payload 指定 background_color 时走此路）；
      None 时逐图四角扫描自动取底色（scan_background_color）——策略：extract
      的输入是用户已有 UI 图，底色未必是官方五枚举，自动扫描兜底任意纯色底
      （任务书"策略自定并注明"）；容差同 generate 线 _KEY_TOLERANCE（色键口径
      全服务一致）。输入契约：各图为不透明纯色底的 UI 图（已在透明图上再跑
      色键是无意义输入，不在此特判）。

    门禁语义与 generate 线完全一致（复用 L1 quality_gate，报告数据不判
    failed），作用在重排后的聚合表上：组件数>0 / 巨型粘连 / bbox 两两重叠
    （确定性装箱本身保证无重叠，重叠规则在此恒为空、如实交付）。
    """
    if not images:
        raise UiPipelineError("extract 至少需要 1 张参考图")
    if len(images) > 8:
        raise UiPipelineError(f"extract 最多 8 张参考图：{len(images)}（官方 §3.2 硬门禁）")

    sheets: list[Image.Image] = []
    grouped: list[list[Component]] = []
    for idx, data in enumerate(images):
        if matte_color is not None:
            img = _decode(remove_background(data, matte_color, _KEY_TOLERANCE))
        else:
            # 自动底色扫描：先解码取四角众数色，再以该色为色键去背
            key = scan_background_color(_decode(data))
            matte = f"#{key[0]:02x}{key[1]:02x}{key[2]:02x}"
            img = _decode(remove_background(data, matte, _KEY_TOLERANCE))
        sheets.append(img)
        grouped.append(segment_components(img))
        logger.debug("源图 %d：提取到 %d 组件", idx, len(grouped[-1]))

    canvas, repacked = shelf_repack(sheets, grouped)
    gate = quality_gate(
        [
            Component(id=i, label=c.label, bbox=c.bbox, area_px=c.area_px)
            for i, c in enumerate(repacked)
        ],
        canvas.width,
        canvas.height,
    )
    return UiExtractResult(
        sheet_png=_encode(canvas),
        components=repacked,
        gate=gate,
        actual_size=(canvas.width, canvas.height),
    )
