# 08 游戏设计 Agent（game-design）：策划 Agent、Markdown 设计文档树、planning model 与异步轮询技术文档

> 复刻对象：原版的 **Game Designer 持久化策划 Agent**——游戏概念/机制/系统/平衡/进度/内容规划/调研/评审，以 project/thread 为上下文容器逐轮对话，产出**持久化 Markdown 设计文档树**（`design_docs/`）与 `game_design_outputs.json` 清单；planning model 按轮增量计费、可断点恢复（`game-design-poll`）。音频（sound/music）与 UI（ui-gen）不在本篇，见文档 06/07。
> 一手资料：`references/game-design.md`（全文 56 行：能力定位 L3、输出 L15、计费 L37-48、工作规则 L50-57）、`web_parameter_contract.json`（policy L3；**全文无 game_design 段**，features 枚举覆盖 L5-321）、原版 CLI 源码（agent API 层 L2244-2653、argparse L5776-5794、命令编排 L7184-7276，均为实际核实行号）、`SKILL.md` L83/L124、`references/capability-routing.md` L7、原版 CLI 文档 L109。

## 1. 功能描述

### 1.1 能力面与产品定位

Game Designer 是原版 87 个子命令中唯一的**文本型 Agent 能力**，与图片/音频/视频 workflow job 有本质区别：**多轮、有状态、可自行调用工具**。官方路由（capability-routing.md L7）：概念/循环/机制/系统/经济/平衡/进度/内容规划/调研/评审/设计文档类意图一律走 `game-design-run`，且 game-design.md L3 明确"**不要用图像生成命令回答策划问题**"——策划与素材是两条独立付费/执行通道（SKILL.md L124）。

官方建议的输入（game-design.md L52）：给 Agent 产品目标、受众、平台、约束、参考、要做的决策；**不要预设文档大纲**（除非结构本身是需求）——文档结构由 Agent 自主规划，复刻端是否固定模板见 §4。

### 1.2 交互模型：project / thread / message / ask_user

- **project**：顶层容器（`POST /api/projects`，标题 + `projectTitleSource: "manual"`）；免费额度在所有 project 间共享。
- **thread**：project 内的 `kind: "agent"` 会话线程，标题固定 `"Game Designer"`。工作规则（game-design.md L55）：**方向无关开新 thread，迭代打磨复用同一 thread**——thread 即设计上下文边界。
- **message**：`game-design-run` 每次提交一条 `kind: "user_message"`，prompt 上限 **20,000 字符**（原版 CLI 源码 L2341-2342）。省略 ID 时 CLI 自动建 project + thread 并打印 `project_id/thread_id/api_job_id`（编排 L7190-7218）；`--thread-id` 必须搭配 `--project-id`（L7187-7188）。
- **ask_user**：Agent 可以反问。公开事件携带 `prompt + options[]{id,label,description}`（L2418-2434），CLI **不代答**，把问题原样透传（game-design.md L53：把 ask_user 当真实设计问题呈现给用户，用户回答后带同一对 ID 继续）。清单落盘时取**最后一个** ask_user 写进 manifest（L2590-2593、L2646-2650）。

### 1.3 持久化产物：design_docs/ 树 + game_design_outputs.json

任务成功后，CLI 拉取**项目级全量文档树**并逐文件下载（L2546-2573）：

```
<output-dir>/<slug(api_job_id)>/
├── design_docs/            # 服务端 design-docs 树的完整本地镜像（子目录结构原样保留）
│   └── .../*.md            # 只接受 .md；路径安全校验见 §2.5
└── game_design_outputs.json
```

- `game_design_outputs.json`（`format_version: 1`，L2635-2652）：`project_id/thread_id/api_job_id/status/stage/assistant_message`（取最后一个含 summary/text/prompt 的事件，L2582-2589）、`model_token_billing`（净化子集，L2594-2634）、`documents[]{path,revision,local_path}`、可选 `ask_user`。
- 文档带服务端 `revision` 号（L2577）——服务端按文件维护版本；manifest **不保存**原始 Agent 事件、provider 响应、内部 URL、prompt、调试元数据（game-design.md L48）。单文档上限 **300,000 字节**（L208，超限报错 L2569-2570）。

