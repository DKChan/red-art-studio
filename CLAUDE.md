# red-art-studio — AI 游戏素材工作台（自部署软件）

## 项目定位（铁律，违反即返工）

1. **自部署的是软件本身，不是模型**。所有模型推理一律通过外部 HTTP API 接入：
   - 远程云 API（OpenAI images 兼容协议等）
   - 本地推理服务（如 ComfyUI API，`http://127.0.0.1:8188`）
   - 代码仓库内**禁止**出现模型权重下载、加载、本地推理代码（torch/diffusers/transformers 直接调用、snapshot-download 等）
2. **最大化复用 ComfyUI 生态**：能用 ComfyUI workflow 解决的生成需求，优先走 ComfyUI API，不重复造轮子。Provider 适配层为此设计。
3. **确定性处理归服务层**：缩放、格式转换、元数据写入、拼接等确定性图像处理在服务层完成，不塞给 Provider。
4. **永远不做**：credit 计费、订阅档位、用户系统、多租户。单用户自用工具。
5. **形态是 B/S 服务**（FastAPI 后台 + Web 前端），不是 TUI，不做 CLI 命令面。
6. **范围是"仿主要功能"**，不与原版的实现逻辑/参数契约逐条对齐；docs/features 调研文档与 open-questions 台账是参考性资料，不是验收标准。

## 技术栈与命令

- Python 3.11+，uv 管理（`uv sync` / `uv run` / `uv add`）
- FastAPI + pydantic v2 + pydantic-settings + httpx（全异步）
- 任务存储：P1 用文件系统（`data/jobs/<job_id>/job.json`），接口留抽象，后续可换 SQLite
- 测试：pytest；外部 API 一律 mock（httpx MockTransport），测试永不真实调用外部服务

```bash
uv sync                                          # 安装依赖
uv run pytest                                    # 测试
uv run ruff check .                              # lint
uv run uvicorn server.app.main:app --port 8600   # 启动（服务端口 8600，避开 8188/20128）
```

## 目录约定

```
server/app/
├── main.py            # FastAPI 入口
├── api/               # HTTP 路由（薄，只做参数校验和调用下层）
├── core/              # 配置(pydantic-settings)、job 存储抽象
├── providers/         # 推理后端适配层（核心抽象）
│   ├── base.py        # Provider 协议
│   ├── openai_compat.py
│   └── comfyui.py
└── jobs/              # 任务状态机与执行器
data/
├── jobs/<job_id>/job.json          # 任务元数据（原子写）
└── artifacts/<job_id>/             # 产物：媒体文件 + final_outputs.json
docs/
├── features/01-08     # 功能调研（参数契约、选型，实现时的参考资料）
├── dev/STATE.md       # 开发状态（Loop Engineering，见下）
├── dev/decisions.md   # 架构决策记录
└── source-reference/  # 官方一手资料（已移出仓库：~/code/red-art-studio-refs/original-skills/，只读勿改）
```

## 曳光弹纪律（Loop Engineering）

开发按 `docs/dev/STATE.md` 的循环推进：

1. **会话开始必读**：`docs/dev/STATE.md`（当前位置、阻塞、决策）+ `ROADMAP.md` 对应 Loop 的验收标准
2. **每 Loop 有验收标准**：ROADMAP §P1 Loop 清单，全过才算完成，才进下一个 Loop
3. **会话结束必写**：更新 STATE.md 迭代日志（做了什么/验收结果/新决策/新阻塞），勾选 ROADMAP checkbox，git commit
4. **状态只存在于文件中**，不依赖任何会话记忆；新会话靠读文件接续
5. 无法自行决定的架构取舍：写入 STATE.md「待定决策」，继续能做的部分，不要阻塞

## 编码规范

- 公开函数/类必须有 type hints；所有契约（API schema、Provider 请求/响应、job.json/final_outputs.json）用 pydantic 模型定义
- 路由层禁止直接 import 具体 Provider，只依赖 `providers/base.py` 协议
- job.json / final_outputs.json 写入用原子写（临时文件 + rename）
- 日志用 logging，禁止 print
- 测试文件镜像源码结构放 `server/tests/`
- 中文注释/文档，英文标识符

## 关键文档

- 功能调研：`docs/features/01-08`（复刻对象参数契约、自部署选型——注意其中"本地 GPU 推理"章节已过时，以本文件铁律为准）
- 官方一手资料：`~/code/red-art-studio-refs/original-skills/skills/game-assets/`（原版 CLI 源码、`web_parameter_contract.json`）
- 疑问台账：`docs/open-questions/`
