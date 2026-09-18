# 04 像素化后处理三件套：去背景、像素化、无缝循环

> 对应原版能力：`remove-background-run`（透明背景）、`pixelate-run`（高清→脆像素）、`self-loop-run`（图片→无缝循环贴图/图块）。本文档给出自部署复刻这三件后处理能力的技术选型与工程细节。

## 1. 功能描述

原版把"对已有图片做视觉格式变换"作为独立能力线，与生成线（pixel-gen 等）平行。三者定位：

| 能力 | CLI 命令 | 输入 → 输出 | 何时使用 |
|---|---|---|---|
| 去背景 | `remove-background-run --image-file <png> --mode pixel/hd --quality standard/advanced --source-background-color '#646464'` | 已有资产 → 透明背景 PNG | 生成/导入的精灵需要进引擎，必须透底 |
| 独立像素化 | `pixelate-run --image-file <png>`（可选 `--pixel-size`，公开命令实际固定自动检测） | 任意位图/照片/缩略图 → 干净的像素画 | 把非像素素材转成像素风格新资产；**禁止**对原版像素生成结果再跑一遍（它们已是"完美像素"） |
| 无缝循环 | `self-loop-run --image-file <png> --variant horizontal/vertical/four-way --generation-speed normal` | 静态图 → 可平铺（tileable）的循环图 | 背景、天空、地形材质；派生内部任务名 `pixel_gen_self_loop` |

SKILL.md 的关键约束（复刻时必须继承的产品语义）：

- 像素资产默认"已完美像素化"，预览只允许整数倍 nearest-neighbor 放大，永不平滑缩略。
- Pixel advanced 去背要求纯色背景（默认白，可传精确 HEX）；1–16 帧 10 credits、17–32 帧 20 credits，>32 帧不支持——暗示其内部按帧批处理且依赖纯色抠像通道。
- 高级去背会关闭其内置的"完美像素预处理"，官方建议顺序：先 pixelate 清理 → 确认 → 再去背。
- `remove_background` 的请求字段是 `method=pixel|hd`、`remove_bg_method=standard|advanced`，说明同一条后端链路按素材类型分流。

## 2. 原版实现线索（来自官方文档与参数契约）

1. **去背景有两条分支**：`mode=hd`（常规抠图，边缘质量取决于源复杂度）与 `mode=pixel + quality=advanced`（要求传入源背景纯色 HEX，说明走的是"色键提取 + 像素级 alpha 清理"而非通用分割模型）。
2. **pixelate 是服务端异步任务**（submit/run/poll 三段式），公开参数只有一个 `pixel-size`（且 run 强制置空 = 自动像素尺寸检测）。文档描述的失败模式——"极简图簇太大被误读、极复杂图簇太小失败"——指向**自动聚类估计最佳像素粒度**的算法，不是简单等比缩小。
3. **self-loop 的请求字段是 `mode/direction`，变体为 horizontal / vertical / four-way**，属于生成型任务（走 `generation-speed`），说明四个方向的 seamless 化不是纯算法，而是"算法预处理 + 生成模型重绘接缝"的混合管线。
4. 所有三个命令都把结果写 `final_outputs.json`，返回前要求人工检查 alpha/边缘——产品层没有自动质检兜底。

## 3. 技术方案对比

### 3.1 去背景

| 方案 | 质量 | 成本 | 可自部署 | 备注 |
|---|---|---|---|---|
| 开源 BiRefNet / BEN2 / MODNet（onnx，GPU 或 CPU） | 高，发丝/半透明均可 | 免费，单卡 0.3–1s | ✅ | 与原版 `hd/advanced` 档对标 |
| rembg（u2net / isnet-general-use） | 中高，开箱即用 | 免费，pip 即装 | ✅ | 通用首选，生态最成熟 |
| SAM / SAM2 + 前景点提示 | 高（可交互修补） | 免费，需要点选 UI | ✅ | 适合做"手动精修"兜底 |
| 色键（chroma-key）+ alpha matting | 对纯色底极高 | 免费，毫秒级 | ✅ | 对标原版 `pixel/advanced`：扫描四角取底色 → 距离阈值抠像 → `AlphaMatting` 修边 |
| 商业 API（remove.bg / Replicate birefnet） | 高 | $0.1–0.2/张 | ❌ | 仅当无 GPU 时考虑 |

### 3.2 像素化

| 方案 | 效果 | 成本 | 备注 |
|---|---|---|---|
| 纯算法：PIL/OpenCV（降采样 + 调色板量化 + 保留 alpha） | 稳定可控 | 免费 | 必做基线；`Image.reduce()` 或 `resize(NEAREST)` 先缩后放 |
| 调色板量化：`Image.quantize(colors=32, method=LIBIMAGEQUANT)` 或 K-means | 决定"脆"感 | 免费 | 像素画的关键是**低色数**，不只是低分辨率 |
| 自动像素粒度检测（边缘密度直方图 / 方块周期估计） | 对标原版自动档 | 免费 | 用梯度能量找最大周期格子尺寸，避免人工猜 |
| 生成模型重绘（pixel-diffusion 类 / SDXL+PixelArt LoRA 后再量化） | 有风格化增益 | 需 GPU | 仅对"高清照片→艺术像素画"有价值 |
| 商业 API | 与自部署差距小 | 按 credits | 不推荐，无技术壁垒 |

