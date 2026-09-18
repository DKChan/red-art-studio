"""animate 生成线核心管线测试（P5-L2 验收 1：prompt 模板/seed 序列/编排）。

夹具全部 Pillow 代码生成；provider 用内存 fake（脚本化响应与失败序列），
永不真实调用外部服务；断言全部数值取证，不读图。
"""

import io
from typing import Any

import pytest
from PIL import Image

from server.app.core.anim_gen_pipeline import (
    AnimGenPipelineError,
    build_frame_prompt,
    build_frame_seeds,
    generate_frames,
    parse_size,
    run_anim_gen_pipeline,
)
from server.app.core.anim_pipeline import AnimPipelineError
from server.app.providers.base import (
    GeneratedImage,
    GenerationResult,
    ProviderRequestError,
    ProviderResponseError,
    sniff_image_format,
)

# ---------- fake provider ----------


class ScriptedProvider:
    """满足 Provider 协议的内存假后端：记录调用、按脚本返回或抛错。

    images_by_call：逐次调用的返回字节序列（循环复用）；errors：优先消费的
    错误脚本（耗尽后回落 images）。requests 留档供断言（prompt/seed/size 透传）。
    """

    def __init__(
        self,
        images_by_call: list[bytes] | None = None,
        errors: list[Exception] | None = None,
    ) -> None:
        self._images = images_by_call or []
        self._errors = list(errors or [])
        self._call = 0
        self.requests: list[Any] = []

    async def generate_image(self, request: Any) -> GenerationResult:
        self.requests.append(request)
        self._call += 1
        if self._errors:
            raise self._errors.pop(0)
        data = self._images[(self._call - 1) % len(self._images)] if self._images else b""
        return GenerationResult(
            images=[GeneratedImage(data=data, format=sniff_image_format(data), source="fake")]
        )

    async def aclose(self) -> None:
        return None


# ---------- 夹具 ----------


