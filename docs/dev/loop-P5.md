# Loop P5：精灵动画（调研文档 02）

> 范围依据：ROADMAP P5 =「精灵动画（调研文档 02）」。受 ADR-001（禁本地模型——AnimateDiff/SVD/CogVideoX/Wan 视频扩散类一律排除）与 ADR-005（无图生视频/图生图通道，pollinations 仅文生图）约束，对调研文档 §3/§4 技术选型做路线收敛（**推断决策**，代码注释与 STATE.md 标注）：
> - **可行（本 Loop 范围）**：帧序列**确定性打包**——spritesheet（含引擎切图元数据）+ 动画 WebP/GIF 编码 + 循环检报告 + 像素纪律后处理（调色板统一/量化/alpha 二值）。纯 Pillow（动画编码能力已预检通过，见下），零 AI、零新依赖。
> - **排除/后移（不在 P5）**：视频扩散生成路线（ADR-001 排除）；keyframes 线与 video 兜底线（依赖图生图/图生视频条件通道，待 openai_compat 端点就绪，STATE.md 待定决策）；骨骼动画（Spine/DragonBones）。
> - 拆两个串行子循环：
>   - **L1（已完成并验收，2026-09-12，commit 53e5f61，295 绿——本节留档）**：帧序列打包线——N 张等尺寸静帧 → spritesheet + 动图 + loop_report。
>   - **L2（本轮委派，2026-09-12 下发）**：animate 生成线——provider 逐帧生成 + 首帧调色板统一（坑 2 治标）+ 复用 L1 打包。

## L1：帧序列打包线（本轮委派，2026-09-12 下发）

### 背景（先读）

- 调研文档：`docs/features/02-sprite-sheet-animation.md` §1（四线与路由顺序）、§2.1/§2.3（帧数偶数约束/帧数域/alpha 双模式/循环元数据）、§2.5（动作类型循环特性表）、§5（坑 1/2/6/7/8）
- 输出硬契约（§5 坑 7）：spritesheet **必须带帧宽高/列数元数据**（Aseprite/Unity 切图约定），否则引擎侧不可用
- 官方行为锚点：最终 WebP **永远循环播放**（§2.3，loop 元数据恒开）；帧数必须为偶数；经典线帧域 4-16、像素线 2-16；像素动画画布 ≤256 硬校验（坑 6）
- P4-L2 已交付最新**确定性任务模板**：`server/app/api/ui_gen.py` 的 extract 端点（multipart `files[]` + `payload` 模式、数量门禁在读文件内容前、零 provider 调用 runner `submit_extract`）——本线全程照抄该模式
- P3-L2 已交付**指标如实交付先例**：`jobs/models.py` 的 `SeamReport`（检缝不过不判 failed）——本线 `loop_report` 照抄该哲学与 <6 阈值口径（P2/P3 同阈值，一致性好记）
- **Pillow 动画编码已预检（2026-09-12，看门狗实测 pillow 12.3.0）**：`save_all` 多帧 WebP/GIF 编码 + 回读 `n_frames` 均通过——零新依赖实证，直接用

### 交付物 1：核心管线 `server/app/core/anim_pipeline.py`（纯函数，provider 无关）

- **帧完整性校验**（入口，ValueError 家族→422）：
  - 帧数 2-16 且为偶数（官方经典 4-16/像素 2-16 的**并集**，推断标注）
  - 所有帧同尺寸，禁静默缩放（tileset 先例）
  - `pixel=true` 时画布任一轴 >256 拒绝（坑 6）；`pixel=false` 不设画布上限（soft/HD 打包线；推断决策：256 硬约束官方语义属像素线）
- **像素纪律后处理**（`pixel=true` 时逐帧，按序）：
  - alpha 处理按 `alpha_mode`：None → `pixel=true` 解析为 sharp、否则 soft（官方"像素默认 sharp"的路由复刻，推断标注）；sharp = alpha≥128 二值化（与 P2/P4 前景判据同口径）；soft = 保留原 alpha
  - **调色板统一**：以第一帧调色板为准对其余帧做 `color_count`（2-64，缺省 32，推断）色量化（坑 2 治标方案逐字落地：「逐帧调色板统一（以首帧调色板为准量化）」）；`color_count` 仅在 `pixel=true` 时合法携带（交叉校验，模式照 `ImageEditParams._check_operation_fields`）
