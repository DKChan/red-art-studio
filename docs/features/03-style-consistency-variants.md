# 03 风格一致性与变体（Style Consistency & Variants）技术文档

> 复刻对象：原版的"公共风格预设 / 一致升级变体 / 八方向角色表"能力。
> 一手资料：`references/pixel-and-hd-assets.md`（八方向章节）、SKILL.md 模块链、web_parameter_contract.json（style_gen / one_click_upgrade / character_multi_view / spine_agent 段）、原版 CLI 源码 L5973-6013、L6350-6420、L6741-6751。

## 1. 功能描述

原版把"一致性"拆成三个不同粒度的产品能力：

| 原版能力 | CLI 命令 | 一致性目标 | 输出 |
|---|---|---|---|
| 公共风格预设 | `style-gen-run` | **项目级风格锚点**：整个项目共用一套风格资产 | 风格图（背景/参考变体） |
| 一键升级/变体 | `one-click-upgrade-prompts` + `one-click-upgrade-run` | **资产级变体族**：1-8 张互为一致的升级/变体图 | 升级图组（如建筑 Lv1→Lv8、道具套装） |
| 八方向角色表 | `character-multi-view-run`（别名 `character-8-direction-*`） | **单角色多视角**：同一角色 8 个朝向 | 方向角色表（directional set） |
| （延伸）Spine 换肤 | `spine-run` / `spine-edit-run` | 部件级一致：同一角色包内 1-10 个部件换肤 | Spine 角色包（骨骼动画） |

生产链定位（SKILL.md）：风格预设/参考图是**输入**（"Treat source images and downloaded references as inputs, never as generated deliverables"），一致性产出物要过统一验证："compare style, scale, anchor, and dimensions across every result"。

## 2. 原版实现线索

### 2.1 style-gen：公共风格预设

```
style-gen-run --prompt ... --template <公共风格模板> \
  --generation-model gpt-image-2-official|nanobanana|gpt-image-2 \
  --variant 121|112|221 --generation-speed normal
```

- `--template` 目前**只有原版自家的公共风格模板（原命名含品牌词，此处不录）一个**选择——即"官方预制风格模板库"，用户选模板而非自由定义风格。说明实现形态是：**官方策划好的风格 prompt/参考组合 + 强模型生成风格资产**，variant `121/112/221` 映射 `background_variant/reference_variant` 两个请求字段，推测是"背景参考数/参考图变体编号"类组合枚举。
- 底层模型标注为 `gpt-image-2-official`（另有 nanobanana 可选），即**用顶级闭源模型保证风格资产的视觉质量**——风格锚点图质量直接决定下游所有资产的上限。
- 公开边界：原版刻意不暴露"内部模板配置"，自部署复刻应把 style preset 做成服务端对象（prompt 模板 + 参考图集 + 模型/参数固化），前端只见模板名。
- 与其他命令的衔接：preset 线与 large-pixel 线都接受 `--reference-file`，即风格预设产物作为参考图喂给下游所有生成命令，形成"项目风格统一"。

### 2.2 one-click-upgrade：1-8 个一致升级/变体

两段式设计（先审 prompt 再付费，与像素线 fill-canvas 的成本哲学一致）：

```bash
one-click-upgrade-prompts --reference-image src.png --count 4 [--prompt "整体升级方向"]
# → 产出 1-8 条简洁可编辑的变体 prompt 清单（--language zh|en）

one-click-upgrade-run --reference-image src.png \
  --variant-prompt "石墙升级为木墙" --variant-prompt "石墙升级为铁墙" ... \
  --mode pixel|hd --generation-model nano-banana|image-2 \
  --quality standard|detailed|ultimate --resolution 1K|2K \
  --remove-bg-method none|standard|advanced
```

- `--count 1..8`（`choices=range(1, 8+1)`），每条 prompt 一张输出，一次任务产出一组。
- 模型路由按模式条件化默认：pixel→nano-banana，hd→image-2（web 契约 `conditional_default`）。
- `--resolution` 也按模式条件默认：pixel→1K、hd→2K。
- SKILL.md 官方工作流透露的实现细节：
  1. **生成前允许最小化扩画布**（"inspect and minimally pad the source canvas when the largest requested change needs more room"）——说明变体不是完全自由重绘，源图画布是硬约束；
  2. 跑完必须**横向对比每张结果的风格/比例/锚点/尺寸**——一致性验证是流程的一部分；
  3. prompts 阶段产出的清单要**人工审阅编辑**后才执行——"AI 起草 + 人类确认 + 批量执行"的三段式。
- 技术本质：**参考图条件生成（多图输入 + 逐条差异化 prompt）**，一致性来自"同一参考 + 同一引擎 + 结构化 prompt 族"，而非专门的微调模型。这是当前多模态大模型时代的一致性主流做法。

### 2.3 character-multi-view：八方向角色表

```bash
character-multi-view-run --reference-image char.png \
  --mode pixel|hd --canvas-resolution 1K|2K \
  --orientation 横版|纵版 --generation-speed normal|fast \
  --extra-constraint "角色行走动作，迈开双腿，一前一后"
```

