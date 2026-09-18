# 05 地图瓦片与纹理（等距/六边瓦片、横版地图、Tileset、无缝纹理）技术文档

> 复刻对象：原版的"环境素材与地图就绪资产"能力：无缝纹理、地形过渡图集（tileset）、等距/六边投影地图瓦片、三层视差横版地图。
> 一手资料：`references/maps-tiles-and-textures.md`、`references/capability-routing.md` L25-31/L51/L56-57、`web_parameter_contract.json` L267-321（pixel_isometric / pixel_hex_isometric / hd_isometric / hd_hex_isometric / side_scrolling_pixel / side_scrolling_hd）、原版 CLI 源码（grep 定位 argparse 与请求构造，引用见各节）。

## 1. 功能描述

原版把地图/环境素材组织为 **8 条生成线 + 2 个免费参考库**，全部围绕一个核心产品契约：**输出是"中心锚定"的地图就绪瓦片——图片中心=逻辑格中心，客户端按中心偏移摆放，禁止对生成文件做任何裁剪/缩放/拉伸**（装饰高出底座的部分用透明 padding 表达，PNG 外框尺寸可以不同）。

### 1.1 生成线 × CLI 命令对照

| 生成线 | CLI 命令（均含 -submit/-run/-poll 三件套） | 输出契约 | 关键约束 |
|---|---|---|---|
| 像素等距瓦片 | `isometric-gen-run`（alias `pixel-isometric-gen-run`） | 中心锚定 RGBA 瓦片，逻辑底座菱形 128×64 | mode: standard/edit/tetraploid/road/wall；有 remove-bg |
| 像素六边等距瓦片 | `hex-isometric-gen-run`（alias `pixel-hex-isometric-gen-run`） | 逻辑底座六边（边长 64），同行中心步距 127px | mode: standard/edit/tetraploid/heptaploid；有 remove-bg |
| HD 等距瓦片 | `hd-isometric-gen-run` | 逻辑顶面 744×372（块边 372/高 119），编辑器归一到 128×64 格 | mode: standard/tetraploid；无 remove-bg，有 `--template` |
| HD 六边等距瓦片 | `hd-hex-isometric-gen-run` | 六边边长 300（显示 0.25×→75），底高层高 96 | mode: standard/tetraploid；可选 `--generation-model nano-banana\|image-2`、`--quality standard\|detailed\|ultimate` |
| 像素横版地图 | `side-scrolling-map-run` | background/midground/foreground 三层对齐图，1K 级 16:9 | 三层描述均必填；remove-bg 仅 standard/advanced；逐层 `--loop-*` |
| HD 横版地图 | `hd-side-scrolling-map-run` | 同上三层 | `--art-style` 7 种（2d_hd/2d_cartoon/2d_ink/clay/low_poly_3d/steampunk/anime_hd）+ `--custom-art-style` 覆盖 |
| 无缝纹理 | `texture-gen-run` | **严格 64×64** 平铺材质，默认 `--self-loop` | 非 64×64 一律拒绝，不静默缩放 |
| 等距无缝纹理 | `isometric-texture-run` | 已投影 2:1 的等距材质瓦片，默认 `--self-loop` | 支持 `--preset`/`--texture-name`/`--reference-image` |
| 俯视 dual-grid 图集 | `tileset-gen-run` | **256×256 的 4×4 图集**（64px dual-grid 格），15 种过渡变体 | terrain-mode: foreground/background/dual；输入纹理必须 64×64 |
| 等距地形图集 | `isometric-tileset-run` | 等距 dual-grid 4×4 图集 | terrain-mode: dual/single（+`--single-terrain-region`），另有 `--show-base-color`、前景/背景/地形色 |

### 1.2 免费参考库（零生成成本的第一入口）

| 库 | 命令 | 内容 | 定位 |
|---|---|---|---|
| 地图参考库 | `map-reference-search` / `map-reference-download` | 官方称 1000+ 预设；type: pixel-isometric / pixel-hex-isometric / hd-isometric / hd-hex-isometric / tileset；layout 按 type 区分（square 系 single/2x2，pixel hex 另有 7-cell/template，tileset 用 template） | 结构化筛选（`--type --theme --layout`，`--query` 仅作精修），下载后**作为生成参考图传入**，非交付物 |
| 纹理参考库 | `texture-reference-search` / `texture-reference-download` | 仅 64×64 标准平面材质 | `tileset-gen-run` 的标准输入；后端 32px 分支不对外暴露 |