- **spritesheet 装箱**（确定性）：`cols=ceil(√n)`、`rows=ceil(n/cols)`；帧按提交序逐格排布，末行空位全透明填充；输出 `cols*fw × rows*fh` RGBA PNG；同输入两次运行字节级一致（测试锁定）
- **动图编码**：animated WebP（`loop=0` 恒循环，官方 §2.3）+ animated GIF（兼容交付；**GIF 仅 1-bit 透明**，诚实标注，WebP 为主交付）；帧时长统一 `duration_ms`（缺省 125ms，推断：官方 16 帧=2 秒）
- **loop_report**：首帧 vs 末帧逐像素最大通道跳变（思路参考 P2 `_max_neighbor_step`，只读参考、不改动既有模块）+ `passed`（<6，与 P2/P3 检缝同口径）。idle/walk/run 官方要求末帧≈首帧（§2.5），指标如实进产物**不判 failed**——与 seam_report 同哲学

### 交付物 2：契约与执行器

- `AnimationPackParams`（pydantic，extra=forbid，`kind="anim_pack"`——**自定标签**：打包线无官方 canonical 对应，官方 animate 指生成线，标注）：
  - `animation_type: idle|walk|run|jump|attack|hit|defeated|other`（默认 other；回显进报告——官方动作模板表的循环校验策略本线只交付 loop_report 指标，不做策略分支）
  - `output_format: webp|gif|spritesheet`（官方 CLI 单选语义保留，默认 webp；要三种各提一个 job）
  - `pixel: bool=false`；`alpha_mode: soft|sharp` 可空（None=按 pixel 路由）；`color_count: 2-64` 可空；`duration_ms: 20-1000` 默认 125
- 产物：webp/gif 按格式落 artifacts；spritesheet → `sheet.png` **+ `spritesheet.json`**（帧网格元数据：frame_size/columns/rows/frame_durations/loop/animation_type——坑 7 引擎切图契约；Content-Type json 映射 P4 已有先例）
- `FinalOutputs` 增可选 `anim_report`（`AnimPackReport`：frame_count/frame_size/columns/rows/duration_ms/animation_type/pixel/alpha_mode/loop_report；**None 序列化省略**——照 seam_report/ui_components 模式，旧线 final_outputs.json 键集不变，测试锁定向后兼容）
- `AnimationPackJobRunner`：**零 provider 调用**（确定性管线，照 `UiGenJobRunner.submit_extract`：线程池模式、无重试语义——无外呼可重试、管线非法输入归 ImageEditError 家族→failed 不悬空）

### 交付物 3：API 路由

- `server/app/api/animations.py`：`POST /api/v1/animations`（multipart：`files[]` 静帧 + `payload` JSON 串，照 ui_gen/extract 模式；**帧数门禁在读文件内容前**——0/1 张、>16 张、奇数张 422）+ `GET /api/v1/animations/{job_id}`
- 上传防线照抄 tilesets `_read_texture`（Content-Type/魔数白名单、20MB 413、payload extra=forbid 422）；动画格式输入（多帧 webp/gif）**拒绝**（422——静帧线收动画输入语义不清，首帧截取是静默丢信息；标注决策）

### 硬性约束

- 禁本地模型推理、**禁新增依赖**（AnimateDiff/视频扩散类一律不引入——调研文档 §3 表格仅是选型分析，铁律见 ADR-001）
- 禁改 `providers/`；禁改既有 core 模块（processors/map_layout/tileset_synth/texture_pipeline/ui_pipeline/imaging 只读复用）；禁改 `generations/artifacts/ui` 现有行为
- 测试夹具一律 Pillow 代码生成；**永不真实调用外部服务**（本线零 provider 调用，测试断言 provider.calls==0 实证——P4-L2 有现成断言模式）；禁止用 Read 工具读任何二进制文件
- **禁读图死坑（2026-09-12 实证）**：用 Read 工具读 PNG 会触发上游网关 `API Error: 400 No target in combo has confirmed vision support`，整个 `-p` 会话立即被杀。一切图像断言/取证必须走 `python3 -c` + Pillow 脚本打印数值（尺寸/n_frames/loop/网格位置/首尾帧 step），证据以文本数字落 STATE.md，全程不看图
- 中文注释、英文标识符；公开函数 type hints；原子写 job/产物；日志 logging 禁 print

### 验收标准（全过才算完成，逐条给证据）

