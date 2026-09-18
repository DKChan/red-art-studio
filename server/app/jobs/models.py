"""任务契约模型（job.json / final_outputs.json / API 视图共用）。

铁律：所有持久化契约用 pydantic 模型锁定，schema 变更必须过测试。
"""

from datetime import datetime, timezone
from typing import Any, Literal, Union

from pydantic import BaseModel, Field, model_serializer, model_validator

JobStatus = Literal["pending", "running", "succeeded", "failed"]


def utc_now_iso() -> str:
    """统一时间戳格式（ISO 8601 UTC，毫秒精度）。"""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class GenerationParams(BaseModel):
    """一次生成任务的参数（API 请求体与 job.json 共用）。"""

    # 任务参数判别字段（JobParams union 落盘读回的显式标签，见 JobParams 注释）
    kind: Literal["generation"] = "generation"
    prompt: str = Field(min_length=1, description="生成提示词")
    size: str = Field(default="1024x1024", pattern=r"^\d+x\d+$", description="宽x高")
    n: int = Field(default=1, ge=1, le=10, description="生成张数")
    # 提交时解析为实际启用的 Provider 名（请求覆盖或配置默认值），留档排查用
    provider: str | None = Field(default=None, description="实际使用的 Provider 名称")


class ImageEditParams(BaseModel):
    """一次后处理任务的参数（P2 三件套，API payload 与 job.json 共用）。

    wire 形态（任务书 API 契约定稿）是嵌套的 {"operation", "params": {...}}；
    模型内部字段扁平（落盘 job.json 与执行器直取）。mode="before" 验证器把嵌套
    形态归一为扁平（job.json 里的扁平形态直通不受影响），extra="forbid" 保证
    未知参数显式 422 而不是被静默丢弃。
    """

    model_config = {"extra": "forbid"}

    # 任务参数判别字段（JobParams union 落盘读回的显式标签，见 JobParams 注释）
    kind: Literal["image_edit"] = "image_edit"
    operation: Literal["pixelate", "remove_background", "self_loop"] = Field(
        description="后处理操作名"
    )
    # pixelate：缺省（None）= 自动估计块周期粒度
    pixel_size: int | None = Field(default=None, ge=4, le=64, description="像素粒度（4-64）")
    # remove_background：缺省底色 = 四角扫描众数；tolerance 缺省 32（None → 执行器落地 32）
    source_background_color: str | None = Field(
        default=None, pattern=r"^#[0-9a-fA-F]{6}$", description="源底色 #RRGGBB"
    )
    tolerance: int | None = Field(
        default=None, ge=0, le=255, description="色距阈值（0-255，缺省 32）"
    )
    # self_loop：必填（无缺省方向）
    direction: Literal["horizontal", "vertical", "four_way"] | None = Field(
        default=None, description="无缝化方向（self_loop 必填）"
    )

    @model_validator(mode="before")
    @classmethod
    def _flatten_nested_payload(cls, data: Any) -> Any:
        """wire 嵌套形态 {"operation", "params": {...}} → 扁平形态。

        API 契约（任务书定稿）把各 operation 的参数包在 params 对象里，而本模型
        （与 job.json 落盘契约）字段在顶层。不归一的话 pydantic 会把 params 整个
        当未知字段——静默吞掉后 pixel_size 等全部回落缺省值，属最危险的静默偏差。
        """
        if isinstance(data, dict) and isinstance(data.get("params"), dict):
            data = {**data, **data["params"]}
            data.pop("params")
        return data

    @model_validator(mode="after")
    def _check_operation_fields(self) -> "ImageEditParams":
        """operation 与参数字段的交叉校验：无关字段不许携带，必需字段不许缺失。

        按"字段值是否为 None"判断携带（显式 null = 未携带），与落盘读回
        （dump 会补全 null 字段）保持一致，round-trip 幂等。
        owned：各 operation 允许携带的参数；required：其中必填的（缺省语义见字段描述——
        pixel_size 缺省自动估计、底色缺省四角扫描、tolerance 缺省 32，均不属必填）。
        """
        owned = {
            "pixelate": {"pixel_size"},
            "remove_background": {"source_background_color", "tolerance"},
            "self_loop": {"direction"},
        }
        required = {
            "pixelate": set(),
            "remove_background": set(),
            "self_loop": {"direction"},
        }
        for name in ("pixel_size", "source_background_color", "tolerance", "direction"):
            value = getattr(self, name)
            if value is not None and name not in owned[self.operation]:
                raise ValueError(f"operation={self.operation} 不接受参数 {name!r}")
            if name in required[self.operation] and value is None:
                raise ValueError(f"operation={self.operation} 缺少必需参数 {name!r}")
        return self


