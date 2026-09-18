"""healthz 路由测试。"""

from fastapi.testclient import TestClient

from server.app.main import app

client = TestClient(app)


def test_healthz_returns_200_ok() -> None:
    """`GET /healthz` 返回 200 与固定契约。"""
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
