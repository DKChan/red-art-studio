# 01 游戏素材文生图（像素 + HD 双产品线）技术文档

> 复刻对象：原版（AI 游戏素材生成平台）的"文生图素材"能力。
> 一手资料：`~/code/red-art-studio-refs/original-skills/skills/game-assets/`（SKILL.md、references/pixel-and-hd-assets.md、web_parameter_contract.json、原版 CLI 源码）。

## 1. 功能描述

原版的文生图素材能力分成**像素（Pixel）**与**高清（HD）**两条产品线，覆盖：

| 原版能力 | CLI 命令 | 用途 |
|---|---|---|
| 像素预设生成 | `pixel-gen-template-info` / `pixel-gen-submit` / `pixel-gen-run` | 固定尺寸/数量的像素精灵、道具、图标 |
| HD 预设生成 | `hd-gen-template-info` / `hd-gen-submit` / `hd-gen-run` | 固定"资产族"的高清角色、道具 |
| 大幅像素图 | `large-pixel-gen-run` | 大像素场景、插画、头像、建筑 |
| 通用像素画布 | `pixel-universal-gen-run` | 4:3 大画布批量素材包、低成本量产 |
| 自定义尺寸像素 | `custom-size-pixel-gen-run` | 指定宽高的单件像素物体 |
| 通用 HD 生成 | `nano-banana-run` / `image-2-run` | 自由构图场景图、批量资产表（sprite sheet） |

产品定位差异：
- **预设线（pixel-gen / hd-gen）**：尺寸、输出数量是**固定契约**，官方文档明确"不能靠 prompt 措辞覆盖"，要换就换 preset。面向"要精确 32px/64px 精灵"的生产需求。
- **自由线（nano-banana / image-2 / pixel-universal）**：构图自由、适合批量与原型，官方明确标注"像素质量低于预设线、精确尺寸控制弱"。

## 2. 原版实现线索（从官方文档与 CLI 提取）

### 2.1 preset 模板体系：template-info / submit / run 三段式

```
pixel-gen-template-info        # 1) 发现：只报告产品级字段（输出尺寸、默认数量）
pixel-gen-submit --template-name <preset> --requirement "..." --job-name ...   # 2) 异步提交，返回 job-id
pixel-gen-run                  # 3) submit + 轮询 + 下载 的等价便捷命令
pixel-gen-poll --job-id ...    # 中断恢复（绝不重复提交付费任务）
pixel-gen-history / -download / -cancel
```

设计要点：
- **preset 是服务端对象**，客户端只传 `--template-name` + `template_config`（direction / generation_speed / remove_bg_method），不暴露内部配置 JSON。size/count/resolution 都在服务端模板里。
- `template_config` 的字段名直接映射 Web 前端（web_parameter_contract.json 标注了 `services/pixelGenService.ts`、`components/SettingsPanel.tsx`），说明 Web 与 CLI 共用同一套模板服务。
- **submit/run 分离 + poll 恢复**是付费 API 的标准容错设计：job 提交成功后，轮询/下载中断可用原 job-id 恢复，避免重复扣费。自部署复刻应保留这个形态。
- 每条产品线都是同一模式：`hd-gen-*`、`large-pixel-*`、`character-multi-view-*`、`remove-background-*` 全部有 `template-info(可选)/submit/run/poll` 四件套。这是一个非常干净的复刻蓝图。

### 2.2 关键参数（自部署 API 需要对齐的契约面）

pixel-gen（预设线）：
- `--template-name`、`--requirement`（自然语言描述）、`--aspect-ratio`（默认 1:1）、`--direction`（preset 暴露的方向枚举）、`--generation-speed normal|fast`、`--remove-bg-method none|standard|advanced`（默认 advanced，即像素线默认出透明底）、`--reference-file/--reference-files`（风格/身份参考）。

hd-gen（预设线）：
- `--quality standard|detailed|ultimate`（质量三档，ultimate 最贵）、`--generation-model nano-banana|image-2`（默认 image-2）、`--resolution`（空=模板默认）、`--remove-bg-method`（默认 standard）。

nano-banana（自由 HD 线，底层即 Gemini 图像系）：
- `--model`：`gemini-3.1-flash-lite-image` / `gemini-3.1-flash-image`(默认) / `gemini-3-pro-image` —— 三档速度/质量。
- `--resolution 512|1K|2K|4K`，宽高比极全（1:1 到 21:9、1:4、1:8 等 14 种）。
- `--reference-image` 最多 8 张。

image-2（自由 HD 线，GPT-Image 系）：
- `--quality standard|detailed|ultimate`（standard 用于廉价试 prompt，detailed 用于生产，官方推荐工作流）、`--resolution 1K|2K`、5 种宽高比。

