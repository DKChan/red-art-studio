# ROADMAP

> 阶段规划与状态总览。细粒度动态状态在 `docs/dev/STATE.md`。

## 阶段总览

| 阶段 | 内容 | 状态 |
|---|---|---|
| P0 | 8 能力域调研 + 参数契约 + 疑问台账 | ✅ 完成（docs/features/01-08） |
| P1 | **曳光弹：文生图竖切 0→1**（架构/接口/异步任务/多 Provider） | ✅ 完成（含 pollinations 真实冒烟） |
| P2 | 后处理三件套（去背/像素化/无缝循环，调研文档 04） | ✅ 完成（8600 真实冒烟三项全过） |
| P3 | 纹理 & tileset（调研文档 05）——L1 + L2 完成 | ✅ |
| P4 | UI 生成（调研文档 06）——L1/L2 完成 | ✅ |
| P5 | 精灵动画（调研文档 02）——L1/L2 完成 | ✅ |
| P6 | 音频：音效+音乐（调研文档 07） | ⚪ |
| P7 | 策划 Agent：本地 LLM + 设计文档树（调研文档 08） | ⚪ |

优先级依据：轻→重、尽早验证管线复用性；P2-P7 顺序可在 P1 结束后按需调整。

## P1 曳光弹：文生图竖切

### 目标

跑通一条完整纵向链路：**HTTP API 进入 → Job 状态机 → Provider 外部推理 API → 产物落盘 → 轮询取回**。
这条竖切验证整体架构的三根支柱：Provider 适配层、异步任务模型、产物契约。做完后，后续所有能力域（P2-P7）都只是「新 Provider + 新路由」的横向扩展。

### 架构（P1 定稿，与 README §架构一致）

```
Web 前端（P1 仅 API，页面后补）
   │
FastAPI (api/) ── 核心资产：preset 体系 / job 状态机 / 输出契约 / 确定性后处理
                  │  POST /api/v1/generations → 202 {job_id}
                  │  GET  /api/v1/generations/{job_id}
                  ▼
              jobs/ 状态机+执行器（pending→running→succeeded|failed）
                  │
                  ▼
              生成后端抽象层（providers/base.py 协议）
               ├── openai_compat.py ──HTTP──▶ 任意 OpenAI 兼容端点（含国内 API）
               └── comfyui.py       ──HTTP──▶ 本地 ComfyUI（/prompt /history /view）
                  │
                  ▼
              data/jobs/<id>/job.json + data/artifacts/<id>/（媒体 + final_outputs.json）
```

### Loop 清单（顺序执行，每 Loop 验收通过才进下一个）

**L1 工程骨架**
- [x] uv 工程（Python 3.11，包目录 `server/`），FastAPI 入口，`GET /healthz`
- [x] 依赖就绪：fastapi、uvicorn[standard]、httpx、pydantic、pydantic-settings、pytest、ruff
- 验收：`uv run pytest` 绿；服务可启动且 `curl :8600/healthz` 返回 200

**L2 Provider 抽象 + openai_compat**
- [x] `providers/base.py`：Provider 协议（async 方法、完整类型）
- [x] `providers/openai_compat.py`：OpenAI images generations 兼容实现（b64_json 与 url 两种响应、超时、错误分支）
- [x] pydantic-settings 配置：provider 选择、base_url、api_key、model（读 `.env`）
- 验收：MockTransport 测试覆盖 成功b64/成功url/4xx/超时 分支

**L3 任务模型 + API**
- [x] `POST /api/v1/generations`（prompt、size 默认 1024x1024、n、provider 可选）→ 202 `{job_id, status}`
- [x] `GET /api/v1/generations/{job_id}` → 状态机 pending/running/succeeded/failed
- [x] 产物落盘：`data/artifacts/<job_id>/` 图片文件 + `final_outputs.json`（schema 见 CLAUDE.md，测试锁定）
- 验收：端到端 mock 测试：submit → 轮询至 succeeded → 产物文件与 final_outputs.json 校验通过 ✅（MockTransport 后端驱动真 Provider/执行器/文件系统存储；8600 真实启动冒烟另证 failed 分支落盘）

