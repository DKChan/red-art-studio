# 07 音频生成（音效 sound + 音乐 music）技术文档

> 复刻对象：原版的游戏音频能力：**音效生成**（单发 / 成包 sound-pack / 变体 variants，0.5–10s 时长契约）与**游戏音乐**（demo 试听 / pro 成品渲染、可挂参考图影响氛围）。两者共用一套 submit → poll 异步 job 模型，交付物落 `final_outputs.json` 清单。
> 一手资料：`references/audio.md` 全文（74 行）、`web_parameter_contract.json` L249-265（sound_effect / music）、原版 CLI 源码（行号见各节，全部实际打开核实）、`SKILL.md` L3/L88/L106、`references/capability-routing.md` L36-37、`references/running-and-outputs.md` L64-76/L104-110。
> 范围界定：游戏设计 Agent（game-design）不在本篇；本篇只覆盖声音资产的生产链路。
> 计费说明仅一句：官方 web 按 credit 计费，本地资料未披露 sound/music 单次扣费数；复刻为单用户自部署本地工具，无 credit / 订阅 / 配额概念。

## 1. 功能描述

原版把音频组织为 **2 条命令 + 3 种音效形态 + 2 档音乐输出**，核心产品契约：**音频是"最后一公里"资产——先定死玩法时序、动作和循环意图，再生成**（audio.md L17：视觉参考可以引导音乐情绪，但不能替代对配器、能量、循环行为的显式描述）。

### 1.1 能力 × 命令对照

| 能力 | CLI 命令 | 最终角色 | 关键限制 |
|---|---|---|---|
| 音效生成 | `sound-run`（alias `sfx-run` / `sound-effect-run`） | 单个音效、连贯音效包、同一音效的变体 | **pack 与 variants 互斥**（argparse 互斥组 + 请求侧双校验） |
| 音乐 | `music-run` | 出 demo 试听或 pro 成品轨 | 对应 web 产品的 `demo` / `pro` 输出模式（contract L264） |

两条命令均为 `-submit / -run / -poll` 三件套（L6504-6515、L6725-6739），异步 job 模型与图像/纹理线同构。

### 1.2 音效三形态（audio.md L19-45）

- **单发**：`--prompt` + `--duration`。官方引导：多数玩法音效保持 **1–2 秒**即覆盖交互/战斗/拾取需求（audio.md L5）。
- **成包**（`--sound-pack`）：一段 prompt 产出多个**不同但连贯**的声音（示例："木质 UI 点击音，覆盖 hover/confirm/cancel/locked/error 五态" + `--count 5 --duration 0.5`）。
- **变体**（`--variants`）：同一声音的多个变奏。与 pack **互斥**——一次请求只能"求同"（变体）或"求异"（包）。
- **循环**（`--loop`）：仅当最终音效需要持续循环时加（audio.md L41）。

时长契约（audio.md L43 + argparse L6492 + 请求侧 L4695-4697 三重一致）：**0.5 秒，或 1–10 的整数秒**。count 仅对 pack/variants 生效，1–10，默认 4（L6497、L4698-4700）。

### 1.3 音乐两档（audio.md L47-66）

- **demo（草稿）**：不渲染完整音频的**方向稿**。官方明确"仅当用户明确要 web 产品的 preview 模式时才用 `--output-mode demo`"（audio.md L66）；argparse 默认 `pro`（L6728）。
- **pro（成品）**：直接渲染交付轨。官方硬警告（audio.md L6）：**30 秒 demo 无法事后延长成同一首 3 分钟曲**——每次渲染相互独立，最终交付需要全长时必须直接渲染全长版。
- **参考图**：`--reference-image` 可重复传入，让视觉素材影响音乐的 mood / 配器（audio.md L66）；CLI 层 prompt 在有参考图时可不填（L6727："Music requirement text; optional when reference images are provided"），但请求字段里 prompt 仍原样提交空串（L9367-9375）。

### 1.4 输入纪律

