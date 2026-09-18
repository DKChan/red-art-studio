"""FileSystemJobStore 测试：读写回路、原子写落盘、非法 job_id 防御。"""

from pathlib import Path

import pytest

from server.app.core.storage import FileSystemJobStore
from server.app.jobs.models import FinalOutputs, JobRecord, OutputFile, utc_now_iso


def _record(job_id: str, status: str = "pending") -> JobRecord:
    from server.app.jobs.models import GenerationParams

    return JobRecord(
        job_id=job_id,
        status=status,  # type: ignore[arg-type]  # 测试直接指定中间态字符串
        params=GenerationParams(prompt="a cat", provider="openai_compat"),
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
    )


def _job_id(seed: str) -> str:
    """由种子生成合法 job_id（32 位十六进制）。"""
    return (seed * 32)[:32]


def test_job_roundtrip(tmp_path: Path) -> None:
    """job.json 写入后可原样读回（status 更新同路径覆盖）。"""
    store = FileSystemJobStore(tmp_path)
    record = _record(_job_id("a"))
    store.save_job(record)

    loaded = store.get_job(record.job_id)
    assert loaded is not None
    assert loaded.job_id == record.job_id
    assert loaded.status == "pending"
    assert loaded.params.prompt == "a cat"

    record.status = "succeeded"  # type: ignore[assignment]
    store.save_job(record)
    loaded = store.get_job(record.job_id)
    assert loaded is not None
    assert loaded.status == "succeeded"


def test_get_job_missing_or_invalid(tmp_path: Path) -> None:
    """不存在的 id / 非法 id（路径注入尝试）都返回 None 而不是抛错。"""
    store = FileSystemJobStore(tmp_path)
    assert store.get_job(_job_id("b")) is None
    assert store.get_job("../escape") is None
    assert store.get_job("") is None
    assert store.get_job("ZZZZ") is None


def test_artifacts_roundtrip(tmp_path: Path) -> None:
    """产物文件与 final_outputs.json 写入 artifacts/<job_id>/ 并可读回。"""
    store = FileSystemJobStore(tmp_path)
    job_id = _job_id("c")

    store.save_artifact(job_id, "000.png", b"\x89PNG-fake")
    final = FinalOutputs(
        job_id=job_id,
        created_at=utc_now_iso(),
        outputs=[OutputFile(filename="000.png", width=64, height=32, format="png")],
    )
    store.save_final_outputs(final)

    assert (tmp_path / "artifacts" / job_id / "000.png").read_bytes() == b"\x89PNG-fake"
    loaded = store.load_final_outputs(job_id)
    assert loaded is not None
    assert loaded.job_id == job_id
    assert loaded.outputs[0].model_dump() == {
        "filename": "000.png",
        "width": 64,
        "height": 32,
        "format": "png",
    }
    assert store.load_final_outputs(_job_id("d")) is None


def test_write_rejects_invalid_job_id(tmp_path: Path) -> None:
    """写入路径的非法 job_id 直接拒绝（防御路径注入）。"""
    store = FileSystemJobStore(tmp_path)
    with pytest.raises(ValueError, match="job_id"):
        store.save_artifact("../../etc", "x.png", b"data")


def test_atomic_write_leaves_no_tmp_files(tmp_path: Path) -> None:
    """原子写（临时文件 + rename）成功后目录里不残留临时文件。"""
    store = FileSystemJobStore(tmp_path)
    job_id = _job_id("e")
    store.save_job(_record(job_id))
    store.save_artifact(job_id, "000.png", b"data")

    job_files = sorted(p.name for p in (tmp_path / "jobs" / job_id).iterdir())
    artifact_files = sorted(p.name for p in (tmp_path / "artifacts" / job_id).iterdir())
    assert job_files == ["job.json"]
    assert artifact_files == ["000.png"]