**L4 comfyui provider**
- [x] text2img workflow 模板（参数化 prompt/尺寸/seed）+ `POST /prompt` + 轮询 `/history/{prompt_id}` + `GET /view` 下载
- [x] 同一 Provider 协议实现，配置切换
- 验收：MockTransport 测试通过；本机 ComfyUI（8188）在跑则真实冒烟一次，没跑则 mock 验收即可（记入 STATE.md）✅（mock 验收，8188 未运行）

**L5 端到端验收 + 收尾**
- [x] `.env.example`、README quickstart、openapi 契约核对
- [x] 架构铁律自查：全仓库无模型加载/权重下载代码（grep 铁律关键词）
- 验收：P1 总验收标准全过 ✅（2026-09-07：53 测试绿、ruff 通过、pollinations 真实端到端冒烟 succeeded、mock 链路全分支、.env 切换零代码改动、铁律 grep 干净；详见 STATE.md L5 条目）

### P1 总验收标准

1. 仅改 `.env` 配置即可在 `openai_compat` / `comfyui` 之间切换，零代码改动
2. submit → poll → artifacts 全链路成功（mock 或真实后端至少其一实证）
3. `final_outputs.json` / `job.json` 契约由 pydantic 模型 + 测试锁定
4. `uv run pytest` 全绿，`uv run ruff check .` 无报错
5. 铁律检查：grep 无 `torch`/`diffusers`/`transformers`/`from_pretrained`/`snapshot_download` 等模型部署痕迹
6. STATE.md 迭代日志完整；每个 Loop 至少一个 git commit

## P2 后处理三件套（去背 / 像素化 / 无缝循环）

### 目标

在 P1 竖切骨架上横向扩展第一条后处理能力线：对**已有图片**做三种确定性视觉变换（服务层纯算法，铁律 #3），复用 Job 状态机 / JobStore / final_outputs 产物契约 / 202+轮询 API 模式。任务书：`docs/dev/loop-P2.md`。

### Loop 清单

**P2 后处理三件套**
- [x] `POST /api/v1/image-edits`（multipart: file + payload JSON）→ 202；`GET /api/v1/image-edits/{job_id}` → 状态响应（复用 generations 结构）
- [x] `processors.py` 三件套纯函数：pixelate（NEAREST 降采样→FASTOCTREE 量化 ≤32 色→alpha 二值化→整数倍回放；自动粒度估计失败回落 16）、remove_background（色键抠像：四角扫描众数底色/显式 #RRGGBB + tolerance 色距→alpha 二值化；不做模型分割）、self_loop（roll 半程 + 接缝带镜像融合；four_way 先水平后垂直串行）
- [x] 执行器分派：`ImageEditJobRunner` 线程池跑纯函数，状态机语义不变；上传防线（Content-Type/魔数双白名单、20MB 上限 413、payload pydantic 校验 422）
- [x] payload 嵌套契约归一：wire 形态 `{"operation","params"}` 经 mode=before 验证器拍平进扁平模型，extra=forbid 杜绝静默吞参（冒烟中实证的漏网 bug，回归测试锁定）
- 验收：`uv run pytest` 全绿（88 项：三 operation 成功路径、自动像素尺寸估计、四角扫描底色、four_way 串行、路由 4xx 分支、final_outputs.json 契约）✅（2026-09-10）；`uv run ruff check .` 通过 ✅；铁律 grep 干净 ✅；8600 真实冒烟三项全过（去背角点透明/蓝块中心不透明、像素化 256 块内一致且 ≤32 色、无缝循环首尾列差 0.50<2，证据见 STATE.md）✅

### P2 验收标准（任务书 loop-P2.md §验收标准）