class TextureParams(BaseModel):
    """一次无缝纹理生成任务的参数（P3-L2，API 请求体与 job.json 共用）。

    extra="forbid" 保证未知参数显式 422 而不是被静默丢弃（P2 冒烟实证的漏网
    bug 模式）。prompt 只透传给 Provider，服务端不做提示词改写/增强（确定性
    归服务层，生成语义归 provider）。
    """

    model_config = {"extra": "forbid"}

    # 任务参数判别字段（JobParams union 落盘读回的显式标签，见 JobParams 注释）
    kind: Literal["texture"] = "texture"
    prompt: str = Field(min_length=1, max_length=2000, description="生成提示词（只透传）")
    quantize: bool = Field(default=False, description="归一化后是否做调色板量化（≤32 色）")
    isometric: bool = Field(
        default=False, description="是否追加等距投影（64×64 平面 → 128×64 瓦片）"
    )


class TilesetParams(BaseModel):
    """一次 tileset 合成任务的参数（P3-L1，API payload 与 job.json 共用）。

    wire 形态是扁平的 {"terrain_mode", "seed"?, "feather_width"?}；
    extra="forbid" 保证未知参数显式 422（P2 实证的静默吞参教训）。
    模式-纹理必填矩阵（foreground/background/dual 各自带哪张纹理）不在此模型——
    那是"两张上传文件 × 模式"的路由层交叉校验，见 tilesets 路由。
    """

    model_config = {"extra": "forbid"}

    # 任务参数判别字段（JobParams union 落盘读回的显式标签，见 JobParams 注释）
    kind: Literal["tileset"] = "tileset"
    terrain_mode: Literal["dual", "foreground", "background"] = Field(
        description="地形模式：dual=B 覆盖 A / foreground=B 镂空 / background=A 反向镂空"
    )
    seed: int = Field(
        default=0, ge=0, le=2**31 - 1, description="blob 噪声种子（同 seed 同输出）"
    )
    feather_width: float = Field(
        default=1.0,
        ge=0.5,
        le=8.0,
        description="边缘羽化宽度（px，0.5-8）",
    )


