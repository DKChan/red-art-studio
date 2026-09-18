# P2 Loop：后处理三件套（去背 / 像素化 / 无缝循环）

> 本文档是本 Loop 的唯一任务书。开工前先读：CLAUDE.md（铁律+规范）、docs/dev/STATE.md（当前位置）、docs/features/04-pixel-art-processing.md（能力语义与官方参数契约——注意其中"rembg/BiRefNet 本地模型"选型章节已被 ADR-001 判死，仅参数契约与产品语义有效）。

## 目标

在 P1 竖切骨架上横向扩展第一条后处理能力线：对**已有图片**做三种确定性视觉变换，复用现有 Job 状态机 / JobStore / final_outputs 产物契约 / 202+轮询 API 模式。

**P2 三件套全部为服务层纯算法实现（确定性图像处理，铁律 #3），不引入任何模型推理、不新增任何 Provider。**

## API 契约（定稿，不得随意更改命名）

```
POST /api/v1/image-edits
  multipart/form-data:
    - file: 上传的图片文件（必填，PNG/JPEG/WebP）
    - payload: JSON 字符串（pydantic 校验）:
        {
          "operation": "pixelate" | "remove_background" | "self_loop",
          "params": { ...按 operation 见下 }
        }
→ 202 {"job_id": "...", "status": "pending"}   （复用现有 job 语义）

GET /api/v1/image-edits/{job_id}
→ 200 状态响应（复用 generations 的状态模型结构：status/outputs/error）

产物：data/artifacts/<job_id>/ 输出图片 + final_outputs.json（现有契约复用）
```

### 各 operation 参数与语义

| operation | params | 语义 |
|---|---|---|
| `pixelate` | `pixel_size?: int`（4-64；缺省=自动估计） | NEAREST 降采样→调色板量化（≤32 色）→alpha 二值化（α≥128→255 否则 0，完美像素）；自动档用块周期/梯度能量估计（参考调研文档 04 §3.2），失败回落 16 |
| `remove_background` | `source_background_color?: "#RRGGBB"`（缺省=四角扫描取众数底色）、`tolerance?: int`（色距阈值 0-255，缺省 32） | 色键抠像：底色距离→alpha 渐变→pixel 模式 alpha 二值化；**不做模型分割**。mode=hd 不实现（铁律），收到也不需要该参数 |
| `self_loop` | `direction: "horizontal" \| "vertical" \| "four_way"` | offset-warp（roll 半宽/半高）+ 接缝带镜像融合；four_way = 先水平后垂直串行；输出尺寸不变 |

三件套产品语义（继承原版，调研文档 04 §1/§5）：
- **先像素化、后去背**是推荐顺序——在 API 文档字符串里注明，不强制。
- 处理是 CPU 秒级同步操作：执行器直接在线程池里跑完落盘，不需要真轮询外部服务；状态机 pending→running→succeeded/failed 语义不变。

## 约束（违反即返工）

1. 铁律全适用：无模型加载/权重下载/本地推理代码；无 credit/用户系统；B/S 不做 CLI。
2. 依赖只允许新增 `pillow`（uv add pillow）。**禁止** rembg、onnxruntime、pymatting、torch 及任何含模型权重的包。
3. 只动这些文件：`server/app/api/`（新增 image_edits 路由 + main.py 挂路由）、`server/app/core/`（新增 processors 实现）、`server/app/jobs/`（如需扩展执行器分派）、`server/tests/`（镜像新增测试）、`pyproject.toml`（pillow）、docs（STATE/ROADMAP）。**不改** generations.py / artifacts.py / providers/ / ui.py 的现有行为。
4. 复用 JobStore 与任务状态模型，不复制状态机代码；产物写入沿用原子写。
5. 处理函数为纯函数（bytes+params → bytes），与 FastAPI/存储解耦，便于测试。
6. 上传文件校验：Content-Type/魔数白名单（png/jpeg/webp），大小上限（默认 20MB，可配），非法 422。
7. 编码规范按 CLAUDE.md：type hints、pydantic 契约、logging、中文注释、测试镜像结构。
8. **不要用 Read 工具读取 .png/.woff2 等二进制文件**（会触发上游 vision 请求导致会话中断）；测试夹具一律用 Pillow 代码生成。

## 验收标准（可执行，全过才算完成）

1. `uv run pytest` 全绿（现有 63 项 + 新增；新增覆盖：三 operation 各自成功路径、自动像素尺寸估计、四角扫描底色、four_way 串行、非法 operation/文件魔数/超大文件 4xx、产物 final_outputs.json 校验）
2. `uv run ruff check .` 通过
3. 铁律 grep：全仓库无 `torch|diffusers|transformers|from_pretrained|snapshot_download|rembg|onnx` 痕迹
4. 真实服务冒烟（脚本或 curl 流程，写进 STATE.md 证据）：
   - 启动 8600 → 用 Pillow 脚本生成合成图（纯红底 + 蓝色块的 64×64 PNG）→ 上传 remove_background → 轮询 succeeded → 取回产物，断言：角点 alpha==0、蓝色块中心 alpha==255
   - 256×256 平滑渐变+几何图形图 → pixelate → 产物同块内像素一致（量化生效）且唯一色数 ≤32
   - 水平渐变图 → self_loop horizontal → 产物尺寸不变、列 0 与列 w-1 的平均绝对差 < 2（roll 保证边界连续性）
5. ROADMAP：阶段总览 P2 改 ✅，追加 P2 Loop 清单小节（含勾选）
6. STATE.md：迭代日志新增本条（做了什么/验收证据/限制说明——去背仅色键、模型分割路线被 ADR-001 排除），git commit（feat(P2): 后处理三件套）
7. 变更面自查：`git diff --stat` 确认未触碰约束 3 列出的禁改文件