- 输入一张稳定角色参考图，输出八方向角色集合。`--extra-constraint` 承载姿势/服装/剪影/一致性要求。
- 像素模式 1K/2K 可选且支持 `normal|fast` 速度；HD 模式固定 2K、Web 对齐默认 HD。
- 官方姿势要求原文（要精确传递给生成请求）：行走加 `角色行走动作，迈开腿，一前一后`，奔跑同理——**姿势描述是固定咒语**，官方文档要求"或其精确等义"，说明姿势短语对输出稳定性影响极大。
- 质量边界（官方承认）：批量八方向比单方向精修"更快，但姿态精度与方向一致性通常更低"；动画前必须逐方向确认"腿、剪影、装备、锚点在各方向下可读"。
- 隐藏线索（argparse 中的隐藏参数）：`--direction-mode mirror|ninegrid`（默认 mirror，SUPPRESS 不对外）——**大概率是"左右镜像复用 + 九宫格布局"的方向合成策略**：8 方向 ≈ 5 个真实绘制方向（下/左下/左/左上/上）+ 3 个水平翻转。这是像素游戏行业经典做法（RPG Maker 等），自部署应直接采用。
- 其他隐藏参数：`--aspect-ratio ""|1:1|3:4|9:16`、`--remove-bg-method`、`--output-size` 均被 SUPPRESS，说明服务端有默认契约。

### 2.4 跨能力的一致性纪律（官方"游戏美术基础"）

- 相关资产间保持：风格、比例、相机、调色板、光方向、锚点约定一致。
- 像素资产整数倍缩放预览（最近邻），禁止平滑缩放展示——一致性验证的显示纪律。
- 每多一道生成链条都可能破坏一致性（"Every generative step can change identity, scale, palette, edges, timing, and cost"）——**最小链条原则**：能不重生成就不重生成，用 edit 而不是 regenerate。

## 3. 自部署路线对比：参考条件 vs 微调

| 维度 | IP-Adapter / reference-only | LoRA 风格微调 | 风格 token（ textual inversion / P+ 等） | 纯 prompt + 种子固定 |
|---|---|---|---|---|
| 原理 | 图像特征注入 cross-attention（+可选 CLIP-vision） | 小数据集训练权重增量 | 学一个词向量即风格 | 无学习，靠措辞 |
| 一致性强度 | 强（结构+风格可分离控权重） | 最强（风格内化） | 中 | 弱 |
| 新风格上线成本 | **零训练，秒级**（给 1-5 张参考图） | 每风格一次训练（20-100 张图、小时级 GPU） | 快（<1h） | 零 |
| 像素风适配 | 好（可只锁调色板/剪影） | 好（像素数据集易标） | 中 | 差 |
| 逐变体差异表达 | 好（prompt 仍主导内容变化） | 中（可能过拟合压制变化） | 好 | 好 |
| 工程/算力 | 推理即用；多参考聚合是难点 | 训练管线（kohya_ss 等） | 训练轻 | 无 |
| 失效模式 | 参考泄漏（把参考图内容搬进来）、与底模风格冲突 | 灾难性遗忘、风格外泛化差 | 表达力有限 | 漂移不可控 |
| 与原版映射 | ≈ one-click-upgrade / multi-view（参考图 + 条件默认路由） | ≈ style-gen 的服务端模板（内部可用 LoRA 固化官方风格） | ≈ 简化版风格预设 | 不推荐单独使用 |

结论：**IP-Adapter 做"资产级变体族"，LoRA 做"项目级风格锚点"，两层叠加**。八方向另用镜像合成 trick（见下）。

## 4. 八方向一致性怎么保证

自部署流水线（结合原版的 mirror 线索）：

1. **5+3 镜像法**：只生成 5 个真实方向（S、SE、NE、N、NW…按八向顺序即 下/右下/右/右上/上/左上/左/左下 中的 下、右下、右上、上 + 侧面）——精确为：**下、右下、右上、上 4 个真实方向 + 右侧面 1 个 = 5 张**，左下/左上/左 由右下/右上/上… 水平翻转得到（侧面=右↔左翻转）。预算立省 37.5%。
2. **同种子 + 同 prompt 骨架 + 逐方向视角短语**：prompt 骨架固定（角色描述 + 服装材质锁定），只替换 "front view / 45° right-back view / back view" 等视角 token；种子固定保证噪声起点一致。
3. **参考图条件**：IP-Adapter（weight 0.6-0.8）锁身份；姿势用 `--extra-constraint` 类固定姿势咒语（行走="迈开双腿，一前一后"）。**先做一张"基准方向图"人工验收，再批量出其余方向**——原版的官方姿势短语策略说明先验锚点很重要。
4. **后验对齐**：输出后做主体包围盒检测 + 锚点归一（对齐脚底中心），不同方向间自动缩放校正；调色板统一到基准方向的调色板（像素风）。
5. **逐方向人工验收门**：原版要求动画前逐方向确认腿/剪影/装备/锚点可读——复刻为验收 checklist（腿是否分迈、左右翻转是否破坏不对称装备，如单肩甲——翻转坑！）。
6. 若预算充足可上**多视角扩散模型**（Zero123++/Era3D 类单图新视角合成）作为方向图生成器，再用像素管线转像素风。

