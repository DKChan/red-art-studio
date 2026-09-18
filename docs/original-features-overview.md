# 原版功能总览（自部署复刻参考）

> 依据本地一手资料（原版官方 Skill 仓库文档 + CLI 源码 + Web 参数契约）与官网补充信息整理。

## 1. 产品定位与目标用户

**一句话定位**：原版产品是一个面向独立开发者和小团队的 **AI 游戏素材工作室**（AI Game Art Studio）——输入自然语言即可生成像素/HD 角色、精灵包、tileset、地图、UI、动画、视频、音效与音乐，并提供游戏策划文档 Agent。

**三大卖点**（官网首页）：
1. **Pixel characters**：可读剪影/姿势，prompt 为游戏美术迭代而建
2. **Sprite packs**：风格一致的 props / tilesets / 环境组件
3. **Animation direction**：关键姿势 / 变体 / 游戏就绪的动画方向探索

**目标用户**：独立游戏开发者、小团队美术/策划、Game Jam 参赛者；官方还提供面向 Agent 的 CLI/Skill（Claude/Codex 等 AI 编程助手可直接调用 87 个子命令）。

## 2. 功能全景图

CLI 共 **87 个子命令**。生成类命令多为 `*-run`（提交+轮询+下载一条龙），部分提供 `*-submit / -poll / -history / -download / -cancel` 细粒度拆分。计费特征：图片类约 1 credit/张，动画类约 10 credit/个（官网 pricing 换算）；Game Designer 按 token 实时扣费（独立于素材 credit）。

| 能力域 | 代表功能 | 对应 CLI 命令 | 计费特征 |
|---|---|---|---|
| 像素美术 | preset 驱动像素生成（32px/64px 等固定尺寸契约）、大画幅像素场景、通用 4:3 像素画布（资产包优化）、自定义尺寸像素、拼豆艺术 | `pixel-gen-template-info/-submit/-run/-poll/-history/-download/-cancel`、`large-pixel-gen-run`、`pixel-universal-gen-run`、`custom-size-pixel-gen-run`、`pindou-run` | 按张计费（约 1 credit） |
| HD 美术 | preset 驱动 HD 资产生成；通用 HD 生图（Nano Banana / Image-2，1K-4K、14 种宽高比） | `hd-gen-template-info/-submit/-run/-poll/-history/-download/-cancel`、`nano-banana-run/-poll`、`image-2-run/-poll` | 按张计费，quality/resolution 影响价格 |
| 角色 | 八方向角色表（横版/纵版）、动作约束 | `character-multi-view-run`（pixel/hd 模式） | 按张计费 |
| 地图瓦片 | 斜 45°/六边形像素瓦片、HD 斜 45°/HD 六边形瓦片（standard=1×1、tetraploid=2×2 占位契约）、横版三层视差地图（像素/HD，7 种画风） | `isometric-gen-*`、`hex-isometric-gen-*`、`hd-isometric-gen-*`、`hd-hex-isometric-gen-*`、`side-scrolling-map-run`、`hd-side-scrolling-map-run` | 按张/套计费 |
| 纹理与 tileset | 64×64 无缝平铺纹理、2:1 等距纹理、64px 双网格地形图集（foreground/background/dual）、等距 tileset | `texture-gen-submit/-run/-poll`、`isometric-texture-run`、`tileset-gen-submit/-run/-poll`、`isometric-tileset-run` | 按张计费 |
| UI | UI/资产表生成（自动去背+组件分割，仅返回聚合表+分割数据）、已有 UI 元素提取重排 | `ui-gen-submit/-run/-poll`（mode: generate/extract，最多 8 张参考图） | 按张计费 |
| 图像编辑处理 | 精确静帧编辑、一键升级/一致变体（先出 prompt 清单再跑）、动画帧编辑（GIF/WebP 保时序换皮）、去背景（standard/advanced）、像素化转换 | `image-edit-run`、`one-click-upgrade-prompts` + `one-click-upgrade-run`、`animation-edit-run`、`remove-background-submit/-run`、`pixelate-submit/-run` | 按次计费 |
| 动画 | 精灵帧动画（像素引擎≤256px 自动路由/帧引擎，2-16/24 帧，WebP/GIF/spritesheet）、关键帧控制动画、新 8/16/24/32 帧像素或 HD 动画、无缝循环图（水平/垂直/四向） | `animate-submit/-run/-poll`、`keyframes-run`、原版帧动画命令（480p/720p）、`self-loop-submit/-run` | 约 10 credit/个动画 |
| 视频 | 首帧（或首尾帧）生成短片，controlled/complex 运动模式，32/40/48 帧，480p/720p | `video-prompt-list`（内置动作 prompt 库）、`video-run` | 高于帧动画计费 |
| 音频 | 音效（单发/成包/变体，0.5-10s）、音乐（demo 试听 30s / pro 成品渲染，可挂参考图影响氛围） | `sound-submit/-run/-poll`、`music-submit/-run/-poll` | 按条计费 |
| 游戏设计文档 | 策划 Agent：概念/机制/平衡/内容规划，产出持久化 Markdown 设计文档树 | `game-design-run`、`game-design-poll` | 按 token 实时增量扣费（无预扣无封顶）；每月 100 万免费 token≈60 credits 共享额度 |
| 风格与一致性 | 公共风格 preset（原版自带的岛系风格模板）、变体一致性工作流、参考图驱动的风格/角色一致性 | `style-gen-run`（template/variant）、`one-click-upgrade-*`、各生成命令的 `--reference-file(s)` | 按张计费 |
| 参考素材库 | 1000+ 内置地图参考（可未登录检索/下载）、标准 64×64 纹理参考库 | `map-reference-search`、`map-reference-download`、`texture-reference-search`、`texture-reference-download` | 免费资源 |
| Spine | 内置模板换皮（4 头身 slim + 5 个 2 头身模板）、上传 Spine 3.6-4.2 包检视并替换 1-10 个部件，导出 4.2 或 3.8 | `spine-run`、`spine-inspect`、`spine-edit-run` | 按次计费（偏专业向） |
| 平台能力 | credit 余额查询、账号级自定义工作流、异步任务模型、preset 模板体系 | `credits-balance`、`custom-workflow-list`、`custom-workflow-run` | — |