关键路由规则（capability-routing.md L51/L56-57）：side-scrolling **不接受**参考预设（参考结果只作文案规划灵感）；参考图是"硬请求契约"——

- 像素等距：standard=2 张、edit=1、tetraploid=3、road=2、wall=1
- 像素六边：standard=2、edit=1、tetraploid=2–4、heptaploid=2–7
- HD 等距 tetraploid=2–4；HD 六边 tetraploid=1–4
- `tetraploid`/`heptaploid` 是**多格占位布局**的兼容名（2×2 / 7 格），不是美术风格

### 1.3 dual-grid 图集工作流（tileset 的核心契约）

纹理优先（texture-first）：先生成/下载 64×64 材质，再进 tileset。

- `foreground`（主推）：只生成中央地形的**镂空图集**（`--remove-bg-method` 去掉白底），可叠画到任意兼容底地形上 → 草/花/雪/浅水等可复用补丁。
- `dual`：两种材质同图生成，过渡最好但**绑定该材质对**（草→水图集 ≠ 沙漠→水图集）。
- `background`：反向镂空，少用。
- prompt 保持极短（"Background is dirt; foreground is grass."），甚至可省略——视觉信息由纹理提供；前后景引导色是后端内部推断、故意不暴露。
- 产物 256×256 图集 = 4×4=16 格，播放/擦除时客户端计算 **15 种过渡变体**（全前景格由纯纹理覆盖）。

## 2. 原版实现线索（file:line 证据）

### 2.1 命令 → 后端 workflow 映射

原版 CLI 源码 L145-170 `MAP_WORKFLOW_ENDPOINTS`：四个地图命令分别 POST `/api/workflows/{pixel_isometric_gen, pixel_hex_isometric_gen, hd_isometric_gen, hd_hex_isometric_gen}/run`；每个命令都有 `-submit/-run/-poll` 三个别名组（含 pixel- 前缀别名），说明后端是**异步 job**：submit 返回 job_id → run 轮询并下载 → poll 可恢复任意历史 job。`tileset_gen`/`texture_gen` 同构（L6517-6563）。交付物统一落 `final_outputs.json` 清单（L2230、L4671、L5603）。

参考库映射：L108-114 `MAP_REFERENCE_TYPE_TO_WORKFLOW`（5 种 type ↔ 4 个生成 workflow + tileset_gen）；L122-144 `MAP_REFERENCE_LAYOUT_GROUPS` 暴露后端模板组名：`pixel_64`（single）、`pixel_128_32`（2x2）、`pixel_single/pixel_tetraploid/pixel_heptaploid/workflow_template`、`hd_single/hd_tetraploid`、`tileset_template` ——**多格布局在后端就是不同的模板组**（推断：模板内含固定网格/引导结构，模型按模板填充）。

### 2.2 共享参数面（L5730-5774 `add_map_workflow_args`）

所有地图生成命令共享：`--prompt`（必填）、`--reference-image`（可重复 append）、`--mode`、`--generation-speed normal|fast`、`--similar-tiles`（store_true/false 对）、`--tile-only`。差异开关：

- `include_remove_bg`：仅像素等距/像素六边有 `--remove-bg-method none|standard|advanced`（默认 standard）；**两个 HD 命令不暴露**（HD 的透明边距由后端内部处理，推断）。
- `include_template`：两个 HD 命令有 `--template`（可选地图预设）。
- `include_hd_provider`：仅 hd-hex 有 `--generation-model nano-banana|image-2`（请求字段 `generation_provider`）+ `--quality standard|detailed|ultimate`（映射 `image2_quality` low/medium/high，L5303-5309）。
- `--tile-only` 只在 mode=standard 时真正生效（请求侧强制 `tile_only and mode=="standard"`，L5301）。
- `--similar-tiles` 默认值按命令不同：像素等距 False（web_parameter_contract.json L272），像素六边/HD 两命令 True（L282/L291/L302）——等距多风格瓦片默认求差异，六边/HD 默认求同风格一致性（语义为推断）。

