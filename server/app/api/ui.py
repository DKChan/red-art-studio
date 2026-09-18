"""Web UI 路由：`GET /` 返回单页生成界面（原版 studio 暗主题复刻）。

形态：单文件内嵌 HTML/CSS/JS（vanilla，无框架无构建步骤，无外部 CDN，离线可用）；
视觉规格唯一来源：docs/dev/web-ui-style-spec.md（原版官方 bundle 实测 token）。
交互闭环：提交 POST /api/v1/generations → 2s 轮询 GET 状态 →
succeeded 用产物端点 /api/v1/artifacts/... 展示图片，failed 显示错误。
字体：Manrope（可变字重 200-800）/ IBM Plex Mono 600，本地 woff2 经
/static/fonts/ 白名单路由提供（路由挂载而非 StaticFiles，收敛改动面在 UI 层）。
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse, HTMLResponse

router = APIRouter(tags=["ui"])

# 字体白名单：文件名 → 磁盘路径（server/app/static/fonts/，仓库内离线资源）
_FONT_DIR = Path(__file__).resolve().parents[1] / "static" / "fonts"
_FONT_FILES = {
    "manrope-latin.woff2": _FONT_DIR / "manrope-latin.woff2",
    "ibm-plex-mono-600-latin.woff2": _FONT_DIR / "ibm-plex-mono-600-latin.woff2",
}

# 单页模板：仅服务端拼入三个 Provider 选项，其余全部静态（避免 JS 里再写死一份取值域）。
# 样式为原版暗主题实测 token（规格 §1），关键特征：data-theme=dark、玻璃面板、
# 金环内描边、三光斑、右 408px 控制栏、pixelated 画布。
_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>red-art-studio — AI 游戏素材工作台</title>
<style>
  /* ===== 字体（离线铁律：本地 woff2，由本服务 /static/fonts/ 白名单路由提供）===== */
  @font-face {
    font-family: "Manrope";
    src: url("/static/fonts/manrope-latin.woff2") format("woff2");
    font-weight: 200 800;
    font-style: normal;
    font-display: swap;
  }
  @font-face {
    font-family: "IBM Plex Mono";
    src: url("/static/fonts/ibm-plex-mono-600-latin.woff2") format("woff2");
    font-weight: 600;
    font-style: normal;
    font-display: swap;
  }

  /* ===== 设计 token（原版 studio 暗主题实测值，规格 §1.1）===== */
  :root, [data-theme="dark"] {
    --theme-canvas:#010120;
    --theme-canvas-soft:#08082a;
    --theme-surface:#0e0e34d6;
    --theme-surface-strong:#0a0a28f5;
    --theme-surface-soft:#ffffff14;
    --theme-line:#ffffff1f;
    --theme-line-strong:#ffffff38;
    --theme-text-primary:#ffffff;
    --theme-text-secondary:#ffffffb8;
    --theme-text-muted:#ffffff75;
    --theme-header-bg:#010120b8;
    --theme-panel-bg:linear-gradient(180deg, #0f0f3aeb, #010120f5);
    --theme-panel-soft-bg:linear-gradient(180deg, #ffffff1a, #ffffff0f);
    --theme-panel-border:#ffffff1f;
    --theme-panel-hover:#ffffff14;
    --theme-panel-text:#ffffff;
    --theme-panel-text-muted:#ffffff75;
    --theme-input-bg:#ffffff14;
    --theme-input-text:#ffffff;
    --theme-input-border:#ffffff1f;
    --theme-action-bg:#ffffff;
    --theme-action-text:#010120;
    --theme-action-border:#ffffffe0;
    --theme-action-hover-bg:#d8d7ff;
    --theme-inline-action-text:#ffffff;
    --theme-inline-action-hover:#bdbbff;
    --theme-orb-one:#ef2cc129;
    --theme-orb-two:#bdbbff38;
    --theme-orb-three:#7dc4ff33;
    --theme-hero-gradient:linear-gradient(135deg, #020214 0%, #5656a9 36%,
        #ef2cc1 72%, #2c9cff 100%);
  }

  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--theme-canvas);
    color: var(--theme-text-primary);
    font: 14px/1.6 "Manrope", -apple-system, "PingFang SC", "Microsoft YaHei",
          sans-serif;
  }
  ::placeholder { color: var(--theme-text-muted); }

  /* ===== 氛围光斑（三个，fixed，内容层之下；规格 §1.3）===== */
  .orb {
    position: fixed;
    z-index: 0;
    border-radius: 50%;
    filter: blur(80px);
    pointer-events: none;
  }
  .orb-one {
    width: 560px; height: 560px;
    top: -140px; left: -100px;
    background: var(--theme-orb-one);
  }
  .orb-two {
    width: 460px; height: 460px;
    top: 32%; right: -160px;
    background: var(--theme-orb-two);
  }
  .orb-three {
    width: 520px; height: 520px;
    bottom: -180px; left: 34%;
    background: var(--theme-orb-three);
  }

  /* ===== 玻璃面板与金环内描边（原版标志性元素，规格 §1.3）===== */
  .glass-panel {
    background: linear-gradient(180deg, var(--theme-surface),
                                var(--theme-surface-strong));
    border: 1px solid var(--theme-line);
    backdrop-filter: blur(24px);
  }
  .gold-ring { box-shadow: inset 0 0 0 1px #bdbbff33, 0 16px 36px #0101201a; }

  /* ===== 头部 ===== */
  header {
    position: relative;
    z-index: 1;
    display: flex;
    align-items: baseline;
    flex-wrap: wrap;
    gap: 4px 14px;
    padding: 18px 24px;
    border-bottom: 1px solid var(--theme-line);
    background: var(--theme-header-bg);
    backdrop-filter: blur(24px);
  }
  header h1 {
    margin: 0;
    font-size: 20px;
    font-weight: 800;
    background: var(--theme-hero-gradient);
    -webkit-background-clip: text;
    background-clip: text;
    color: transparent;
  }
  header p { margin: 0; font-size: 13px; color: var(--theme-text-muted); }

  /* ===== 主体骨架：左画布 + 右 408px 停靠控制栏（规格 §2 骨架图）===== */
  .studio {
    position: relative;
    z-index: 1;
    display: grid;
    grid-template-columns: minmax(0,1fr) 408px;
    align-items: stretch;
    min-height: calc(100vh - 61px);
  }
  .canvas-wrap { display: flex; padding: 24px; }

  /* 左：画布/预览区（主面板，32px 大圆角 + 卡片投影，玻璃 + 金环） */
  .canvas-area {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px;
    border-radius: 32px;
    box-shadow: 0 24px 80px rgba(0,0,0,0.14);
  }
  .canvas-empty {
    width: min(640px, 100%);
    padding: 56px 32px;
    border: 1px dashed var(--theme-line-strong);
    border-radius: 12px;
    text-align: center;
    color: var(--theme-text-muted);
  }
  .canvas-empty-title {
    margin: 0 0 8px;
    font-size: 17px;
    font-weight: 700;
    color: var(--theme-text-secondary);
  }
  .canvas-empty-hint { margin: 0; font-size: 13px; }
  #result {
    display: flex;
    flex-wrap: wrap;
    gap: 16px;
    align-items: center;
    justify-content: center;
  }
  #result img {
    width: 100%;
    max-width: 640px;
    height: auto;
    border-radius: 12px;
    box-shadow: 0 22px 48px #02061757;
    image-rendering: pixelated;
  }

  /* 右：控制栏（408px 固定停靠，panel-bg 背景 + border-left，16px 内边距，纵向滚动） */
  .control-panel {
    position: sticky;
    top: 61px;
    height: calc(100vh - 61px);
    overflow-y: auto;
    padding: 16px;
    border-top: 0;
    border-right: 0;
    border-bottom: 0;
    border-left: 1px solid var(--theme-line);
    border-radius: 0;
    background: var(--theme-panel-bg);
  }
  .control-panel section + section {
    border-top: 1px solid var(--theme-line);
    margin-top: 16px;
    padding-top: 16px;
  }

  /* 字段标签：mono 字体 11px / 800（原版控制栏字段标签风格，规格 §2） */
  label {
    display: block;
    margin: 0 0 6px;
    font-family: "IBM Plex Mono", monospace;
    font-size: 11px;
    font-weight: 800;
    letter-spacing: .06em;
    color: var(--theme-text-secondary);
  }

  /* 输入框：半透明白玻璃（规格 §2） */
  textarea, input, select {
    width: 100%;
    min-height: 36px;
    padding: 8px 12px;
    background: var(--theme-input-bg);
    border: 1px solid var(--theme-input-border);
    border-radius: 10px;
    color: var(--theme-input-text);
    font: inherit;
    outline: none;
  }
  textarea { resize: vertical; min-height: 96px; }
  textarea:focus, input:focus, select:focus {
    border-color: var(--theme-inline-action-hover);
  }
  .row { display: flex; gap: 12px; }
  .row > div { flex: 1; }

  /* 生成按钮：is-generate 风格（规格 §2 二选一之蓝色方案，保持一致） */
  button.is-generate {
    width: 100%;
    min-height: 38px;
    padding: 10px 16px;
    background: #2563eb;
    border: 1px solid #2563eb;
    border-radius: 10px;
    color: #fff;
    font-family: inherit;
    font-size: 13px;
    font-weight: 800;
    letter-spacing: .02em;
    cursor: pointer;
  }
  button.is-generate:hover:not(:disabled) {
    background: #1d4ed8;
    border-color: #1d4ed8;
  }
  button.is-generate:disabled { opacity: .5; cursor: not-allowed; }

  #status { margin-top: 12px; font-size: 13px; color: var(--theme-text-secondary); }
  #error { margin-top: 8px; font-size: 13px; color: #f87171; white-space: pre-wrap; }

  /* 响应式（<1024px）：单栏，控制栏移到画布下方（规格 §2） */
  @media (max-width: 1023px) {
    .studio { grid-template-columns: 1fr; }
    .control-panel {
      position: static;
      height: auto;
      border-left: 0;
      border-top: 1px solid var(--theme-line);
    }
  }
</style>
</head>
<body>
<div class="orb orb-one"></div>
<div class="orb orb-two"></div>
<div class="orb orb-three"></div>
<header>
  <h1>red-art-studio</h1>
  <p>自部署 AI 游戏素材工作台 · 文生图</p>
</header>
<main class="studio">
  <div class="canvas-wrap">
    <section class="canvas-area glass-panel" aria-label="画布预览">
      <div id="canvas-empty" class="canvas-empty">
        <p class="canvas-empty-title">画布还是空的</p>
        <p class="canvas-empty-hint">在右侧控制栏填写提示词并点击「生成」，出图后在此预览</p>
      </div>
      <div id="result"></div>
    </section>
  </div>
  <form id="gen-form" class="control-panel glass-panel gold-ring">
    <section>
      <label for="prompt">提示词（prompt）</label>
      <textarea id="prompt" name="prompt" required
                placeholder="例：pixel art sword icon, 16-bit style"></textarea>
    </section>
    <section>
      <div class="row">
        <div>
          <label for="width">宽（px）</label>
          <input id="width" name="width" type="number" value="512" min="64" max="2048">
        </div>
        <div>
          <label for="height">高（px）</label>
          <input id="height" name="height" type="number" value="512" min="64" max="2048">
        </div>
        <div>
          <label for="n">张数</label>
          <input id="n" name="n" type="number" value="1" min="1" max="10">
        </div>
      </div>
    </section>
    <section>
      <label for="provider">推理后端</label>
      <select id="provider" name="provider">{provider_options}</select>
    </section>
    <section>
      <button id="submit-btn" class="is-generate" type="submit">生成</button>
      <div id="status" role="status"></div>
      <div id="error" role="alert"></div>
    </section>
  </form>
</main>
<script>
(function () {
  "use strict";
  var form = document.getElementById("gen-form");
  var statusEl = document.getElementById("status");
  var resultEl = document.getElementById("result");
  var errorEl = document.getElementById("error");
  var emptyEl = document.getElementById("canvas-empty");
  var submitBtn = document.getElementById("submit-btn");
  var pollTimer = null;

  function setBusy(busy) { submitBtn.disabled = busy; }

  function submitJob(event) {
    event.preventDefault();
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    resultEl.innerHTML = "";
    errorEl.textContent = "";
    statusEl.textContent = "提交中…";
    emptyEl.style.display = "none";
    var payload = {
      prompt: document.getElementById("prompt").value.trim(),
      size: document.getElementById("width").value + "x" + document.getElementById("height").value,
      n: parseInt(document.getElementById("n").value, 10),
      provider: document.getElementById("provider").value
    };
    setBusy(true);
    fetch("/api/v1/generations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    })
      .then(function (resp) {
        if (resp.status !== 202) { return resp.json().then(rejectWithStatus(resp)); }
        return resp.json();
      })
      .then(function (body) {
        statusEl.textContent = "任务已受理（job " + body.job_id.slice(0, 8) + "…），生成中…";
        pollTimer = setInterval(function () { pollStatus(body.job_id); }, 2000);
      })
      .catch(function (err) { fail(err.message); });
  }

  function rejectWithStatus(resp) {
    return function (detail) {
      throw new Error(detail && detail.detail ? String(detail.detail) : "HTTP " + resp.status);
    };
  }

  function pollStatus(jobId) {
    fetch("/api/v1/generations/" + jobId)
      .then(function (resp) {
        if (!resp.ok) { throw new Error("HTTP " + resp.status); }
        return resp.json();
      })
      .then(function (job) {
        if (job.status === "succeeded") {
          clearInterval(pollTimer); pollTimer = null;
          setBusy(false);
          statusEl.textContent = "生成完成，共 " + job.outputs.length + " 张";
          job.outputs.forEach(function (out) {
            var img = document.createElement("img");
            img.src = "/api/v1/artifacts/" + jobId + "/" + encodeURIComponent(out.filename);
            img.alt = out.filename;
            resultEl.appendChild(img);
          });
        } else if (job.status === "failed") {
          clearInterval(pollTimer); pollTimer = null;
          setBusy(false);
          fail("生成失败：" + (job.error || "未知错误"));
        } else {
          statusEl.textContent = "任务 " + (job.status === "running" ? "生成中…" : "排队中…");
        }
      })
      .catch(function (err) {
        clearInterval(pollTimer); pollTimer = null;
        setBusy(false);
        fail("轮询失败：" + err.message);
      });
  }

  function fail(message) {
    statusEl.textContent = "";
    errorEl.textContent = message;
    if (!resultEl.childElementCount) { emptyEl.style.display = ""; }
  }

  form.addEventListener("submit", submitJob);
})();
</script>
</body>
</html>
"""

# 与 core.config.ProviderName 取值域一致（此处为展示层副本，改取值域需同步）
_PROVIDER_LABELS = {
    "openai_compat": "openai_compat（OpenAI 兼容端点）",
    "comfyui": "comfyui（本地 ComfyUI）",
    "pollinations": "pollinations（免 key 直连）",
}


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index() -> HTMLResponse:
    """渲染生成页：Provider 下拉按服务端取值域生成，默认选中 pollinations。"""
    options = []
    for name, label in _PROVIDER_LABELS.items():
        selected = " selected" if name == "pollinations" else ""
        options.append(f'<option value="{name}"{selected}>{label}</option>')
    return HTMLResponse(_PAGE_TEMPLATE.replace("{provider_options}", "".join(options)))


@router.get("/static/fonts/{filename}", include_in_schema=False)
async def get_font(filename: str) -> FileResponse:
    """字体文件（font/woff2）：白名单直读，其余（含穿越形态）一律 404。"""
    path = _FONT_FILES.get(filename)
    if path is None or not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"字体不存在：{filename}",
        )
    return FileResponse(path, media_type="font/woff2")
