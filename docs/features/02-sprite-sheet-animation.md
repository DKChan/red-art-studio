# 02 精灵动画（Sprite Sheet Animation）技术文档

> 复刻对象：原版的"静图 → 帧动画/短视频"能力。
> 一手资料：`references/animation-and-video.md`、SKILL.md 动画章节、web_parameter_contract.json（animation_model / animation_keyframes / frame_animation_new / video_model 段）、原版 CLI 源码 L6851-7010。

## 1. 功能描述

原版有**四条动画线**，输入都是一张已定稿的静图，输出面向游戏引擎可直接使用的帧动画：

| 动画线 | CLI 命令 | 一句话定位 |
|---|---|---|
| 经典精灵动画 | `animate-run`（+ `-submit`/`-poll`） | 主力路径：单图 + 动作描述 → WebP/GIF/ spritesheet，直接可用 |
| 多关键帧控制 | `keyframes-run` | 2+ 张关键姿势图约束过渡，输出仍是帧动画；用于攻击/转身/闪避等需要精确中间姿态的动作 |
| 新一代帧动画 | 原版帧动画命令 | 8/16/24/32 帧、Pixel/HD 双风格、480p/720p、支持首尾帧（last-frame）控制 |
| 短视频 | `video-run`（+ `video-prompt-list`） | 兜底：静图 → 短视频片段，分辨率更高但**返回原始视频**，还需人工抽帧/去背 |

官方路由顺序（重要产品决策）：**animate 优先 → 复杂动作用 keyframes → 最后才是 video**。video 明确定位为"帧动画表达不了、或确实需要更高分辨率时"的 fallback，因为视频输出不是游戏就绪资产（需手动定时、选帧、清背）。

另有外围能力：`self-loop-run`（水平/垂直/四向无缝循环背景图）、`animation-edit-run`（对已有 GIF/WebP 换肤，改外观不改动作）。

## 2. 原版实现线索

### 2.1 animate-run（经典线）参数与行为

```bash
animate-run --image-file char.png \
  --prompt "8 帧紧凑待机动画，轻微呼吸与衣摆飘动" \
  --output-frames 8 --output-format webp --animation-type idle \
  --animation-model pixel-engine-v1.1 --optimize-prompt \
  --color-count 32 \
  --padding-top 16 --padding-down 8 --padding-left 12 --padding-right 20 \
  --remove-bg-method advanced --background-color '#c6c6c6'
```

- **双引擎按源图尺寸自动路由**：PNG 宽高均 ≤256px → `pixel-engine-v1.1`（像素专用运动/边缘处理）；任一边 >256 → `frame-engine-v1.1`（通用帧引擎，兼容大像素图、Q 版、小 HD 角色，但无像素优化）。CLI 不传 model 时镜像 Web 默认路由。
- 帧数：4/6/8/10/12/16 可选，默认 8；**帧数必须为偶数**；像素动画支持 2-16 帧，HD（原版帧动画）支持 2-24 帧。
- `--output-format webp|gif|spritesheet` —— spritesheet 直接面向引擎。
- `--animation-type`：`idle / walk / run / jump / attack / hit / defeated / other` 八类动作枚举（对前端是动作模板选择器）。
- `--color-count 2-64`：像素线专属调色板大小控制。
- **四向独立透明 padding**（top/down/left/right）：在**不缩放主体**的前提下扩出运动空间。源图尺寸=动画画布尺寸，画布任一轴 >256px 直接校验失败（防无效付费提交）。64px 角色推荐最终画布 ≤128×128。
- `--optimize-prompt` 默认开启：后端读源图 + 用户动作描述，翻译成英文并改写为动画模型易懂的 prompt。用户只表达意图，不手写技术 prompt。
- 通用帧模式输出上限 **480p**（源图再大也压到 480p）。
- 质量官方提示：16 帧是 8 帧两倍时间预算，模型可能"多编一个动作节拍"而不是把同一动作做得更好；要用 16 帧需显式写慢动作词（jumps gently / attacks slowly）。

### 2.2 keyframes-run（关键帧控制线）

```bash
keyframes-run --keyframe 0=attack-start.png --keyframe 4=attack-impact.png \
  --keyframe 7=attack-recovery.png --keyframe-strength 4=0.85 \
  --prompt "一次清晰的向右挥剑并回到起手式" --total-frames 8 ...
```

- `--keyframe INDEX=PATH`：帧索引从 0 开始、必须含第 0 帧、至少 2 张；`INDEX < total-frames`。
- `--keyframe-strength INDEX=0~1`：每个关键帧可设约束强度（默认未给，示例 0.85）——实现上大概率是"该帧处对参考图的条件权重"。
- total-frames 6/8/10/12/16/20 可选，必须偶数；所有关键帧**同画布、同锚点、同 padding**。
- 官方工作流：关键姿势图可用 `image-edit-run`（精度最高）或 `character-multi-view-run`（批量多方向、姿态精度较低）预制。
- 定位：约束动作轨迹、保留帧动画输出，优于 video 的场景 = 攻击/跳跃/闪避等需要特定过渡的动作。

### 2.3 原版帧动画命令（新一代线）

