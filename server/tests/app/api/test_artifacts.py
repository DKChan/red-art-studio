"""artifacts API 契约测试（L6 验收）。

直接经 FileSystemJobStore 构造产物数据（真实文件系统、临时目录），
TestClient 驱动真实路由；覆盖 成功/404/清单外/路径穿越全形态。零真实外呼。
"""

from pathlib import Path

from fastapi.testclient import TestClient

from server.app.core.config import Settings
from server.app.core.storage import FileSystemJobStore
from server.app.jobs.models import (
    FinalOutputs,
    GenerationParams,
    JobRecord,
    OutputFile,
    utc_now_iso,
)
from server.app.main import create_app

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-png-body"
JPG_BYTES = b"\xff\xd8\xff\xe0fake-jpeg-body"

# 合法 job_id（32 位十六进制，与 uuid4().hex 同形态）
_JOB = "a" * 32
_UNKNOWN_JOB = "b" * 32


def _client_with_artifacts(tmp_path: Path) -> TestClient:
    """组装应用 + 预置一个 succeeded job（000.png / 001.jpg）与其 final_outputs.json。"""
    settings = Settings(_env_file=None, provider="pollinations", data_dir=tmp_path)
    store = FileSystemJobStore(settings.data_dir)
    store.save_job(
        JobRecord(
            job_id=_JOB,
            status="succeeded",
            params=GenerationParams(prompt="a pixel sword", size="512x512", n=2),
            created_at=utc_now_iso(),
            updated_at=utc_now_iso(),
        )
    )
    store.save_artifact(_JOB, "000.png", PNG_BYTES)
    store.save_artifact(_JOB, "001.jpg", JPG_BYTES)
    store.save_final_outputs(
        FinalOutputs(
            job_id=_JOB,
            created_at=utc_now_iso(),
            outputs=[
                OutputFile(filename="000.png", width=512, height=512, format="png"),
                OutputFile(filename="001.jpg", width=512, height=512, format="jpeg"),
            ],
        )
    )
    app = create_app(settings=settings, store=store)
    app.state.runner = None  # 本文件只测产物读取，不触发生成链路
    return TestClient(app)


def test_artifact_success_with_content_type(tmp_path: Path) -> None:
    """成功：返回图像字节 + 按 final_outputs.json 的 format 映射 Content-Type。"""
    client = _client_with_artifacts(tmp_path)
    resp = client.get(f"/api/v1/artifacts/{_JOB}/000.png")
    assert resp.status_code == 200
    assert resp.content == PNG_BYTES
    assert resp.headers["content-type"] == "image/png"

    resp = client.get(f"/api/v1/artifacts/{_JOB}/001.jpg")
    assert resp.status_code == 200
    assert resp.content == JPG_BYTES
    assert resp.headers["content-type"] == "image/jpeg"
    client.close()


def test_artifact_unknown_format_octet_stream(tmp_path: Path) -> None:
    """format=unknown（扩展名 .bin）→ application/octet-stream。"""
    settings = Settings(_env_file=None, provider="pollinations", data_dir=tmp_path)
    store = FileSystemJobStore(settings.data_dir)
    store.save_job(
        JobRecord(
            job_id=_JOB,
            status="succeeded",
            params=GenerationParams(prompt="x"),
            created_at=utc_now_iso(),
            updated_at=utc_now_iso(),
        )
    )
    store.save_artifact(_JOB, "000.bin", b"\x00\x01mystery")
    store.save_final_outputs(
        FinalOutputs(
            job_id=_JOB,
            created_at=utc_now_iso(),
            outputs=[OutputFile(filename="000.bin", width=0, height=0, format="unknown")],
        )
    )
    app = create_app(settings=settings, store=store)
    app.state.runner = None
    client = TestClient(app)
    resp = client.get(f"/api/v1/artifacts/{_JOB}/000.bin")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/octet-stream"
    client.close()


def test_artifact_job_not_found_404(tmp_path: Path) -> None:
    """job 不存在（合法格式但无记录）→ 404。"""
    client = _client_with_artifacts(tmp_path)
    resp = client.get(f"/api/v1/artifacts/{_UNKNOWN_JOB}/000.png")
    assert resp.status_code == 404
    client.close()


def test_artifact_filename_not_in_manifest_404(tmp_path: Path) -> None:
    """文件名不在 final_outputs.json 清单 → 404（即使文件恰好存在于磁盘也不放行）。"""
    client = _client_with_artifacts(tmp_path)
    # final_outputs.json 本身在清单里没有条目，读取它也必须 404
    assert client.get(f"/api/v1/artifacts/{_JOB}/final_outputs.json").status_code == 404
    # 清单外任意名字 → 404
    assert client.get(f"/api/v1/artifacts/{_JOB}/999.png").status_code == 404
    # 模拟磁盘上存在但清单未登记的文件（如写入中断遗留）→ 仍 404
    (tmp_path / "artifacts" / _JOB / "orphan.png").write_bytes(PNG_BYTES)
    assert client.get(f"/api/v1/artifacts/{_JOB}/orphan.png").status_code == 404
    client.close()


def test_artifact_job_not_succeeded_404(tmp_path: Path) -> None:
    """job 存在但产物未生成（无 final_outputs.json）→ 404。"""
    settings = Settings(_env_file=None, provider="pollinations", data_dir=tmp_path)
    store = FileSystemJobStore(settings.data_dir)
    store.save_job(
        JobRecord(
            job_id=_JOB,
            status="running",
            params=GenerationParams(prompt="x"),
            created_at=utc_now_iso(),
            updated_at=utc_now_iso(),
        )
    )
    app = create_app(settings=settings, store=store)
    app.state.runner = None
    client = TestClient(app)
    assert client.get(f"/api/v1/artifacts/{_JOB}/000.png").status_code == 404
    client.close()


def test_artifact_path_traversal_rejected(tmp_path: Path) -> None:
    """路径穿越攻击全形态：../、绝对路径、URL 编码变体 → 一律 404，绝不 500 或放行。"""
    client = _client_with_artifacts(tmp_path)
    attacks = [
        # 相对穿越（原样与 URL 编码 %2e%2e / %2E%2E）
        "../../etc/passwd",
        "..%2F..%2Fetc%2Fpasswd",
        "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
        "%2E%2E%2Fetc%2Fpasswd",
        # 绝对路径形态
        "/etc/passwd",
        "%2Fetc%2Fpasswd",
        # 反斜杠与混合形态
        "..\\..\\etc\\passwd",
        "..%5C..%5Cetc%5Cpasswd",
        # 穿越后仍指向清单内名字（双写防御：字符集过不了 ".." 前缀）
        "../a" + _JOB + "/000.png",
        # 点开头隐藏文件与空名
        ".",
        "..",
        "%2e",
    ]
    for attack in attacks:
        resp = client.get(f"/api/v1/artifacts/{_JOB}/{attack}")
        assert resp.status_code == 404, f"穿越形态未被拒绝：{attack!r} → {resp.status_code}"
    # job_id 位同样不接受非法形态
    assert client.get("/api/v1/artifacts/..%2F..%2Fetc/000.png").status_code in (404, 422)
    assert client.get("/api/v1/artifacts/zzz/000.png").status_code == 404
    client.close()
