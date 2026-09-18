# Loop P4：UI 生成与组件分割（调研文档 06）

> 范围依据：ROADMAP P4 =「UI 生成（调研文档 06）」。受 ADR-001（禁本地模型）与 ADR-005（pollinations 过渡）约束，对调研文档 §4 技术选型做如下路线收敛（**推断决策**，代码注释与 STATE.md 标注）：
> - **可行（本 Loop 范围）**：§4.2 路线 A——alpha 连通域组件分割（零模型、零新依赖）；§4.3 确定性重排（shelf 装箱）；色键去背（复用 P2 `processors.remove_background`）。
> - **排除/后移（不在 P4）**：路线 B（GroundingDINO+SAM2 语义分割）、路线 C（VLM 规划）、rembg/BiRefNet/alpha matting（官方 advanced 档）、生成式重排、generate 模式参考图风格融合（IP-Adapter/Redux）——全部依赖本地模型或图生图端点，铁律排除或待 openai_compat 端点就绪（STATE.md 待定决策）。
> - 拆两个串行子循环：
>   - **L1（本轮委派）**：generate 模式全链——provider 生成 → matte 色键去背 → alpha 连通域组件分割 → 质量门禁 → 透明聚合表 + components.json。
>   - **L2（后续委派）**：extract 模式——1-8 张参考图提取组件 → 确定性 shelf 装箱重排。
>   - **后移**：语义标签（component 命名带语义）、advanced matting、生成式重排、参考图风格融合。

## L1：generate 模式 UI 生成线（本轮委派，2026-09-12 下发）

### 背景（先读）

- 调研文档：`docs/features/06-ui-generation.md` §1.2（输出契约）、§2（参数契约）、§3.2（请求构造与映射）、§4.1（generate 管线）、§5（坑 1/2/5/6/7/9）
- 输出硬契约（§1.2，逐字对齐）：交付物 = **一张透明聚合表 + 组件分割数据**；**绝不返回逐个组件的独立裁剪文件**（坑 1）；只交付 `final_outputs.json` 清单里的文件
- 行为规则：`remove_background=false` 时**强制覆盖**去背档位为 none（坑 2 优先级关系）；`split_components=false` 按"仅跳过 components.json 生成"实现（坑 9 假设，标注）
- P2 已交付 `server/app/core/processors.py` 的 `remove_background`（色键）与 `scan_background_color`——只读复用，不重写
- P3-L2 已交付本仓库最新管线模板：`server/app/api/textures.py`（契约 + 路由 + `TextureJobRunner`）、`server/app/core/texture_pipeline.py`（纯函数）、`FinalOutputs` 可选字段 None 序列化省略模式（旧契约文件键集不变）——本线全程照抄该模式
- 通道实况（2026-09-12）：pollinations 免 key 直连（provider 已显式 `trust_env=False`），延迟 6-45s；冒烟时勿给服务进程注入 HTTP(S)_PROXY

### 交付物 1：核心管线 `server/app/core/ui_pipeline.py`（纯函数，provider 无关）

- `resolution_size(resolution, aspect_ratio) -> tuple[int, int]`：固定映射表——1:1→1024²/2048²、4:3→1024×768/2048×1536、3:4→768×1024/1536×2048、16:9→1024×576/2048×1156、9:16→576×1024/1156×2048（**推断约定**：官方只承诺"服务分辨率档位"不披露精确像素矩阵，坑 4；实际输出尺寸必须写进 components.json 元数据，消费方以元数据为准）
- `segment_components(img, min_area) -> list[...]`：对去背后 RGBA 的 alpha 通道做连通域分析（**纯 Pillow + stdlib 实现，禁新增依赖**——环境无 numpy；4/8-连通自选一种并在 docstring 注明选择理由）；每组件 `{id, label, bbox[x,y,w,h], area_px}`；label 为 `component_01` 起顺序编号（**无语义标签**——语义分割属路线 B/C 已后移，标注）；`min_area` 噪点过滤（默认值自定并标注推断）
- `quality_gate(components, canvas_w, canvas_h) -> dict`：§5-7 自动门禁——①组件数 >0；②单块巨型粘连告警（单组件面积占画布 >60% 置 flag，阈值标注推断）；③组件 bbox 两两重叠率检查（重叠对列出）；结果为报告数据**不判 failed**——与 P3-L2 检缝同哲学：指标如实进产物，是否重生成由用户决定
- 装箱重排不在 L1（extract 专属，L2 实现）

