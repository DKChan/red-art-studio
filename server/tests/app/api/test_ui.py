"""Web UI 端点契约测试：GET / 返回原版暗主题复刻页（样式规格 §4 验收特征）。"""

from pathlib import Path

from fastapi.testclient import TestClient

from server.app.core.config import Settings
from server.app.core.storage import FileSystemJobStore
from server.app.main import create_app


def _client(tmp_path: Path) -> TestClient:
    settings = Settings(_env_file=None, provider="pollinations", data_dir=tmp_path)
    app = create_app(settings=settings, store=FileSystemJobStore(settings.data_dir))
    app.state.runner = None
    return TestClient(app)


def test_index_returns_html_with_key_elements(tmp_path: Path) -> None:
    """200 + HTML；含表单、轮询脚本、产物端点引用、red-art-studio 标识、三个 Provider。"""
    client = _client(tmp_path)
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    html = resp.text

    # 页面标题与标题栏体现 red-art-studio
    assert "<title>red-art-studio" in html
    assert "red-art-studio" in html
    # 表单关键元素
    for element_id in ("gen-form", "prompt", "width", "height", "n", "provider", "submit-btn"):
        assert f'id="{element_id}"' in html, f"缺少表单元素：{element_id}"
    # Provider 下拉：三个取值域齐全，默认 pollinations
    assert '<option value="pollinations" selected>' in html
    for name in ("openai_compat", "comfyui"):
        assert f'value="{name}"' in html
    # 轮询脚本特征：2s setInterval + 状态查询 + 产物端点拼 URL
    assert "/api/v1/generations" in html
    assert "/api/v1/artifacts/" in html
    assert "setInterval" in html
    assert "2000" in html
    client.close()


def test_index_carries_dark_theme_signature(tmp_path: Path) -> None:
    """样式规格 §4.3 全部特征：暗主题 token / 玻璃面板 / 金环 / 光斑 / 408px 栏 / pixelated。"""
    client = _client(tmp_path)
    html = client.get("/").text

    assert 'data-theme="dark"' in html                      # 暗主题开关
    assert "--theme-canvas:#010120" in html                 # 深空蓝紫底色
    assert "backdrop-filter: blur(24px)" in html            # 玻璃面板模糊
    assert "grid-template-columns: minmax(0,1fr) 408px" in html  # 原版双栏骨架
    assert "image-rendering: pixelated" in html             # 像素风渲染
    assert '"Manrope"' in html and "Manrope" in html        # 正文/UI 字体
    assert "IBM Plex Mono" in html                          # 字段标签字体
    assert html.count('class="orb orb-') == 3               # 三个氛围光斑
    assert "glass-panel" in html                            # 玻璃面板类
    assert "gold-ring" in html                              # 金环内描边类
    client.close()


def test_index_has_no_external_resource_refs(tmp_path: Path) -> None:
    """离线铁律：无任何 http(s) 外链资源引用（src/href 均为本站相对路径）。"""
    client = _client(tmp_path)
    html = client.get("/").text
    assert 'src="http' not in html and 'href="http' not in html
    client.close()


def test_font_endpoint_serves_whitelisted_local_fonts(tmp_path: Path) -> None:
    """字体端点：白名单内返回 woff2 字节；白名单外（含穿越形态）一律 404。"""
    client = _client(tmp_path)

    resp = client.get("/static/fonts/manrope-latin.woff2")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "font/woff2"
    assert resp.content.startswith(b"wOF2")  # woff2 魔数，证明返回真实字体文件

    resp = client.get("/static/fonts/ibm-plex-mono-600-latin.woff2")
    assert resp.status_code == 200
    assert resp.content.startswith(b"wOF2")

    # 白名单外与路径注入一律 404
    for bad in ("../config.py", "..%2Fconfig.py", "nope.woff2", "manrope-latin.woff2%00.js"):
        assert client.get(f"/static/fonts/{bad}").status_code == 404, bad
    client.close()