1. 全量 pytest 绿（新增：帧数域逐值——0/1/17 张与奇数张 422、2/16 边界通过；异尺寸帧 422；`pixel=true` 画布 257 拒绝/256 通过、`pixel=false` 不限；`color_count` 无 `pixel` 携带 422；alpha 路由断言——None+pixel→sharp 二值化、None 无 pixel→soft 保留半透明、显式值覆盖路由；调色板统一断言——统一后各帧颜色集 ⊆ 首帧调色板；spritesheet 3 帧→2×2 布局逐格坐标断言 + 末行空位透明格 + 字节级确定性；webp/gif 回读 n_frames=帧数、loop=0、duration 回读一致；loop_report 数值与独立计算一致；路由：payload extra 字段 422、坏文件魔数 422、20MB 413、未知 job 404、**provider.calls==0**；final_outputs.json 新旧契约键集断言）
2. `uv run ruff check .` 通过
3. 铁律 grep 无新增命中（本线零新依赖，pyproject/uv.lock 不动）
4. 8600 真实冒烟（**确定性通道，无 provider**）：Pillow 生成 8 帧测试序列（如小球位移渐变）→ curl multipart POST → 轮询 succeeded → artifacts 端点取回产物 → Pillow 脚本断言 n_frames=8/sheet 网格逐格/loop_report 数值；证据（数值+尺寸）落 STATE.md
5. ROADMAP 增加 P5 章节（L1 勾选、L2 与后移项标注）；STATE.md 迭代日志（帧域并集推断、duration 缺省推断、alpha 路由决策、GIF 透明局限、kind 自定标注）
6. `git diff --stat` 自查禁改文件零触碰；commit：`feat(P5-L1): 帧序列打包线（spritesheet/动图编码/循环检报告）`

## L2：animate 生成线（本轮委派，2026-09-12 下发）

### 背景（先读）

- 调研文档：`docs/features/02-sprite-sheet-animation.md` §2.1（animate-run 参数面：偶数帧/动作枚举/画布约束/像素引擎路由）、§2.5（动作类型循环特性表——服务端动作模板的依据）、§2.6（高质量动画官方前置工作流——prompt 纪律）、§5（坑 1 循环闭合/坑 2 帧间漂移/坑 3 padding/坑 5 帧数预算）
- 通道现实（诚实路线，ADR-001/005 约束下收敛）：无图生视频/图生图通道 → **provider 逐帧文生图**（prompt 帧 i + seed 序列）+ 首帧调色板统一（坑 2 治标）+ 复用 L1 `anim_pipeline` 全套打包。**帧间主体一致性有客观局限**（纯文生图无参考图条件），代码注释与 STATE.md 如实标注，不假装修复
- Provider 现状（2026-09-12 看门狗预检）：`providers/pollinations.py` GET /prompt 直连可用（免 key、延迟 3-45s、seed 固定时输出确定性可复现——md5 实证）；**`GenerateImageRequest` 无 seed 字段，GET 参数 `seed=` 未透传**——本线需最小扩展（见交付物 1），openai_compat 同步补 seed 透传（其请求体协议支持 seed，透传属对齐协议非新功能）
- L1 已交付 `server/app/core/anim_pipeline.py` 全套纯函数：`validate_frames`（帧数域 2-16 偶数/同尺寸/pixel 画布 ≤256）、`unify_palette`（首帧可见像素调色板量化）、`resolve_alpha_mode`/`apply_alpha_mode`、`pack_spritesheet`、`encode_animation_webp/gif`、`loop_report`、`run_anim_pack_pipeline`——**全部原样复用，禁改动**；契约与执行器模式见 `server/app/jobs/models.py`（`AnimationPackParams`）与 `server/app/api/animations.py`
- 帧数域决策：官方经典线 4/6/8/10/12/16（§2.1），本线生成帧数域取 **4-16 偶数**（经典线口径；像素线 2-16 的 2 帧无动画语义，推断决策标注）；`animation_type` 八枚举沿用 §2.1（idle/walk/run/jump/attack/hit/defeated/other）
- 画布校验决策（§5 坑 6 复刻）：`pixel=true` 时最终画布（尺寸参数值）任一轴 >256 拒绝 422——服务端复算、不信客户端（坑 6 原文语义）；`pixel=false` 不设上限（P5-L1 同决策）
- padding 语义决策：官方 padding 是「画布不动、给运动空间」的图生帧概念（§2.1），本线为纯文生图逐帧生成，**padding 不进参数面**（无源图锚定，扩 padding 无操作对象；§2.5 动作模板表降级为 loop_report 的校验提示文案与文档建议，不做成硬校验——标注推断决策，避免假契约）
- kind 标签沿用 L1 `anim_pack` 契约思想：本线 `kind="animate"`——**官方 animate-run 指生成线，此标签与官方语义对齐**（与 L1 打包线的自定 `anim_pack` 区分）