### 2.3 各命令模式枚举（argparse 实证）

| 命令 | add_parser 行 | modes | 备注 |
|---|---|---|---|
| isometric-gen | L6630-6645 | standard/edit/tetraploid/road/wall | `include_remove_bg=True`，similar_tiles 默认 False |
| hex-isometric-gen | L6655-6676 | standard/edit/tetraploid/heptaploid | similar_tiles 默认 True |
| hd-isometric-gen | L6686-6699 | standard/tetraploid | `include_template=True`，无 remove-bg |
| hd-hex-isometric-gen | L6705-6719 | standard/tetraploid | + provider/quality |

像素六边独有 `heptaploid`（7 格）；HD 系后端有内部 seven-hex 布局但 Skill 层**故意不暴露**（maps doc L55：不得文档化/路由用户到该模式）。

### 2.4 tileset-gen 的前端→后端归一化（L4870-4916）

前端三模式在客户端归一为后端两模式：`foreground|background → terrain_mode=single + single_terrain_region=<同值>`；`dual → terrain_mode=dual` 且**强制 `remove_bg_method=none`**（双地形保留两块区域）。纹理必填校验矩阵也在这里（foreground 只需前景纹理，dual 两者都要）。这与官方"单地形=后端 internal single-terrain workflow、用户无需感知"的口径一致（maps doc L400）。

### 2.5 几何契约（自部署必须逐字复刻的数字）

**像素等距**（maps doc L24-29）：底座菱形 128×64；两轴邻居中心偏移 `(±64, 32)`；同行中心横向间距 128px、同竖线中心纵向间距 64px；`tetraploid` ≈ 4 个底座 = 逻辑 256×128。图片中心即逻辑中心，外框可为任意大于底座的尺寸（透明 padding）。

**像素六边**（L31-35）：边长 64 → 同行中心步距 **127 = 2×64−1**（相邻六边**共享一列边缘像素**，不留缝）；下一行水平偏移 64、垂直偏移 64，两条下行对角偏移 `(64,64)` 与 `(−63,64)`；跨两行的竖向中心距 128。

**HD 等距**（L41-47）：块边 372、块高 119，标准顶面 744×372 占 1 格；工作流坐标轴偏移 `(±372,186)`；产品地图编辑器归一到 128×64 逻辑格（显示偏移 `(±64,32)`），HD 用平滑采样禁最近邻。tetraploid 边长翻倍 744 → 顶面 1488×744，占 2×2；单资产绘制缩放 `128/min(image_width, 744)`，2×2 资产 `128/min(image_width/2, 744)`，缩放后中心对齐 footprint 中心。

**HD 六边**（L49-54）：边长 300、底高层高 96；同行步距 599（2×300−1），下行 `(300, 354)` / `(−299, 354)`（354 = 1.5×300−96）；编辑器 0.25× 显示缩放 → 边长 75、同行步距 149.75、行距 88.5、奇数行偏移 75。tetraploid 占据格偏移**按锚点行奇偶不同**（偶数行 `(0,0),(1,0),(−1,1),(0,1)`；奇数行 `(0,0),(1,0),(0,1),(1,1)`）。深度排序必须遍历 2×2 资产**所有占据格**，不能只按锚点或 column+row（L62）。

### 2.6 预览与校验工具链（复刻时照抄的配套件）

- `scripts/map-tile-layout.js`：像素/HD 中心函数、邻居偏移、HD 显示缩放、按中心放置图片的参考实现。
- `scripts/map-preview-server.py`：本地 loopback 预览后端，校验模式名/footprint 声明（1×1/2x2），把本地路径换成交互不透明的 `media/<id>` URL，绝不向前端泄露文件路径；支持 isometric/hex/hd-isometric/hd-hex-isometric/dual-grid/iso-dual-grid/side-scrolling 七种模式一页切换。
- dual-grid 模式：加载唯一 4×4 图集，用户在画布上涂/擦地形时**客户端实时算 15 种过渡变体**；Side View 三层视差可拖 Y 偏移、调相对速度、内置四帧猫精灵验证纵深。
- 采样纪律：像素模式最近邻 + 仅 1×–4× 整数缩放；HD 模式 Canvas backing buffer = preview zoom × devicePixelRatio + 高质量平滑采样，禁止 CSS 拉伸低清画布（L75）。
- 校验清单（L490-499）：文件未被裁剪/缩放/重锚 → 小图拼装截图看缝 → 尺寸与格对齐 → 双轴重复看缝 → 2:1 投影与锚点 → 横版三层同画布、loop 层左右接缝 → 仅交付 `final_outputs.json` 列出的文件。

