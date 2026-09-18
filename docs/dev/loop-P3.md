# Loop P3：纹理 & tileset（调研文档 05）

> 范围依据：ROADMAP P3 =「纹理 & tileset」。受 ADR-001（禁本地模型）与 ADR-005（暂无生图端点，pollinations 过渡）约束，拆两个串行子循环：
> - **L1（本轮委派）**：全确定性部分——dual-grid tileset 程序化合成 + 地图瓦片几何契约模块。零 AI、零外部依赖，纯 Pillow + 纯数学。
> - **L2（后续委派）**：无缝纹理生成线（provider 生成 → 64×64 归一 → self_loop → tiling_preview + 检缝）。
> - **后移（不在 P3）**：等距/六边/HD 瓦片模板条件生成、横版三层地图——依赖 ComfyUI tiling latent / ControlNet（铁律允许但基础设施未就绪，STATE.md 已记录）。等距纹理的 2:1 投影是确定性的，归入 L2 顺带实现。

## L1：dual-grid tileset 程序化合成 + 几何契约模块

### 背景（先读）

- 调研文档：`docs/features/05-maps-tiles-and-textures.md` §1.3（dual-grid 契约）、§2.5（几何契约数字）、§3.3（合成路线 A）、§5（坑 1/2/6/7）
- 核心产品契约：输出是 256×256 的 4×4 图集（64px dual-grid 格），客户端据 4 角地形位实时取格摆放；**全前景格由纯纹理覆盖**，其余 15 格为过渡变体
- P2 已交付 `server/app/core/processors.py`（self_loop 等），任务存储/执行器模式见 `server/app/jobs/`，路由模式见 `server/app/api/image_edits.py`

### 交付物 1：几何契约模块 `server/app/core/map_layout.py`（纯函数 + 常量，无 I/O）

逐字复刻调研文档 §2.5 的数字（这是原版契约的护城河，一个数都不能错）：

- 像素等距：底座菱形 128×64；两轴邻居中心偏移 (±64, 32)；同行中心横向间距 128、同竖线纵向间距 64；tetraploid 逻辑 256×128
- 像素六边：边长 64；同行中心步距 **127**（= 2×64−1，共享一列边缘像素）；下行偏移：下一行水平 64/垂直 64，对角 (64,64) 与 (−63,64)；跨两行竖向中心距 128
- HD 等距：块边 372、块高 119，标准顶面 744×372；坐标轴偏移 (±372, 186)；编辑器归一 128×64（显示偏移 (±64,32)）；tetraploid 顶面 1488×744；资产绘制缩放 128/min(image_width, 744)
- HD 六边：边长 300、底高层高 96；同行步距 599；下行 (300,354) / (−299,354)；0.25× 显示：边长 75、步距 149.75、行距 88.5、奇数行偏移 75
- 提供 footprint(1×1 / 2×2) 描述与"中心锚定"常量语义（图片中心=逻辑中心，透明 padding 承载装饰）
- 六边 tetraploid 锚点行奇偶两套占据格偏移表（偶数行 (0,0),(1,0),(−1,1),(0,1)；奇数行 (0,0),(1,0),(0,1),(1,1)）

### 交付物 2：tileset 程序化合成 `server/app/core/tileset_synth.py`（纯函数 bytes→bytes）

- 输入：两张 64×64 RGBA 无缝纹理（A=背景材质、B=前景材质）+ terrain_mode + 可选参数（seed、边缘羽化宽度）
- 输出：256×256 RGBA 图集（4×4 格，每格 64×64）
- 合成算法（路线 A，确定性）：
  - 每格由 4 角地形位插值出符号距离场 → 阈值化得前景 coverage mask；可加**带 seed 的 blob 噪声**让边缘有机（同 seed 同输出，测试可复现）
  - **dual**：mask 内 B、mask 外 A（过渡带有 1px 级 alpha 羽化可接受）
  - **foreground**：只保留 B 的 coverage 区域（mask 外 alpha=0，镂空图集）
  - **background**：反向镂空（mask 内 alpha=0，保留 A）