音效 prompt 要具体到：声源、材质、动作、强度、视角、环境、尾音、循环要求（audio.md L43）。音乐要先显式写配器（instrumentation）、能量（energy）、循环行为（loop behavior）——参考图只是情绪侧信道（audio.md L17）。

## 2. 原版实现线索（file:line 证据）

### 2.1 后端 workflow 映射与命名泄露

`sound-run` → POST `/api/workflows/elevenlabs_generator/run`（L4715）。**workflow id 直接叫 `elevenlabs_generator`**（L1591 声明字段白名单、L8789 保存时传参、L8824-8826 poll 映射）——这是整个 CLI 里唯一一个以第三方厂商命名的后端 workflow，强烈暗示官方音效后端构建在（或兼容）ElevenLabs 的 SFX API 之上；但 CLI 仅是命名泄露，官方文档未证实，接线细节见 §5 缺口。

`music-run` → POST `/api/workflows/music_generator/run`（L5428），workflow 名是中性的 `music_generator`（L1605、L9431），**底模完全未泄露**。

两个 workflow 的终产物字段白名单相同：`{"audio_path", "audio_paths", "url"}`（L1591 音效、L1605 音乐）——即服务端可能返回单个路径、路径数组或裸 url 三种形态之一，runner 逐字段允许下载。

### 2.2 音效请求构造（form 编码，非 JSON）

`submit_sound_effect_generator`（L4679-4726）要点：

- **客户端先校验再发请求**：`duration != 0.5 且非 1–10 整数` → ValueError（L4695-4697）；`count` 出 1–10 → ValueError（L4698-4700）；`sound_pack and variants` 同时为真 → ValueError（L4701-4702）。
- 请求体是 **form 字段**（`data: dict[str, str]`，L4703-4713）：`prompt / duration / loop / sound_pack / variants / count / language / temperature / normalize`，布尔值序列化为字符串 `"true"/"false"`，duration/temperature 转字符串。无 JSON body——与图像线的 multipart/JSON 混用风格不同，复刻网关时按 form 处理。
- `--language` 默认 `"en"`，help 注明"Name language; prompt generation stays English"（L6498）——推断：它只影响**输出文件命名/元数据语言**，生成用 prompt 恒为英文（help 文本如此声明，服务端行为未证实）。
- `--temperature` 默认 0.3（L6499），是音效线唯一的采样参数。
- `--normalize` 默认 True（`set_defaults` L6500，store_true/false 对 L6501-6502），对应响度归一化开关（contract L257 也有 `normalize` 默认 true）。

### 2.3 音乐请求构造（multipart，参考图走文件上传）

`submit_music_generator`（L5405-5440）要点：

- form 字段仅三个：`prompt / audio_generate / demo`（L5416-5420）。**`audio_generate` 是 CLI 层硬编码 True**（L9368、L9400）——即请求协议里存在"不渲染音频、只出文字方向稿"的暗门（`audio_generate=false`），但 CLI submit 命令从不暴露它；结合 audio.md L49 的措辞"Draft a music direction **without rendering audio**"，说明 demo/pro 之外服务端还有一个纯文案层，Skill 层不可达。
- **`demo` 与 `--output-mode` 的关系**：`demo = args.output_mode == "demo"`（L9369/L9401），即 `--output-mode pro`（默认）→ `demo=false`、`audio_generate=true`；`--output-mode demo` → `demo=true`。contract L264 把两个 flag 记为 `request_field: "demo/audio_generate"` 印证这是两个字段。
- **参考图走 multipart 文件上传**：`files: list[tuple]`，字段名 `reference_images`，逐个本地读字节 + MIME 推断（L5421-5426），文件不存在 → FileNotFoundError（L5424-5425）。音效线没有任何上传通道。
- **没有时长、没有 count**：音乐请求面里没有任何长度/数量参数——demo 30s、pro 3min 这些长度由服务端按档位决定，CLI 无法指定（缺口见 §5）。

### 2.4 异步轮询链（音频域的具体形态）