### 交付物 1：Provider 最小扩展（seed 透传）

- `GenerateImageRequest` 增可选 `seed: int | None = None`（≥0；None=不携带，既有线行为零变化，测试锁定向后兼容）
- `PollinationsProvider.generate_image`：`seed is not None` 时追加 GET 参数 `seed=<value>`（既有测试分支补一条带 seed 断言 + seed=None 不带参数断言）
- `OpenAICompatProvider` 同步透传 seed 进请求体（协议对齐；MockTransport 测试同步补）
- 禁改其它 provider 行为；改后全量测试绿

### 交付物 2：逐帧生成管线 `server/app/core/anim_gen_pipeline.py`（编排层，薄）

- `build_frame_prompt(animation_type, user_prompt, frame_index, frame_count) -> str`：按 §2.6 官方 prompt 纪律组装——动作 + 帧序描述（"frame i of n of a <action> animation"式模板）+ locked camera + 循环要求（idle/walk/run 按 §2.5 循环特性表末帧≈首帧）+ 保持项（same character/silhouette/palette/hard edges）；中英映射按动作模板表；纯函数可独立测试
- `build_frame_seeds(base_seed: int, frame_count: int) -> list[int]`：确定性 seed 序列（`base_seed + i`，命名空间隔离防相邻 seed 意外相关为过度设计——推断决策：直接线性递增，代码注释标注）；base_seed 缺省 0、显式给则用之
- `generate_frames(provider, params) -> list[Image.Image]`：逐帧调用 provider（**串行 await，i=0..n-1**——pollinations 无 key 档并发受限，串行纪律与队列模式一致）、每帧 bytes → `_decode_checked` 复用 L1 私有函数（**从 anim_pipeline import，不改其可见性亦可；若 import 私有函数不妥则在本模块写同款解码+格式白名单，二选一标注**）、任一帧失败整单失败（ProviderError 家族上抛——半成品帧序列无交付价值，标注决策）
- `run_anim_gen_pipeline(provider, params) -> AnimGenResult`：generate_frames → `unify_palette`（**坑 2 治标本体**，color_count 仅 pixel=true 合法携带——照 L1 交叉校验）→ 复用 L1 `run_anim_pack_pipeline` 打包（webp/gif/spritesheet 输出语义与 L1 完全一致）；返回 L1 `AnimationPackResult` + 生成元数据（frame_prompts 摘要/seeds）
- 帧尺寸决策（§2.6 无源图）：所有帧由同一 size 参数生成，天然同尺寸；provider 返回实际尺寸与请求不符时按 L1 `validate_frames` 异尺寸拒绝（诚实失败，禁静默缩放——tileset 先例）

### 交付物 3：契约与执行器

- `AnimateParams`（pydantic，extra=forbid，`kind="animate"`）：
  - `prompt: str`（1-500 字符，动作描述）
  - `animation_type`: 八枚举，默认 other（回显进报告与 prompt 模板）
  - `frame_count: int`：4-16 偶数（经典线口径，缺省 8——§2.1 官方默认）
  - `size: str`：`宽x高`（64-2048；`pixel=true` 时任一轴 >256 拒绝 422）
  - `output_format: webp|gif|spritesheet`（默认 webp，语义同 L1）
  - `pixel: bool=false`；`alpha_mode: soft|sharp` 可空（路由语义同 L1）；`color_count: 2-64` 可空（仅 pixel=true 合法携带）；`duration_ms: 20-1000` 默认 125
  - `seed: int | None = None`（缺省 0——确定性可复现；回显进报告）
- `AnimateReport`（进 `FinalOutputs` 可选 `animate_report`，**None 序列化省略**——照 anim_report 模式，旧线 final_outputs.json 键集不变，测试锁定）：frame_count/size/seeds/frame_prompts/animation_type/pixel/alpha_mode + L1 `AnimPackReport` 全量内嵌（loop_report 如实交付不判 failed，同 L1）
- `AnimateJobRunner`：有 provider 调用——重试语义照纹理线（4xx→failed 不重试 / 5xx·超时·网络错误→最多重试 2 次，`_TEXTURE_MAX_RETRIES` 同款常量；**重试在整帧序列级别**：任一帧耗尽重试即整单 failed，不做帧级部分重试——避免帧间风格断层，标注决策）；管线非法输入归 ImageEditError 家族→failed 不悬空；provider 调用经 asyncio.to_thread 或原生 async 照既有 runner 模式

