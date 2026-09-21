# red-art-studio

自部署 AI 游戏素材服务，仿照原版的主要功能（不追求复刻其内部逻辑）。

## 产品定位（2026-09-06 已确认）

- **形态**：本地部署的 B/S 服务——后台 + Web 前端。**不是 TUI，不做 CLI 命令面**
- **技术栈**：全 Python（FastAPI 后台 + Web 前端 + 推理调用）
- **推理方式**：可配置的模型 API（adapter 模式），不绑定本地 GPU；ComfyUI 本地引擎作为可选后端之一（见下）
- **范围**：仿主要功能，不需要与原版的实现逻辑/参数契约对齐；open-questions 台账降级为"参考性调研记录"，不再作为对齐目标
- **优先级**：美术素材生成最优先（文生图/后处理/纹理瓦片/UI/动画/风格一致性），音频、策划 Agent 滞后

## Quickstart

要求：Python 3.11+（uv 自动管理虚拟环境），[uv](https://docs.astral.sh/uv/)。
推理后端默认 pollinations（免 key 直连），**开箱即用**；要换后端见手动步骤第 2 步。

### 一键启动（推荐）

- **Windows**：双击 `start.bat`
- **Linux/macOS**：`./start.sh`

脚本做四件事：检查 uv → `uv sync` 装依赖 → 在 8600 端口启动服务（已在跑则跳过）→ 自动打开浏览器进工作台。服务跑在独立窗口/后台，关掉即停。

### 手动步骤

```bash
# 1. 安装依赖
uv sync

# 2.（可选）配置推理后端：复制示例后按需修改；不建 .env 也有内置默认值（pollinations 免 key）
cp .env.example .env
#   → 免 key 快速体验：PROVIDER=pollinations（pollinations.ai 直连，无需任何配置）
#   → 用本地 ComfyUI：PROVIDER=comfyui，PROVIDER_BASE_URL=http://127.0.0.1:8188
#   → 用 OpenAI 兼容端点：PROVIDER=openai_compat，填 PROVIDER_BASE_URL / PROVIDER_API_KEY / PROVIDER_MODEL

# 3. 启动服务（端口 8600，避开 ComfyUI 8188）
uv run uvicorn server.app.main:app --port 8600
```

### 使用

- **Web 工作台**：浏览器打开 `http://127.0.0.1:8600/`——八条能力线全部有界面（图像生成 / 图像处理 / 纹理与瓦片 / UI 聚合表 / 精灵动画），右上角可切亮暗主题，报告与组件数据在画布区如实呈现
- **API 调用**（示例：文生图；其余 7 条线见 `/docs`）：

```bash
curl -s -X POST http://127.0.0.1:8600/api/v1/generations \
  -H 'Content-Type: application/json' \
  -d '{"prompt": "pixel art sword icon, 16-bit retro style", "size": "1024x1024", "n": 1}'
# → {"job_id": "...", "status": "pending"}（异步受理，轮询见下）
curl -s http://127.0.0.1:8600/api/v1/generations/<job_id>
```

产物落盘在 `data/artifacts/<job_id>/`（图片 + final_outputs.json），任务记录在 `data/jobs/<job_id>/job.json`。
交互式 API 文档：`http://127.0.0.1:8600/docs`（OpenAPI schema 在 `/openapi.json`）。

```bash
# 测试与 lint（测试全 mock，永不真实调用外部服务）
uv run pytest
uv run ruff check .
```

## 架构（P1 定稿，Web 前端已补齐）

```
Web 前端（单页工作台：static/app.css + app.js，路由表驱动 8 条能力线表单）
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
               ├── comfyui.py       ──HTTP──▶ 本地 ComfyUI（/prompt /history /view）
               └── pollinations.py  ──HTTP──▶ pollinations.ai 免 key 直连（临时冒烟通道）
                  │
                  ▼
              data/jobs/<id>/job.json + data/artifacts/<id>/（媒体 + final_outputs.json）
```

原则：
1. 确定性图像处理（像素化/tileset 合成/UI 装箱重排/瓦片几何校验）统一在服务层 Python，不进生成后端的工作流图
2. 生成后端可插拔，换后端不动业务逻辑（切换只改 `.env` 的 `PROVIDER`）
3. ComfyUI 的独特价值：社区工作流生态可 import 当原型，调好后导出 workflow JSON 参数化入模板库
4. 模型推理一律通过外部 HTTP API（云 API 或本地推理服务如 ComfyUI）；本仓库不含模型权重下载/加载代码

## 目录

- `docs/original-features-overview.md` — 官方功能全景（87 CLI 命令、机制调研）
- `docs/features/01~08` — 分能力域技术调研文档（选型章节按"本地推理 + CLI"写，待按新定位批量修订）
- `docs/open-questions/` — 官方资料缺口台账（参考性，非对齐目标）
- `~/code/red-art-studio-refs/original-skills/` — 一手资料（官方 Skill 仓库：SKILL.md、CLI 源码、参数契约）
- `docs/dev/STATE.md` — 开发状态（Loop Engineering：会话开始必读，结束必写）
- `docs/dev/decisions.md` — 架构决策记录
- `ROADMAP.md` — 阶段规划与 Loop 清单（P1-P7）
