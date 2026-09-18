# 06 UI 生成与组件分割（ui-gen，generate/extract 双模式）技术文档

> 复刻对象：原版的 `ui-gen-run`——提示词驱动的 UI / 资产表生成，自动去背 + 组件分割；以及 extract 模式：从已有 UI 图中提取可见元素重排成一张聚合表。模块名虽叫 UI，实际是"HD 资产表 + 自动去背 + 组件分割"的通用入口。
> 一手资料：`references/ui-and-image-editing.md` L14-18（Important guidance）、L32-47（能力表）、L53-94（generate/extract 双示例与规则）、L174-179（Validation）；`web_parameter_contract.json` L120-133（ui_agent）；原版 CLI 源码（行号见各节，均已实际打开核实）；`SKILL.md` L74/L99/L105；`references/capability-routing.md` L17/L49。
> 计费说明仅一句：官方 web 按 credit 计费、quality 档位影响成本，本地资料未披露 ui-gen 单次扣费数；复刻为单用户自部署本地工具，无 credit / 订阅 / 配额概念。

## 1. 功能描述

### 1.1 定位与双模式

`ui-gen-run` 是一个命令、两种模式（`--mode generate|extract`，默认 generate）：

- **generate**：纯 prompt（可带最多 8 张参考图）生成一张 UI 表 / HUD / 菜单 / 按钮图标集，甚至普通资产批或精灵表——"结果主要取决于 prompt，而不取决于模块名"（SKILL.md L74、ui doc L93）。官方强调不要因模块名把它当纯 UI 工具（capability-routing.md L49）。
- **extract**：已有 UI 图（1–8 张参考，至少 1 张硬性必填）→ 提取全部可见 UI 元素（默认全量，ui doc L18）→ 重排成一张"可复用观感"的聚合表。

两个模式共享同一条输出契约与后处理开关（自动去背、组件分割默认开启）。

### 1.2 输出契约（复刻必须逐字对齐）

- 交付物 = **一张透明聚合表 + 组件分割数据**；**不返回逐个组件的独立裁剪媒体文件**（ui doc L38 能力表、L94 原文，SKILL.md L105 再次强调）。
- 验收口径（ui doc L177）：组件视觉上相互分离、透明度可用、分割包围盒合理；且**不得声称产出了单独的组件文件**。
- 只交付 `final_outputs.json` 清单里的文件（ui doc L179；running-and-outputs.md L19）。

### 1.3 与已写文档的边界（本篇不重复展开）

| 相邻能力 | 归属 | 本篇关系 |
|---|---|---|
| `remove-background-run` / `pixelate-run` / `self-loop-run` 独立后处理 | 04 篇 | ui-gen 内嵌的去背只是串联调用，档位语义引用 04，不重写方案 |
| `animation-edit-run` 动画帧编辑 | 02 篇 | 同一参考文档章节，但能力不同，本篇不展开 |
| `one-click-upgrade-*` 一致升级/变体 | 03 篇 | 同上 |
| `image-edit-run` 静态图编辑 | ui doc L133-154（未成篇） | 本篇只划界：编辑保持画布贴近原图；ui-gen 是"生成新表" |

生产链位置（SKILL.md L99/L105）：HD 批量生成时若需要自动去背+组件分割，用 ui-gen 替代 nano-banana/image-2 裸生成；UI 链路为 "UI 生成或提取 → 静态图精修（image-edit）"。

## 2. 官方参数契约

`web_parameter_contract.json` L120-133 `ui_agent` 块（web_sources: `types.ts:UiAgentGenerationSettings` / `SettingsPanel.tsx` / `uiGenService.ts`）：

| 参数 | CLI flag | 默认 | 取值 | 请求字段 |
|---|---|---|---|---|
| mode | `--mode` | generate | generate / extract | `generation_mode` |
| generation_model | `--generation-model` | image-2 | nano-banana / image-2 | `generation_provider` |
| quality | `--quality` | detailed | standard / detailed / ultimate | `image2_quality` |
| generation_speed | `--generation-speed` | normal | normal / fast | `generation_speed` |
| resolution | `--resolution` | 2K | 1K / 2K | `resolution` |
| aspect_ratio | `--aspect-ratio` | 1:1 | 4:3 / 3:4 / 16:9 / 9:16 / 1:1 | `aspect_ratio` |
| background_color | `--background-color` | #cccccc | 黑/白/#cccccc/灰/#333333 五枚举 | `background_color` |
| remove_background | `--remove-background` / `--no-remove-background` | true | bool | `remove_background` |
| split_components | `--split-components` / `--no-split-components` | true | bool | `split_components` |

