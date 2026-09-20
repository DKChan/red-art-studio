/* red-art-studio 工作台逻辑（vanilla，无框架无构建，离线可用）。
   架构（任务书 §5 定稿）：一张路由表 {mode: {submit, status, transport}}
   驱动全部模式——表单字段由 FIELDS 配置生成、提交按 transport 组包、轮询
   沿用 2s 间隔、产物经 /api/v1/artifacts 渲染；不做 8 份重复轮询代码。
   字段取值域的唯一真源是 server/app/jobs/models.py，本文件是其展示层投影，
   改契约需同步（不做运行时 schema 拉取，保持零后端改动）。
   铁律：前端只做展示与参数表单，不做图像处理；零外链资源。 */

(function () {
  "use strict";

  /* ---------- 启动数据（服务端注入） ---------- */
  var BOOTSTRAP = {};
  try {
    BOOTSTRAP = JSON.parse(
      document.getElementById("bootstrap-data").textContent || "{}"
    );
  } catch (err) { BOOTSTRAP = {}; }

  /* ---------- 路由表：submit/status 前缀每线不同（ui_gen 双模式同前缀、
     animations 与 animate 是两个前缀），逐线显式声明 ---------- */
  var ROUTES = {
    generation: { submit: "/api/v1/generations", status: "/api/v1/generations", transport: "json" },
    image_edit: { submit: "/api/v1/image-edits", status: "/api/v1/image-edits", transport: "multipart" },
    texture: { submit: "/api/v1/textures", status: "/api/v1/textures", transport: "json" },
    tileset: { submit: "/api/v1/tilesets", status: "/api/v1/tilesets", transport: "multipart" },
    ui_gen: { submit: "/api/v1/ui_gen", status: "/api/v1/ui_gen", transport: "json" },
    ui_extract: { submit: "/api/v1/ui_gen/extract", status: "/api/v1/ui_gen", transport: "multipart" },
    anim_pack: { submit: "/api/v1/animations", status: "/api/v1/animations", transport: "multipart" },
    animate: { submit: "/api/v1/animate", status: "/api/v1/animate", transport: "json" }
  };

  /* ---------- 模式分组：五个导航入口，组内二级分段 ---------- */
  var MODE_GROUPS = [
    { id: "image", label: "图像生成", modes: ["generation"] },
    { id: "process", label: "图像处理", modes: ["image_edit"] },
    { id: "texture", label: "纹理与瓦片", modes: ["texture", "tileset"] },
    { id: "ui", label: "UI 聚合表", modes: ["ui_gen", "ui_extract"] },
    { id: "anim", label: "精灵动画", modes: ["anim_pack", "animate"] }
  ];

  var MODE_LABELS = {
    generation: "文生图",
    image_edit: "后处理三件套",
    texture: "无缝纹理",
    tileset: "tileset 合成",
    ui_gen: "UI 生成",
    ui_extract: "UI 提取重排",
    anim_pack: "帧序列打包",
    animate: "精灵动画生成"
  };

  /* ---------- 表单字段配置（取值域见 jobs/models.py）----------
     type: composer 提示词 | segmented 枚举 | number | toggle | swatches
     五枚举底色 | provider 服务端注入下拉 | files 上传（路由表定义张数域）
     showIf: 动态字段区（如 color_count 仅 pixel=true；后处理按 operation）。 */
  var FIELDS = {
    generation: [
      { key: "prompt", type: "composer", label: "提示词（prompt）", required: true,
        placeholder: "例：pixel art sword icon, 16-bit style" },
      { key: "width", type: "number", label: "宽（px）", min: 64, max: 2048, value: 512 },
      { key: "height", type: "number", label: "高（px）", min: 64, max: 2048, value: 512 },
      { key: "n", type: "number", label: "张数", min: 1, max: 10, value: 1 },
      { key: "provider", type: "provider", label: "推理后端" }
    ]
  };

  /* ---------- 全局状态 ---------- */
  var state = {
    group: "image",
    mode: "generation",
    values: {},        // 各模式当前表单值 {mode: {key: value}}
    files: {},         // 各模式已选文件 {mode: [File,...]}
    history: [],       // 会话内任务 {jobId, mode, status, data}
    pollTimer: null,
    busy: false
  };

  /* ---------- DOM 快捷 ---------- */
  function $(id) { return document.getElementById(id); }
  var els = {};

  function h(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        if (k === "class") node.className = attrs[k];
        else if (k === "text") node.textContent = attrs[k];
        else if (k.slice(0, 2) === "on") node.addEventListener(k.slice(2), attrs[k]);
        else node.setAttribute(k, attrs[k]);
      });
    }
    (children || []).forEach(function (c) { if (c) node.appendChild(c); });
    return node;
  }

  /* ---------- 主题（默认亮色，localStorage 记忆） ---------- */
  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", theme === "dark" ? "#010120" : "#ffffff");
    try { localStorage.setItem("ras-theme", theme); } catch (err) { /* 隐私模式忽略 */ }
  }
  function initTheme() {
    var saved = "light";
    try { saved = localStorage.getItem("ras-theme") || "light"; } catch (err) { /* 同上 */ }
    applyTheme(saved === "dark" ? "dark" : "light");
  }

  /* ---------- 状态点 ---------- */
  function setStatus(text, kind) {
    var dot = els.status;
    if (!dot) return;
    dot.textContent = text;
    dot.classList.remove("is-busy", "is-ok", "is-error");
    if (kind) dot.classList.add("is-" + kind);
  }

  /* ---------- 表单渲染（FIELDS 配置 → 面板 DOM） ---------- */
  function modeValues(mode) {
    if (!state.values[mode]) state.values[mode] = {};
    return state.values[mode];
  }

  function renderPanel() {
    var panel = els.panel;
    panel.innerHTML = "";
    var group = MODE_GROUPS.filter(function (g) { return g.id === state.group; })[0];
    if (!group) return;

    // 组内二级分段（>1 个模式时）
    if (group.modes.length > 1) {
      var bar = h("div", { class: "submode-bar" });
      var seg = h("div", { class: "segmented", role: "tablist" });
      group.modes.forEach(function (mode) {
        seg.appendChild(h("button", {
          type: "button",
          class: mode === state.mode ? "is-active" : "",
          text: MODE_LABELS[mode] || mode,
          onclick: function () { switchMode(mode); }
        }));
      });
      bar.appendChild(seg);
      panel.appendChild(bar);
    }

    var form = h("form", { id: "job-form", novalidate: "novalidate" });
    var fields = FIELDS[state.mode];
    if (!fields) {
      form.appendChild(h("div", {
        class: "panel-note",
        text: "该模式表单在下一段提交接入（本轮先交付视觉骨架与文生图线）。"
      }));
      panel.appendChild(form);
      return;
    }
    fields.forEach(function (spec) {
      if (spec.showIf && !spec.showIf(modeValues(state.mode))) return;
      form.appendChild(renderField(spec));
    });

    var section = h("section", null, [
      h("button", {
        id: "submit-btn", class: "primary-btn", type: "submit",
        text: state.busy ? "提交中…" : "生成"
      }),
      h("div", { id: "status-line", class: "status-line", role: "status" }),
      h("div", { id: "error-line", class: "error-line", role: "alert" })
    ]);
    form.appendChild(section);
    form.addEventListener("submit", onSubmit);
    panel.appendChild(form);
    refreshVisibility();
  }

  function renderField(spec) {
    var wrap = h("div", { class: "field", "data-field": spec.key });
    var vals = modeValues(state.mode);

    if (spec.type !== "toggle" && spec.label) {
      wrap.appendChild(h("label", { class: "field-label", text: spec.label }));
    }
    switch (spec.type) {
      case "composer":
        var ta = h("textarea", { class: "composer", rows: "3", placeholder: spec.placeholder || "" });
        ta.value = vals[spec.key] || "";
        ta.addEventListener("input", function () { vals[spec.key] = ta.value; });
        wrap.appendChild(ta);
        break;
      case "segmented": {
        var seg = h("div", { class: "segmented", role: "radiogroup" });
        var current = vals[spec.key] !== undefined ? vals[spec.key] : spec.value;
        vals[spec.key] = current;
        spec.options.forEach(function (opt) {
          seg.appendChild(h("button", {
            type: "button",
            class: vals[spec.key] === opt ? "is-active" : "",
            text: opt,
            onclick: function () {
              vals[spec.key] = opt;
              Array.prototype.forEach.call(seg.children, function (b) {
                b.classList.toggle("is-active", b.textContent === opt);
              });
              refreshVisibility();
            }
          }));
        });
        wrap.appendChild(seg);
        break;
      }
      case "number": {
        var input = h("input", { class: "input", type: "number", min: spec.min, max: spec.max });
        input.value = vals[spec.key] !== undefined ? vals[spec.key] : spec.value;
        input.addEventListener("input", function () { vals[spec.key] = input.value; });
        wrap.appendChild(input);
        break;
      }
      case "toggle": {
        var row = h("div", { class: "toggle-row" });
        if (spec.label) row.appendChild(h("span", { class: "field-label", text: spec.label }));
        var cb = h("input", { class: "toggle", type: "checkbox" });
        cb.checked = vals[spec.key] !== undefined ? !!vals[spec.key] : !!spec.value;
        cb.addEventListener("change", function () {
          vals[spec.key] = cb.checked;
          refreshVisibility();
        });
        row.appendChild(cb);
        wrap.appendChild(row);
        break;
      }
      case "swatches": {
        var rowSw = h("div", { class: "swatch-row", role: "radiogroup" });
        var cur = vals[spec.key] !== undefined ? vals[spec.key] : spec.value;
        vals[spec.key] = cur;
        spec.options.forEach(function (color) {
          var b = h("button", {
            type: "button", class: "swatch" + (vals[spec.key] === color ? " is-active" : ""),
            "aria-label": color, title: color
          });
          b.style.background = color;
          b.addEventListener("click", function () {
            vals[spec.key] = color;
            Array.prototype.forEach.call(rowSw.children, function (s) {
              s.classList.toggle("is-active", s.getAttribute("aria-label") === color);
            });
          });
          rowSw.appendChild(b);
        });
        wrap.appendChild(rowSw);
        if (spec.hint) wrap.appendChild(h("p", { class: "panel-note", text: spec.hint }));
        break;
      }
      case "provider": {
        var select = h("select", { class: "input" });
        var opts = (BOOTSTRAP.provider_options || []);
        opts.forEach(function (o) {
          var opt = h("option", { value: o.value, text: o.label });
          if (o.selected) opt.selected = true;
          select.appendChild(opt);
        });
        select.value = vals[spec.key] || (function () {
          var d = opts.filter(function (o) { return o.selected; })[0];
          return d ? d.value : "";
        })();
        select.addEventListener("change", function () { vals[spec.key] = select.value; });
        wrap.appendChild(select);
        break;
      }
      default:
        break;
    }
    return wrap;
  }

  /* showIf 依赖值变化后重算各字段可见性（color_count / 后处理参数区等） */
  function refreshVisibility() {
    var form = $("job-form");
    if (!form) return;
    var vals = modeValues(state.mode);
    (FIELDS[state.mode] || []).forEach(function (spec) {
      var node = form.querySelector('[data-field="' + spec.key + '"]');
      if (!node) return;
      var visible = !spec.showIf || spec.showIf(vals);
      node.style.display = visible ? "" : "none";
      if (!visible && spec.clearWhenHidden) vals[spec.key] = undefined;
    });
  }

  function switchMode(mode) {
    state.mode = mode;
    var group = MODE_GROUPS.filter(function (g) { return g.modes.indexOf(mode) >= 0; })[0];
    state.group = group ? group.id : state.group;
    Array.prototype.forEach.call(els.nav.querySelectorAll(".mode-tab"), function (b) {
      b.classList.toggle("is-active", b.getAttribute("data-group") === state.group);
    });
    renderPanel();
  }

  /* ---------- 提交与轮询（2s 间隔，四态可见反馈） ---------- */
  function setBusy(busy) {
    state.busy = busy;
    var btn = $("submit-btn");
    if (btn) { btn.disabled = busy; btn.textContent = busy ? "提交中…" : "生成"; }
    if (!busy && els.status) setStatus("就绪", null);
  }

  function formError(message) {
    var line = $("error-line");
    if (line) line.textContent = message || "";
    if (els.status) setStatus("出错", "error");
  }

  function collectValues() {
    var vals = modeValues(state.mode);
    var fields = FIELDS[state.mode] || [];
    var out = {};
    fields.forEach(function (spec) {
      if (spec.type === "provider") return; // provider 由 select 单独取
      var v = vals[spec.key];
      if (spec.type === "number") {
        if (v === undefined || v === "") return;
        out[spec.key] = parseInt(v, 10);
      } else if (v !== undefined && v !== "") {
        out[spec.key] = v;
      }
    });
    return out;
  }

  function validateRequired() {
    var fields = FIELDS[state.mode] || [];
    var vals = modeValues(state.mode);
    for (var i = 0; i < fields.length; i++) {
      var spec = fields[i];
      if (!spec.required) continue;
      var v = vals[spec.key];
      if (v === undefined || v === null || String(v).trim() === "") {
        return (spec.label || spec.key) + " 不能为空";
      }
    }
    return null;
  }

  function onSubmit(event) {
    event.preventDefault();
    if (state.busy) return;
    var missing = validateRequired();
    if (missing) { formError(missing); return; }
    var errLine = $("error-line");
    if (errLine) errLine.textContent = "";

    var payload = buildPayload(state.mode);
    if (!payload) return; // buildPayload 内部已报错

    setBusy(true);
    if (els.status) setStatus("提交中…", "busy");
    fetch(ROUTES[state.mode].submit, {
      method: "POST",
      headers: payload.headers,
      body: payload.body
    }).then(function (resp) {
      return resp.json().then(function (data) {
        if (resp.status !== 202) {
          throw new Error(data && data.detail ? String(data.detail) : "HTTP " + resp.status);
        }
        return data;
      });
    }).then(function (data) {
      trackJob(data.job_id);
      if (els.status) {
        setStatus("任务已受理（job " + data.job_id.slice(0, 8) + "…），生成中…", "busy");
      }
      var line = $("status-line");
      if (line) line.textContent = "任务已受理（job " + data.job_id.slice(0, 8) + "…），生成中…";
      startPolling(data.job_id);
    }).catch(function (err) {
      setBusy(false);
      formError("提交失败：" + err.message);
    });
  }

  function buildPayload(mode) {
    var route = ROUTES[mode];
    var values = collectValues();
    if (route.transport === "json") {
      return {
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(values)
      };
    }
    var fd = new FormData();
    fd.append("payload", JSON.stringify(values));
    // 上传线（image_edits 单文件 / tilesets 双文件 / extract·animations 多文件）
    var list = state.files[mode] || [];
    if (mode === "image_edit") {
      if (!list.length) { formError("请选择一张待处理图片"); return null; }
      fd.append("file", list[0], list[0].name);
    } else {
      if (!list.length) { formError("请先上传文件"); return null; }
      list.forEach(function (f) { fd.append("files", f, f.name); });
    }
    return { headers: {}, body: fd };
  }

  function startPolling(jobId) {
    stopPolling();
    state.pollTimer = setInterval(function () { pollStatus(jobId); }, 2000);
    pollStatus(jobId);
  }
  function stopPolling() {
    if (state.pollTimer) { clearInterval(state.pollTimer); state.pollTimer = null; }
  }

  function pollStatus(jobId) {
    fetch(ROUTES[state.mode].status + "/" + encodeURIComponent(jobId))
      .then(function (resp) {
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.json();
      })
      .then(function (job) {
        updateHistory(jobId, job);
        if (job.status === "succeeded") {
          stopPolling();
          setBusy(false);
          if (els.status) setStatus("完成，共 " + (job.outputs || []).length + " 个产物", "ok");
          var line = $("status-line");
          if (line) line.textContent = "完成，共 " + (job.outputs || []).length + " 个产物";
          renderResult(job);
        } else if (job.status === "failed") {
          stopPolling();
          setBusy(false);
          formError("任务失败：" + (job.error || "未知错误"));
        } else {
          var txt = job.status === "running" ? "生成中…" : "排队中…";
          if (els.status) setStatus(txt, "busy");
          var line2 = $("status-line");
          if (line2) line2.textContent = txt;
        }
      })
      .catch(function (err) {
        stopPolling();
        setBusy(false);
        formError("轮询失败：" + err.message);
      });
  }

  /* ---------- 产物渲染 ---------- */
  function artifactUrl(jobId, filename) {
    return "/api/v1/artifacts/" + encodeURIComponent(jobId) + "/" + encodeURIComponent(filename);
  }

  function renderResult(job) {
    var box = els.result;
    box.innerHTML = "";
    els.empty.style.display = "none";
    var grid = h("div", { class: "result-grid" });
    (job.outputs || []).forEach(function (out) {
      if (out.format === "json") return; // components.json 走下载链接（下一段渲染报告）
      var fig = h("div", null);
      var img = h("img", { alt: out.filename, src: artifactUrl(job.job_id, out.filename) });
      img.loading = "lazy";
      fig.appendChild(img);
      var size = out.width && out.height ? out.width + "×" + out.height : out.format;
      fig.appendChild(h("div", { class: "result-caption", text: out.filename + " · " + size }));
      grid.appendChild(fig);
    });
    box.appendChild(grid);
  }

  /* ---------- 会话任务列表（本轮内存态） ---------- */
  function trackJob(jobId) {
    var exists = state.history.filter(function (j) { return j.jobId === jobId; }).length;
    if (!exists) {
      state.history.unshift({ jobId: jobId, mode: state.mode, status: "pending", data: null });
      renderHistory();
    }
  }
  function updateHistory(jobId, job) {
    state.history.forEach(function (j) {
      if (j.jobId === jobId) { j.status = job.status; j.data = job; }
    });
    renderHistory();
  }
  function renderHistory() {
    var strip = els.history;
    strip.innerHTML = "";
    state.history.slice(0, 12).forEach(function (j) {
      var label = (MODE_LABELS[j.mode] || j.mode) + " " + j.jobId.slice(0, 8) + "… " + j.status;
      strip.appendChild(h("button", {
        type: "button",
        class: "job-chip" + (j.jobId === lastViewed ? " is-current" : ""),
        text: label,
        onclick: function () {
          lastViewed = j.jobId;
          switchMode(j.mode);
          if (j.data) renderResult(j.data);
        }
      }));
    });
  }
  var lastViewed = null;

  /* ---------- 面板拖拽调宽 + 收起（官方 workspace 核心交互观感） ---------- */
  var PANEL_MIN = 320, PANEL_MAX = 560;
  function initResize() {
    var handle = els.resize, shell = els.shell;
    var dragging = false;
    handle.addEventListener("pointerdown", function (e) {
      dragging = true;
      handle.setPointerCapture(e.pointerId);
      shell.style.transition = "none";
    });
    handle.addEventListener("pointermove", function (e) {
      if (!dragging) return;
      var width = window.innerWidth - e.clientX;
      width = Math.max(PANEL_MIN, Math.min(PANEL_MAX, width));
      document.documentElement.style.setProperty("--settings-panel-width", width + "px");
    });
    function stopDrag() {
      if (!dragging) return;
      dragging = false;
      shell.style.transition = "";
    }
    handle.addEventListener("pointerup", stopDrag);
    handle.addEventListener("pointercancel", stopDrag);

    var collapse = $("panel-collapse");
    collapse.hidden = false;
    collapse.addEventListener("click", function () {
      var collapsed = shell.classList.toggle("is-collapsed");
      collapse.setAttribute("aria-label", collapsed ? "展开控制面板" : "收起控制面板");
    });
  }

  /* ---------- 启动 ---------- */
  function init() {
    els.status = $("conn-status");
    els.nav = $("mode-nav");
    els.panel = $("settings-panel");
    els.result = $("result");
    els.empty = $("canvas-empty");
    els.history = $("history-strip");
    els.shell = $("settings-shell");
    els.resize = $("resize-handle");

    initTheme();
    $("theme-toggle").addEventListener("click", function () {
      var cur = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
      applyTheme(cur);
    });
    els.nav.addEventListener("click", function (e) {
      var btn = e.target.closest(".mode-tab");
      if (!btn) return;
      var group = btn.getAttribute("data-group");
      var g = MODE_GROUPS.filter(function (x) { return x.id === group; })[0];
      if (g) switchMode(g.modes[0]);
    });
    renderPanel();
    initResize();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