class UiGenParams(BaseModel):
    """一次 UI 生成任务的参数（P4-L1，API 请求体与 job.json 共用）。

    取值域照官方 CLI 面（调研文档 06 §2/§3.3）：quality 三档 / resolution 两档 /
    aspect_ratio 五档 / background_color 五枚举（官方同款五值）。三个"契约面参数位"
    的诚实标注：
    - quality：对齐官方 CLI 面保留参数位，但本地 provider 无质量档位概念，**实际
      不入 provider 请求**（官方映射 standard→low / detailed→medium / ultimate→high
      只在官方后端有意义；该映射连同"未生效"事实由路由层记入 job 请求快照注释
      与 STATE.md）；
    - remove_bg_method：不进 HTTP 参数面（官方 web 契约本就没有该参数；本地仅
      色键一档，等价官方 standard；advanced 属模型路线已后移）；
    - generate 模式参考图：官方语义是风格参考（IP-Adapter/Redux 类图生图通道），
      本地 provider 无此通道，放假接口=假行为，不进 L1 参数面（STATE.md 待定
      决策：openai_compat 图生图端点就绪后再议）。

    extra="forbid" 保证未知参数显式 422 而不是被静默丢弃（P2 实证的静默吞参
    教训）。prompt 只透传，服务端不做提示词改写/模板增强（坑 6：官方纪律是
    描述性 prompt，模板逻辑不进服务端）。
    """

    model_config = {"extra": "forbid"}

    # 任务参数判别字段（JobParams union 落盘读回的显式标签，见 JobParams 注释）
    kind: Literal["ui_gen"] = "ui_gen"
    prompt: str = Field(min_length=1, max_length=2000, description="生成提示词（只透传）")
    # 契约面参数位：本地 provider 无质量档，实际不入 provider 请求（见类 docstring）
    quality: Literal["standard", "detailed", "ultimate"] = Field(
        default="detailed", description="质量档位（契约面对齐官方；本地 provider 不消费）"
    )
    resolution: Literal["1k", "2k"] = Field(
        default="2k", description="分辨率档位（官方默认 2K）"
    )
    aspect_ratio: Literal["4:3", "3:4", "16:9", "9:16", "1:1"] = Field(
        default="1:1", description="长宽比（官方默认 1:1）"
    )
    # 官方 CLI 同款五值枚举（#cccccc 中性灰为官方默认，作 matte 底色）
    background_color: Literal["#000000", "#ffffff", "#cccccc", "#808080", "#333333"] = Field(
        default="#cccccc", description="matte 底色（官方五枚举，服务端色键去背用）"
    )
    remove_background: bool = Field(
        default=True, description="是否色键去背；false 时强制覆盖去背档位为 none（坑 2）"
    )
    split_components: bool = Field(
        default=True,
        description="是否生成组件分割数据；false 仅跳过 components.json（坑 9 假设，标注）",
    )


class AnimationPackParams(BaseModel):
    """一次帧序列打包任务的参数（P5-L1，multipart payload 与 job.json 共用）。

    **kind="anim_pack" 为自定标签**：打包线（N 张等尺寸静帧 → spritesheet/动图）
    在官方无 canonical 对应——官方 animate 指的是 L2 生成线（静图→帧序列），
    本线是它的确定性后半段；不冒用官方标签，诚实标注。

    与官方参数面的差异（诚实标注）：官方 animate-run/原版帧动画 的帧数
    面是 4/6/8/10/12/16（经典）与 2-24（HD），本线作为打包线取两源并集
    2-16（偶数，动画经 files[] 上传进本面，不进本模型）；
    extra="forbid" 保证未知参数显式 422（P2 实证的静默吞参教训）。
    """

    model_config = {"extra": "forbid"}

    # 任务参数判别字段（JobParams union 落盘读回的显式标签；自定标签，见类 docstring）
    kind: Literal["anim_pack"] = "anim_pack"
    animation_type: Literal[
        "idle", "walk", "run", "jump", "attack", "hit", "defeated", "other"
    ] = Field(default="other", description="动作类型（官方八枚举；回显进报告与切图元数据）")
    output_format: Literal["webp", "gif", "spritesheet"] = Field(
        default="webp", description="交付格式单选（官方 CLI 语义；三种各要一个 job）"
    )
    pixel: bool = Field(
        default=False, description="像素纪律：alpha 路由 + 调色板统一 + 画布 ≤256 校验"
    )
    # None = 按 pixel 路由（pixel→sharp / 否则 soft，官方"像素默认 sharp"复刻）
    alpha_mode: Literal["soft", "sharp"] | None = Field(
        default=None, description="alpha 处理；None=按 pixel 路由，显式值覆盖路由"
    )
    color_count: int | None = Field(
        default=None,
        ge=2,
        le=64,
        description="统一调色板色数（2-64，仅 pixel=true 合法携带；缺省 32）",
    )
    duration_ms: int = Field(
        default=125,
        ge=20,
        le=1000,
        description="帧时长 ms（20-1000；缺省 125 推断自官方 16 帧=2 秒）",
    )

    @model_validator(mode="after")
    def _check_pixel_fields(self) -> "AnimationPackParams":
        """color_count 仅 pixel=true 时合法携带（交叉校验，模式照 _check_operation_fields）。

        按"字段值是否为 None"判断携带（显式 null = 未携带），与落盘读回
        round-trip 幂等。
        """
        if self.color_count is not None and not self.pixel:
            raise ValueError("color_count 仅在 pixel=true 时合法携带（否则请省略）")
        return self


