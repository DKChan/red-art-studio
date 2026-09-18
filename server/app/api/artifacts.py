"""artifacts 路由：产物文件 HTTP 访问。

安全模型（CLAUDE.md 硬要求）：
- job_id / filename 的路径注入防御收敛在存储层（FileSystemJobStore.load_artifact）：
  id 格式校验 + 文件名字符集白名单 + final_outputs.json 清单白名单，任一不过返回 None → 404。
- URL 路径参数由 Starlette 解码后传入，`..` / 绝对路径 / 编码穿越（%2e%2e）落到
  load_artifact 时均无法通过字符集或清单校验，统一 404，不泄漏目录结构。

Content-Type 按 final_outputs.json 记录的 format 映射；unknown → application/octet-stream。
"""

from fastapi import APIRouter, HTTPException, Request, Response, status

from server.app.core.storage import JobStore

router = APIRouter(prefix="/api/v1/artifacts", tags=["artifacts"])

# final_outputs.json 的 format 字段 → HTTP Content-Type（契约取值域见 jobs/models.py OutputFile）
_CONTENT_TYPE_BY_FORMAT = {
    "png": "image/png",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
    "json": "application/json",  # P4-L1 UI 线 components.json（非图像产物）
}


def _store(request: Request) -> JobStore:
    """依赖获取（实例由 main.py lifespan 装配在 app.state，与 generations.py 同风格）。"""
    return request.app.state.store


@router.get(
    "/{job_id}/{filename}",
    responses={status.HTTP_404_NOT_FOUND: {"description": "job 不存在或产物不在清单"}},
)
async def get_artifact(job_id: str, filename: str, request: Request) -> Response:
    """返回单个产物文件字节；任何不可达（不存在/未生成/清单外/穿越形态）一律 404。"""
    data = _store(request).load_artifact(job_id, filename)
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"产物不存在：{job_id}/{filename}",
        )
    fmt = "unknown"
    final = _store(request).load_final_outputs(job_id)
    if final is not None:
        for output in final.outputs:
            if output.filename == filename:
                fmt = output.format
                break
    return Response(
        content=data,
        media_type=_CONTENT_TYPE_BY_FORMAT.get(fmt, "application/octet-stream"),
    )