- 提交响应兼容三种载体：`jobs_url` 优先 → `poll_job_until_done` 直接 GET 轮询该 url（L5488-5498、L3831-3840）；否则取 `job_id`/`api_job_id` → `poll_job` GET `/api/jobs/{id}`（L5443-5462）。
- 终态集合 `TERMINAL_JOB_STATUSES = {"success", "failure", "cancelled"}`（L65）；轮询循环里遇到白名单外的中间状态只打 WARN 不中止（L2688-2689）。
- 节奏参数全局共享：`DEFAULT_TIMEOUT=240`、`DEFAULT_MAX_WAIT=900`（15 分钟）、`DEFAULT_POLL_INTERVAL=3.0s`（L61-63）。即音频 job 的官方预期完成窗 ≤15 分钟（音乐 pro 渲染显然比音效慢，但用同一上限）。
- 轮询超时直接 TimeoutError，**不留部分产物**（L2692-2693）；`sound-poll`/`music-poll` 用 `--api-job-id` 可恢复任意历史 job 再下载（L6513-6515、L6737-6739、L9447-9457）。

### 2.5 交付下载与产物目录

- 下载 URL 双重过滤：必须 HTTPS（L1741-1742）+ key 叶子字段命中 workflow 白名单 `audio_path/audio_paths/url`（L1752-1771），`url` 叶子还要求父容器 ∈ {output, result}（L1765-1770）。音频响应里的其他 URL 一律视为不可信，被 `_sanitize_diagnostic_text` 打码成 `<omitted-non-final-url>`（L1789-1795）。
- job 成功但白名单内无可下载媒体 → 报契约错误，**拒绝写空 `final_outputs.json`**（L5582-5588，running-and-outputs.md L96-98 同口径）；下载后再次空列表也 RuntimeError（L5599-5602）。
- 产物目录：`output_root/<prompt-slug>/`（`_predict_saved_dir`，L1343-1344；音效 slug 取 prompt L8750，音乐 prompt 为空时取**第一张参考图文件名 stem**，再退到字面量 "music"，L9402）。
- `final_outputs.json` 清单固定结构：`status / job_id / outputs[{type, path, mime_type}]`（L5603-5616），audio 文件下载前强制 `audio/*` Content-Type 校验（running-and-outputs.md L74）。
- `sound-run`/`music-run` 都在执行前打印 `planned_output_dir=`（L8751、L9403）——先算目录再跑 job，复刻时保留这个可预期性细节。

### 2.6 argparse 注册全景（已核实行号）

| 命令 | 注册行 | 参数要点 |
|---|---|---|
| `sound-submit`（alias sfx-submit / sound-effect-submit） | L6504-6506 | add_sound_args 全量 |
| `sound-run`（alias sfx-run / sound-effect-run） | L6508-6511 | 同上 + runtime |
| `sound-poll`（alias sfx-poll / sound-effect-poll） | L6513-6515 | 仅 `--api-job-id` |
| `music-submit` | L6725-6729 | prompt 可空、`--output-mode` 默认 pro、`--reference-image` append |
| `music-run` | L6731-6735 | 同 submit + runtime |
| `music-poll` | L6737-6739 | 仅 `--api-job-id` |

`add_shared_runtime_args` 是空实现（L5693-5694）——音频命令没有 `--max-wait/--poll-interval` 之外的特殊运行时开关（这两个实际来自更外层的通用参数，音频域无专属差异）。公开命令列表里 `sound-run`/`music-run` 与所有 `-poll` 后缀命令被保留（L7036/L7044/L7057-7061），submit 变体从 `--help` 隐藏但代码可达。

### 2.7 模块链定位（SKILL.md）

- 音频在能力表中的定位："Create sound effects, coherent sound packs, music direction, and rendered tracks"，前置条件是"**gameplay timing and visual direction 已知**"（SKILL.md L88）。
- 模块链上音频是末端消费者："Audio-visual asset: finalize timing and action first → create matching effects or music"（SKILL.md L106）——即音效跟随动画/视频定稿的时序，音乐跟随视觉定稿的风格方向。
- 路由表：单音/包/变体 → `sound-run`；方向稿或成品轨 → `music-run`（capability-routing.md L36-37）。