### 2.7 横版地图细节

- 输出 1K 级 16:9 三层（实际像素尺寸需检视产出，官方未给精确值）；`--loop-midground/--loop-background/--loop-foreground` 分别请求**首尾列无缝**。
- 像素线对 foreground+midground **强制去背**（故 remove-bg 仅 standard/advanced；`--custom-art-style` 非空覆盖 HD 线预设风格）。
- 前后景去背后叠在实绘背景上 → 运行时按层差速滚动即视差。

## 3. 自部署候选底模/技术对比

### 3.1 无缝纹理（64×64）

| 路线 | 思路 | 优点 | 缺点 |
|---|---|---|---|
| A. tiling latent（圆形 padding） | SD1.5/SDXL/Flux 的卷积 latent 改环形 padding（A1111 "Tiling"、ComfyUI Make Circular Latent / Seamless 节点） | 零训练、原生无缝、主流默认 | 高频材质偶有隐形重复感；对 64px 极小目标需后降采样 |
| B. 材质专用模型/LoRA | tiling texture LoRA、Material Diffusion 类材质模型 | 材质质感好 | 生态碎片化、版本管理成本高 |
| C. 大图生成 + 后处理无缝 | 任意生成 512² → offset-wrap 检缝 → 边缘修补（PatchMatch/混合）→ 降采样量化 | 底模无约束 | 检缝修补是额外工程，质量不稳 |
| D. 程序化贴图 | 纯算法噪声/材质合成 | 绝对无缝、可控 | 艺术表现力弱，非"AI 平台"卖点 |

**判断**：A 为主 + 生成后降采样到 64×64（像素风再调色板量化）是复刻原版 `texture-gen-run` 的最短路径；`--self-loop` 语义 ≈ tiling latent + 交付前双轴平铺预览（原版输出清单里就带 `tiling_preview_path`，原版 CLI 源码 L1618 `pixel_gen_self_loop` 字段集，实证其服务端做平铺预览图）。

### 3.2 等距/六边瓦片

| 路线 | 思路 | 与原版契约的距离 |
|---|---|---|
| A. 模板条件生成 | 渲染 128×64 菱形/六边线框模板 → ControlNet(lineart/canny) 或 img2img + 参考图（IP-Adapter/ Redux）→ 输出即投影正确 | 最近：等价原版的"模板组 + 参考图硬契约" |
| B. 指令编辑模型 | Qwen-Image-Edit / Flux Kontext 类："按此参考风格，把线框菱形画成 X 材质瓦片" | 多参考融合更强，几何控制稍弱，需锚点后处理 |
| C. 平面生成 + 投影变换 | 先生成正交材质图，再仿射压扁成 2:1 | 便宜但装饰物（树/墙）立体感会错，高件露馅 |
| D. 3D 渲染管线 | 简模 + 材质球 → 固定 45° 相机渲染出瓦片 | 几何完美、风格受限，工程最重 |

**判断**：A 为主、B 为辅（edit 模式对应原版的 `mode=edit`：1 张参考改图）。关键不是底模而是**几何后处理与元数据**：输出按中心锚点归一、透明 padding 保留、footprint(1×1/2×2) 入库——这些是原版契约的真正护城河。像素风瓦片末端串文档 04 的像素化管线；HD 瓦片直接出图。

### 3.3 dual-grid tileset（地形过渡图集）

