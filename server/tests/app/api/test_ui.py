"""Web UI 端点契约测试：GET / 骨架 + /static/ 清单白名单（样式规格 v2 验收特征）。

骨架断言五模式导航 / 双主题开关 / 静态资源引用 / Provider 注入；静态资源断言
清单直读、穿越与清单外 404；CSS 断言 meowa.ai 实测 token（两套主题都在）；
铁律断言前端产物零商业词（credit/wallet 等，见 CLAUDE.md §4）。
"""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from server.app.core.config import Settings
from server.app.core.storage import FileSystemJobStore
from server.app.main import create_app

# 铁律商业词（任务书 §6.5）：前端产物零命中
_FORBIDDEN_WORDS = (
    "credit", "wallet", "balance", "subscribe", "subscription",
    "pricing", "payment", "login", "signup", "tenant",
)


def _client(tmp_path: Path) -> TestClient:
    settings = Settings(_env_file=None, provider="pollinations", data_dir=tmp_path)
    app = create_app(settings=settings, store=FileSystemJobStore(settings.data_dir))
    app.state.runner = None
    return TestClient(app)


def test_index_returns_skeleton_html(tmp_path: Path) -> None:
    """200 + HTML 骨架：标题、五模式导航入口、静态资源引用、Provider 注入。"""
    client = _client(tmp_path)
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    html = resp.text

    assert "<title>red-art-studio" in html
    assert "red-art-studio" in html
    # 五个模式导航入口（数据驱动组，JS 按组展开表单）
    for group in ("image", "process", "texture", "ui", "anim"):
        assert f'data-group="{group}"' in html, f"缺少模式入口：{group}"
    # 静态资源引用指向 /static/（拆分后的骨架不再内联样式与脚本）
    assert '<link rel="stylesheet" href="/static/app.css">' in html
    assert '<script src="/static/app.js" defer></script>' in html
    # Provider 注入：三个取值域齐全，默认 pollinations
    assert "pollinations" in html and "openai_compat" in html and "comfyui" in html
    bootstrap = json.loads(
        html.split('<script id="bootstrap-data" type="application/json">')[1]
        .split("</script>")[0]
    )
    selected = [o["value"] for o in bootstrap["provider_options"] if o["selected"]]
    assert selected == ["pollinations"]
    client.close()


def test_index_default_light_theme_signature(tmp_path: Path) -> None:
    """默认亮色：html data-theme="light" + theme-color #ffffff（官方亮色默认对齐）。"""
    client = _client(tmp_path)
    html = client.get("/").text
    assert 'data-theme="light"' in html
    assert '<meta name="theme-color" content="#ffffff">' in html
    client.close()


def test_css_carries_both_theme_token_sets(tmp_path: Path) -> None:
    """app.css：实测 token 两套齐全（亮 :root + 暗 [data-theme="dark"]）。

    断言项 = 任务书 §6.4 验收清单（lavender/magenta/panel-bg/settings-bg/
    gold-ring 淡紫内描边/blur24/收起缓动/section-label 字距）。
    """
    client = _client(tmp_path)
    resp = client.get("/static/app.css")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/css")
    css = resp.text

    assert ":root {" in css
    assert '[data-theme="dark"] {' in css
    for token in (
        "--lavender:#bdbbff",
        "--magenta:#ef2cc1",
        "--theme-canvas:#fff",
        "--theme-canvas:#010120",  # 暗色覆盖在同一文件内
        "--theme-panel-bg:",
        "--theme-settings-bg:",
        "--theme-hero-gradient:",
        "#bdbbff33",  # gold-ring 淡紫内描边（已非金色）
        "backdrop-filter: blur(24px)",
        "cubic-bezier(.22, 1, .36, 1)",  # 收起/展开缓动
        "letter-spacing: .055em",  # section-label 字距
        "image-rendering: pixelated",
        '"Manrope"',
        "IBM Plex Mono",
    ):
        assert token in css, f"缺少 token：{token}"
    client.close()