## 3. 自部署复刻技术选型

> 约束：单用户、本地 GPU、无队列/配额/计费。官方底模只有音效线的 `elevenlabs_generator` 命名泄露，音乐线零信息——选型按"输出契约等价"而非"底模对齐"。

### 3.1 音效（对标 sound-run）

| 路线 | 代表方案 | 时长控制 | 质量/工程量 | 备注 |
|---|---|---|---|---|
| A. 文本→音效专用模型 | **AudioGen**（Meta audiocraft，open-medium/open-large 约 1.5B 参数） | 条件长度（最长约 10s 量级，open-large 官方支持到 10s） | 与原版契约最贴近；单卡 8-12GB 可推理（量化后更低，量级为估算） | prompt 风格（声源+材质+动作）正是 AudioGen 训练分布 |
| B. 扩散音频 | **Stable Audio Open**（Stability，约 1.2B 参数，主打 47s 内音效/采样） | 秒数可指定（同样本内） | 质感好、license 限非商用（自用可）；显存约 6-10GB（估算） | 对"材质尾音"类 prompt 表现好 |
| C. 音频 LM | AudioLDM 2 / AudioLDM（约 0.3-1.5B） | 固定窗长 | 生态成熟但质量已被 A/B 超越 | 备选 |
| D. 商业 API（ElevenLabs SFX） | 官方同名后端 | 0.5-22s | 免 GPU | 违背自部署原则，仅作质量上限参照 |

三个原版特有契约的自部署等价物：

- **pack（连贯多音）**：A/B 都不会原生输出"一包"。等价实现 = 一次调用 LLM 把总 prompt 分解成 N 个子 prompt（"hover/confirm/cancel/locked/error"），共享同一风格后缀 + 固定 seed 邻域批量生成 → 一致性来自共享上下文而非模型能力（推断性方案，官方实现未知）。
- **variants（同音变体）**：固定子 prompt + 微调 temperature/seed 扰动生成 count 份；原版的 `--temperature` 默认 0.3 恰好适配"扰动足够小、听感同源"（等价映射，非证实）。
- **loop**：生成 target 时长 +1s 余量 → 用首尾交叉淡化（crossfade）/ 环形 padding 裁出无缝 loop；或选 Stable Audio Open 的 loop 能力。原版未披露其 loop 的实现机制（缺口见 §5）。
- **normalize**：pyloudnorm 按 -1 dBTP / 目标 LUFS 归一，一行后处理，默认开。

### 3.2 音乐（对标 music-run demo/pro）

| 路线 | 代表方案 | 时长 | 参考图条件 | 备注 |
|---|---|---|---|---|
| A. **MusicGen**（Meta audiocraft，small/medium/large 0.3/1.5/3.3B） | 文本→音乐，支持描述性 prompt | 最高 30s（`generation_length` 可调；更长需滑动窗口续写，非原生） | 原生不支持图像；可用 melody 条件变体（MusicGen-melody）替代情绪锚 | demo 档 30s 恰好可原生对齐；pro 长轨需续写管线 |
| B. **ACE-Step**（ACE Studio × 阶跃星辰开源，3.5B 级，2025 发布） | 文本+歌词→成品歌，支持最长数分钟 | 无原生图像条件 | 有时间轴/标签控制，pro 档质感更接近"成品"；显存约 8-16GB（量级为社区数据，未实测需标注） |
| C. **Stable Audio 2.x / Open** | 最长约 3 分钟（2.x），Open 约 47s | Open 不支持；2.x 支持音频/图像风格迁移但权重未全开源（2.x 闭源 API） | demo 可用 Open，pro 档无法自部署（2.x 闭源），license 限制需注意 |
| D. YuE / DiffRhythm 等开源歌曲模型 | 带人声成曲 | 分钟级 | 无图像条件 | 游戏 BGM 常无人声，价值有限 |