pixel-universal（通用像素线）：
- 固定 4:3 `xlarge` 大画布（可选 4:3/3:4/2:1/1:2/2:3/3:2），`--view standard|top-down`（普通构图 or 俯视游戏视角，写入 `template_config.direction`），主打素材包：一次生成多个不同尺寸资产、后期切割。

custom-size-pixel（自定义尺寸）：
- `--width/--height`、`--content-mode portrait|illustration|asset_pack|other`、`--fill-canvas`（默认开，让主体尽量占满目标画幅）、`--strong-pixelation`（强制重绘，必须配参考图）、`--generation-model` 默认 nano-banana。**此命令刻意禁用去背**——先确认生成满意再单独调 remove-background，避免为废稿付去背钱（成本控制意识值得复刻）。

### 2.3 官方 prompt 纪律（重要！与常见 SD 工作流相反）

SKILL.md 明确要求：**简单自然语言**，描述主体、动作、视角、材质即可；**禁止** diffusion 式 prompt 工程（关键词堆叠、正负 prompt 块、质量词复读、token 权重、采样器语法）。这说明原版底层是"指令跟随型"大模型（Gemini/GPT-Image 系），而不是传统 SD prompt 驱动。自部署若用 SDXL/Flux，需要一层 prompt 模板适配层来弥合这个差异。

### 2.4 复刻用的预设契约示意（依据公开参数反推）

```json
{
  "template_name": "pixel-char-64",
  "family": "pixel",
  "canvas": {"width": 64, "height": 64, "aspect_ratio": "1:1"},
  "contract": {"output_count": 1, "output_size": [64, 64], "fixed": true},
  "options": {
    "direction": ["", "front", "back", "side"],
    "generation_speed": ["normal", "fast"],
    "remove_bg_method": ["none", "standard", "advanced"],
    "default_remove_bg": "advanced"
  },
  "inputs": {"requirement": "string", "reference_files": "0..n"},
  "flags": {"animation_safe": true, "max_animation_canvas": 256}
}
```

自部署时把这张表做成数据库对象：`template-info` 读表返回产品字段，`submit` 校验 option 合法性后落 job，`run` = submit + 轮询 + 下载 `final_outputs.json`。

## 3. 自部署候选底模对比

| 底模 | 像素风适配 | 游戏素材质量 | 指令跟随 | 透明背景 | 许可/成本 | 备注 |
|---|---|---|---|---|---|---|
| **SDXL + 像素 LoRA** | 好（Pixel Art LoRA/像素风 checkpoint 成熟） | 高 | 一般（需 prompt 工程） | 需后处理 | 开源、可自托管 | 生态最全，ControlNet/IP-Adapter 齐备 |
| **SD 1.5** | 中（老牌 pixelart LoRA 多但底子弱） | 中 | 弱 | 需后处理 | 开源、显存最低 | 适合低成本批量、老卡复用 |
| **Flux.1-dev** | 中（像素风需 LoRA，效果偏"伪像素"） | 很高（HD 线强） | **强（自然语言友好，最接近原版体验）** | 需后处理 | 非商用许可（dev）；schnell 弱 | HD 产品线首选底模；12B 显存压力大 |
| **Pony Diffusion V6 (SDXL)** | 中 | 高（角色向） | 中 | 需后处理 | 开源 | 角色立绘强但风格偏差，需调教；许可争议需评估 |
| **Qwen-Image / 其他新指令系** | 视生态 | 高 | 强 | 部分 | 多为 Apache-2.0/开源 | 2025 后崛起的指令跟随系，可作为 Flux 替代 |
| **像素专用：PixelArt Diffusion / pixel-art-xl LoRA / PixelWave 等 SDXL 像素融合模型** | 优秀 | 高（真像素网格） | 中 | 需后处理 | 开源 | 像素线专用；常配"下采样+调色板量化"后处理保真 |

像素风专用技术栈（无论底模）：
1. **生成于 2-4 倍分辨率 → 最近邻下采样到目标逻辑像素（32/48/64）→ 调色板量化**。这是社区标准"完美像素化"管线，对应原版的 `pixelate-run`（自动像素尺寸检测）。
2. Negative prompt 固定注入 "blurry, smooth, anti-aliasing, gradient"（在 API 适配层做，用户不感知）。
3. `remove-bg`：`rembg`（U2Net/BiRefNet/ISNet）可覆盖 standard 档；advanced 档用 SAM/掩码扩散类。

## 4. 路线对比：开源自部署 vs 商业 API

