# red-art-studio

自部署 AI 游戏素材服务，仿照原版的主要功能（不追求复刻其内部逻辑）。

## 产品定位（2026-09-06 已确认）

- **形态**：本地部署的 B/S 服务——后台 + Web 前端。**不是 TUI，不做 CLI 命令面**
- **技术栈**：全 Python（FastAPI 后台 + Web 前端 + 推理调用）
- **推理方式**：可配置的模型 API（adapter 模式），不绑定本地 GPU；ComfyUI 本地引擎作为可选后端之一（见下）
- **范围**：仿主要功能，不需要与原版的实现逻辑/参数契约对齐；open-questions 台账降级为"参考性调研记录"，不再作为对齐目标
- **优先级**：美术素材生成最优先（文生图/后处理/纹理瓦片/UI/动画/风格一致性），音频、策划 Agent 滞后

## Quickstart（P1：文生图竖切）

要求：Python 3.11+，[uv](https://docs.astral.sh/uv/)。P1 当前仅 API（Web 前端 P2 前补）。

```bash
# 1. 安装依赖
uv sync

# 2. 配置推理后端（复制示例后按需修改；不建 .env 也有内置默认值）
cp .env.example .env
#   → 免 key 快速体验：PROVIDER=pollinations（pollinations.ai 直连，无需任何配置）
#   → 用本地 ComfyUI：PROVIDER=comfyui，PROVIDER_BASE_URL=http://127.0.0.1:8188
#   → 用 OpenAI 兼容端点：PROVIDER=openai_compat，填 PROVIDER_BASE_URL / PROVIDER_API_KEY / PROVIDER_MODEL

# 3. 启动服务（端口 8600，避开 ComfyUI 8188）
uv run uvicorn server.app.main:app --port 8600

# 4. 提交生成任务（异步：202 受理，后台执行）
curl -s -X POST http://127.0.0.1:8600/api/v1/generations \
  -H 'Content-Type: application/json' \
  -d '{"prompt": "pixel art sword icon, 16-bit retro style", "size": "1024x1024", "n": 1}'
# → {"job_id": "...", "status": "pending"}

# 5. 轮询任务状态（succeeded 时 outputs 附产物清单）
curl -s http://127.0.0.1:8600/api/v1/generations/<job_id>

# 产物落盘在 data/artifacts/<job_id>/（图片 + final_outputs.json），
# 任务记录在 data/jobs/<job_id>/job.json

# 测试与 lint（测试全 mock，永不真实调用外部服务）
uv run pytest
uv run ruff check .
```

交互式 API 文档：服务启动后访问 `http://127.0.0.1:8600/docs`（OpenAPI schema 在 `/openapi.json`）。

## 架构（P1 定稿）

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