**demo 档（30s 试听）**：MusicGen（medium/large）是契约最短路径——时长上限 30s 与官方 demo 恰好一致；prompt 直接映射配器/能量描述。**pro 档（约 3min 成品轨）**：优先 ACE-Step（原生分钟级 + 结构控制）；无显存预算时用 MusicGen 30s 段落 + 滑窗续写（用上一段尾部作延续上下文），但段落间衔接一致性明显弱于原生长轨模型（此为已知工程折衷，非官方机制）。

**参考图→音乐条件**（原版的 `--reference-image` 影响 mood/配器）：开源底模均无原生图像条件。等价管线 = 本地多模态 VLM（Qwen2.5-VL 类，7B 量级）把参考图转成情绪/配器文字（"像素海岛 → 轻快马林巴+海浪白噪"）→ 拼进音乐 prompt。这是自部署唯一现实路线，属推断性设计；原版后端如何消费参考图完全未披露。

## 4. 推荐方案（自部署最短路径）

复用第 05 篇的薄 API 骨架（`submit/run/poll` job 模型 + `final_outputs.json` 清单），新增两条本地管线：

1. **音效线**（≈sound-run）：
   `form 入参（0.5/1-10s 校验、pack/variants 互斥、count≤10）→ pack 模式先 LLM 分解子 prompt（variants 模式固定 prompt）→ AudioGen open-large（或 Stable Audio Open）批量推理（temperature 0.3 映射到采样参数）→ normalize（pyloudnorm）→ loop 需求时 crossfade 裁剪 → 按 slug 目录交付 audio_path[s] + final_outputs.json`
   契约照抄：三重时长校验、互斥组、planned_output_dir 预告、成功但零媒体视为错误。
2. **音乐线**（≈music-run）：
   `入参（prompt 可空 + 参考图列表）→ 有参考图先过本地 VLM 生成情绪/配器描述 → demo 档 MusicGen 一次生成 30s → pro 档 ACE-Step 分钟级渲染（或 MusicGen 滑窗续写）→ normalize → 交付`
   `audio_generate=false` 暗门不必复刻（Skill 层不可达），仅保留 `demo/pro` 两档语义。

硬件量级：音效+音乐双底模（AudioGen-large 3.3B 或 Stable Audio Open 1.2B + ACE-Step 3.5B）在单张 16-24GB 消费卡上可串行推理；24h 内单用户量级完全无需队列，进程内串行即可。

## 5. 关键工程细节 / 坑

1. **互斥是双向校验**：argparse 互斥组（L6494-6496）+ 请求构造再查一次（L4701-4702）。自部署 API 层也要做服务端校验——不要只靠客户端 argparse。
2. **时长 0.5 的特殊性**：允许值是 {0.5} ∪ 整数[1,10]，不是"任意半秒"。浮点比较用 `== 0.5` + `is_integer()`（L4695-4697 的写法），自部署校验器照抄可避免 0.50/1.0 类边界差异。
3. **demo 不可逆**：官方把"30s demo 无法延长成同一首 3min 曲"写进 Important guidance（audio.md L6）。复刻的 CLI 帮助文本里必须保留同等警告，否则用户会先用 demo 试听再指望"升级"到 pro。
4. **音乐请求面极小**：只有 prompt + demo 布尔 + 参考图。时长/风格/ BPM/循环点全部由服务端按档位决定——自部署若把 MusicGen 的 `generation_length` 等暴露成参数，属于**超出原版契约的扩展**，默认关闭以保持行为对齐；"clear loop point"这类需求（audio.md L61 示例）目前只能靠 prompt 引导。
5. **prompt 可空但请求字段恒在**：音乐 prompt 为空时提交空串（request_payload L9405 原样带上、submit 侧 data L5417 恒含该字段），slug 目录退到参考图名（L9402）。自部署要处理"纯参考图请求"，且目录命名 fallback 顺序：prompt → 参考图 stem → "music"。
6. **form vs multipart**：音效是纯 form 字段，音乐是 multipart（带文件上传）。复刻网关时两条路由的 Content-Type 不同，照抄以降低对接歧义。
7. **终产物三形态都要兼容**：`audio_path`（单文件）/`audio_paths`（数组）/`url`（裸链接）是白名单全集（L1591/L1605），自部署 runner 三种都识别后再下载，避免只认一种导致"成功但零媒体"误报。
8. **15 分钟 max_wait 对音乐可能偏紧**：官方统一 900s 上限（L62）。本地 GPU 渲染 3 分钟长轨可能超时，复刻时该值应做成配置项（CLI 已有 `--max-wait` 通道），而不是写死。
9. **音频交付纪律与图像一致**：只信 `final_outputs.json` 列出的文件、循环素材必须做端到端重复播放测试、pack 内逐个听是否真的不同（audio.md L68-74 的 validate 清单原样保留为自部署的验收脚本）。