1. pytest 全绿 + ruff 通过 + 铁律 grep 无模型部署痕迹
2. 真实服务冒烟三项（上传→轮询 succeeded→取回产物→像素断言）证据落 STATE.md
3. 限制说明：去背仅色键抠像，模型分割路线被 ADR-001 排除（原版的 mode=hd 不实现）

## P3 纹理 & tileset（调研文档 05）

### 目标

环境素材与地图就绪资产的第一条能力线。受 ADR-001（禁本地模型）与 ADR-005（无生图端点，pollinations 过渡）约束，拆两个串行子循环（任务书：`docs/dev/loop-P3.md`）：
**L1** 全确定性部分（dual-grid tileset 程序化合成 + 地图瓦片几何契约，零 AI 零外部依赖）；**L2** 无缝纹理生成线（provider 生成 → 64×64 归一 → self_loop → tiling_preview + 检缝，等距变体追加 2:1 仿射投影）。

### Loop 清单

**L1 dual-grid tileset 程序化合成 + 几何契约模块**
- [x] `server/app/core/map_layout.py`：§2.5 几何契约逐字复刻（像素等距 128×64/(±64,32)；像素六边 127 步距/(64,64)/(−63,64)；HD 等距 372/119/744×372/(±372,186)/缩放 128/min(w,744)；HD 六边 300/96/599/354/0.25×=75/149.75/88.5/偏移 75；六边 tetraploid 奇偶两套占据格表；footprint 与中心锚定常量）+ dual-grid 格位官方实证表（`DUAL_GRID_ATLAS_CELL_BY_KEY` + `dual_grid_tile_key`，照抄原版官方 map-tile-layout.js，见 ~/code/red-art-studio-refs/original-skills/）
- [x] `server/app/core/tileset_synth.py`：两张 64×64 RGBA 无缝纹理 → 256×256 的 4×4 图集；三模式（dual/foreground/background 镂空）；四角位双线性场 + 带种子周期 blob 噪声 + 阈值羽化；非 64×64 拒绝（禁止静默缩放）；同 seed 确定性
- [x] `POST /api/v1/tilesets`（multipart: background + foreground + payload）→ 202；`GET /api/v1/tilesets/{job_id}`（复用 P2 上传防线与状态机；`TilesetJobRunner` 线程池）
- 验收：`uv run pytest` 全绿（148 项，新增 60：几何逐值断言 + 合成器三模式格位/镂空方向/确定性/强校验 + 路由 4xx 分支）✅；`uv run ruff check .` 通过 ✅；铁律 grep 无新增命中 ✅；8600 真实冒烟通过（Pillow 双纹理 → 202 → succeeded → 取回 256×256 产物 → 官方格位 key0/key15/过渡格像素断言，证据见 STATE.md）✅（2026-09-11）

**L2 无缝纹理生成线（下一轮委派）**
- [x] prompt → provider（pollinations 过渡）→ 最近邻降采样 64×64 →（可选调色板量化）→ self_loop（复用 P2）→ 双轴平铺 tiling_preview + 检缝报告 → 交付正图+预览；等距变体追加 2:1 仿射投影 ✅（2026-09-12：`core/texture_pipeline.py` 四段 + `isometric_project`（map_layout 常量逆仿射）；`TextureParams`/`SeamReport` 契约 + `FinalOutputs.seam_report`（None 序列化省略，旧契约向后兼容）；`TextureJobRunner`（4xx 不重试 / 5xx·超时重试 2 次）；`POST/GET /api/v1/textures`（extra=forbid）；24 项新测试）

**后移（不在 P3，依赖 ComfyUI tiling latent / ControlNet 基础设施就绪，已记录 STATE.md）**
- [ ] 等距/六边/HD 瓦片模板条件生成（模板线框 + ControlNet lineart + IP-Adapter 参考图）
- [ ] 横版三层地图（三段生成 + 水平 tiling latent + 去背分层）