class SpritesheetMetaModel(BaseModel):
    """帧网格元数据（spritesheet.json 产物；坑 7 引擎切图契约——Aseprite/Unity
    切图约定：没有帧宽高/列数元数据的 sheet 引擎侧不可用）。"""

    frame_size: tuple[int, int] = Field(description="单帧像素尺寸 (宽, 高)")
    columns: int = Field(ge=1, description="网格列数")
    rows: int = Field(ge=1, description="网格行数")
    frame_durations: list[int] = Field(description="逐帧时长 ms（按提交序）")
    loop: bool = Field(description="是否循环播放（官方 §2.3：动图恒循环）")
    animation_type: str = Field(description="动作类型（官方八枚举之一，回显）")


class LoopCheckReport(BaseModel):
    """循环检报告数值（P5-L1 动画线专属，随 final_outputs.json 交付）。

    数值语义：首帧 vs 末帧逐像素最大通道绝对跳变，<6 视为闭合（阈值与 P2/P3
    检缝一致）。idle/walk/run 官方要求末帧≈首帧（§2.5）；检不过不判 failed
    ——指标如实进产物，是否重打包由用户决定（SeamReport 同哲学）。
    """

    first_last_max_step: int = Field(ge=0, description="首帧 vs 末帧最大通道跳变")
    passed: bool = Field(description="跳变是否 <6（阈值与 P2/P3 检缝一致）")


class AnimPackReport(BaseModel):
    """动画打包报告（P5-L1，随 final_outputs.json 交付 + 状态响应回显）。

    sheet_meta 仅 spritesheet 格式交付（None 序列化时省略，webp/gif 线的
    anim_report wire 形态保持不变，测试锁定）。
    """

    frame_count: int = Field(ge=2, le=16)
    frame_size: tuple[int, int]
    columns: int | None = Field(default=None, description="网格列数（仅 spritesheet；None 省略）")
    rows: int | None = Field(default=None, description="网格行数（仅 spritesheet；None 省略）")
    duration_ms: int
    animation_type: str
    pixel: bool
    alpha_mode: str = Field(description="实际生效的 alpha 模式（路由解析后回显）")
    loop_report: LoopCheckReport
    sheet_meta: SpritesheetMetaModel | None = Field(
        default=None, description="帧网格元数据（仅 spritesheet；None 序列化时省略）"
    )

    @model_serializer
    def _omit_null_optional_fields(self) -> dict[str, Any]:
        """columns/rows/sheet_meta 为 None 时不序列化（非 spritesheet 线的
        anim_report 键集保持精简，向后兼容锁定）。"""
        data: dict[str, Any] = {
            "frame_count": self.frame_count,
            "frame_size": self.frame_size,
            "duration_ms": self.duration_ms,
            "animation_type": self.animation_type,
            "pixel": self.pixel,
            "alpha_mode": self.alpha_mode,
            "loop_report": self.loop_report,
        }
        if self.columns is not None:
            data["columns"] = self.columns
        if self.rows is not None:
            data["rows"] = self.rows
        if self.sheet_meta is not None:
            data["sheet_meta"] = self.sheet_meta
        return data