## 3. 关键产品机制

### 3.1 Credit 计费模型
- **订阅档位**：Starter $6/600cr、Creator $15/1600cr、Adventurer $60/7000cr+体验credits、Archmage $150。约 **1 credit=1 张图、10 credits=1 个动画**。
- credit 分四类入账：`paid + subscription + trial`，trial 有到期时间（`next_trial_credit_expires_at`），`credits-balance` 可查。
- Game Designer 单独按 token 计费：uncached 输入 150cr/M、cached 15cr/M、输出 900cr/M，逐轮增量扣，任务失败全额退款。
- 档位还限制：custom templates 上限 5/10/20、并发任务队列 1/2/5；生成素材商业所有权归用户。

### 3.2 异步 Job 模型
- `*-run` = submit + poll + download 一条龙；中断后用 `*-poll --job-id <原ID>` **恢复而非重提**（重提=重复扣费），这是官方反复强调的铁律。
- 任务产物落盘为「任务子目录 + `final_outputs.json`（脱敏清单）+ 最终媒体」，运行器刻意不保存中间产物/签名 URL/调试信息。
- 下载仅接受 HTTPS + `image/*|audio/*|video/*` Content-Type 白名单校验。
- 认证：官方 CLI 的 API key 环境变量（`ma_live_` 前缀）走环境变量或 .env，不接受命令行凭据；旧版 runner 兼容运行但提示升级。

### 3.3 Preset 模板体系
- 像素/大像素/HD 生成均以 **preset 为交付契约**：preset 定义输出尺寸与默认数量，prompt 不能覆盖；先 `*-template-info` 发现 preset 再选择，无匹配就明说差距。
- Web 端参数契约（`web_parameter_contract.json`）逐能力固化了可选参数/默认值/取值枚举（如 nano-banana 的 model/speed/resolution/aspect-ratio），CLI 与 Web 参数一一同构——自部署时可直接把该 JSON 当 API 合约蓝本。