- `--style-mode pixel|hd`；`--resolution 480p|720p`（像素限 480p，HD 720p 加收 10 credits）；`--output-frames 8|16|24|32`（默认 16 帧=2 秒）；`--quality-mode standard|medium|advanced`（ultimate 未上线）；`--alpha-mode soft|sharp`（保留半透明 vs 二值化 alpha；像素默认 sharp）。
- `--animation-mode loop|non_loop`；给 `--last-image-file`（首尾帧控制，两图尺寸必须一致）时强制 non_loop——**首帧图 + 尾帧图夹住运动轨迹**。
- `--padding`（单值）+ `--padding-alignment`（9 宫格对齐），与经典线的四向独立 padding 是两套交互，但目的相同：扩运动空间。
- 最终下载的 WebP **永远循环播放**，`--animation-mode` 只影响运动设计而非播放元数据。
- 去背仅 none/standard（advanced 暂不可用），底色默认 `#c6c6c6`。

### 2.4 video-run（短视频兜底线）

- `--motion-mode controlled`（严格首尾关键帧控制）| `complex`（动作更复杂、整体质量优先）；`--frame-count 32|40|48`；`--resolution 480p|720p`；`--pixel` 开关保像素运动。
- 首帧必填，尾帧可选（都给 = first-to-last 控制）；服务端自动选画布，**不允许传宽高比**。
- `--action` + `--direction` 必须成对使用（内置动作模板，可用 `video-prompt-list --motion-mode <mode>` 预览），替代自由 prompt。
- 视频模型遵循长 prompt 能力差：官方要求 prompt 短、直白、字面；主体+一个动作+方向+至多一个特效。
- 输出**无音频**、**不自动抽帧、不自动去背**——官方明确这些后处理"AI 还做不可靠"，需人工导入编辑器。这条边界是产品化的诚实设计。

### 2.5 动作类型的循环特性（复刻时预置到动作模板表）

| animation-type | 循环要求 | 画布敏感方向 | 备注 |
|---|---|---|---|
| idle | 必须无缝循环 | 上（呼吸起伏） | 8 帧默认足够 |
| walk / run | 必须循环、末帧≈首帧 | 前+后 | 起手姿势=双腿前后分开 |
| jump | 循环可选（常用非循环+落地帧） | 上 | 起跳前留顶部空间 |
| attack | 通常非循环，回起手式 | 攻击方向 | 最依赖 keyframes 线 |
| hit / defeated | 非循环 | 受击反方向 | defeated 常接消失帧 |

这张表对应 animate-run 的 `--animation-type` 枚举，自部署时作为服务端动作模板（每类固化 padding 建议、循环校验策略、默认帧数），而不是把策略散在客户端。

### 2.6 高质量动画的官方前置工作流（复刻必抄的领域知识）

1. **源图即第一帧**：源图会成为动画第一帧，所以要用"动作起手姿势"而不是中立站姿（攻击图=持械欲击姿势；跑=两腿前后分开）。起手姿势正确 → 模型把帧预算花在动作上并自然回归起点；错误起点会产生废帧、难以闭环。
2. **预留透明运动空间**：跳要头顶空间、右向攻击要右侧空间；模型无法进入不存在的空间，prompt 增强也补不回来。padding 按动作路径定向给，不是每边都拉满。
3. **prompt 只写动作**：动作 + 强度 + 镜头行为（locked camera）+ 循环要求 + 保持不变的东西（剪影/调色板/硬边）。
4. **生成后验证**：完整播放、查循环边界（idle/walk/run）、查相机漂移、像素图整数倍缩放下是否清晰。

## 3. 自部署路线对比

| 维度 | A. 逐帧生成（多图模型逐帧 + 一致性约束） | B. AnimateDiff / 插帧（视频扩散） | C. 骨骼动画（Spine/Live2D 思路） |
|---|---|---|---|
| 思路 | LLM/图像模型按首帧批量产出 N 张姿势图，拼 sheet | 单图+prompt → AnimateDiff/SD 视频模型生成帧序列，或 SVD/CogVideoX 类出片后抽帧 | 静图拆部件 → 蒙皮绑定 → 引擎内骨骼驱动 |
| 一致性 | 难（帧间漂移，需 IP-Adapter/噪声治理） | 中-好（时序模块天生保一致） | 最好（同一套部件） |
| 像素风适配 | 好（可逐帧走像素管线） | 中（时序平滑会糊像素边；需硬边保持 + 量化后处理） | 差（像素角色拆件工作量巨大） |
| 循环动画 | 靠首尾帧约束 | 靠 loop 提示词/首尾帧条件（AnimateDiff 有 loop 支持） | 天然循环 |
| 引擎友好度 | 高（直接 spritesheet） | 中（需抽帧+清背+对齐） | 高（运行时插值，体积小、可换肤） |
| 工程复杂度 | 中（批量编排+挑选） | 低-中（现成模型管线） | 高（拆件/绑定自动化是大坑） |
| 代表开源件 | SDXL+IP-Adapter、控制网姿态序列 | **AnimateDiff（SD1.5/SDXL）**、SVD、CogVideoX、Wan2.1、LTX-Video | Spine runtime（付费编辑器）、DragonBones（免费）、社区骨骼拆件模型 |
| 与原版四线映射 | ≈ keyframes-run / 原版帧动画 的手工版 | ≈ animate-run / 原版帧动画（引擎=私有"pixel/frame-engine"） | ≈ spine-run（原版的 Spine 换肤产品线，独立于帧动画） |