### P3-L1 验收标准（任务书 loop-P3.md §验收标准）

1. pytest 全绿（几何逐值 + 合成器三模式 + 路由 4xx）✅ 2. ruff 通过 ✅ 3. 铁律 grep 无新增 ✅ 4. 8600 真实冒烟证据落 STATE.md ✅ 5. ROADMAP/STATE 更新 ✅ 6. diff 自查未触碰禁改文件 + commit ✅

### P3-L2 验收标准（任务书 loop-P3.md §验收标准）

1. pytest 全绿（四段流水线逐段 + 路由分支 + final_outputs 契约，新增 24 项至 172）✅ 2. ruff 通过 ✅ 3. 铁律 grep 零命中 ✅ 4. 8600 真实冒烟（pollinations 真通道两发：普通 + isometric=true，尺寸/检缝数值独立复核一致）证据落 STATE.md ✅ 5. ROADMAP/STATE 更新（3×3 推断标注、投影方式选择说明）✅ 6. diff 自查禁改文件零触碰 + commit ✅

## P4 UI 生成与组件分割（调研文档 06）

### 目标

UI 聚合表能力线：生成（或提取）→ 去背 → **alpha 连通域组件分割** → 质量门禁 → 一张透明聚合表 + components.json（§1.2 输出硬契约：**绝不返回逐组件独立裁剪文件**，坑 1）。受 ADR-001 约束对调研文档 §4 做路线收敛（详见 loop-P4 任务书）：可行 = 路线 A（alpha 连通域，零模型零新依赖）+ 确定性 shelf 装箱 + 色键去背（复用 P2）；排除/后移 = 路线 B（语义分割模型）/路线 C（VLM）/rembg 类 matting/生成式重排/参考图风格融合。拆两个串行子循环。

### Loop 清单

**L1 generate 模式 UI 生成线（2026-09-12 完成并验收）**
- [x] `server/app/core/ui_pipeline.py`（纯函数，provider 无关）：`resolution_size` 档位矩阵（1K/2K × 五长宽比，**推断约定**：官方只承诺档位不披露像素矩阵，坑 4）；`segment_components` alpha 8-连通域分割（纯 Pillow + stdlib，零新依赖；min_area 噪点过滤；label 顺序编号**无语义**）；`quality_gate` 三规则门禁（组件数>0 / 巨型粘连告警 >40% / bbox 两两重叠）——报告数据不判 failed，与 P3-L2 检缝同哲学
- [x] 契约与执行器：`UiGenParams`（extra=forbid；quality 为契约面参数位，本地 provider 不消费）；`UiGenJobRunner`（4xx 不重试 / 5xx·超时重试 2 次）；matte 色键去背复用 P2 `processors.remove_background`；坑 2 优先级（remove_background=false 强制不去背）+ 坑 9 假设（split=false 仅跳过 components.json）
- [x] `POST /api/v1/ui_gen` + `GET /api/v1/ui_gen/{job_id}`（响应结构照抄 textures 线）；artifacts Content-Type 增 json
- [x] 产物：`sheet.png`（透明聚合表 RGBA）+ `components.json`（components/gate/actual_size，**schema 自定**——官方分割格式未披露）+ `FinalOutputs.ui_components` 可选字段（None 序列化省略，旧契约键集向后兼容）
- 验收：`uv run pytest` 全绿（218 项，新增 46）✅；ruff 通过 ✅；铁律 grep 零命中（零新依赖，pyproject 与 P3 基线一致）✅；8600 真实冒烟两发（证据落 STATE.md，第二发 `background_color=#808080` 实证去背生效：四角 alpha=0、透明占比 0.726、11 组件 gate passed）✅；ROADMAP/STATE 更新 ✅；diff 自查 + commit ✅（2026-09-12）