- **格位索引约定**：先查 `~/code/red-art-studio-refs/original-skills/`（tileset_template 相关）有无实证布局；查得到照抄并注明出处；查不到采用推断约定并在代码注释与 STATE.md 标注推断——`idx = tl + tr*2 + bl*4 + br*8`（tl/tr/bl/br 为四角地形位），格位置 `(col=idx%4, row=idx//4)`；idx=15（全前景）=纯 B，idx=0=纯 A（dual）/全透明（foreground）/全 A（background）
- 入口强校验（坑 6）：非 64×64 输入一律拒绝（ValueError 家族，映射 422），**禁止静默缩放**

### 交付物 3：API 路由（复用 P2 模式）

- `POST /api/v1/tilesets`：multipart 两个纹理文件（`background` / `foreground`）+ `payload` JSON 串（terrain_mode: dual|foreground|background，可选 seed/feather）→ 202 {job_id}
- `GET /api/v1/tilesets/{job_id}`：复用 image-edits 的状态响应结构
- 上传防线照抄 P2：Content-Type/魔数双白名单（png/jpeg/webp）、20MB 上限 413、payload pydantic 校验 422（extra=forbid；wire 嵌套形态用 mode=before 验证器拍平——P2 冒烟实证过这里的漏网 bug 模式）
- 执行器：确定性纯函数，沿用 `ImageEditJobRunner` 同款线程池模式

### 硬性约束

- 禁改 `generations/artifacts/providers/ui` 现有行为；禁新增依赖（pillow 已有）；禁本地模型推理
- 测试夹具一律 Pillow 代码生成；**禁止用 Read 工具读任何二进制文件**
- 中文注释、英文标识符；公开函数 type hints；原子写 job/产物

### 验收标准（全过才算完成，逐条给证据）

1. 全量 pytest 绿（新增：几何模块逐值断言 §2.5 全部数字；合成器三模式格位断言——dual 模式 idx=0 格==A 像素、idx=15 格==B 像素、过渡格同时含 A/B 色且四角 coverage 位形正确；foreground/background 镂空方向断言；非 64×64 输入拒绝；同 seed 确定性复现；路由 4xx 分支）
2. `uv run ruff check .` 通过
3. 铁律 grep：无 torch/diffusers/transformers/权重下载痕迹
4. 8600 真实冒烟：Pillow 生成两张 64×64 测试纹理 → curl POST → 轮询 succeeded → 取回产物 → Pillow 断言 256×256 + idx0/idx15/过渡格 → 证据（数值）落 STATE.md
5. ROADMAP 增加 P3 章节（L1 勾选、L2/后移项标注）；STATE.md 迭代日志（含格位索引约定出处/推断说明）
6. `git diff --stat` 自查未触碰禁改文件；commit：`feat(P3-L1): dual-grid tileset 程序化合成 + 地图几何契约模块`

## L2：无缝纹理生成线（本轮委派，2026-09-12 下发）

### 背景（先读）

- 调研文档：`docs/features/05-maps-tiles-and-textures.md` §3.1（无缝纹理路线判断）、§4（流水线）、§5（坑 6）
- P2 已交付 `server/app/core/processors.py`（self_loop、相邻跳变探针思路）；P3-L1 已交付 `server/app/core/map_layout.py`（等距常量 128×64 / (±64,32)）
- Provider 层已就绪：`server/app/providers/pollinations.py`（已显式 trust_env=False 防本机代理劫持；免 key；仅 n=1；实测延迟 6-45s，provider_timeout_seconds 默认 120）
- 通道实况（2026-09-12 复测）：pollinations 直连 HTTP 200 / 3.8s 出 64×64 真图；⚠️ shell 代理环境变量会让流量超时——provider 已处理，冒烟时勿给服务进程注入 HTTP(S)_PROXY
- P1 的 generation 路由 + JobRunner 模式是本路由的模板；CQRS/目录约定见 CLAUDE.md

### 交付物 1：纹理流水线 `server/app/core/texture_pipeline.py`（纯函数 bytes→bytes，provider 无关）

按序四段，每段可独立测试：