CLI 在 web 契约之外多暴露一个 `--remove-bg-method none|standard|advanced`（默认 standard，argparse L6595-6600），web 契约块中**没有**这个参数——推断 web 面板固定用 standard 或隐藏了该选择（未披露）。此外三个语义口径（ui doc）：

- `1K`/`2K` 是**服务分辨率档位，不是统一像素承诺**，实际尺寸以产出图为准（L88）；参考图应匹配目标档：1:1 画布下 1K≈1024×1024、2K≈2048×2048（L16）。
- quality 档位语义：standard=快速草稿、detailed=常规生产、ultimate=小字/密纹最终资产（L89）。
- 去背档位语义：standard=简单高对比边缘，advanced=精细复杂边缘的透明度（L91）。

## 3. CLI 源码证据（原版 CLI 源码）

### 3.1 端点与异步 job 形态

- L190：`UI_GEN_ENDPOINT = "/api/workflows/general_ui_gen/run"`——后端 workflow 名是 `general_ui_gen`（"general" 即"不限于 UI"）。
- L191-202：`UI_GEN_SUBMIT/RUN/POLL_COMMANDS` 三组命令名：`ui-gen-submit`（alias `general-ui-gen-submit`）、`ui-gen-run`（alias `general-ui-gen-run`）、`ui-gen-poll`（alias `general-ui-gen-poll`）。与其他模块同构：submit 返回 job → run 轮询到终态并下载 → poll 用 `--api-job-id`（L6620-6622）恢复任意历史 job（poll 路由到 `poll_workflow_id="general_ui_gen"`，L8834-8835）。
- L1593：`_WORKFLOW_FINAL_OUTPUT_FIELDS["general_ui_gen"] = frozenset({"output_path", "url"})`——runner 只投影**单个** `output_path`，与"只交付一张聚合表"契约互证；组件分割数据不在下载投影里（见 §5 缺口）。
- 终态后 `_save_run_outputs(workflow_id="general_ui_gen")`（L9232）落盘并写 `final_outputs.json`（L5603，通用逻辑同 L2230）。
- L7039：`ui-gen-run` 在 `public_commands` 集合内（`--version` 校验时提示需更新的命令名单，SKILL.md L113 语义）。

### 3.2 请求构造（L5150-5213 `submit_ui_generator`）

- **模式归一**（L5142-5147）：`generate→"generate"`、`extract→"ui_extract"`——后端 canonical 值是 `ui_extract`，请求字段 `generation_mode` 实际发送 `generate|ui_extract`。
- **参考图**（L5131-5139）：append 收集后 **>8 张直接抛错**（"UI generation accepts at most 8 reference images"），逐张校验存在性，以 multipart 字段 **`reference_files`** 上传。
- **extract 硬门禁**（L5198-5199）：`ui_extract` 模式 0 张参考 → 抛错 "ui_extract mode requires at least one --reference-image"。
- **quality 映射**（L5170/L5186）：standard→low、detailed→medium、ultimate→high，发到 `image2_quality`——复用 Image-2 的三档质量接口。
- **provider 映射**（L5187）：nano-banana→`nanobanana`、image-2→`image2`，发到 `generation_provider`。
- **去背开关耦合**（L5181）：`effective_remove_bg_method = remove_bg_method if remove_background else "none"`——`--no-remove-background` 会**强制覆盖** remove-bg-method 为 none；`remove_background` 布尔与档位字符串两个字段都发送（L5190-5191）。
- 其余字段（L5182-5194）：prompt、resolution、aspect_ratio、generation_speed、background_color、split_components 原样透传。POST JSON + multipart，非 2xx 抛错（L5201-5213）。
- `run_ui_generator`（L5216-5268）：submit → 取 `job_id/api_job_id` → `wait_submitted_workflow_job(label="ui-gen")` 轮询至终态。

### 3.3 argparse 面（L6565-6618）