**L2 extract 模式提取重排线（2026-09-12 完成并验收）**
- [x] `ui_pipeline.shelf_repack`：确定性 shelf 装箱（**source_index 分组 → 图内面积降序** → 行排布（行宽目标 1024，超宽组件独占行）→ padding 8/行高取整 8，均为推断约定；从源图按 bbox 抠组件含 alpha（paste 以自身 alpha 为 mask）贴入新画布；label 全局重编号防多图重名）
- [x] `POST /api/v1/ui_gen/extract`（multipart `files` 1-8 张 + `payload` JSON；0 张/>8 张 422 **在读文件内容前**，官方 §3.2 硬门禁挡在管线前；逐文件防线复用 tilesets `_read_texture`）
- [x] 每图：decode → 色键去背（显式 matte=全请求单一色 / None=逐图 `scan_background_color` 自动扫描，策略已注明）→ 复用 L1 `segment_components` → 全组件重排为单张透明聚合表 + components.json（`source_index`+`source_bbox` 与表内 bbox 双坐标，可选字段 None 序列化省略、L1 wire 形态不变）；**不导出单组件裁剪文件**（坑 1）
- [x] 执行器扩展 `UiGenJobRunner.submit_extract`：**零 provider 调用**（确定性管线线程池跑，重试语义不适用——无外呼可重试；管线非法输入归 failed 不悬空）
- 验收：pytest 全绿（239 项，新增 21：管线 13 + 路由 8）✅；ruff 通过 ✅；铁律 grep 零命中（pyproject/uv.lock 未动=零新依赖）✅；8600 真实冒烟四发（generate 两发复现 L1 结论 + extract 两发：显式 matte 双坐标精确/自动扫描双底色全过，证据落 STATE.md）✅；ROADMAP/STATE 更新 ✅；diff 自查 + commit ✅

**后移（不在 P4，全部依赖本地模型或图生图端点，ADR-001 排除或待端点就绪，记 STATE.md）**
- [ ] 语义标签（component 命名带语义）——路线 B/C 语义/VLM 分割
- [ ] advanced matting（rembg/BiRefNet/alpha matting，官方 advanced 档）
- [ ] 生成式重排（L2 的 shelf 装箱先行，生成式待图生图通道）
- [ ] generate 模式参考图风格融合（IP-Adapter/Redux 类，openai_compat 图生图端点就绪再议）

### P4-L1 验收标准（任务书 §验收标准）

1. pytest 全绿（档位矩阵逐值 + 连通域精确断言 + 门禁三规则 + 路由 4xx/重试 + final_outputs 新旧键集，新增 46 项至 218）✅ 2. ruff 通过 ✅ 3. 铁律 grep 零新增命中（上轮 docstring 提及模型库名造成 1 处字面命中，本轮改写消除）✅ 4. 8600 真实冒烟（pollinations 真通道两发：①#cccccc 实证杂色背景杂色→1 巨型组件 gate passed=false 如实交付；②#808080 去背生效 11 组件全门禁通过，四角 alpha=0 独立复核）证据落 STATE.md ✅ 5. ROADMAP P4 章节（L1 勾选/L2/后移）+ STATE.md 迭代日志（档位矩阵推断、schema 自定、quality 参数位、门禁阈值、8-连通判据）✅ 6. diff 自查禁改文件零触碰 + commit ✅

## P5 精灵动画（调研文档 02）

### 目标

帧动画能力线。受 ADR-001（禁本地模型——AnimateDiff/SVD/CogVideoX/Wan 视频扩散类一律排除）与 ADR-005（无图生视频/图生图通道）约束，对调研文档 §3/§4 做路线收敛（任务书 loop-P5）：可行 = 帧序列**确定性打包**（spritesheet 含引擎切图元数据 + 动画 WebP/GIF 编码 + 循环检报告 + 像素纪律后处理，纯 Pillow 零 AI 零新依赖）；排除/后移 = 视频扩散生成路线（ADR-001）、keyframes 线与 video 兜底线（依赖图生图/图生视频通道，待 openai_compat 端点）、骨骼动画。拆两个串行子循环。