def _png(
    size: tuple[int, int] = (8, 8), color: tuple[int, int, int, int] = (255, 0, 0, 255)
) -> bytes:
    img = Image.new("RGBA", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _two_color_png(size: tuple[int, int] = (8, 8)) -> bytes:
    """左半红右半蓝的双色帧（基准调色板 = {红, 蓝}）。"""
    img = Image.new("RGBA", size, (255, 0, 0, 255))
    px = img.load()
    for y in range(size[1]):
        for x in range(size[0] // 2, size[0]):
            px[x, y] = (0, 0, 255, 255)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _shifted_dot_png(idx: int, size: tuple[int, int] = (8, 8)) -> bytes:
    """红底 + (idx,idx) 蓝点 + (idx,7-idx) 灰点的位移帧（idx=0 无灰点）。

    各帧映射后仍互不相同（蓝点位移；灰点映射入首帧调色板），避免 Pillow
    WebP 编码器合并连续相同帧。灰点色 (40,40,40) 在首帧调色板外 → pixel=true
    路径的调色板统一必须把它映射进 {红,蓝}，subset 断言才有实证力。
    """
    img = Image.new("RGBA", size, (255, 0, 0, 255))
    px = img.load()
    px[idx, idx] = (0, 0, 255, 255)
    if idx > 0:
        px[idx, size[1] - 1 - idx] = (40, 40, 40, 255)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------- prompt 模板 ----------


def test_frame_prompt_contains_frame_index_action_and_discipline() -> None:
    """模板逐段断言：帧序号（自然计数）/动作词/locked camera/保持项。"""
    prompt = build_frame_prompt("idle", "a red knight", 0, 8)
    assert "frame 1 of 8" in prompt
    assert "a red knight" in prompt
    assert "idle breathing" in prompt
    assert "locked camera" in prompt
    assert "same character design" in prompt
    assert "same silhouette" in prompt
    assert "same color palette" in prompt
    assert "hard edges" in prompt
    # 帧序号逐帧递增
    assert "frame 8 of 8" in build_frame_prompt("idle", "x", 7, 8)


def test_frame_prompt_loop_clause_follows_section_25_table() -> None:
    """循环要求按 §2.5 表：idle/walk/run 给末帧≈首帧从句；attack 给回起手式。"""
    for anim_type in ("idle", "walk", "run"):
        prompt = build_frame_prompt(anim_type, "x", 0, 8)
        assert "seamless loop" in prompt, anim_type
        assert "last frame identical to the first frame" in prompt, anim_type
    attack = build_frame_prompt("attack", "x", 0, 8)
    assert "returns to the starting pose" in attack
    # jump（循环可选）/hit/defeated（非循环）/other（无约定）→ 无循环从句
    for anim_type in ("jump", "hit", "defeated", "other"):
        prompt = build_frame_prompt(anim_type, "x", 0, 8)
        assert "seamless loop" not in prompt, anim_type
        assert "starting pose" not in prompt, anim_type


def test_frame_prompt_other_omits_action_phrase() -> None:
    """other 无动作词：模板不出现 "animation" 动作段（仅帧序/镜头/保持项）。"""
    prompt = build_frame_prompt("other", "a bouncing ball", 0, 4)
    assert "a bouncing ball" in prompt
    assert "animation" not in prompt


def test_frame_prompt_all_eight_enums_map() -> None:
    """八枚举全部可组装（映射表完备性）。"""
    for anim_type in ("idle", "walk", "run", "jump", "attack", "hit", "defeated", "other"):
        prompt = build_frame_prompt(anim_type, "x", 0, 4)
        assert "frame 1 of 4" in prompt
    with pytest.raises(AnimGenPipelineError):
        build_frame_prompt("dance", "x", 0, 4)  # 枚举外


# ---------- seed 序列 ----------


def test_frame_seeds_deterministic_linear() -> None:
    """seed 序列确定性：base_seed+i 线性递增、长度=帧数、逐值断言。"""
    assert build_frame_seeds(0, 4) == [0, 1, 2, 3]
    assert build_frame_seeds(42, 4) == [42, 43, 44, 45]
    assert build_frame_seeds(7, 16) == [7 + i for i in range(16)]
    # 两次调用一致（确定性）
    assert build_frame_seeds(42, 8) == build_frame_seeds(42, 8)


# ---------- size 解析 ----------


def test_parse_size() -> None:
    """合法尺寸解析；非法格式抛 AnimGenPipelineError。"""
    assert parse_size("64x64") == (64, 64)
    assert parse_size("512X256") == (512, 256)
    with pytest.raises(AnimGenPipelineError):
        parse_size("512")
    with pytest.raises(AnimGenPipelineError):
        parse_size("axb")


# ---------- generate_frames ----------


async def test_generate_frames_serial_calls_with_prompt_and_seed() -> None:
    """逐帧调用：调用次数=帧数、逐帧 prompt/seed 透传正确、返回解码帧序列。"""
    provider = ScriptedProvider(images_by_call=[_png((16, 16))])
    frames = await generate_frames(
        provider,
        prompt="a ball",
        animation_type="walk",
        frame_count=4,
        size="16x16",
        base_seed=10,
    )
    assert len(frames) == 4
    assert all(f.size == (16, 16) for f in frames)
    assert len(provider.requests) == 4
    # 逐帧 prompt 与 seed 序列透传（串行按序）
    for i, req in enumerate(provider.requests):
        assert req.prompt == build_frame_prompt("walk", "a ball", i, 4)
        assert req.seed == 10 + i
        assert req.size == "16x16"
        assert req.n == 1


async def test_generate_frames_empty_images_fails_whole_batch() -> None:
    """provider 返回空图像列表 → 整单失败（半成品帧序列无交付价值）。"""
    provider = ScriptedProvider(images_by_call=[_png()])

    async def empty(_request: Any) -> GenerationResult:
        return GenerationResult(images=[])

    provider.generate_image = empty  # type: ignore[method-assign]
    with pytest.raises(AnimGenPipelineError, match="空图像列表"):
        await generate_frames(
            provider, prompt="x", animation_type="idle", frame_count=2, size="8x8"
        )


async def test_generate_frames_size_mismatch_rejected() -> None:
    """实际尺寸与请求不符 → 拒绝（诚实失败，禁静默缩放）。"""
    provider = ScriptedProvider(images_by_call=[_png((32, 32))])
    with pytest.raises(AnimGenPipelineError, match="尺寸与请求不符"):
        await generate_frames(
            provider, prompt="x", animation_type="idle", frame_count=2, size="16x16"
        )


async def test_generate_frames_bad_bytes_normalized_to_pipeline_error() -> None:
    """坏字节 → _decode_checked 归一为 AnimPipelineError（ValueError 家族）。"""
    provider = ScriptedProvider(images_by_call=[b"not-an-image"])
    with pytest.raises(AnimPipelineError):
        await generate_frames(
            provider, prompt="x", animation_type="idle", frame_count=2, size="8x8"
        )


# ---------- run_anim_gen_pipeline 编排 ----------


async def test_pipeline_success_webp_with_palette_unification() -> None:
    """成功路径：4 帧 → 打包全产物 + 调色板统一实证（各帧用色 ⊆ 首帧调色板）。"""
    # 首帧红蓝双色，后续帧带首帧调色板外灰点（pixel=true 触发统一）
    frames = [_two_color_png((8, 8)), _shifted_dot_png(1), _shifted_dot_png(2), _shifted_dot_png(3)]
    provider = ScriptedProvider(images_by_call=frames)
    result = await run_anim_gen_pipeline(
        provider,
        prompt="a ball",
        animation_type="idle",
        frame_count=4,
        size="8x8",
        base_seed=42,
        output_format="webp",
        pixel=True,
    )
    assert result.pack.frame_count == 4
    assert result.pack.frame_size == (8, 8)
    assert result.pack.alpha_mode == "sharp"  # None + pixel=true → sharp 路由
    assert result.requested_size == (8, 8)
    assert result.seeds == [42, 43, 44, 45]
    assert len(result.frame_prompts) == 4
    # webp 产物存在且回读 n_frames=4
    (name, data, fmt) = result.pack.files[0]
    assert (name, fmt) == ("animation.webp", "webp")
    webp = Image.open(io.BytesIO(data))
    assert webp.n_frames == 4  # type: ignore[attr-defined]
    assert webp.info.get("loop") == 0  # 官方 §2.3 恒循环


async def test_pipeline_unified_palette_subset_assertion() -> None:
    """调色板统一断言：统一后各帧颜色集 ⊆ 首帧调色板（验收逐字落地）。"""
    frames = [_two_color_png((8, 8)), _shifted_dot_png(1), _shifted_dot_png(2), _shifted_dot_png(3)]
    provider = ScriptedProvider(images_by_call=frames)
    result = await run_anim_gen_pipeline(
        provider,
        prompt="x",
        animation_type="idle",
        frame_count=4,
        size="8x8",
        base_seed=0,
        output_format="webp",
        pixel=True,
    )
    # 从 webp 回读逐帧断言可见像素用色（seek+load 逐帧取，夹具帧间有差异
    # 故编码器不会合并帧）
    webp = Image.open(io.BytesIO(result.pack.files[0][1]))
    assert webp.n_frames == 4  # type: ignore[attr-defined]
    palettes: list[set[tuple[int, int, int]]] = []
    for i in range(webp.n_frames):  # type: ignore[attr-defined]
        webp.seek(i)
        webp.load()
        frame = webp.convert("RGBA")
        px = frame.load()
        colors = {
            px[x, y][:3]
            for y in range(frame.height)
            for x in range(frame.width)
            if px[x, y][3] > 0
        }
        palettes.append(colors)
    for colors in palettes[1:]:
        assert colors <= palettes[0]


async def test_pipeline_provider_failure_propagates_without_retry() -> None:
    """管线层不做重试：provider 5xx 直接上抛（重试归执行器层）。"""
    provider = ScriptedProvider(errors=[ProviderResponseError("HTTP 502")])
    with pytest.raises(ProviderResponseError):
        await run_anim_gen_pipeline(
            provider, prompt="x", animation_type="idle", frame_count=2, size="8x8"
        )
    assert len(provider.requests) == 1  # 单次调用即抛，无重试


async def test_pipeline_4xx_propagates_immediately(tmp_path=None) -> None:
    """4xx 同样直接上抛（不可重试错误，执行器层也不重试）。"""
    provider = ScriptedProvider(errors=[ProviderRequestError("HTTP 401")])
    with pytest.raises(ProviderRequestError):
        await run_anim_gen_pipeline(
            provider, prompt="x", animation_type="idle", frame_count=2, size="8x8"
        )


async def test_pipeline_spritesheet_line_delivers_sheet_and_meta() -> None:
    """spritesheet 线：sheet_meta 元数据齐备（列数/行数/时长/loop）。"""
    provider = ScriptedProvider(images_by_call=[_png((8, 8))])
    result = await run_anim_gen_pipeline(
        provider,
        prompt="x",
        animation_type="idle",
        frame_count=4,
        size="8x8",
        base_seed=0,
        output_format="spritesheet",
        pixel=True,
    )
    assert result.pack.sheet_meta is not None
    assert result.pack.sheet_meta.columns == 2  # ⌈√4⌉
    assert result.pack.sheet_meta.rows == 2
    assert result.pack.sheet_meta.frame_durations == (125, 125, 125, 125)
    assert result.pack.sheet_meta.loop is True