class UiExtractParams(BaseModel):
    """一次 UI 提取重排任务的参数（P4-L2，multipart payload 与 job.json 共用）。

    与 UiGenParams 的差异：无 prompt（提取不生成）、无分辨率/长宽比档位（聚合表
    尺寸由装箱结果决定）、无 provider 相关语义——extract 是确定性管线（色键去背
    + alpha 连通域 + shelf 装箱），零 provider 调用。background_color 可选：
    None = 逐图四角扫描自动取底色（用户已有 UI 图底色未必是官方五枚举；
    显式给出时以该色为 matte 色键）。

    与官方请求面的差异（诚实标注）：官方 generate/ui_extract 归一在
    generation_mode 字段里，本 HTTP 面用独立端点（POST /api/v1/ui_gen/extract）
    表达模式；参考图经 multipart files[] 上传，不进本模型。
    extra="forbid" 保证未知参数显式 422（P2 实证的静默吞参教训）。
    """

    model_config = {"extra": "forbid"}

    # 任务参数判别字段（JobParams union 落盘读回的显式标签；对齐官方 canonical
    # 值 ui_extract，调研文档 06 §3.2 模式归一）
    kind: Literal["ui_extract"] = "ui_extract"
    # None = 自动四角扫描；显式值限定官方五枚举（与 generate 线同枚举面）
    background_color: Literal["#000000", "#ffffff", "#cccccc", "#808080", "#333333"] | None = (
        Field(default=None, description="matte 底色；None=逐图四角扫描自动取色")
    )