### Loop 清单

**L1 帧序列打包线（2026-09-12 完成并验收）**
- [x] `server/app/core/anim_pipeline.py`（纯函数，provider 无关）：帧完整性校验（2-16 偶数=经典线 4-16 ∪ 像素线 2-16 并集，推断；同尺寸禁静默缩放；pixel=true 画布 ≤256 坑 6）；像素纪律后处理（alpha 双模式路由 None+pixel→sharp/否则 soft，坑 8；调色板统一以首帧可见像素调色板为准，坑 2 治标）；spritesheet 确定性装箱（cols=⌈√n⌉，末行空位透明）；动图编码（WebP 无损 loop=0 恒循环 §2.3 + GIF 兼容交付 1-bit 透明局限如实标注）；loop_report（首末帧最大通道跳变 <6 与 P2/P3 同阈值，不判 failed）✅
- [x] 契约与执行器：`AnimationPackParams`（extra=forbid，kind="anim_pack" **自定标签**——打包线无官方 canonical 对应；color_count 仅 pixel=true 合法携带）；`SpritesheetMetaModel`/`LoopCheckReport`/`AnimPackReport`（None 序列化省略）；`FinalOutputs.anim_report` 可选字段（旧契约键集向后兼容）；`AnimationPackJobRunner` 零 provider 调用（submit_extract 同款线程池模式，无重试语义）✅
- [x] `POST /api/v1/animations`（multipart files[] 2-16 张 + payload；帧数门禁在读文件内容前：0/1/17/奇数 422；动画格式输入拒绝 422——首帧截取是静默丢信息）+ `GET /api/v1/animations/{job_id}`（anim_report 回显）✅
- 验收：pytest 全绿（295 项，新增 56：管线 39 + 路由 17）✅；ruff 通过 ✅；铁律 grep 零新增命中（pyproject/uv.lock 未动=零新依赖）✅；8600 真实冒烟两发（8 帧位移序列：spritesheet 线网格逐格球心断言+空位透明+loop_report 独立复算一致；webp 线 n_frames=8/loop=0/duration=125 回读一致，证据落 STATE.md）✅；ROADMAP/STATE 更新 ✅；diff 自查 + commit ✅（2026-09-12）

**L2 animate 生成线（2026-09-12 完成并验收）**
- [x] Provider 最小扩展：`GenerateImageRequest` 增可选 `seed`（None=不携带，既有线行为零变化测试锁定）；pollinations GET 参数透传 + openai_compat 请求体透传（协议对齐）✅
- [x] `core/anim_gen_pipeline.py`（薄编排层）：`build_frame_prompt`（§2.6 prompt 纪律 + §2.5 动作循环特性表，纯函数）+ `build_frame_seeds`（base_seed+i 线性递增，推断决策）+ `generate_frames`（**串行 await**，pollinations 无 key 档并发受限；尺寸不符诚实拒绝）→ 复用 L1 `run_anim_pack_pipeline` 全套打包（调色板统一由 L1 pixel 分支执行）；帧间一致性局限如实标注不假装修复 ✅
- [x] 契约与执行器：`AnimateParams`（extra=forbid，**kind="animate" 对齐官方语义**；帧数 4-16 偶数经典线口径；pixel=true 画布 ≤256 服务端复算坑 6；color_count 交叉校验；seed 缺省 0 确定性可复现；**padding 不进参数面**——纯文生图无源图锚定，§2.5 表降级为 prompt 建议与 loop_report 指标）；`AnimateReport`（L1 AnimPackReport 全量内嵌 pack 键 + seeds/frame_prompts 留档可复现）；`FinalOutputs.animate_report`（None 序列化省略，旧契约键集不变）；`AnimateJobRunner`（**整帧序列级重试**：4xx 不重试 / 5xx·超时重试 2 次，任一帧耗尽即整单 failed——不做帧级部分重试防帧间风格断层）✅
- [x] `POST /api/v1/animate`（JSON body 纯参数任务）+ `GET /api/v1/animate/{job_id}`（animate_report 回显）✅
- 验收：pytest 全绿（322 项，新增 27：provider 3 + 管线 15 + 路由 10——seed 透传/逐帧 prompt 模板/seed 序列/整帧序列重试/契约 422 组/final_outputs 新旧键集）✅；ruff 通过 ✅；铁律 grep 零命中（pyproject/uv.lock 未动=零新依赖）✅；8600 真实冒烟（pollinations 真通道 4 帧 pixel 线：succeeded 后 webp 回读 n_frames=4/loop=0/duration 125、调色板统一实证各帧用色 ⊆ 首帧 32 色调色板、animate_report 全字段回显；首轮两发撞上游 429 限流实证整帧序列重试与如实 failed，第三发错峰成功，证据落 STATE.md）✅；ROADMAP/STATE 更新 ✅；diff 自查 + commit ✅（2026-09-12）