### 3.4 风格一致性方法论
- 参考图机制：单参考保风格/角色身份，UI 最多 8 张；`one-click-upgrade` 先生成可编辑 prompt 清单再批量出 1-8 个一致变体，产物需比对风格/比例/锚点/尺寸。
- 生产链路约束：先定资产契约（类型/表示/尺寸/透明/交付结构）→ 选最小专用能力 → 静帧稳定后才动画 → 编辑保画布。prompt 主张「简短自然语言」，反对扩散模型式关键词堆砌。
- 动画质量三板斧：动作化首帧（不要中性站姿）、按动作方向预留透明画布（像素动画>256px 直接拒收）、默认 prompt 自动增强。

### 3.5 UGC 公开画廊
- 官网 Gallery 展示真实 prompt + 结果，分类含 Illustration(272) / Videos & Storyboards(102) / Character Design(97) / Product Design(61) / Sound Effect(42) / UI Layout(26)。
- 双重作用：转化示范 + SEO/搜索引擎可索引；本地文档中未见对应公开 API，CLI 只覆盖生成侧。

## 4. 自部署复刻 MVP 边界建议

### 核心必做（对齐三大卖点与计费闭环）
| 功能 | 理由 |
|---|---|
| 通用 HD 生图（nano-banana/image-2 等价物）+ 图像编辑 + 去背景 | 一切能力的底座；参数契约最完整，接自选生图模型即可起步 |
| preset 驱动像素生成（pixel-gen） | 「Pixel characters」卖点核心，preset 尺寸契约是差异化 |
| 帧动画（animate-run 等价物） | 「Animation direction」卖点核心、10 credit 高价值付费点 |
| one-click-upgrade（一致变体） | 「Sprite packs」卖点核心 |
| credit 计费 + 订阅档位 + API key + credits 查询 | 商业闭环，架构必须先定 |
| 异步 job 模型（submit/poll/history/download/cancel + 恢复不重扣） | 生成普遍分钟级，是可靠性骨架 |
| UGC 公开画廊 + 真实 prompt 展示 | 低成本获客/SEO，复刻门槛低 |

### 可后置（有依赖或工程量大，等核心验证后再加）
| 功能 | 理由 |
|---|---|
| tileset/纹理/isometric/横版地图全家族 | 依赖严密的锚点/占位契约与参考库，建议在通用生图上先包一层 preset 伪实现 |
| keyframes、原版帧动画（新帧动画）、video | 依赖动画底座稳定后叠加；video 计费高但频率低 |
| sound/music 音频 | 独立模型接入，产品完整性需要但非首周交付 |
| character-multi-view、large-pixel-gen、pixel-universal-gen、custom-size-pixel-gen | 可先用通用生图+降采样/裁切做降级版 |
| map/texture 参考素材库（1000+） | 内容运营成本高，可先用公开纹理源填充 |
| Game Designer Agent | token 计费模型与素材 credit 完全不同，建议 v2 |
| animation-edit、style-gen、custom-workflow | 长尾增强 |

### 可砍
| 功能 | 理由 |
|---|---|
| Spine 全家桶（run/inspect/edit） | 专业骨骼动画向，模板与格式兼容工程量大，受众窄 |
| pindou 拼豆 | 利基爱好向功能 |
| HD tetraploid(2×2) 精确坐标体系、七宫格内部模式 | 仅服务重度地图编辑场景，先只做 1×1 |
| 本地 map-preview/装配工具链 | 属于 Skill 附带工具，非 SaaS 产品核心 |

## 5. 信息来源清单
- 本地官方 Skill 仓库：`~/code/red-art-studio-refs/original-skills/skills/game-assets/`
  - `SKILL.md`（能力路由总纲、工作流链、生产规则）
  - 原版 CLI 源码（87 个子命令 argparse 定义）
  - 原版 CLI 文档（认证/版本兼容/恢复命令）
  - `web_parameter_contract.json`（Web→CLI 参数同构契约，26 项能力）
  - `references/`：capability-routing / pixel-and-hd-assets / ui-and-image-editing / maps-tiles-and-textures / animation-and-video / audio / game-design / running-and-outputs
- 官网补充（上级代理抓取）：首页定位与三大卖点、Pricing 四档位与 credit 换算、Gallery 分类计数、商业所有权声明