## 6. 官方资料缺口清单（诚实缺口，本文档不编造）

- 音效后端与 ElevenLabs 的确切关系：`elevenlabs_generator` 只是 workflow 命名泄露（L4715），是否直连其 SFX API、模型版本、voice 克隆有无，全部未知。影响：复刻音效底模只能按"契约等价"选型，无法对齐内部实现。
- 音效变体/成包的服务端实现机制未知（一次多采样？LLM 分解？多次调用？）。影响：自部署 pack/variants 只能自研等价策略，一致性效果无官方基准可对照。
- 音乐底模完全未披露（workflow 名 `music_generator` 中性，无厂商泄露）。影响：demo/pro 的质量差距无法归因，选型只能在开源池里赌。
- demo 30s / pro 成品的精确时长：官方只说 demo 30s、"three-minute track"是示例措辞，pro 档时长上限与是否可配均未知。影响：pro 管线的目标长度要自定。
- `--loop` 的服务端保证程度未知（真无缝？首尾淡化？仅标注 loop 点？）。影响：自部署 loop 后处理（crossfade）的有效性需自行验收。
- `--temperature` 映射到什么采样器参数未知（底模是 API 黑盒）。影响：自部署只能做语义等价映射。
- 参考图如何影响音乐（VLM 转述？专用图像条件编码器？）未知。影响：参考图→mood 管线是纯自研，效果无锚点。
- `audio_generate=false` 的纯方向稿模式在服务端的实际输出形态未知（CLI 永远传 true）。影响：只确认不复刻该暗门即可。
- 音频输出格式（wav/mp3/ogg）、采样率、声道数：官方文档与 runner 均未声明，只按 `audio/*` MIME 校验。影响：交付格式需自定（建议 wav 母带 + 按需转码）。
- sound/music 的官方 credit 单价未知（本地资料零披露）。影响：仅计费调研一句带过，无复刻影响。

## 7. 参考链接

- 官方音频文档（本地镜像）：`references/audio.md`、`SKILL.md` L88/L106、`references/capability-routing.md` L36-37、`references/running-and-outputs.md` L64-76
- 参数契约：`web_parameter_contract.json` L249-265（sound_effect / music）
- CLI：原版 CLI 源码 L61-65（超时/终态常量）、L1591/L1605（字段白名单）、L2656-2694（jobs_url 轮询）、L4679-4775（音效 submit/run）、L5405-5513（音乐 submit/run）、L6490-6515 / L6725-6739（argparse）、L8749-8800 / L9367-9445（命令分支与请求构造）
- AudioGen / MusicGen（audiocraft）: https://github.com/facebookresearch/audiocraft
- Stable Audio Open: https://huggingface.co/stabilityai/stable-audio-open-1.0 （license 非商用，自用场景需自行核对）
- ACE-Step: https://github.com/ace-step/ACE-Step
- AudioLDM 2: https://github.com/haoheliu/AudioLDM2
- 响度归一化：pyloudnorm https://github.com/pyloudnorm/pyloudnorm
