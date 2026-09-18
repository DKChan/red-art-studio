"""任务与产物存储抽象（ADR-002）。

P1 用文件系统实现：data/jobs/<job_id>/job.json + data/artifacts/<job_id>/。
所有写入走原子写（临时文件 + os.replace）；接口留抽象，后续可换 SQLite。
"""

import logging
import os
import re
import uuid
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from server.app.jobs.models import FinalOutputs, JobRecord

logger = logging.getLogger(__name__)

# job_id 由 uuid4().hex 生成（32 位十六进制）；存储层据此防御路径注入
_JOB_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
# 产物文件名白名单字符集：字母/数字开头，仅含字母数字与 . _ -（拒绝 / \ 与 .. 前缀等穿越形态）；
# 真正的主防线是「必须出现在 final_outputs.json 清单中」，字符集校验是第一道廉价拦截
_FILENAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class JobStore(Protocol):
    """任务/产物持久化协议。

    同步方法：本地盘小文件，单用户场景下不引入异步 IO 复杂度（P5+ 需要时再议）。
    """

    def save_job(self, record: JobRecord) -> None:
        """写入/更新任务元数据（原子写）。"""
        ...

    def get_job(self, job_id: str) -> JobRecord | None:
        """按 id 取任务元数据；不存在或 id 非法返回 None。"""
        ...

    def save_artifact(self, job_id: str, filename: str, data: bytes) -> None:
        """写入单个产物文件（原子写）。"""
        ...

    def save_final_outputs(self, final: FinalOutputs) -> None:
        """写入 final_outputs.json（原子写）。"""
        ...

    def load_final_outputs(self, job_id: str) -> FinalOutputs | None:
        """按 id 取产物清单；不存在或 id 非法返回 None。"""
        ...

    def load_artifact(self, job_id: str, filename: str) -> bytes | None:
        """按清单白名单读取单个产物文件；job/final_outputs/文件任一缺失或名称非法返回 None。"""
        ...


class FileSystemJobStore:
    """文件系统实现：jobs/ 与 artifacts/ 两棵目录树，惰性建目录。"""

    def __init__(self, data_dir: Path) -> None:
        self._jobs_root = data_dir / "jobs"
        self._artifacts_root = data_dir / "artifacts"
        self._jobs_root.mkdir(parents=True, exist_ok=True)
        self._artifacts_root.mkdir(parents=True, exist_ok=True)

    def save_job(self, record: JobRecord) -> None:
        job_dir = self._checked_job_dir(self._jobs_root, record.job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(job_dir / "job.json", record)
        logger.debug("job.json 已写入 job_id=%s status=%s", record.job_id, record.status)

    def get_job(self, job_id: str) -> JobRecord | None:
        if not _JOB_ID_PATTERN.fullmatch(job_id):
            return None
        path = self._jobs_root / job_id / "job.json"
        if not path.is_file():
            return None
        return JobRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def save_artifact(self, job_id: str, filename: str, data: bytes) -> None:
        artifact_dir = self._checked_job_dir(self._artifacts_root, job_id)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_bytes(artifact_dir / filename, data)

    def save_final_outputs(self, final: FinalOutputs) -> None:
        artifact_dir = self._checked_job_dir(self._artifacts_root, final.job_id)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(artifact_dir / "final_outputs.json", final)

    def load_final_outputs(self, job_id: str) -> FinalOutputs | None:
        if not _JOB_ID_PATTERN.fullmatch(job_id):
            return None
        path = self._artifacts_root / job_id / "final_outputs.json"
        if not path.is_file():
            return None
        return FinalOutputs.model_validate_json(path.read_text(encoding="utf-8"))

    def load_artifact(self, job_id: str, filename: str) -> bytes | None:
        """产物读取三重防线：id 格式 → 文件名字符集 → final_outputs 清单白名单。"""
        if not _JOB_ID_PATTERN.fullmatch(job_id) or not _FILENAME_PATTERN.fullmatch(filename):
            return None
        final = self.load_final_outputs(job_id)
        if final is None or not any(o.filename == filename for o in final.outputs):
            # 不在清单里的名字一律拒绝（final_outputs.json 本身也是清单外名字，同样被拒）
            logger.warning(
                "产物请求被拒绝（不在清单或清单缺失）job_id=%s file=%s", job_id, filename
            )
            return None
        path = self._artifacts_root / job_id / filename
        if not path.is_file():
            return None
        return path.read_bytes()

    def _checked_job_dir(self, root: Path, job_id: str) -> Path:
        """写入路径的 job_id 必须合法（防御路径注入，内部调用恒过）。"""
        if not _JOB_ID_PATTERN.fullmatch(job_id):
            raise ValueError(f"非法 job_id：{job_id!r}")
        return root / job_id


def _atomic_write_json(path: Path, model: BaseModel) -> None:
    """临时文件 + rename 原子写（序列化由 pydantic 完成）。"""
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(model.model_dump_json(indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """临时文件 + rename 原子写（字节产物）。"""
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