### 交付物 4：API 路由

- `server/app/api/animate.py`：`POST /api/v1/animate`（**JSON body**——本线无文件上传，纯参数任务，照 generations 路由模式；非 multipart）+ `GET /api/v1/animate/{job_id}`（响应结构照 animations 线，含 animate_report 回显）
- 校验：prompt 长度/枚举/帧数域/尺寸域/pixel 画布/color_count 交叉全部 422 精确报文；未知字段 422（extra=forbid）
- mock 测试用 fake provider（照纹理线 fake 模式，可脚本化失败序列驱动重试逻辑），**永不真实调用外部服务**

### 硬性约束

- 禁本地模型推理、**禁新增依赖**（AnimateDiff/视频扩散类一律不引入——ADR-001）
- Provider 改动仅限交付物 1 的 seed 透传（`GenerateImageRequest` + pollinations + openai_compat 三处）；禁改 `anim_pipeline.py` 既有函数行为（只 import 复用）；禁改既有路由/执行器行为（`_drive_job` 若需新增闭包参数，只增不改既有调用点——P3-L2 惰性求值教训：报告经闭包传入防提前求值 None）
- 测试夹具一律 Pillow 代码生成；**永不真实调用外部服务**（单测全 mock/fake）；禁止用 Read 工具读任何二进制文件
- **禁读图死坑（2026-09-12 实证）**：用 Read 工具读 PNG 会触发上游网关 `API Error: 400 No target in combo has confirmed vision support`，整个 `-p` 会话立即被杀。一切图像断言/取证必须走 `python3 -c` + Pillow 脚本打印数值，证据以文本数字落 STATE.md，全程不看图
- 中文注释、英文标识符；公开函数 type hints；原子写 job/产物；日志 logging 禁 print

### 验收标准（全过才算完成，逐条给证据）

1. 全量 pytest 绿（新增：seed 透传——pollinations 带 seed 参数断言/seed=None 不携带/openai_compat 请求体 seed 字段/既有无 seed 行为零变化回归；prompt 组装——逐帧模板断言（含帧序号/动作词/循环要求按 §2.5 表）/八枚举映射；seed 序列——确定性/长度/逐值；管线编排——成功路径帧序列→打包全产物、单帧 provider 失败整单失败、调色板统一断言（统一后各帧颜色集 ⊆ 首帧调色板）、pixel=true 画布 257 拒绝、color_count 无 pixel 携带 422；契约——extra=forbid/帧数域逐值（3/5/17 422，4/16 通过）/prompt 长度域/枚举外 422；执行器——4xx 不重试/5xx 重试后成功/耗尽 failed、final_outputs 新旧契约键集断言（animate_report None 省略）；路由——202/GET/404/422 组/fake provider 端到端）
2. `uv run ruff check .` 通过
3. 铁律 grep 无新增命中（本线零新依赖，pyproject/uv.lock 不动）
4. 8600 真实冒烟（**pollinations 真通道**，服务进程无 HTTP(S)_PROXY 污染；curl 预检 30s 超时，预检不过如实记录并改约 mock 证据）：Pillow 生成参照帧不需要——直接 POST 参数任务（如 `{"prompt":"a small red pixel art ball bouncing","animation_type":"idle","frame_count":4,"size":"64x64","pixel":true,"seed":42}`）→ 202 → 轮询 succeeded → artifacts 端点取回 webp → Pillow 脚本断言 n_frames=4/loop=0 + 调色板统一实证（各帧用色 ⊆ 首帧调色板）+ animate_report 数值回显；证据（数值+尺寸）落 STATE.md。**延迟预期管理**：4 帧 × 3-45s/帧，总时长可能 10-180s，轮询耐心等
5. ROADMAP P5-L2 勾选（后移项保持标注）；STATE.md 迭代日志（seed 透传决策、串行纪律、整帧序列重试决策、padding 不进参数面决策、帧间一致性局限如实标注、kind=animate 对齐官方语义标注）
6. `git diff --stat` 自查禁改文件零触碰；commit：`feat(P5-L2): animate 生成线（逐帧生成+调色板统一+打包）`