与素材命令不同（素材只交付允许清单内的媒体文件），game-design 交付物是**全量文档树**：每轮对话后树可能整体演化（含删除/改名），CLI 每轮重拉全量而非增量 diff。

### 1.4 异步轮询与断点恢复

- `game-design-run` = 建 project/thread（可选）→ 提交 message → **同步轮询**到终态 → 拉取落盘。与地图命令的 -submit/-run/-poll 三件套不同，它**没有 -submit 变体**（单次 run 含多轮 planning call，耗时长）。
- 轮询实现（L2438-2520）：GET 事件流接口，参数 `jobId + after_seq + limit=500`，用 `nextAfterSeq` 游标增量拉取（L2505）；默认 `max_wait=900s`、`poll_interval=3.0s`（L62-63，仅 `set_defaults` 内部默认 L5658-5667，**未暴露为 CLI flag**）。超时抛 `TimeoutError` 并把 `game-design-poll` 恢复命令写进报错文案（L2470-2475、L2514-2519）。
- 任务状态机：active = `queued/pending/running`，terminal = `success/failure/cancelled`（L64-65）；未知状态直接抛兼容性错误（L2512-2513）。退出码：**仅 status==success 返回 0**（L7244-7246、L7274-7276）。
- 恢复纪律与素材线一致（running-and-outputs.md L94）：本地轮询超时**绝不重新提交**，用 `game-design-poll` 恢复原 job——重复提交会重复计费。`game-design-poll` 要求三个 ID 全部显式传入（L5791-5793），成功后走同一落盘逻辑（L7260-7271）。

### 1.5 官方 token 计费机制（调研记录；复刻按自部署不做）

game-design.md L37-48 的完整口径：