- `ui-gen-submit`（L6565-6612）：`--prompt` 必填；`--reference-image/--reference-file` append，help 明示 "Required in extract mode; optional style reference in generate mode; repeat up to 8 times"（L6568-6575）→ **generate 模式参考图的角色被官方定义为风格参考**；`--resolution`（L6576）、`--aspect-ratio`（L6577）、`--quality`（L6578-6583）、`--generation-model`（L6584-6588，choices 用全局 `GENERATION_MODEL_CHOICES` L83）、`--generation-speed`（L6589，L84）、`--background-color` **五值枚举** `#000000/#ffffff/#cccccc/#808080/#333333`（L6590-6594）、`--remove-bg-method`（L6595-6600）、`--mode`（L6601）。
- `store_true/store_false` 成对开关（L6602-6606）：`--remove-background/--no-remove-background`、`--split-components/--no-split-components`，`set_defaults` 先钉 true（L6602）。
- L6607-6612 `set_defaults(template="hd_retro_rpg", generation_provider="image2", project_id=None, thread_id=None)`：注意 **template 只是解析器默认值，`submit_ui_generator` 的请求体里没有 template 字段**（对照 L5182-5194）——客户端残留默认，不构成后端契约，复刻时不要照抄。
- `ui-gen-run`（L6614-6618）复用 submit 的全部 action 再加共享运行时参数（`add_shared_runtime_args`：timeout/max_wait/poll_interval 等）；`ui-gen-poll`（L6620-6622）只要 `--api-job-id` + 路径参数。

### 3.4 运行时分发（L9138-9246）

submit 分支回显 request_payload 并写 meta JSON；run 分支先打印 `planned_output_dir`（slug 由 prompt 派生，L9187-9188），跑完 `_save_run_outputs`（下载投影见 §3.1）+ `_write_meta`（args/request/response/downloads 全量留档）+ 打印 `final_outputs.json`。单用户自部署可直接沿用这套"落盘即清单"的产物纪律。

## 4. 自部署复刻技术选型

### 4.1 generate 模式管线

```
prompt(+≤8 风格参考)
→ 底模生成分辨率档位（1K/2K × 五种长宽比 → 本地固定像素映射，见 §5-4）
→ 在纯色 matte（默认 #cccccc，同官方五枚举）上出整表
→ 去背（BiRefNet/rembg，standard 单模型 / advanced 加 alpha matting 精修，方案=04 篇）
→ 组件分割（§4.2）→ 透明聚合表 PNG + components.json → final_outputs.json 清单
```

底模：**Flux.1-dev 或 Qwen-Image**（HD 线质量），ComfyUI 编排；参考图经 IP-Adapter / Redux 融合风格（argparse 把 generate 模式的参考定义为 style reference，L6574）。像素风资产表末端可串 04 篇像素化，但注意 SKILL.md L35 的禁令语义：原版声称像素产物已像素化；自部署则由我们自己保证。

### 4.2 组件分割（本模块的真正技术核）

| 路线 | 思路 | 优点 | 缺点 |
|---|---|---|---|
| A. alpha 连通域 | 去背后对 alpha 做连通域分析，每块=一个组件 | 零额外模型、快 | 元素相触即粘连；无语义标签 |
| B. 开放词表检测+分割 | GroundingDINO（"panel . button . icon . tab . slider . progress bar . dialog" 类 UI 词表）出框 → SAM2 逐框出 mask | 语义标签 + 分离鲁棒，最贴近"component segmentation"语义 | 需 GPU 两段推理；UI 词表要自己维护 |
| C. VLM 规划 | Qwen2.5-VL 看图列举元素清单再配 B | extract 模式理解"提取目标"必需；可输出人读清单 | 慢、坐标精度靠 B 兜底 |

**判断**：generate 模式用 A 起步、B 增强并打标签；extract 模式必须 C+B。分割结果统一落 `components.json`：`[{id, label, bbox[x,y,w,h], mask(RLE 或蒙版文件), area_px, confidence}]`——这是我们自己定义的 schema（官方格式未披露，见缺口清单）。

### 4.3 extract 模式

- **确定性重排（推荐默认）**：参考图检测+分割 → 按 mask 抠出组件像素 → shelf/skyline 装箱算法排进新画布（留 padding、按类别分组）→ 直接输出。优点：像素级忠实、零风格漂移、"reusable-looking" 天然成立。
- **生成式重排（可选增强）**：Qwen-Image-Edit / Flux Kontext 类指令模型"把这些元素重排成一张整洁的表"。更美观但组件身份/细节可能漂移，且与原版 "不返回独立组件文件、强调分离度" 的保守口径相悖——仅作开关选项。
- prompt 描述提取目标（ui doc L18/L75 示例："Extract the reusable panels, buttons, icons, and tabs"），默认全量提取；C 路线先产出元素清单供单用户确认再跑，把"提取错对象"的成本前置。

### 4.4 job 形态与交付

复刻 submit/run/poll 三命令与 `final_outputs.json` 清单：本地单用户无需并发配额，用进程内队列 + job 目录（`runs/<slug>-<ts>/`：sheet.png、components.json、final_outputs.json、meta.json）即可，保留 poll 以便恢复中断任务（官方 L6620-6622 语义）。交付校验照抄 ui doc L176-179：打开成品确认没把参考图当产出、组件分离、透明可用、bbox 合理、只交付清单内文件。