| 维度 | 开源自部署（ComfyUI / diffusers / A1111 API） | 商业 API（Gemini 图像 / GPT-Image / 即梦 / SD B2B API） |
|---|---|---|
| 复刻原版体验（自然语言出图） | 需 prompt 模板层 + LLM 改写 prompt | 原生支持，开箱即用 |
| 像素精确尺寸（32/64px 契约） | 强（下采样+量化管线完全可控） | 弱-中（多为大图后像素化） |
| HD 素材质量 | Flux.1-dev 很高但硬件要求高 | 顶级（Pro 档） |
| 透明背景 | 全流程可控（生成后 rembg） | 部分支持（GPT-Image 可出透明 PNG） |
| 批量素材表（sheet → 切割） | 可控性最强（布局 ControlNet） | nano-banana/image-2 类多模态模型擅长 |
| 成本 | 一次硬件投入，边际成本低 | 按张计费，量大烧钱 |
| 运维 | 模型/显存/队列自管 | 无运维，但有速率限制与账号风控 |
| 一致性控制 | IP-Adapter/LoRA/ControlNet 全套 | 依赖参考图多图输入 |
| 推荐服务框架 | **ComfyUI（API 模式）** 或 diffusers 自写 FastAPI；A1111 `--api` 最省事但迭代慢 | OpenAI/Gemini 官方 API；OpenRouter 聚合 |

三种开源服务化方案：
1. **ComfyUI API 模式**：把像素化管线（generate → downscale → quantize → rembg）编排成 workflow，`/prompt` 提交 + websocket 轮询，天然对应原版的 submit/poll 形态。**最主流**。
2. **diffusers + FastAPI 自研**：控制粒度最高，适合把"preset 模板"做成数据库对象（正是原版的模板体系）。
3. **A1111 WebUI API**（`--api` + `/sdapi/v1/txt2img`）：上手最快，但社区已转向 ComfyUI/Forge，长期维护弱。

## 5. 推荐方案

**最便捷 + 最主流组合（双线混合）：**

- **HD 线**：直接接 **Gemini 图像 API（nano-banana 对应 gemini-3.1-flash-image 档）或 GPT-Image**。理由：原版自己就是这么做的（web_parameter_contract 暴露了 `gemini-3.1-*` 与 `gpt-image-2` 系模型名），指令跟随 + 多参考图 + 批量 sheet 是这两家独有能力，自训底模短期无法企及。用 OpenRouter/官方 API 双通道做降级。
- **像素线**：**ComfyUI + SDXL 像素 LoRA（如 pixel-art-xl）自托管**，后处理固定接"下采样 + 调色板量化 + rembg"，用 preset 数据库表复刻原版的模板契约（每个 preset 固化 尺寸/数量/默认去背档位）。
- **架构上照抄三段式**：`template-info → submit(job-id) → poll/run`，所有能力一个形；`final_outputs.json` 式的产物清单落盘，前端只渲染声明过的交付物。
- 成本闸门：像原版一样把"去背"从生成命令中拆开（custom-size 线的做法），先确认图再付费去背。

## 6. 关键工程细节 / 坑

1. **像素资产不要再像素化**：原版明确 pixel-gen 输出即"完美像素"，重复像素化会毁图。自部署管线要区分"真像素输出"（已量化）与"伪像素大图"（需后处理）两种资产标签。
2. **动画用像素精灵 ≤128×128**：官方建议动画向角色本体 128px 以内、画布 ≤256px（像素动画画布任一轴 >256 直接拒绝）。preset 表要预留 `animation_safe` 标记。
3. **批量 sheet 的坑**：prompt 让模型"12 个药水、间距清晰、无文字"，仍可能生成意外的小标签文字；需要检测（OCR）+ 重生成或局部编辑回路。
4. **参考图越多越不稳**：custom-size 线官方明确"文本生成稳定、参考引导不稳，复杂参考要显式降复杂度（'保持极简、色块表达'）"。参考图路由要做置信度/回退策略。
5. **质量档位是计费/质量双轴**：standard 试 prompt → detailed 出 production → ultimate 仅最终图。复刻时要做成显式参数而非隐藏策略。
6. **`--fill-canvas` 不能违背几何**：站立角色在横宽画布上仍会留白，填充只是"尽量占满"，前端不要承诺满幅。
7. **任务幂等**：所有付费能力必须有 job-id + poll，客户端崩溃不重复扣费。

## 7. 参考链接

- 原版官方 Skill（本地镜像）：`~/code/red-art-studio-refs/original-skills/skills/game-assets/SKILL.md`
- 像素/HD 资产参考：`references/pixel-and-hd-assets.md`
- Web 参数契约：`web_parameter_contract.json`（nano_banana / general_image2 / pixel_preset / hd_preset / custom_size_pixel / pixel_universal 段）
- CLI 定义：原版 CLI 源码 L6098-6285（pixel 系）、L6287-6350（hd-gen 系）
- ComfyUI: https://github.com/comfyanonymous/ComfyUI
- diffusers: https://github.com/huggingface/diffusers
- pixel-art-xl LoRA: https://civitai.com/models/120964/pixel-art-xl
- rembg: https://github.com/danielgatis/rembg
- Flux.1-dev: https://huggingface.co/black-forest-labs/FLUX.1-dev