- 每账号每月（UTC）**100 万免费 tokens**，结算为全部 project 共享的 60 credits（L39）；**无预扣、无单次封顶**（L40）。
- 免费额度用尽后按 planning model 每轮**增量扣费**：uncached 输入 150 cr/M、cached 输入 15 cr/M、输出（含 reasoning）900 cr/M，单 job 累计向上取整（L41）。
- 接受消息前与**每轮 planning call 前**双重校验：先核销月度额度，再查余额能否覆盖本轮预估，不覆盖则不调用（L42）；CLI 轮询时逐轮打印计费（L2486-2494），不足时在 `finish` 事件携带 `rechargeRequired/rechargeUrl`（L2391-2395）并停止。
- **服务端失败全额退还本 run 扣费**并恢复月度额度（L44；`serverFailureRefunded` L2624-2625）；用户取消或余额不足停止时，**已完成的 model call 照常扣费**，未开始的不扣（L45）。
- Agent 自动调用的素材/**web-research** 等付费工具**另行计费**（L46）。

> 复刻定位注记：以上仅作调研记录。自部署单用户无 credit/钱包/月度额度，对应物是**资源护栏**（每轮 max tokens、每 run 最大轮数/墙钟时间），见 §4。

## 2. 原版实现线索（file:line 证据）

### 2.1 web_parameter_contract.json 的"缺位"本身就是证据

参数契约文件收录了 26 个 web 产品功能段（L5-321：nano_banana、image_edit、……、side_scrolling_hd），**唯独没有 game_design**。其 policy（L3）写明："Public Skill product parameters mirror the **selectable web product parameters**……project identity、recovery controls、debug/provider internals 均排除。"即：图像类能力有"web 面板上可选项 → CLI flag"的映射，而 Game Designer 是纯 Agent 会话——**它的运行参数（planning 模型、质量档、预算上限）不是 web 产品可选参数**，而是 CLI 内写死的 settings（见 2.2）。这佐证了官方刻意不暴露 planning model 身份（与 game-design.md 全文从不提模型名一致）。

### 2.2 端点与请求构造（原版 CLI 源码）

| 动作 | 端点 | 行号 | 关键 payload / 响应 |
|---|---|---|---|
| 统一请求封装（含 402 译码） | — | L2244-2278 | 402 + `error=="game_designer_credits_exhausted"` → `GameDesignerCreditsExhaustedError`，透出 `requiredCredits/availableCredits/rechargeUrl`（L2267-2276） |
| 建 project | `POST /api/projects` | L2281-2301 | `{"title", "projectTitleSource":"manual"}`；响应必须有 `id` |
| 建 agent thread | `POST /api/projects/{pid}/threads` | L2304-2324 | `{"title":"Game Designer","kind":"agent"}` |
| 提交消息 | `POST /api/projects/{pid}/threads/{tid}/agent/messages` | L2327-2371 | 见下 |
| 事件轮询 | `GET /api/projects/{pid}/threads/{tid}/agent/events?jobId&after_seq&limit=500` | L2456-2467 | 游标 `nextAfterSeq`（L2505） |
| 文档树 | `GET /api/projects/{pid}/design-docs/tree` | L2546-2553 | 响应 `files[]{path}` |
| 单文档 | `GET /api/projects/{pid}/design-docs/file?path=` | L2559-2567 | 响应 `content`+`revision` |

提交消息体（L2354-2366）是**全文件最有信息量的一段**：

```json
{
  "clientId": "skill_game_design_<ms>_<sha256(prompt)[:10]>",
  "kind": "user_message",
  "text": "<prompt ≤ 20000 字>",
  "settings": {
    "agentProfile": "game_designer",
    "qualityMode": "standard",
    "budgetCapCredits": 200,
    "autoExecuteTools": true,
    "confirmPaidTools": false,
    "responseLocale": "zh-CN | en"
  }
}
```

可推知的服务端 Agent 架构（推断处已标注）：`agentProfile` 说明服务端有**多 profile 的 agent 运行时**，game_designer 只是其一；`autoExecuteTools=true + confirmPaidTools=false` 说明 Agent 有**工具面且付费工具免确认自动执行**（对应 game-design.md L46 的 web-research 另行计费）；`clientId` = 毫秒时间戳 + prompt 哈希（L2343），形态像**幂等去重键**（推断）；`budgetCapCredits: 200` 与官方"无单次封顶"（game-design.md L40）的精确关系**未披露**（见 §5 坑 6）。

### 2.3 CLI 参数面（argparse 实证）

`game-design-run`（L5776-5785）：`--prompt`（必填）、`--project-title`（默认 `"Game Design"`）、`--project-id`、`--thread-id`、`--locale`（`zh-CN|en`，**默认 zh-CN**）、`--output-dir`。`game-design-poll`（L5787-5794）：三个 ID 必填 + `--output-dir`。没有 model/quality 参数——**Agent 运行参数完全固化**。输出目录 = `<output-root>/<safe_slug(api_job_id)>`（`_predict_saved_dir` L1343-1344）。

### 2.4 事件协议：服务端事件全集的一个"公开投影"

CLI 只把 4 种事件翻译为公开形态（`_public_game_design_event` L2374-2435），其余一律丢弃（返回 None）：

| 公开事件 | 字段 | 行号 | 含义 |
|---|---|---|---|
| `assistant_message` | `text` | L2381-2383 | Agent 正文回复 |
| `finish` | `summary`、`finishStatus`、可选 `recharge_required/recharge_url` | L2384-2396 | 终态与停止原因 |
| `model_call` | 12 个计费/用量字段（estimated/calculated/charged credits、monthly_free、refunded、remaining、input/cached_input/output tokens、`credits_exhausted`） | L2397-2417 | **每轮 planning call 一条**，逐轮扣费的观测点 |
| `ask_user` | `prompt + options[]{id,label,description}` | L2418-2434 | 结构化反问 |

manifest 级 billing 子集更细：另含 `uncached_input_tokens`、`reasoning_output_tokens`、`total_tokens`、`pricing_version`、`monthly_free_*`、`server_failure_refunded` 等（L2596-2634）。**推理 token 计入输出计价**（game-design.md L41"output including reasoning"）说明 planning model 是 reasoning 模型（或至少输出链路含 thinking 段）——本地选型的重要参照。投影之外服务端必有工具调用、文档写入等内部事件，公开面不可见（缺口清单）。

### 2.5 文档树落盘与路径安全校验

- `_safe_game_design_document_path`（L2523-2528）：反斜杠归一为 `/` → `PurePosixPath` → 拒绝**绝对路径、含 `..`、后缀非 `.md`**，否则抛 "unsafe document path"。这是复刻端文档树写入器必须照抄的安全边界。
- 落盘（L2571-2573）：`design_docs/<path>` 逐级 mkdir 后写 UTF-8 文本；`documents[]` 记录 `revision` 与本地路径（L2574-2580）。树接口一次拿全部 `files[]`，文件接口逐个拉内容——**全量镜像**语义。
- 输出卫生与素材线同规（running-and-outputs.md L69、`_write_meta` L1359-1360 有意不持久化任何请求/响应/凭据/调试元数据）：manifest 只留 provider 中立的 token 数与结算（game-design.md L48）。

### 2.6 模块链位置

SKILL.md L83 模块表把 Game Designer 定位为"把游戏概念调研成持久化 Markdown 设计文档"；L124 强调其 token 计费与被其调用的付费素材工具分离；capability-routing.md L7 将其列为意图路由第一行——即原版产品心智里**先策划后素材**，设计文档树是后续所有素材生成的上游语境。

## 3. 自部署候选方案对比

### 3.1 planning model（本地 LLM 选型）

| 路线 | 候选 | 优点 | 缺点 |
|---|---|---|---|
| A. 本地推理模型（推荐） | Qwen3-32B/30B-A3B、DeepSeek-R1-Distill-Qwen-32B/70B、GLM-4 系列（llama.cpp/vLLM/Ollama 服务化） | 官方"输出含 reasoning 计 900cr/M"证明其 planning model 是 reasoning 型，本地推理模型对齐该行为；中文策划文本质量好 | 32B 级量化后仍需 24GB+ 显存；thinking 过长拖轮次耗时 |
| B. 本地非推理大模型 + 长 system prompt | Qwen2.5-72B、Llama-3.1-70B | 简单、快 | 结构化长文档的规划/自查能力明显弱于推理模型 |
| C. 混合：小模型起稿 + 大模型评审/平衡 | 两级调用 | 省算力 | 工程复杂度高，单用户收益小 |

**判断**：A。cached 输入 15cr/M ≈ uncached 的 1/10，说明 planning 链路存在**稳定前缀**（系统提示 + 上下文骨架）；本地等价物 = vLLM prefix caching / llama.cpp prompt cache，把"角色系统提示 + 文档树索引 + 滚动摘要"放前缀头部。`qualityMode` 写死 standard、语义未披露；本地做两档（每轮 max_tokens 与是否展开 thinking）即可。

### 3.2 Agent 运行时与工具面

| 路线 | 思路 | 评价 |
|---|---|---|
| A. 自研 function-calling 循环 | 本地模型原生 tool-call（Qwen3/GLM 支持），工具 `write_doc/patch_doc/read_tree/web_search/fetch_url/ask_user` + 循环/墙钟护栏 | 最贴近官方形态（agentProfile + autoExecuteTools 证明其就是带工具的循环）；可控性最好 |
| B. 复用框架（LangGraph / Letta / Open Hands 内核） | 拿现成 agent 状态机 + 持久化 memory | 快，但文档树契约、事件投影、恢复语义都要逆向适配，长期维护成本高 |
| C. 单轮长生成（无循环） | 一次 prompt 生成整套文档 | 实现最简；无法 ask_user、无法 web-research、跨轮一致性差，等于放弃原版该模块的核心价值 |

**判断**：A。工具白名单收窄为 6 个左右：文档树三件套（读/写/补丁）、web_search + fetch_url（§3.3）、ask_user（透传 CLI）。**不给**素材生成工具（官方虽可将付费素材工具接进 Agent，复刻端策划与素材保持两条独立 CLI 通道，降低耦合）。

### 3.3 web-research 集成

官方只透露 Agent 能自动执行 web-research 且单独计费（game-design.md L46、settings 两开关），实现完全未披露。本地可选：**SearXNG**（自托管元搜索，JSON API）+ **trafilatura** 正文抽取；或 Tavily/Brave API（需 key，非纯本地）。市场调研类 prompt → search → 抽正文 → 截断注入下一轮上下文，来源 URL 随引用写进设计文档。**判断**：SearXNG + trafilatura，零外部依赖、可断网降级（断网时 Agent 明确声明"未做在线调研"而非幻觉）。

### 3.4 文档树管理与异步任务

| 项 | 候选 | 判断 |
|---|---|---|
| 文档树存储 | A. 纯文件系统 + `index.json` 索引；B. SQLite；C. Git 仓库 | A+C：文件树保持与官方 `design-docs/tree` 同构的可浏览形态；每轮提交后 `git commit` 天然获得官方 `revision` 字段的等价物与回滚能力 |
| 文档更新方式 | A. Agent 每轮整文件重写；B. 结构化 patch（diff/搜索替换）；C. frontmatter + 分节锁 | B 为主：`patch_doc(path, old, new)` 强制 Agent 做局部修改，配合 ≤300KB 文件上限（对齐官方 L208）防雪崩；`write_doc` 仅允许建新文件 |
| 上下文组装 | 全量 thread 历史 / 文档树全文 + 摘要 | 滚动摘要 + **文档树索引（路径+标题+revision）**+ 最近 N 轮原文；对齐官方 cached 前缀形态（推断） |
| 异步任务 | A. 进程内线程 + SQLite job 表；B. Celery/Redis；C. 同步阻塞 | A：单用户无需消息队列，但**必须保留 job 表**以支持 `game-design-poll` 等价的断点恢复（CLI 恢复语义是产品契约，见 §1.4）；状态机照抄 queued/pending/running → success/failure/cancelled |
| 事件流 | SSE / 轮询 JSON | 轮询 JSON（`after_seq` 游标）照抄官方形态，CLI 与未来 web 前端共用 |

## 4. 推荐方案（最便捷且主流的复刻路径）

底座：**Qwen3-32B（AWQ/8bit，vLLM 起服务，开 prefix caching）+ 自研 function-calling 循环 + 文件系统/Git 文档树 + SearXNG**。单条管线：

1. **会话层**：`game-design-run` 等价 CLI——省略 ID 时建 project（目录）+ thread（SQLite 行，`kind=agent`）；prompt ≤20,000 字符校验照抄；`--locale` 映射 system 提示响应语言；`--thread-id` 依赖校验照抄（L7187-7188）。
2. **Agent 循环**：system 提示固定角色，工作规则对齐官方（区分"已确认决策/提案/待定问题"，game-design.md L54 口径写进提示）；护栏 = 每 run 最大轮数（默认 25）+ 墙钟超时（默认 900s，对齐 L62）+ 单文件 ≤300KB（L208）+ 单轮 max_tokens。`ask_user` 触发时 job 转入挂起终态，答案作为下一条 message 提交同一 thread。
3. **文档树管理器**：每轮变更 → 路径安全校验（照抄 L2523-2528）→ 应用 patch → revision+1 → Git commit（message=本轮摘要）→ 更新 `index.json`。维护根文档 `index.md`（树目录 + 每篇一句话摘要）作为每轮上下文的稳定前缀。
4. **事件与轮询**：planning call/工具调用/文档写入全部追加事件表（`seq` 自增）；poll 等价命令用 `after_seq` 游标增量拉取并落盘全量 `design_docs/` + manifest（字段名对齐官方，billing 段换成 `token_usage` 计数，供容量规划）。
5. **失败语义**：官方"失败退款"的对应物是**失败 run 不污染文档树**（Git 回滚本轮 commit）；取消/护栏触发时已完成轮次的变更保留（对应"已完成 model call 保持计费"的反面——保留工作成果）。

CLI 面保持 `game-design-run / game-design-poll` 两命令 + 三 ID 恢复模型不变，使本模块与 01-07 篇的异步 job 形态在自部署版里统一。

## 5. 关键工程细节 / 坑

1. **job ID 是落盘与恢复的唯一锚点**：官方把 `project_id/thread_id/api_job_id` 三元组作为恢复凭据（poll 三参数必填 L5791-5793），且任务目录名就是 api_job_id 的 slug（L1343-1344）。复刻端必须把三个 ID 打进 stdout 固定格式（`[INFO] submitted api_job_id=…` 照抄），否则自动化脚本无法恢复。
2. **prompt 上限 20,000 字符是硬校验**（L2341-2342）：本地虽无此限制，但保留同值可防"把整个策划案塞进一条 message"的反模式——上下文应该进文档树而不是 message。
3. **全量镜像文档树，不做增量下载**：官方每轮都重新 `tree + file` 拉全量（L2546-2573）。复刻端若做增量同步，必须处理"Agent 删除/改名文档"的情况——全量树对账是唯一可靠方案。
4. **路径安全校验不可省**：`.md` 后缀 + 拒绝 `..`/绝对路径（L2523-2528）是防 LLM 生成路径越权写入的唯一闸门；本地单用户也不能豁免（Agent 输出不可信等级同外部输入）。
5. **事件投影要区分"内部"与"公开"**：官方 CLI 丢弃了全部未列名事件种类（L2374-2435 只认 4 种）。复刻端建议内部事件全记（便于调试 Agent 循环），但 CLI 公开输出与 manifest 只留 assistant_message/finish/model_call/ask_user 同构字段，保持契约干净。
6. **budgetCapCredits=200 与"无封顶"的矛盾不要自行脑补**：官方文档说无单次最大扣费（game-design.md L40），CLI 却随请求提交 200 的预算上限（L2361）。真实语义（服务端硬顶？软提示？遗留字段？）未披露。复刻端按自己的护栏语义实现，不要照抄数字。
7. **退出码语义要精确**：仅 `status==success` 返回 0，failure/cancelled 一律 1（L7244-7246）——余额不足触发的"礼貌停止"在官方语境里也算非成功。复刻端护栏触发同理：job 标 `cancelled` + finish 事件说明原因，而不是静默成功。
8. **默认 locale 是 zh-CN**（L5784）：官方产品默认中文响应。复刻端 system 提示需按 locale 切换输出语言，且设计文档正文语言应跟随 thread 首轮 locale 固定，避免跨轮中英混杂。
9. **ask_user 是一等公民**：它不是错误，是 Agent 的正常停机方式之一。CLI 必须把 ask_user 写进 manifest 并以非零但可区分的方式呈现（官方塞进 manifest 的 `ask_user` 字段 L2646-2650，job 本身可能仍是 success）。复刻端建议 manifest 增加显式 `awaiting_user: true`。
10. **与素材链路的接缝靠约定不靠管道**：官方让 Agent 自动执行付费素材工具（autoExecuteTools），但 Skill 文档层又要求策划/素材分命令走（capability-routing.md L7 与 SKILL.md L124 的分工）。复刻端保持策划 Agent 只产文档，素材命令由用户/上层脚本读 `design_docs/` 后显式调用——接缝是文件树，不是进程内调用。

## 6. 参考链接

- 官方 Game Designer 文档（本地镜像）：`references/game-design.md`；模块链：`SKILL.md` L83/L124、`references/capability-routing.md` L7、`references/running-and-outputs.md` L80-98（轮询与恢复通用纪律）、原版 CLI 文档 L109
- 参数契约：`web_parameter_contract.json`（policy L3；无 game_design 段）
- CLI：原版 CLI 源码 L61-65（超时/轮询/状态机常量）、L208（300KB 文档上限）、L2244-2653（agent API 层）、L5776-5794（argparse）、L7184-7276（命令编排）
- 本地推理服务：vLLM（prefix caching）https://docs.vllm.ai/ ；llama.cpp prompt cache https://github.com/ggml-org/llama.cpp ；Ollama https://ollama.com/
- 本地模型候选：Qwen3 https://github.com/QwenLM/Qwen3 ；DeepSeek-R1-Distill https://huggingface.co/collections/deepseek-ai ；GLM https://github.com/zai-org/GLM-4.5
- Agent 运行时参考（工具循环形态）：OpenAI function calling 规范 https://platform.openai.com/docs/guides/function-calling ；Letta（持久 memory agent）https://github.com/letta-ai/letta
- web-research 自建：SearXNG https://docs.searxng.org/ ；trafilatura 正文抽取 https://github.com/adbar/trafilatura
- 文档树版本化：Git plumbing（每轮 commit = revision）https://git-scm.com/book/zh/v2 ；Markdown frontmatter（python-frontmatter）https://github.com/eyeseast/python-frontmatter