class AnimateParams(BaseModel):
    """一次 animate 生成任务的参数（P5-L2，API 请求体与 job.json 共用）。

    **kind="animate" 与官方语义对齐**：官方 animate-run 指生成线（静图/prompt
    → 帧序列），本线是其通道收敛复刻（provider 逐帧文生图；无图生帧模型，
    ADR-001/005），与 L1 打包线的自定标签 anim_pack 有意区分。

    与官方参数面的差异（诚实标注）：
    - 官方输入是**源图**（源图=第一帧，图生帧引擎），本线无图生帧通道 → 纯
      prompt 逐帧文生图，帧间主体一致性有客观局限（不假装修复，见
      anim_gen_pipeline docstring）；
    - padding 不进参数面：官方 padding 是"画布不动、给运动空间"的图生帧概念
      （§2.1），纯文生图无源图锚定、扩 padding 无操作对象（推断决策）；
      §2.5 动作模板表的校验策略降级为 prompt 建议文案（build_frame_prompt）
      与 L1 loop_report 指标，不做硬校验——避免假契约；
    - 帧数域取经典线口径 4-16 偶数（§2.1；像素线 2 帧无动画语义，推断）；
    - pixel=true 时尺寸任一轴 >256 拒绝（坑 6 复刻：服务端复算，不信客户端）；
      pixel=false 不设上限（L1 同决策）。

    extra="forbid" 保证未知参数显式 422 而不是被静默丢弃（P2 实证的静默吞参
    教训）。
    """

    model_config = {"extra": "forbid"}

    # 任务参数判别字段（JobParams union 落盘读回的显式标签；对齐官方 animate 语义）
    kind: Literal["animate"] = "animate"
    prompt: str = Field(min_length=1, max_length=500, description="动作描述（1-500 字符）")
    animation_type: Literal[
        "idle", "walk", "run", "jump", "attack", "hit", "defeated", "other"
    ] = Field(default="other", description="动作类型（官方八枚举；进 prompt 模板与报告）")
    # 经典线帧数域 4-16 且偶数（校验器实现；缺省 8=官方 §2.1 默认）
    frame_count: int = Field(default=8, ge=4, le=16, description="帧数（4-16 且偶数，缺省 8）")
    size: str = Field(
        default="512x512",
        pattern=r"^\d+x\d+$",
        description="生成尺寸 宽x高（64-2048；pixel=true 时任一轴 ≤256）",
    )
    output_format: Literal["webp", "gif", "spritesheet"] = Field(
        default="webp", description="交付格式单选（语义同 L1 打包线）"
    )
    pixel: bool = Field(
        default=False, description="像素纪律：alpha 路由 + 调色板统一 + 画布 ≤256 校验"
    )
    # None = 按 pixel 路由（pixel→sharp / 否则 soft，L1 同语义）
    alpha_mode: Literal["soft", "sharp"] | None = Field(
        default=None, description="alpha 处理；None=按 pixel 路由，显式值覆盖路由"
    )
    color_count: int | None = Field(
        default=None,
        ge=2,
        le=64,
        description="统一调色板色数（2-64，仅 pixel=true 合法携带；缺省 32）",
    )
    duration_ms: int = Field(
        default=125,
        ge=20,
        le=1000,
        description="帧时长 ms（20-1000；缺省 125 推断自官方 16 帧=2 秒）",
    )
    # 缺省 0=确定性可复现（pollinations seed 固定输出 md5 一致）；回显进报告
    seed: int = Field(default=0, ge=0, description="基础随机种子（逐帧 seed=seed+i）")

    @model_validator(mode="after")
    def _check_frame_and_pixel_fields(self) -> "AnimateParams":
        """帧数偶数 + pixel 画布上限 + color_count 交叉校验（模式照 L1）。

        - 帧数必须为偶数（官方 §2.1；ge/le 边界由 Field 承担，此处补奇偶）；
        - pixel=true 时尺寸任一轴 >256 拒绝（坑 6：服务端复算，不信客户端）；
          pixel=false 不设上限（L1 同决策：256 硬约束官方语义属像素线）；
        - color_count 仅 pixel=true 时合法携带（按"字段值是否为 None"判断
          携带，与落盘读回 round-trip 幂等）。
        """
        if self.frame_count % 2 != 0:
            raise ValueError(f"帧数必须为偶数（官方 §2.1），实际 {self.frame_count}")
        width, height = (int(v) for v in self.size.lower().split("x", 1))
        if not (64 <= width <= 2048 and 64 <= height <= 2048):
            raise ValueError(f"尺寸超出 64-2048 范围：{self.size}")
        if self.pixel and max(width, height) > 256:
            raise ValueError(
                f"像素动画画布任一轴不得超过 256px（坑 6 硬校验），实际 {self.size}"
            )
        if self.color_count is not None and not self.pixel:
            raise ValueError("color_count 仅在 pixel=true 时合法携带（否则请省略）")
        return self


class AnimateReport(BaseModel):
    """animate 生成报告（P5-L2，随 final_outputs.json 交付 + 状态响应回显）。

    L1 AnimPackReport **全量内嵌**于 pack 键（loop_report 数值如实交付不判
    failed，同 L1；嵌套而非平铺——生成元数据与打包报告各有 frame_count/
    animation_type，平铺撞名）。生成元数据（seeds/frame_prompts）留档可复现：
    同 seed 同 prompt 下 pollinations 输出确定性可复现（2026-09-07 md5 实测）。
    AnimPackReport 自身的 None 序列化省略在其模型内生效（嵌套后 wire 形态
    保持精简）。
    """

    frame_count: int = Field(ge=4, le=16)
    size: tuple[int, int] = Field(description="生成尺寸 (宽, 高)")
    seeds: list[int] = Field(description="逐帧 seed 序列（base_seed+i）")
    frame_prompts: list[str] = Field(description="逐帧 prompt（模板组装后原文）")
    animation_type: str
    pixel: bool
    alpha_mode: str = Field(description="实际生效的 alpha 模式（路由解析后回显）")
    pack: AnimPackReport = Field(description="L1 打包报告全量（含 loop_report/sheet_meta）")