**关键判断**：原版的 `pixel-engine-v1.1` / `frame-engine-v1.1` 本质就是"图生帧"专用视频扩散模型（输入=静图+prompt，输出=定长偶数帧、循环）。自部署最接近的等价物是 **AnimateDiff 类时序模型**，而不是 LLM 逐帧画。逐帧生成（A）适合做 keyframes 线的"关键姿势图"供给，以及 video 抽帧后的补帧，不适合直接量产 sheet。

## 4. 推荐方案

分层复刻，对应原版四线：

1. **主力（≈animate-run / 原版帧动画）**：**AnimateDiff v3（SDXL 底）或 CogVideoX-5B / Wan2.1-1.3B 图生视频 + 后处理抽帧**，封装为统一管线：
   `静图 → (padding 校验器) → 图生帧模型 → 帧对齐/循环回写 → 像素量化(像素线) → 去背 → WebP/GIF/spritesheet`
   - 帧数=8 默认、偶数约束、像素 ≤16 帧/HD ≤24 帧等契约原样保留。
   - 像素线追加"每帧最近邻下采样 + 调色板量化到 `color-count`"步骤，保住硬边。
2. **关键帧线（≈keyframes-run）**：AnimateDiff 的 ControlNet（openpose/涂鸦）关键帧条件，或对生成序列做**首尾帧插值**（FLF 端到端模型或 SVD 的 keyframe 条件）。`keyframe-strength` 映射为条件权重。
3. **视频兜底线（≈video-run）**：直接用图生视频模型（Wan2.1 / CogVideoX）出 32-48 帧片段，产品侧明确"原始视频，需人工抽帧清背"——照抄原版的诚实边界。
4. **工程框架**：ComfyUI 编排（AnimateDiff 节点生态最全）+ 自研薄 API 层实现 submit/poll/契约校验（画布 >256px 拒绝等）。

## 5. 关键工程细节 / 坑

1. **循环闭合是第一优先级**：idle/walk 必须末帧≈首帧。做法：末帧用首帧图做参考条件、或生成 N+1 帧后与首帧做光流对齐混合；验证脚本自动算首尾帧差异阈值。
2. **帧间主体漂移**（逐帧路线最痛）：颜色/细节逐帧变化 → 拼成 sheet 后"闪"。缓解：低 denoise 强度的 img2img 链、IP-Adapter 锁主体、种子固定；治标方案是逐帧调色板统一（以首帧调色板为准量化）。
3. **padding 是质量放大器**：原版反复强调"运动空间不足是动画失败最常见原因"。自部署要在提交前校验：检测主体包围盒（alpha 通道）→ 按动作类型（attack 需前向空间、jump 需顶部空间）建议/强制 padding。
4. **首帧=源图**：图生帧模型天然如此，但产品要让用户上传"动作起手式"而非站姿——UI 提示 + 示例图引导（原版用文档教育用户，我们可再加检测：起手式识别提醒）。
5. **偶数帧、帧数预算**：8 帧出标准动作，16 帧不是"更流畅"而是"模型可能加戏"，要配合慢动作措辞。复刻时把官方这套 prompt 策略写进 prompt 增强层。
6. **像素动画画布 ≤256 硬约束**：提交前校验，省无效算力；`source + padding` 的最终画布计算要在服务端复算，不信客户端。
7. **spritesheet 排布**：输出 spritesheet 时要记录帧宽高/列数元数据（Aseprite/Unity 切图约定），否则引擎侧不可用。
8. **alpha 双模式**：sharp（二值化，像素风必需）vs soft（HD 半透明特效）。像素线默认 sharp，否则边缘半透明像素在引擎里发糊。
9. **去背时机**：动画线去背在**生成后**做（animate-run 有 remove-bg-method），每帧独立去背可能导致帧间边缘闪烁——自部署可先去背源图再动画，或对全帧统一背景模型。

## 6. 参考链接

- 原版官方动画文档（本地镜像）：`references/animation-and-video.md`、SKILL.md 动画章节
- 参数契约：`web_parameter_contract.json` → animation_model / animation_keyframes / frame_animation_new / video_model
- CLI：原版 CLI 源码 L6851-7010（animate / 原版帧动画 / keyframes / video）
- AnimateDiff: https://github.com/guoyww/AnimateDiff
- ComfyUI AnimateDiff-Evolved: https://github.com/Kosinkadink/ComfyUI-AnimateDiff-Evolved
- CogVideoX: https://github.com/THUDM/CogVideo
- Wan2.1（开源图生视频）: https://github.com/Wan-Video/Wan2.1
- Stable Video Diffusion: https://huggingface.co/stabilityai/stable-video-diffusion-img2vid-xt
- DragonBones（免费骨骼替代）: https://egret.com/zh/dragonbones