## 5. 批量变体流水线设计（复刻 one-click-upgrade 三段式）

```
[输入] 参考图 + 数量(1-8) + 可选整体方向
   ↓
① prompt 起草（LLM）：生成 N 条简洁变体 prompt（升级阶梯：材质/时代/稀有度递进）
   ↓
② 人工审阅：清单可编辑、可删改（对应 one-click-upgrade-prompts 的存在意义）
   ↓
③ 画布预处理：按最大变化量最小扩画布（原版明示允许）；
   统一锚点/透明边距，保证 8 张输出同画布同锚点
   ↓
④ 批量条件生成：同一参考图 + IP-Adapter（+风格 LoRA）
   逐条差异 prompt → N 张输出（一次任务、一个 job-id）
   ↓
⑤ 一致性验证：风格(嵌入距离/调色板直方图) · 比例 · 锚点 · 尺寸四项横向比对，
   不达标自动重roll 或标记人工复核
   ↓
[输出] 变体族 + 元数据（prompt、参数、验证结果）落盘
```

要点：④ 的成本闸门（先审后付）、⑤ 的自动对比（原版文档把它写成强制人工步骤，自部署可半自动化）。

## 6. 推荐方案

- **项目级风格**：style preset = 服务端对象（风格 prompt 模板 + 3-5 张官方风格参考图 + 可选 LoRA）。开源自部署用 **LoRA 微调（每风格一次）+ 推理时 IP-Adapter 双保险**；接商业 API 时用 **GPT-Image / Gemini 多参考图输入**（原版的 style-gen 正是用 `gpt-image-2-official` 生成风格资产）。
- **资产级变体（1-8）**：**IP-Adapter + 同参考 + prompt 族**，严格复刻原版的 prompts→review→run 三段式与 conditional 路由（pixel→快模型、hd→质量模型）。
- **八方向**：**5+3 镜像合成**为默认（`direction_mode=mirror`），`ninegrid` 留作高级选项；姿势咒语内置为常量表。
- **框架**：ComfyUI（IPAdapter Plus / ControlNet 节点齐全）或 diffusers（`StableDiffusionXLPipeline` + `ip_adapter` API 两行启用）；服务层复刻 submit/poll/参考图上传协议。

## 7. 关键工程细节 / 坑

1. **参考泄漏**：IP-Adapter 权重过高会把参考图的姿势/背景也搬进变体；变体线建议 weight 分层（风格层高、结构层低），必要时 reference-only 控制网只锁风格。
2. **翻转不对称道具**：水平镜像会翻转单肩甲、武器手位、文字纹样。需要在翻转方向上做部件级修复（inpaint）或接受并文档化该限制；原版未提此问题，属自部署必须补的工程点。
3. **调色板漂移**（像素变体族）：逐张独立量化会得到 N 套调色板，拼进游戏后"不像一套"。必须以基准图为调色板源统一量化。
4. **升级阶梯的画布约束**：升级变体常要"更大/更华丽"，原版允许先最小扩画布再生成——直接在原画布强挤会出畸形；流水线要把扩画布做成显式步骤。
5. **prompt 族要同构**：变体 prompt 只应在一个语义轴上变化（材质/颜色/等级），句式长度结构尽量一致——prompt 起草阶段用模板而非自由发挥。
6. **八方向姿势咒语敏感**：官方要求精确传递固定短语，暗示模型对姿势措辞敏感；自部署要把姿势短语做成常量而非让用户自由描述。
7. **风格锚点图本身要最高质量**：原版用 gpt-image-2-official 生成风格资产——下游所有资产的上限由锚点图决定，这里不要省成本。
8. **不要用"重新生成"来修一致性问题**：原版的最小链条原则——用 edit（局部重绘）修单张偏差，代价远小于整族重roll。

## 8. 参考链接

- 原版官方文档（本地镜像）：`references/pixel-and-hd-assets.md`（八方向章节）、SKILL.md（模块链与一致性纪律）
- 参数契约：`web_parameter_contract.json` → style_gen / one_click_upgrade / character_multi_view / spine_agent
- CLI：原版 CLI 源码 L5973-6013（one-click-upgrade）、L6350-6420（character-multi-view，含隐藏 mirror/ninegrid）、L6741-6751（style-gen）
- IP-Adapter: https://github.com/tencent-ailab/IP-Adapter
- IPAdapter Plus（ComfyUI）: https://github.com/cubiq/ComfyUI_IPAdapter_plus
- kohya_ss 训练: https://github.com/bmaltais/kohya_ss
- Zero123++（单图多视角）: https://github.com/SUDO-AI-3D/zero123plus
- Era3D: https://github.com/pengHTYX/Era3D