**后移（不在 P5，ADR-001 排除或待 openai_compat 端点，记 STATE.md）**
- [ ] keyframes 关键帧控制线（依赖图生图条件通道）
- [ ] video 短视频兜底线（依赖图生视频通道）
- [ ] 视频扩散生成（AnimateDiff/SVD/CogVideoX/Wan 类，ADR-001 排除）
- [ ] 骨骼动画（Spine/DragonBones）

### P5-L1 验收标准（任务书 §验收标准）

1. pytest 全绿（帧数域逐值/异尺寸/pixel 画布/color_count 交叉/alpha 路由/调色板统一/装箱布局与确定性/动图回读/loop_report 数值/路由 4xx/provider.calls==0/final_outputs 新旧键集，新增 56 项至 295）✅ 2. ruff 通过 ✅ 3. 铁律 grep 无新增命中（本线零新依赖，pyproject/uv.lock 不动）✅ 4. 8600 真实冒烟（确定性通道无 provider：8 帧序列 → curl multipart → succeeded → artifacts 端点取回 → Pillow 数值取证）证据落 STATE.md ✅ 5. ROADMAP P5 章节（L1 勾选、L2 与后移项标注）+ STATE.md 迭代日志（帧域并集推断、duration 缺省推断、alpha 路由决策、GIF 透明局限、kind 自定标注）✅ 6. diff 自查禁改文件零触碰 + commit ✅

### P5-L2 验收标准（任务书 §验收标准）

1. pytest 全绿（seed 透传/prompt 模板/seed 序列/管线编排/调色板统一/契约域/整帧序列重试/final_outputs 新旧键集/路由 4xx 组/fake provider 端到端，新增 27 项至 322）✅ 2. ruff 通过 ✅ 3. 铁律 grep 无新增命中（零新依赖，pyproject/uv.lock 不动）✅ 4. 8600 真实冒烟（pollinations 真通道 4 帧 pixel 线：succeeded → webp 回读 n_frames=4/loop=0 + 调色板统一实证 + animate_report 数值回显；上游 429 限流两发实证重试与如实 failed）证据落 STATE.md ✅ 5. ROADMAP P5-L2 勾选 + STATE.md 迭代日志（seed 透传决策、串行纪律、整帧序列重试决策、padding 不进参数面、帧间一致性局限、kind=animate 对齐官方）✅ 6. diff 自查禁改文件零触碰 + commit ✅

## 待定事项（不阻塞 P1）

- ~~参考库功能取舍~~ —— 已拍板：砍掉（ADR-004，2026-09-07）
- Web 前端页面（形态已定 B/S；P1 仅 API，完整页面 P2 前做）
- ~~openai_compat 实测指向哪个真实端点~~ —— 用户当前无生图 API（ADR-005）；将来提供后即插即用
- fal / replicate 云端 adapter（README §架构提及）——P2+ 按需加