### 交付物 2：契约与执行器

- `UiGenParams`（pydantic，extra=forbid，kind 取值遵循 `jobs/models.py` 现有约定）：
  - `prompt: str(1..2000)`；`quality: standard|detailed|ultimate`（默认 detailed）；`resolution: 1k|2k`（默认 2k，官方默认档）；`aspect_ratio: 4:3|3:4|16:9|9:16|1:1`（默认 1:1）；`background_color: #000000|#ffffff|#cccccc|#808080|#333333` 五枚举（默认 #cccccc，官方 CLI 同款五值）；`remove_background: bool=true`；`split_components: bool=true`
  - **quality 是契约面参数位**（对齐官方 CLI 面）：本地 provider 无质量档，实际不入 provider 请求；官方映射 standard→low / detailed→medium / ultimate→high（§3.2）连同"未生效"事实记入 job 请求快照，STATE.md 标注
  - `remove_bg_method` 不进 HTTP 参数面（官方 web 契约本就没有该参数；本地仅色键一档等价 standard；advanced 已后移）
  - generate 模式参考图不进 L1 参数面（官方语义是风格参考，本地 provider 无图生图通道，放假接口=假行为；记 STATE.md 待定决策）
- `UiGenJobRunner`（照抄 `TextureJobRunner`）：`factory.get()` → `generate_image(size=resolution_size(...))` → decode → `remove_background=true` 时 `processors.remove_background`（matte 色 = background_color 参数值；色键容差自定并在注释注明）→ `split_components=true` 时 `segment_components` + `quality_gate` → 原子写产物
- 产物契约：`sheet.png`（透明聚合表 RGBA PNG）+ `components.json`（`{components: [...], gate: {...}, actual_size: [w, h]}`；**schema 为自定**——官方分割数据格式未披露（诚实缺口 §5 末尾），标注）；`FinalOutputs` 增加可选字段照 `seam_report` 模式（None 序列化省略，非 ui 线 final_outputs.json 保持旧键集，测试锁定向后兼容）
- Provider 故障映射照 P1/P3：4xx→failed 不可重试；5xx/超时/网络错误→最多重试 2 次（fake provider 脚本化失败序列驱动）

### 交付物 3：API 路由

- `server/app/api/ui_gen.py`：`POST /api/v1/ui_gen`（JSON body → 202 {job_id}，响应结构照抄 textures 线）+ `GET /api/v1/ui_gen/{job_id}`（状态响应复用 P1/P2/P3 结构）
- 4xx 分支：空 prompt / 枚举外取值 / extra 字段 → 422；未知 job → 404

### 硬性约束

- 禁本地模型推理、**禁新增依赖**（GroundingDINO/SAM2/VLM/rembg/BiRefNet/numpy 一律不引入——调研文档 §4 表格仅是选型分析，铁律见 ADR-001）
- 禁改 `providers/`；禁改 `processors.py`（`remove_background` 只读复用）；禁改 `map_layout.py` / `tileset_synth.py` / `texture_pipeline.py`（只读）；禁改 `generations/artifacts/ui` 现有行为
- 测试夹具一律 Pillow 代码生成 + fake provider（或 httpx MockTransport）；**永不真实调用外部服务**；禁止用 Read 工具读任何二进制文件
- **禁读图死坑（2026-09-12 实证）**：用 Read 工具读 PNG 会触发上游网关 `API Error: 400 No target in combo has confirmed vision support`，整个 `-p` 会话立即被杀（上一轮死于此，P4-L1 实现已写完却死在取证阶段）。一切图像断言/取证必须走 `python3 -c` + Pillow 脚本打印数值（尺寸/mode/alpha 采样/组件数），证据以文本数字落 STATE.md，全程不看图
- prompt 只透传，服务端不做提示词改写/模板增强（坑 6：官方纪律是描述性 prompt，模板逻辑不进服务端）
- 中文注释、英文标识符；公开函数 type hints；原子写 job/产物；日志 logging 禁 print

### 验收标准（全过才算完成，逐条给证据）