# 任务参数 union：不同能力线的 job.json params 形态不同（P1 生成 / P2 后处理 /
# P3 纹理 / P3 tileset / P4 UI 生成/提取 / P5 动画打包/生成）。各模型携带恒定的
# kind 标签（"generation"/"image_edit"/"texture"/"tileset"/"ui_gen"/
# "ui_extract"/"anim_pack"/"animate"）参与落盘，smart union 区分零歧义；kind
# 带缺省值使旧 job.json（无 kind）也能读回（向后兼容）
JobParams = Union[
    GenerationParams,
    ImageEditParams,
    TextureParams,
    TilesetParams,
    UiGenParams,
    UiExtractParams,
    AnimationPackParams,
    AnimateParams,
]


class JobRecord(BaseModel):
    """`data/jobs/<job_id>/job.json` 契约。"""

    job_id: str
    status: JobStatus = "pending"
    params: JobParams
    error: str | None = Field(default=None, description="失败原因（仅 failed 时非空）")
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    updated_at: str


class OutputFile(BaseModel):
    """单个产物文件描述（文件名/尺寸/格式，CLAUDE.md 锁定的契约）。"""

    filename: str = Field(description="相对 artifacts/<job_id>/ 的文件名")
    width: int = Field(ge=0, description="像素宽（头部无法解析时为 0）")
    height: int = Field(ge=0, description="像素高（头部无法解析时为 0）")
    format: str = Field(description="图像格式：png/jpeg/webp/gif/unknown")


class SeamReport(BaseModel):
    """检缝报告数值（P3-L2 纹理线专属，随 final_outputs.json 交付）。

    数值语义与 P2 self_loop 验收同口径：相邻列/行最大通道绝对跳变，<6 视为
    无缝。检缝不过不判 failed——指标如实进产物，是否重生成由用户决定。
    """

    horizontal_max_step: int = Field(ge=0, description="3×3 平铺图相邻列最大通道跳变")
    vertical_max_step: int = Field(ge=0, description="3×3 平铺图相邻行最大通道跳变")
    passed: bool = Field(description="双轴跳变是否均 <6（阈值与 P2 一致）")


class ComponentEntry(BaseModel):
    """单个 UI 组件的分割数据（components.json 的元素）。

    **schema 为自定**——官方分割数据格式未披露（调研文档 06 §5 末尾诚实缺口；
    官方 runner 下载投影只有 output_path/url，格式无从考证）。label 为
    component_01 起顺序编号，无语义标签（语义分割属路线 B/C 已后移）。

    source_index / source_bbox 仅 extract 线交付（P4-L2）：组件的来源图序号与
    源图内 bbox——与聚合表内 bbox 构成双坐标（消费方可溯源、可裁用；坑 1 硬
    契约下不导出单组件裁剪文件）。generate 线不写这两个字段（None 序列化时
    省略，L1 的 components.json wire 形态保持不变，测试锁定）。
    """

    id: int = Field(ge=0, description="组件序号（0 起）")
    label: str = Field(description="component_NN 顺序标签（无语义）")
    bbox: tuple[int, int, int, int] = Field(description="包围盒 [x, y, w, h]")
    area_px: int = Field(gt=0, description="前景像素数")
    source_index: int | None = Field(
        default=None, description="来源参考图序号（仅 extract 线；None 序列化时省略）"
    )
    source_bbox: tuple[int, int, int, int] | None = Field(
        default=None, description="源图内包围盒（仅 extract 线；None 序列化时省略）"
    )

    @model_serializer
    def _omit_null_source_fields(self) -> dict[str, Any]:
        """source_index / source_bbox 为 None 时不序列化（L1 generate 线的
        components.json 键集保持 {id,label,bbox,area_px} 不变，向后兼容锁定）。"""
        data = {
            "id": self.id,
            "label": self.label,
            "bbox": self.bbox,
            "area_px": self.area_px,
        }
        if self.source_index is not None:
            data["source_index"] = self.source_index
        if self.source_bbox is not None:
            data["source_bbox"] = self.source_bbox
        return data