| 路线 | 思路 | 评价 |
|---|---|---|
| A. 程序化合成（推荐） | 两张无缝 64×64 纹理 + 确定性边缘 mask（blob 噪声阈值）→ 直接合成 4×4 图集 16 格 | 无 AI 不确定性；图集格位与 15 变体契约精确对齐；foreground 模式=对 mask 外区域做 alpha 抠空 |
| B. AI 整图生成 | 按模板一次生成 256×256 图集 | 过渡自然但**格位对不齐 64 网格**，需切片矫正，成功率低 |
| C. 混合 | A 合成后过一遍 img2img 低强度统一质感 | 质感最好，需控制漂移 |

原版的 `tileset-gen-run` 后端是"引导色推断 + 单/双地形 workflow"（推断内部有颜色引导的生成步骤），自部署用 A 起步完全可以满足同一输出契约（256×256、4×4、64px 格、15 变体），B/C 作为质感增强可选。

### 3.4 横版三层地图

| 路线 | 思路 | 评价 |
|---|---|---|
| A. 分层独立生成 + 风格锚 | 每层一段 prompt + 共享风格参考/seed → SDXL/Flux 16:9 1K 出图 | 最贴近原版三描述输入；难点是三层色调/光线一致（用文档 03 的风格一致性手段） |
| B. 单图生成再抠层 | 一张全景 → 分割/深度模型拆三层 | 层间天然一致，但前景/中景边界拆分不可靠 |
| C. 视频/多图模型 | 一次生成多层布局 | 无成熟开源方案 |

**判断**：A。`--loop-*` 的技术等价物 = **横向 tiling latent**（与 3.1 同一机制，只开水平环形 padding）+ 出图后左右接缝检视；前景/中景去背接 BiRefNet/rembg（对应 remove-bg standard/advanced 两档）。

## 4. 推荐方案（最便捷且主流的复刻路径)

统一底座：**SDXL（像素线）+ Flux.1-dev 或 Qwen-Image（HD 线）**，ComfyUI 编排，自研薄 API 复刻 submit/run/poll 异步 job 与 `final_outputs.json` 清单。四条管线：

1. **无缝纹理线**（≈texture-gen-run / isometric-texture-run）：
   `prompt → tiling latent 生成 512² → 最近邻降采样 64×64 →（像素风：调色板量化）→ 双轴平铺自动检缝（offset 差异阈值）→ 出 tiling_preview → 交付 64×64 正图 + 预览图`
   契约照抄：非 64×64 拒绝不缩放；`isometric-texture` 追加"先平面后仿射 2:1 投影"或模板条件出图（标注推断）。
2. **tileset 线**（≈tileset-gen-run / isometric-tileset-run）：**程序化合成**——两张 64×64 无缝纹理 + blob 边缘 mask → 4×4 图集（dual）或抠空图集（foreground/background，串 BiRefNet）；输出固定 256×256。等距版对每格做同一 2:1 投影。
3. **地图瓦片线**（≈isometric/hex/hd-*）：**模板条件生成**——
   `选参考（内置瓦片参考库）→ 渲染目标几何模板（128×64 菱形 / 127 步距六边线框）→ ControlNet lineart + IP-Adapter 参考图（或 Kontext/Qwen-Edit 的 edit 模式）→ 中心锚定归一（裁到已知 lattice、保留透明 padding）→ footprint 元数据入库 → 按 §2.5 偏移表拼装预览`
   2×2（tetraploid）走 2×2 模板 + 多参考（2–4 张）；road/wall 模式先不做，参考库按 5 type 组织提供搜索/下载 CLI。
4. **横版地图线**（≈side-scrolling-map-run / hd-）：三段描述并行生成三层（共享风格参考）→ 需 loop 的层用水平 tiling latent → fg/mg 去背 → 统一画布尺寸对齐 → 三层元数据（建议视差速率）随 `final_outputs.json` 交付。

配套必做（原版差异化体验的一半在这里）：① 按 §2.5 逐字实现 map-tile-layout 等价前端模块；② 本地 loopback 预览服务（七模式单页、media URL 不泄露路径）；③ 内置参考库种子数据（CC0/itch.io tileable 包 + 自建瓦片预设，标注许可证）。

## 5. 关键工程细节 / 坑