1. 全量 pytest 绿（新增：档位映射表逐值断言（5 长宽比 × 2 档全组合）；segment 连通域——Pillow 合成夹具 3 个分离色块 → 3 组件 bbox 精确断言、min_area 噪点过滤、相触色块粘连被合并的语义断言、全透明图 0 组件；quality_gate 三规则各有独立断言；`remove_background=false` 强制 none 的行为断言、`split_components=false` 仅 sheet 无 components.json；路由：空 prompt 422、extra 字段 422、枚举外 quality/resolution/aspect_ratio/background_color 各 422、未知 job 404、provider 4xx→failed、5xx→重试后 succeeded（fake provider 脚本化失败序列）；final_outputs.json 新旧契约键集断言）
2. `uv run ruff check .` 通过
3. 铁律 grep 无新增命中（本线零新依赖）
4. 8600 真实冒烟（provider=pollinations 真通道）：`POST /api/v1/ui_gen {"prompt":"pixel art game UI button set, icons and panels, flat colors, plain background", "resolution":"1k", "aspect_ratio":"1:1"}` → 202 → 轮询 succeeded（6-60s 正常）→ artifacts 端点取回产物断言：sheet.png 为带透明像素的 RGBA PNG（角落 alpha=0 至少一处实证去背生效）、components.json 组件数>0 且含 gate 与 actual_size；证据（组件数/门禁数值/实际尺寸）落 STATE.md。pollinations 出图带杂色背景导致组件粘连/数量异常属"指标如实交付"范畴，如实记录不判失败；通道不可用则如实记录停止（**禁伪造**），标注环境阻塞待复测
5. ROADMAP 增加 P4 章节（L1 勾选、L2 与后移项标注）；STATE.md 迭代日志（档位矩阵推断标注、components.json schema 自定标注、quality 参数位语义、门禁阈值、连通判据选择）
6. `git diff --stat` 自查禁改文件零触碰；commit：`feat(P4-L1): UI 生成线（matte 去背+alpha 连通域组件分割+质量门禁）`

## L2：extract 模式提取重排线（后续委派，本轮不动）

### 背景（先读）

- 调研文档：§1.1（extract 语义：提取全部可见元素默认全量 → 重排成一张可复用观感聚合表）、§3.2（`ui_extract` 归一 / 0 张硬门禁 / ≤8 张上限）、§4.3（**确定性重排是唯一路线**，生成式重排已后移）、§5（坑 5/10）
- L1 已交付 `ui_pipeline.segment_components` / `quality_gate` / 契约模式（复用不重写）

### 交付物（要点）

- `server/app/core/ui_pipeline.py` 增加 `shelf_repack(...)`：确定性装箱——组件按面积降序 → shelf 行排布 → padding 常量 → 画布尺寸取整；按来源图分组排序；从各源图按 bbox 抠出组件像素（含 alpha）贴入新画布
- 路由：`POST /api/v1/ui_gen/extract`（multipart：`files[]` 参考图 1-8 张 + `payload` JSON 串照 tilesets 模式；0 张 422、>8 张 422——官方 §3.2 硬门禁，把无效请求挡在 provider 前）。官方请求字段 `generation_mode` 值为 `generate|ui_extract`，本 HTTP 面用独立端点表达模式即可（标注与官方归一的差异）
- 每张参考图：decode → 色键去背（matte 色取 payload `background_color`，或 `scan_background_color` 自动扫描，策略自定并注明）→ `segment_components`（复用 L1）→ 收集全部组件
- 产物：单张透明聚合表 `sheet.png` + `components.json`（组件含 `source_index` 与聚合表内 bbox 双坐标）+ 同 L1 质量门禁；同样**不导出单组件裁剪文件**（坑 1 硬契约）
- 执行器复用/扩展 `UiGenJobRunner`；重试语义同 L1

### 验收标准（后续委派时展开，同 L1 六条结构）

- 全量 pytest 绿（multipart 上传防线：0 张/超 8 张/坏文件 4xx；重排确定性：同输入两次运行字节级一致；组件源坐标与聚合表坐标双断言；门禁复用断言）
- ruff 通过；铁律 grep 无新增；8600 真实冒烟两发（generate 一发 + Pillow 合成含明确色底 UI 图做 extract 一发，断言聚合表与 components.json）；ROADMAP P4-L2 勾选 + STATE.md 日志；`git diff --stat` 自查；commit：`feat(P4-L2): UI 提取重排线（多图提取+确定性 shelf 装箱）`