## 5. 工程细节与坑

1. **"单张聚合表"是硬契约不是实现细节**：组件分割数据与图同交付，但绝不裁剪导出单件。自部署虽然裁剪零成本，仍建议默认保持同构（可加 `--export-crops` 自用扩展并标注为复刻超集），否则下游脚本按官方契约写的消费逻辑会分叉。
2. **`--no-remove-background` 与 `--remove-bg-method` 有覆盖关系**（L5181）：前者把后者强制为 none。复刻时要原样实现这条优先级，否则两个开关组合的行为与官方不一致。
3. **background_color 的作用是"给去背留 matte"**：五枚举（默认 #cccccc 中性灰）显然服务于"先在纯色底上画、再抠掉"的管线（推断，官方未明说）。自部署固定用中性灰 matte + matting 模型即可，不必真的实现五色——但保留参数位以对齐 CLI 面。
4. **分辨率档位要自定具体像素映射**：官方只承诺"档位"（L88），1K/2K 精确尺寸未披露。自部署建议写死：1:1→1024²/2048²，4:3→1024×768/2048×1536，16:9→1024×576/2048×1156（再就近取底模支持值），并把**实际输出尺寸写进 components.json/final_outputs.json**，消费方永远以元数据为准。
5. **参考图数量校验要在提交前做**（>8 抛错、extract 0 张抛错，L5133/L5198）——把无效请求挡在 GPU 前；同时 generate 模式的参考是风格参考，别在 prompt 里指望它当布局模板逐像素复刻。
6. **prompt 纪律**（ui doc L17/L92 + SKILL.md L23-25）：描述流派/层级/配色/材质/状态/所需组件即可，禁止 diffusion 式关键词堆叠——过细的指令反而压缩变化、降低质量。这与传统 SD 工作流习惯相反，复刻品的提示词模板要按此设计。
7. **分割质量自动门禁**：交付前自动检查——组件数>0、无单块巨型粘连 blob（占画布面积超阈值告警）、每个组件透明像素可用（alpha 连通、非全零）、bbox 两两重叠率。对应 ui doc L177 的"分离/透明/合理包围盒"。
8. **`template="hd_retro_rpg"` 是解析器残留默认**（L6608），请求体根本不带 template 字段（L5182-5194）。复刻时不要脑补一个"UI 模板系统"，官方证据不支持。
9. **split_components=false 的效果未知**：关掉分割后是只省略分割数据、还是连聚合表内容都不同，本地资料无证据。复刻先按"仅跳过 components.json 生成"实现并标注假设。
10. **extract 的"重排"边界**：官方只说 reorganized/reusable-looking（L18/L71），未说是否重新渲染元素。确定性装箱重排是保守且可验证的实现；生成式重排会引入身份漂移，两者不要混在默认路径里。

**官方未披露机制（诚实缺口，不在本文档编造）**：分割数据的具体格式与字段（L1593 投影只有 output_path/url，runner 不下载分割数据，格式无从考证）；1K/2K 的精确像素矩阵；extract 是否重渲染；standard/advanced 去背的真实模型；split_components=false 的实际效果；ui-gen 的官方 credit 单价。

## 6. 参考链接

- 本地镜像一手资料：`references/ui-and-image-editing.md` L14-18/L53-94/L174-179；`SKILL.md` L74/L99/L105；`references/capability-routing.md` L17/L49
- 参数契约：`web_parameter_contract.json` L120-133（ui_agent）
- CLI：原版 CLI 源码 L83-84、L190-202、L1593、L5131-5213、L6565-6622、L8834-8835、L9138-9246
- 相关篇：04（去背 standard/advanced 方案）、03（one-click-upgrade）、02（animation-edit）
- 开放词表检测：GroundingDINO https://github.com/IDEA-Research/GroundingDINO ；分割：SAM2 https://github.com/facebookresearch/sam2
- VLM 元素清单：Qwen2.5-VL https://github.com/QwenLM/Qwen2.5-VL
- 去背：BiRefNet https://github.com/ZhengPeng7/BiRefNet 、rembg https://github.com/danielgatis/rembg
- 参考融合：IP-Adapter https://github.com/tencent-ailab/IP-Adapter 、Redux https://github.com/black-forest-labs/flux
- 游戏资产表布局参考：Skyline/shelf bin-packing https://en.wikipedia.org/wiki/Shelf_packing ；游戏 UI 九宫格面板规范 https://en.wikipedia.org/wiki/9-slice_scaling