### 3.3 无缝循环

| 方案 | 效果 | 成本 | 备注 |
|---|---|---|---|
| 纯算法：offset-warp（把接缝移到中间）+ 镜像融合 | 快但常有"对称感" | 免费 | PIL：`img.offset` 后对中间带做 cross-fade |
| 纯算法：frequency-domain 环形化（FFT 相位处理） | 对噪声/材质好，对结构性内容差 | 免费 | 备选 |
| 生成式：offset 后让 inpainting/生图模型重绘接缝带（类 Photoshop "Seamless" / polyinfill） | 与原版 four-way 对标 | GPU | 推荐路线：先镜像融合，再用 Flux/SDXL inpaint 只重绘接缝区 |
| 商业 API（原版本身 / Scenario seamless） | 好 | credits | 作为质量上限参照 |

## 4. 推荐方案（自部署）

**全部三件套自部署可行，无需商业 API。** 建议 1 个 GPU 容器 + 纯 CPU 后备：

```
后处理服务（FastAPI + 任务队列）
├─ 去背：BiRefNet-HR (onnxruntime, GPU) 为 advanced；rembg(isnet-general-use) 为 standard；
│         纯色底快路径：色键 + pymatting alpha matting（对应 pixel/advanced）
├─ 像素化：全自动管线（纯 CPU）：
│    1) 自动粒度估计（块周期检测，4–64px 候选打分）
│    2) NEAREST 降采样 → libimagequant 调色板量化（≤32 色）
│    3) 边缘 alpha 清理：阈值化 alpha，去除半透明"灰边"
│    4) 最近邻放大回整数倍尺寸（可选）
└─ 无缝循环：offset-warp 预处理 → SDXL/Flux inpaint 重绘接缝带（horizontal/vertical）；
              four-way = 先水平后垂直串行两遍；mask 自动取接缝带 ±16% 区域
```

去背与像素化的顺序要复刻原版的产品语义：**先像素化、后去背**（对像素源），并在去背 API 里暴露 `mode`/`quality`/`source_background_color` 三参数，保持与原版 CLI 的兼容映射。

## 5. 关键工程细节与坑

1. **完美像素（perfect pixel）**：像素画放大后每个逻辑像素必须是实心方块，不能有半透明边界像素。量化后必须做 alpha 二值化（α≥128 → 255，否则 0），并可选"多数票"去孤立像素。
2. **去背残边**：分割模型输出常带 1px 白边（源是白底时）。对策：erode 1px + 用邻近色回填 + 对 `pixel` 模式强制 alpha 二值化；HD 模式保留 2–3 级 soft alpha。
3. **pixelate 的自动检测失败模式**就是原版文档描述的两类：极简图（大色块）会误判出过大粒度，极复杂图（地图）会判出过小粒度。要做候选粒度打分（块内方差、跨块色彩重复率），并像原版一样**强制人工 review**，不要静默交付。
4. **调色板要防"脏色"**：K-means 会把抗锯齿过渡色聚成中间灰。先去背景色簇，或用 median cut / libimagequant（带 dithering 抑制）。
5. **self-loop 的 four-way**：两次串行 inpaint 时第二次要基于第一次的输出重新算接缝带，否则水平接缝修好了垂直的又坏。gen 速度档（normal/fast）可映射为推理步数 30/12。
6. **异步任务语义**：原版全部是 submit → poll → download。自部署建议同样拆三段（尤其 seamless/inpaint 需要 GPU 排队），job 落库，幂等下载 `final_outputs.json` 同款投影文件。
7. **禁止对像素资产二次像素化**：在 API 层加尺寸启发式（<256×256 且色数 <64 时告警拒绝或提示确认），复刻原版的保护性约束。

## 6. 参考链接

- rembg: https://github.com/danielgatis/rembg
- BiRefNet: https://github.com/ZhengPeng7/BiRefNet
- pymatting（alpha matting）: https://github.com/pymatting/pymatting
- libimagequant / pngquant: https://github.com/ImageOptim/libimagequant
- Pillow Image.quantize: https://pillow.readthedocs.io/en/stable/reference/Image.html#PIL.Image.Image.quantize
- Aseprite 像素画规范（完美像素参考）: https://www.aseprite.org/docs/
- Seamless texture 综述（offset + inpaint 实践）: https://huggingface.co/blog
- 本仓库原版原始参考：`~/code/red-art-studio-refs/original-skills/skills/game-assets/references/pixel-and-hd-assets.md`