class OverlapEntry(BaseModel):
    """一对 bbox 重叠的组件（质量门禁规则 ③ 输出）。"""

    a_label: str
    b_label: str
    ratio: float = Field(ge=0.0, le=1.0, description="交叠面积/较小组件 bbox 面积")


class QualityGateReportModel(BaseModel):
    """质量门禁报告（调研文档 06 §5-7 三规则；报告数据不判 failed）。

    passed=False 时任务仍 succeeded——pollinations 等通道出图带杂色背景导致
    粘连属常态，指标如实进产物，是否重生成由用户决定。
    """

    component_count: int = Field(ge=0)
    passed: bool
    giant_components: list[str] = Field(
        default_factory=list, description="面积占比超阈值的巨型粘连组件标签"
    )
    overlaps: list[OverlapEntry] = Field(
        default_factory=list, description="bbox 相交的组件对"
    )


class ComponentsManifest(BaseModel):
    """`components.json` 产物契约（P4-L1；**schema 为自定**，官方未披露）。

    与 sheet.png 同交付；actual_size 是去背后聚合表的实际像素尺寸（分辨率档位
    是档位承诺不是精确承诺，消费方以本字段为准，坑 4）。extract 线（P4-L2）
    复用本契约，组件元素额外携带 source_index/source_bbox（见 ComponentEntry）。
    """

    components: list[ComponentEntry] = Field(default_factory=list)
    gate: QualityGateReportModel
    actual_size: tuple[int, int]


class FinalOutputs(BaseModel):
    """`data/artifacts/<job_id>/final_outputs.json` 契约。

    seam_report 是纹理线（P3-L2）的可选附加指标；ui_components 是 UI 线（P4-L1）
    的可选附加指标；anim_report 是动画打包线（P5-L1）的可选附加指标；
    animate_report 是动画生成线（P5-L2）的可选附加指标；其余能力线不写这些
    字段（缺省 None 序列化时省略，旧契约文件读回不受影响）。
    """

    model_config = {"extra": "forbid"}

    job_id: str
    created_at: str
    outputs: list[OutputFile] = Field(default_factory=list)
    seam_report: SeamReport | None = Field(
        default=None, description="检缝报告（仅纹理线交付；None 序列化时省略）"
    )
    ui_components: ComponentsManifest | None = Field(
        default=None, description="组件分割数据（仅 UI 线交付；None 序列化时省略）"
    )
    anim_report: AnimPackReport | None = Field(
        default=None, description="动画打包报告（仅动画打包线交付；None 序列化时省略）"
    )
    animate_report: AnimateReport | None = Field(
        default=None, description="animate 生成报告（仅动画生成线交付；None 序列化时省略）"
    )

    @model_serializer
    def _omit_null_optional_reports(self) -> dict[str, Any]:
        """seam_report / ui_components / anim_report / animate_report 为 None 时
        不序列化对应键（final_outputs.json 键集契约向后兼容：非纹理/UI/动画线
        产物清单保持 P1/P2 的三键形态，由测试锁定）。"""
        data = {
            "job_id": self.job_id,
            "created_at": self.created_at,
            "outputs": self.outputs,
        }
        if self.seam_report is not None:
            data["seam_report"] = self.seam_report
        if self.ui_components is not None:
            data["ui_components"] = self.ui_components
        if self.anim_report is not None:
            data["anim_report"] = self.anim_report
        if self.animate_report is not None:
            data["animate_report"] = self.animate_report
        return data


_EXTENSION_BY_FORMAT = {"png": ".png", "jpeg": ".jpg", "webp": ".webp", "gif": ".gif"}


def extension_for_format(fmt: str) -> str:
    """图像格式 → 文件扩展名；未知格式落 .bin，保住字节不丢。"""
    return _EXTENSION_BY_FORMAT.get(fmt, ".bin")