1. **中心锚定是全模块第一契约**：服务端出图必须归一到"中心=逻辑中心"，透明 padding 承载装饰物；客户端只按中心偏移摆放，禁止任何裁剪/缩放。校验器要检查"可见主体包围盒 + 逻辑几何"，而不是外框尺寸（原版明言外框可以不同）。
2. **六边 127 步距是反接缝技巧**：`2×64−1` 让相邻六边共享一列边缘像素。自部署若按"标准 128 步距"实现会处处细缝；渲染器与导出器都要写死 127（HD 系同理：599/0.25×=149.75、行距 354/0.25×=88.5，小数偏移必须用平滑采样渲染）。
3. **2×2（tetraploid）深度排序**：必须遍历所有占据格 + 渲染包围盒排序，只按锚点格或 column+row 排序会在 2×2 与 1×1 混排时穿帮；六边 tetraploid 的占据格还依赖锚点行奇偶（两套偏移表）。
4. **采样模式双纪律**：像素=最近邻 + 整数倍缩放；HD=平滑采样 + devicePixelRatio 感知画布。同一个预览器里按模式切换 `imageSmoothingEnabled`，CSS 拉伸会毁掉两者。
5. **参考图数量是硬契约**：每种 mode 的参考数上下限要在服务端校验后再提交（省无效算力）；参考库下载文件只作输入，预览器必须区分"参考预览"与"成品预览"两个阶段，防止把参考当交付。
6. **dual-grid 输入纹理质量被图集放大**：源纹理一条缝 = 图集和整张地图上处处缝。入口强校验 64×64（拒绝而非缩放）+ 双轴平铺自动预览是必做门禁。
7. **去背的作用面不同**：tileset 仅单地形模式（dual 强制关闭）；横版仅前景/中景且只有 standard/advanced 两档；HD 瓦片命令完全没有去背参数（后端内部处理，推断）。复刻时按命令白名单暴露，不要做全局通用开关。
8. **多格布局 = 模板组而非 prompt**：原版用 `pixel_tetraploid/pixel_heptaploid/hd_tetraploid` 等后端模板承载多格结构，`--layout 7-cell` 这类过滤在参考库层就路由好了。自部署同样把多格结构做成模板资产，不要指望 prompt 稳定产出多格布局。
9. **tile_only 只对 standard 模式有意义**（请求侧强制，L5301）；similar_tiles 默认值按命令区分（等距 False，六边/HD True）——这类"看起来一样其实不同"的默认值要原样保留，是产品行为契约的一部分。
10. **横版三层必须同画布对齐**：不同层分辨率/长宽比不一致会导致视差滚动错位；loop 层交付前自动做左右接缝检视（首尾列差异阈值）。

## 6. 参考链接

- 原版官方地图/纹理文档（本地镜像）：`references/maps-tiles-and-textures.md`、`references/capability-routing.md` L25-31
- 参数契约：`web_parameter_contract.json` → pixel_isometric / pixel_hex_isometric / hd_isometric / hd_hex_isometric / side_scrolling_pixel / side_scrolling_hd（L267-321）
- CLI：原版 CLI 源码 L145-170（workflow 端点/别名）、L5730-5774（共享参数）、L5796-5838（参考库）、L6049-6093（等距纹理/tileset/横版）、L6517-6563（纹理/tileset）、L6630-6723（四地图命令）
- dual-grid 概念（jess::codes 免费图集与讲解，社区最常引用）: https://jess.codes/blog/dual-grid-tilesets ；像素/等距实现参考 https://github.com/yurkth/dual-grid-template
- 程序化过渡/自动瓦片：blob tileset 位掩码（47-tile blob）https://www.boristhebrave.com/2013/07/14/tileset-roundup/ ；Wang tiles https://en.wikipedia.org/wiki/Wang_tile
- 无缝纹理生成：ComfyUI tiling latent 节点 https://github.com/Jonseed/ComfyUI-Detail-Daemon （及 A1111 "Tiling" 实现、Circular Latent/SeamlessKiller 类节点生态）
- 去背：BiRefNet https://github.com/ZhengPeng7/BiRefNet 、rembg https://github.com/danielgatis/rembg
- 参考纹理包来源（CC0 优先）：itch.io tileable/game-art texture packs https://itch.io/game-assets/free/tag-tileable
- Map/tile 预览渲染参考：Tiled Map Editor（契约对照）https://www.mapeditor.org/