1. **归一化 normalize**：任意尺寸输入 → **最近邻（NEAREST）**降采样到 64×64，禁止平滑采样（像素纪律）；可选 `quantize` 调色板量化（默认关）
2. **自环绕 self_loop**：复用 P2 `processors.self_loop`，不重写
3. **平铺预览 tiling_preview**：64×64 → 3×3 平铺 192×192（原版输出清单实证带 tiling_preview_path；3×3 为推断约定，代码注释与 STATE.md 标注）
4. **检缝报告 seam_report**：对 3×3 平铺图做相邻列/行最大通道跳变全图扫描（复用 P2 _max_neighbor_step 思路），阈值 <6（与 P2 一致）；报告 {horizontal_max_step, vertical_max_step, passed}。检缝不过不判 failed——指标如实进产物，是否重生成由用户决定

### 交付物 2：等距投影 `isometric_project`（texture_pipeline 内）

- 64×64 平面无缝纹理 → 确定性 2:1 仿射投影 → **128×64** RGBA 等距材质瓦片（尺寸/偏移一律用 map_layout 常量，禁写魔数）
- 菱形区域覆盖纹理内容、四角透明；相邻瓦片按 (±64,32) 中心偏移可无缝拼接
- 投影方式按调研文档 §3.1 判断：选确定性后投影（先平面生成后变换），标注推断

### 交付物 3：API 路由 + 执行器

- `POST /api/v1/textures`：JSON body {prompt: str(1..2000), quantize?: bool=false, isometric?: bool=false}，pydantic extra=forbid → 202 {job_id}（响应结构照抄 P1 generation）
- `GET /api/v1/textures/{job_id}`：状态响应复用 P1/P2 结构
- 执行器 `TextureJobRunner`：factory.get() → generate_image(size="1024x1024") → normalize → self_loop → tiling_preview + seam_report（isometric=true 追加投影）→ 原子写产物
- 产物契约 final_outputs.json：texture（64×64 正图）、tiling_preview、seam_report 数值、isometric_texture（可选）；PNG 落 artifacts/<job_id>/
- Provider 故障映射照 P1：4xx→failed 不可重试、5xx/超时→可重试分支

### 硬性约束

- 禁改 `providers/`（pollinations 已就绪；发现缺陷记 STATE.md 待定决策，不自行改）；禁改 `map_layout.py` / `tileset_synth.py` / `processors.py`（只读复用）；禁改 `generations/artifacts/ui` 现有行为
- 测试夹具一律 Pillow 代码生成 + fake provider（或 httpx MockTransport）；**永不真实调用外部服务**；禁止用 Read 工具读任何二进制文件
- prompt 只透传，服务端不做提示词改写/增强（确定性归服务层，生成语义归 provider）
- 中文注释、英文标识符；公开函数 type hints；原子写 job/产物；日志 logging 禁 print

### 验收标准（全过才算完成，逐条给证据）

1. 全量 pytest 绿（新增：四段流水线逐段断言——非方形输入 NEAREST 降采样正确、quantize 开关生效、self_loop 后 3×3 平铺扫描 <6、seam_report 数值与独立计算一致、isometric 输出 128×64 + 四角透明 + 中心对齐；路由分支：空 prompt 422、extra 字段 422、未知 job 404、provider 4xx→failed、5xx→重试后 succeeded（fake provider 脚本化失败）；final_outputs.json 契约断言）
2. `uv run ruff check .` 通过
3. 铁律 grep 无新增命中
4. 8600 真实冒烟（provider=pollinations 真通道）：curl POST {"prompt":"seamless pixel art grass texture, top down"} → 202 → 轮询 succeeded（延迟 6-60s 正常）→ 取回产物断言：正图 64×64、tiling_preview 192×192、seam_report 有数值；isometric=true 再跑一发 → 128×64 断言。证据（数值+尺寸）落 STATE.md。pollinations 偶发 5xx/超时可重试 2 次；若仍不可用，如实记录停止（禁伪造），标注环境阻塞待复测
5. ROADMAP P3-L2 勾选带证据；STATE.md 迭代日志（含 3×3 推断标注、投影方式选择说明）
6. `git diff --stat` 自查禁改文件零触碰；commit：`feat(P3-L2): 无缝纹理生成线（降采样/self_loop/平铺检缝/等距投影）`
