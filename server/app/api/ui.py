"""Web UI 路由：`GET /` 返回工作台骨架，静态资源走显式清单白名单。

形态（2026-09-20 架构定稿）：HTML 骨架 + /static/app.css + /static/app.js
（vanilla，无框架无构建步骤，无外部 CDN，离线可用）。样式/交互的唯一来源：
docs/dev/web-ui-style-spec.md（v2，meowa.ai 生产 CSS 2026-09-20 实测复核）。
ui.py 只保留三件事：HTML 骨架模板、{provider_options} 服务端注入、静态资源
清单白名单路由（沿用 fonts 白名单防线语义：清单直读，穿越/清单外一律 404，
不用 StaticFiles 挂目录）。
"""

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse, HTMLResponse

router = APIRouter(tags=["ui"])

_STATIC_DIR = Path(__file__).resolve().parents[1] / "static"

# 静态资源清单：相对路径 → (磁盘路径, Content-Type)。唯一放行通道——清单外
# 文件名（含 ../ 穿越、反斜杠、URL 编码形态，解码后都不过是字符串查表）一律 404。
_STATIC_FILES: dict[str, tuple[Path, str]] = {
    "app.css": (_STATIC_DIR / "app.css", "text/css; charset=utf-8"),
    "app.js": (_STATIC_DIR / "app.js", "text/javascript; charset=utf-8"),
    "fonts/manrope-latin.woff2": (
        _STATIC_DIR / "fonts" / "manrope-latin.woff2",
        "font/woff2",
    ),
    "fonts/ibm-plex-mono-600-latin.woff2": (
        _STATIC_DIR / "fonts" / "ibm-plex-mono-600-latin.woff2",
        "font/woff2",
    ),
}

# 与 core.config.ProviderName 取值域一致（此处为展示层副本，改取值域需同步）
_PROVIDER_LABELS = {
    "openai_compat": "openai_compat（OpenAI 兼容端点）",
    "comfyui": "comfyui（本地 ComfyUI）",
    "pollinations": "pollinations（免 key 直连）",
}
_DEFAULT_PROVIDER = "pollinations"

# HTML 骨架：只承载结构（五模式导航 / 画布区 / 可拖拽设置面板），全部表单
# 字段由 app.js 的路由表配置驱动生成；视觉 token 全在 app.css。
_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#ffffff">
<title>red-art-studio — AI 游戏素材工作台</title>
<link rel="stylesheet" href="/static/app.css">
</head>
<body class="ambient-page">
<header class="app-header">
  <h1>red-art-studio</h1>
  <p class="subtitle">自部署 AI 游戏素材工作台</p>
  <span class="spacer"></span>
  <span id="conn-status" class="status-dot">就绪</span>
  <button id="theme-toggle" class="theme-toggle" type="button"
          aria-label="切换亮暗主题" title="切换亮暗主题">◐</button>
</header>
<nav id="mode-nav" class="mode-nav" aria-label="能力模式">
  <button class="mode-tab is-active" type="button" data-group="image">图像生成</button>
  <button class="mode-tab" type="button" data-group="process">图像处理</button>
  <button class="mode-tab" type="button" data-group="texture">纹理与瓦片</button>
  <button class="mode-tab" type="button" data-group="ui">UI 聚合表</button>
  <button class="mode-tab" type="button" data-group="anim">精灵动画</button>
</nav>
<main class="workspace">
  <div class="canvas-wrap">
    <section class="canvas-area" aria-label="画布预览">
      <div id="history-strip" class="history-strip" aria-label="本次会话任务"></div>
      <div id="canvas-empty" class="canvas-empty">
        <p class="canvas-empty-title">画布还是空的</p>
        <p class="canvas-empty-hint">在右侧控制面板填写参数并提交，产物与报告在此呈现</p>
      </div>
      <div id="result" class="result-stack"></div>
      <div class="canvas-toolbar" aria-label="画布工具（装饰占位）">
        <span class="tool-pill">100%</span>
        <button class="tool-btn" type="button" disabled
                title="画布缩放：视觉占位，功能待后续轮">⤢</button>
        <button class="tool-btn" type="button" disabled
                title="图层列表：视觉占位，功能待后续轮">▤</button>
      </div>
    </section>
  </div>
  <div id="settings-shell" class="settings-shell">
    <button id="panel-collapse" class="theme-toggle" type="button" hidden
            aria-label="收起/展开控制面板" title="收起/展开"><span class="glyph">›</span></button>
    <aside id="settings-panel" class="settings-panel" aria-label="任务参数"></aside>
    <button class="workspace-resize-handle" id="resize-handle" type="button"
            aria-label="拖拽调整面板宽度"></button>
  </div>
</main>
<script id="bootstrap-data" type="application/json">{bootstrap_data}</script>
<script src="/static/app.js" defer></script>
</body>
</html>
"""


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index() -> HTMLResponse:
    """渲染工作台骨架：Provider 取值域按服务端配置注入，默认选中 pollinations。"""
    bootstrap = {
        "provider_options": [
            {"value": name, "label": label, "selected": name == _DEFAULT_PROVIDER}
            for name, label in _PROVIDER_LABELS.items()
        ],
    }
    return HTMLResponse(
        _PAGE_TEMPLATE.replace(
            "{bootstrap_data}", json.dumps(bootstrap, ensure_ascii=False)
        )
    )


@router.get("/static/{filepath:path}", include_in_schema=False)
async def get_static(filepath: str) -> FileResponse:
    """静态资源（css/js/字体）：清单直读，清单外（含穿越形态）一律 404。

    filepath 由 Starlette 完成一次 URL 解码，任何 `..`/反斜杠/编码穿越形态
    都不过是指纹查表——命中清单才放行，语义与原 fonts 白名单路由一致。
    """
    entry = _STATIC_FILES.get(filepath)
    if entry is None or not entry[0].is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"静态资源不存在：{filepath}",
        )
    path, media_type = entry
    return FileResponse(path, media_type=media_type)