def test_css_carries_measured_layout_skeleton(tmp_path: Path) -> None:
    """app.css：工作区骨架特征（可拖拽手柄 / 收起宽度过渡 / pixelated / 三光斑）。"""
    client = _client(tmp_path)
    css = client.get("/static/app.css").text
    assert "workspace-resize-handle" in css
    assert "col-resize" in css
    assert "ambient-page::before" in css and "ambient-page::after" in css
    assert "radial-gradient" in css
    assert ".glass-panel" in css and ".gold-ring" in css
    assert "settings-bg" in css
    client.close()


def test_js_carries_route_table_for_all_lines(tmp_path: Path) -> None:
    """app.js：路由表覆盖 8 条能力线（含 ui_gen 双模式同前缀、animations/animate 双前缀）。"""
    client = _client(tmp_path)
    resp = client.get("/static/app.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]
    js = resp.text

    for path in (
        'submit: "/api/v1/generations"',
        'submit: "/api/v1/image-edits"',
        'submit: "/api/v1/textures"',
        'submit: "/api/v1/tilesets"',
        'submit: "/api/v1/ui_gen"',
        'submit: "/api/v1/ui_gen/extract"',
        'submit: "/api/v1/animations"',
        'submit: "/api/v1/animate"',
    ):
        assert path in js, f"路由表缺少提交端点：{path}"
    # 状态轮询与产物端点形态
    assert 'status: "/api/v1/ui_gen"' in js
    assert "/api/v1/artifacts/" in js
    assert "2000" in js  # 2s 轮询间隔
    client.close()


def test_index_has_no_external_resource_refs(tmp_path: Path) -> None:
    """离线铁律：HTML 无任何 http(s) 外链资源引用；CSS 无 url() 外链。"""
    client = _client(tmp_path)
    html = client.get("/").text
    assert 'src="http' not in html and 'href="http' not in html
    css = client.get("/static/app.css").text
    assert "url(http" not in css and 'url("http' not in css
    js = client.get("/static/app.js").text
    assert '"http://' not in js and '"https://' not in js
    client.close()


def test_ui_products_carry_no_forbidden_words(tmp_path: Path) -> None:
    """铁律 grep（任务书 §6.5）：HTML/CSS/JS 零商业词命中。"""
    client = _client(tmp_path)
    products = [
        client.get("/").text,
        client.get("/static/app.css").text,
        client.get("/static/app.js").text,
    ]
    for word in _FORBIDDEN_WORDS:
        for product in products:
            assert word not in product.lower(), f"前端产物出现铁律禁词：{word}"
    client.close()


def test_static_endpoint_serves_whitelisted_files(tmp_path: Path) -> None:
    """清单白名单路由：清单内 200（css/js/woff2 魔数），清单外与穿越一律 404。"""
    client = _client(tmp_path)

    resp = client.get("/static/app.css")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/css")

    resp = client.get("/static/app.js")
    assert resp.status_code == 200

    resp = client.get("/static/fonts/manrope-latin.woff2")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "font/woff2"
    assert resp.content.startswith(b"wOF2")  # woff2 魔数

    resp = client.get("/static/fonts/ibm-plex-mono-600-latin.woff2")
    assert resp.status_code == 200
    assert resp.content.startswith(b"wOF2")

    # 清单外与路径注入（编码穿越/截断形态）一律 404。
    # 注：裸 `../` 形态被 httpx 客户端按 RFC 3986 规范化后才会发出（到不了服务端），
    # 服务端防线由编码形态（原样到达、路径参数解码后不过清单查表）验证。
    for bad in (
        "..%2Fconfig.py",
        "app.css%00.js",
        "nope.css",
        "fonts/nope.woff2",
        "%2e%2e/app.css",
        "fonts/%2e%2e/app.css",
    ):
        assert client.get(f"/static/{bad}").status_code == 404, bad
    client.close()
