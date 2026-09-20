/* red-art-studio 工作台逻辑（vanilla，无框架无构建，离线可用）。
   架构（任务书 §5 定稿）：一张路由表 {mode: {submit, status, transport}}
   驱动全部八条能力线——表单字段由 FIELDS 配置生成、提交按 transport 组包、
   轮询沿用 2s 间隔、产物经 /api/v1/artifacts 渲染；不做 8 份重复轮询代码。
   字段与取值域的唯一真源是 server/app/jobs/models.py，本文件是其展示层投影
   （改契约需同步；不做运行时 schema 拉取，保持零后端改动）。
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

  var MAX_UPLOAD_BYTES = 20 * 1024 * 1024; // 服务端 settings.max_upload_bytes 缺省值

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

  /* ---------- 上传槽位（multipart 线；张数门禁与 wire 字段名逐线对应）---------- */
  var FILE_SPECS = {
    image_edit: [{ key: "file", label: "待处理图片", min: 1, max: 1, hint: "PNG/JPEG/WebP，≤20MB" }],
    tileset: [
      { key: "background", label: "背景纹理", min: 1, max: 1, hint: "必须 64×64，PNG/JPEG/WebP" },
      { key: "foreground", label: "前景纹理", min: 1, max: 1, hint: "必须 64×64，PNG/JPEG/WebP" }
    ],
    ui_extract: [{ key: "files", label: "参考图", min: 1, max: 8, hint: "1-8 张，PNG/JPEG/WebP，≤20MB/张" }],
    anim_pack: [{ key: "files", label: "静帧序列", min: 2, max: 16, even: true, hint: "2-16 张且偶数、等尺寸；动画格式（多帧 WebP/GIF）会被拒绝" }]
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

  /* 面板 intro 头的一句话描述（官方工作台：居中 mono 标签 + 大标题 + 描述） */
  var MODE_DESCRIPTIONS = {
    generation: "文本生成图像素材，适合图标、道具与概念图。",
    image_edit: "像素化、去背与无缝化三件套，确定性本地处理。",
    texture: "生成可平铺的 64×64 无缝纹理，支持等距投影。",
    tileset: "双纹理合成 256×256 dual-grid 地形图集。",
    ui_gen: "生成游戏 UI 并做组件分割，输出聚合表与组件数据。",
    ui_extract: "从已有 UI 图提取组件并重排为聚合表。",
    anim_pack: "静帧序列打包为 spritesheet 或动图，附循环检报告。",
    animate: "逐帧生成精灵动画，调色板统一，附生成报告。"
  };

  var ANIM_TYPES = ["idle", "walk", "run", "jump", "attack", "hit", "defeated", "other"];
  var BG_COLORS = ["#000000", "#ffffff", "#cccccc", "#808080", "#333333"];

  /* ---------- 表单字段配置（取值域见 jobs/models.py）----------
     type: composer 提示词 | segmented 枚举 | number | toggle | swatches
     五枚举底色 | text 自由文本 | note 说明行 | provider 服务端注入下拉
     showIf: 动态字段区（color_count 仅 pixel=true；后处理按 operation 切换）。 */
  var FIELDS = {
    generation: [
      { key: "prompt", type: "composer", label: "提示词（prompt）", required: true,
        placeholder: "例：pixel art sword icon, 16-bit style" },
      { key: "width", type: "number", label: "宽（px）", min: 64, max: 2048, value: 512 },
      { key: "height", type: "number", label: "高（px）", min: 64, max: 2048, value: 512 },
      { key: "n", type: "number", label: "张数", min: 1, max: 10, value: 1 },
      { key: "provider", type: "provider", label: "推理后端" }
    ],

    image_edit: [
      { key: "operation", type: "segmented", label: "操作", required: true, value: "pixelate",
        options: ["pixelate", "remove_background", "self_loop"] },
      { key: "pixel_size", type: "number", label: "像素粒度（4-64）", min: 4, max: 64,
        showIf: function (v) { return v.operation === "pixelate"; },
        hint: "留空 = 自动估计粒度" },
      { key: "source_background_color", type: "text", label: "源底色（#RRGGBB）",
        placeholder: "#RRGGBB，留空 = 四角扫描",
        showIf: function (v) { return v.operation === "remove_background"; } },
      { key: "tolerance", type: "number", label: "色距阈值（0-255）", min: 0, max: 255,
        showIf: function (v) { return v.operation === "remove_background"; },
        hint: "留空 = 32（缺省）" },
      { key: "direction", type: "segmented", label: "无缝化方向（必填）", required: true,
        options: ["horizontal", "vertical", "four_way"],
        showIf: function (v) { return v.operation === "self_loop"; } }
    ],

    texture: [
      { key: "prompt", type: "composer", label: "提示词（prompt）", required: true,
        placeholder: "例：seamless pixel art grass texture, top down" },
      { key: "quantize", type: "toggle", label: "调色板量化（≤32 色）" },
      { key: "isometric", type: "toggle", label: "等距投影（64×64 → 128×64 瓦片）" }
    ],

    tileset: [
      { key: "terrain_mode", type: "segmented", label: "地形模式", required: true, value: "dual",
        options: ["dual", "foreground", "background"] },
      { type: "note", text: "dual：两张纹理都参与合成（B 覆盖 A）。",
        showIf: function (v) { return v.terrain_mode === "dual"; } },
      { type: "note", text: "foreground：本次只消费前景纹理（背景纹理仅过校验，不参与合成）。",
        showIf: function (v) { return v.terrain_mode === "foreground"; } },
      { type: "note", text: "background：本次只消费背景纹理（前景纹理仅过校验，不参与合成）。",
        showIf: function (v) { return v.terrain_mode === "background"; } },
      { key: "seed", type: "number", label: "噪声种子（0-2^31-1）", min: 0, max: 2147483647, value: 0,
        hint: "同 seed 同输出" },
      { key: "feather_width", type: "number", label: "边缘羽化（0.5-8 px）", min: 0.5, max: 8, step: 0.5, value: 1 }
    ],

    ui_gen: [
      { key: "prompt", type: "composer", label: "提示词（prompt）", required: true,
        placeholder: "例：3x3 grid of fantasy RPG menu buttons, stone and gold" },
      { key: "quality", type: "segmented", label: "质量档位", value: "detailed",
        options: ["standard", "detailed", "ultimate"] },
      { type: "note", text: "注意：quality 是契约面参数位，本地推理通道不消费该档位（不生效）。" },
      { key: "resolution", type: "segmented", label: "分辨率档位", value: "2k", options: ["1k", "2k"] },
      { key: "aspect_ratio", type: "segmented", label: "长宽比", value: "1:1",
        options: ["4:3", "3:4", "16:9", "9:16", "1:1"] },
      { key: "background_color", type: "swatches", label: "matte 底色", value: "#cccccc", options: BG_COLORS },
      { key: "remove_background", type: "toggle", label: "色键去背", value: true },
      { key: "split_components", type: "toggle", label: "组件分割数据", value: true }
    ],

    ui_extract: [
      { key: "background_color", type: "swatches", label: "matte 底色", value: "", options: BG_COLORS,
        optionalAuto: true, autoLabel: "自动扫描",
        hint: "留空（自动扫描）= 逐图四角取众数为底色" }
    ],

    anim_pack: [
      { key: "animation_type", type: "segmented", label: "动作类型", value: "other", options: ANIM_TYPES },
      { key: "output_format", type: "segmented", label: "交付格式", value: "webp",
        options: ["webp", "gif", "spritesheet"] },
      { key: "pixel", type: "toggle", label: "像素纪律（alpha 路由 + 调色板统一 + 画布 ≤256）" },
      { key: "alpha_mode", type: "segmented", label: "alpha 处理", value: "", options: ["soft", "sharp"],
        optionalAuto: true, autoLabel: "自动（按像素纪律路由）" },
      { key: "color_count", type: "number", label: "统一调色板色数（2-64）", min: 2, max: 64,
        clearWhenHidden: true,
        showIf: function (v) { return v.pixel === true; },
        hint: "仅 pixel=true 时合法携带（缺省 32）" },
      { key: "duration_ms", type: "number", label: "帧时长（20-1000 ms）", min: 20, max: 1000, value: 125 }
    ],

    animate: [
      { key: "prompt", type: "composer", label: "动作描述（prompt，1-500 字符）", required: true,
        placeholder: "例：a small red pixel art ball bouncing" },
      { key: "animation_type", type: "segmented", label: "动作类型", value: "other", options: ANIM_TYPES },
      { key: "frame_count", type: "number", label: "帧数（4-16 且偶数）", min: 4, max: 16, value: 8 },
      { key: "width", type: "number", label: "宽（px）", min: 64, max: 2048, value: 512 },
      { key: "height", type: "number", label: "高（px）", min: 64, max: 2048, value: 512 },
      { key: "output_format", type: "segmented", label: "交付格式", value: "webp",
        options: ["webp", "gif", "spritesheet"] },
      { key: "pixel", type: "toggle", label: "像素纪律（画布任一轴 ≤256）" },
      { key: "alpha_mode", type: "segmented", label: "alpha 处理", value: "", options: ["soft", "sharp"],
        optionalAuto: true, autoLabel: "自动（按像素纪律路由）" },
      { key: "color_count", type: "number", label: "统一调色板色数（2-64）", min: 2, max: 64,
        clearWhenHidden: true,
        showIf: function (v) { return v.pixel === true; },
        hint: "仅 pixel=true 时合法携带（缺省 32）" },
      { key: "duration_ms", type: "number", label: "帧时长（20-1000 ms）", min: 20, max: 1000, value: 125 },
      { key: "seed", type: "number", label: "随机种子（≥0）", min: 0, value: 0,
        hint: "同 seed 同 prompt 可复现；逐帧 seed=seed+i" }
    ]
  };

  /* ---------- 组包器（wire 形态逐线对应契约；image_edit 是嵌套形态）---------- */
  function num(v, fallback) {
    return v === undefined || v === "" || v === null ? fallback : parseFloat(v);
  }
  function intOrNull(v) {
    return v === undefined || v === "" || v === null ? null : parseInt(v, 10);
  }

  var BUILDERS = {
    generation: function (v) {
      var out = {
        prompt: String(v.prompt || "").trim(),
        size: (intOrNull(v.width) || 512) + "x" + (intOrNull(v.height) || 512),
        n: num(v.n, 1)
      };
      var provider = providerValue();
      if (provider) out.provider = provider;
      return out;
    },
    image_edit: function (v) {
      var params = {};
      if (v.operation === "pixelate") {
        var px = intOrNull(v.pixel_size);
        if (px !== null) params.pixel_size = px;
      } else if (v.operation === "remove_background") {
        if (v.source_background_color) params.source_background_color = v.source_background_color;
        var tol = intOrNull(v.tolerance);
        if (tol !== null) params.tolerance = tol;
      } else if (v.operation === "self_loop") {
        params.direction = v.direction; // 必填，VALIDATORS 已拦截缺失
      }
      return { operation: v.operation, params: params };
    },
    texture: function (v) {
      return { prompt: String(v.prompt || "").trim(), quantize: v.quantize === true, isometric: v.isometric === true };
    },
    tileset: function (v) {
      var out = { terrain_mode: v.terrain_mode };
      var seed = intOrNull(v.seed);
      if (seed !== null) out.seed = seed;
      var fw = num(v.feather_width, null);
      if (fw !== null) out.feather_width = fw;
      return out;
    },
    ui_gen: function (v) {
      return {
        prompt: String(v.prompt || "").trim(),
        quality: v.quality || "detailed",
        resolution: v.resolution || "2k",
        aspect_ratio: v.aspect_ratio || "1:1",
        background_color: v.background_color || "#cccccc",
        remove_background: v.remove_background !== false,
        split_components: v.split_components !== false
      };
    },
    ui_extract: function (v) {
      var out = {};
      if (v.background_color) out.background_color = v.background_color;
      return out;
    },
    anim_pack: function (v) {
      var out = {
        animation_type: v.animation_type || "other",
        output_format: v.output_format || "webp",
        pixel: v.pixel === true,
        duration_ms: num(v.duration_ms, 125)
      };
      if (v.alpha_mode) out.alpha_mode = v.alpha_mode;
      var cc = intOrNull(v.color_count);
      if (out.pixel && cc !== null) out.color_count = cc;
      return out;
    },
    animate: function (v) {
      var out = {
        prompt: String(v.prompt || "").trim(),
        animation_type: v.animation_type || "other",
        frame_count: num(v.frame_count, 8),
        size: (intOrNull(v.width) || 512) + "x" + (intOrNull(v.height) || 512),
        output_format: v.output_format || "webp",
        pixel: v.pixel === true,
        duration_ms: num(v.duration_ms, 125),
        seed: num(v.seed, 0)
      };
      if (v.alpha_mode) out.alpha_mode = v.alpha_mode;
      var cc = intOrNull(v.color_count);
      if (out.pixel && cc !== null) out.color_count = cc;
      return out;
    }
  };

  /* ---------- 客户端预校验（服务端 422 兜底前的第一道；消息与服务端门禁同语义）---------- */
  var HEX_COLOR = /^#[0-9a-fA-F]{6}$/;
  var VALIDATORS = {
    image_edit: function (v) {
      if (v.source_background_color && !HEX_COLOR.test(v.source_background_color)) {
        return "源底色需为 #RRGGBB 形态（如 #cccccc）";
      }
      return null;
    },
    animate: function (v) {
      var prompt = String(v.prompt || "").trim();
      if (!prompt) return "动作描述不能为空";
      if (prompt.length > 500) return "动作描述最长 500 字符，实际 " + prompt.length;
      var fc = num(v.frame_count, 8);
      if (fc % 2 !== 0) return "帧数必须为偶数（官方约束），实际 " + fc;
      if (v.pixel === true) {
        var w = intOrNull(v.width) || 512, hgt = intOrNull(v.height) || 512;
        if (Math.max(w, hgt) > 256) {
          return "像素动画画布任一轴不得超过 256px，实际 " + w + "x" + hgt;
        }
      }
      return null;
    }
  };

  /* ---------- 全局状态 ---------- */
  var state = {
    group: "image",
    mode: "generation",
    values: {},        // 各模式当前表单值 {mode: {key: value}}
    files: {},         // 各模式已选文件 {mode: {slotKey: [File,...]}}
    history: [],       // 会话内任务 {jobId, mode, status, data}
    pollTimer: null,
    busy: false
  };

  /* ---------- DOM 快捷 ---------- */
  function $(id) { return document.getElementById(id); }
  var els = {};

  /* 配置加载期给每个字段生成稳定 DOM id（composer 沉底重排后 showIf 定位不错位） */
  Object.keys(FIELDS).forEach(function (mode) {
    FIELDS[mode].forEach(function (spec, i) {
      spec._fid = spec.key || "note-" + mode + "-" + i;
    });
  });

  function h(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        if (k === "class") node.className = attrs[k];
        else if (k === "text") node.textContent = attrs[k];
        else if (k.slice(0, 2) === "on") node.addEventListener(k.slice(2), attrs[k]);
        else if (attrs[k] !== undefined && attrs[k] !== null) node.setAttribute(k, attrs[k]);
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

  /* ---------- 表单值存取 ---------- */
  function modeValues(mode) {
    if (!state.values[mode]) state.values[mode] = {};
    return state.values[mode];
  }
  function modeFiles(mode) {
    if (!state.files[mode]) state.files[mode] = {};
    return state.files[mode];
  }
  function providerValue() {
    var select = els.panel.querySelector("select[data-provider]");
    return select ? select.value : "";
  }

  /* ---------- 面板渲染（FIELDS/FILE_SPECS 配置 → 面板 DOM） ---------- */
  function renderPanel() {
    var panel = els.panel;
    panel.innerHTML = "";
    var group = MODE_GROUPS.filter(function (g) { return g.id === state.group; })[0];
    if (!group) return;

    // intro 头（官方工作台形态：居中小 mono 标签 + 模式名 + 一句描述）
    panel.appendChild(h("div", { class: "panel-intro" }, [
      h("span", { class: "section-label", text: "red art studio" }),
      h("h2", { text: MODE_LABELS[state.mode] || state.mode }),
      h("p", { text: MODE_DESCRIPTIONS[state.mode] || "" })
    ]));

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
    var fields = FIELDS[state.mode] || [];
    // composer 沉底（官方工作台：参数在上，prompt 输入区在面板底部）
    var ordered = fields.filter(function (f) { return f.type !== "composer"; })
      .concat(fields.filter(function (f) { return f.type === "composer"; }));
    ordered.forEach(function (spec, i) {
      form.appendChild(renderField(spec, i));
    });
    // 上传槽位（multipart 线）
    (FILE_SPECS[state.mode] || []).forEach(function (spec) {
      form.appendChild(renderFileField(spec));
    });

    form.appendChild(h("section", { class: "submit-section" }, [
      h("div", { id: "status-line", class: "status-line", role: "status" }),
      h("div", { id: "error-line", class: "error-line", role: "alert" }),
      h("button", {
        id: "submit-btn", class: "primary-btn", type: "submit",
        text: state.busy ? "提交中…" : submitLabel(state.mode)
      })
    ]));
    form.addEventListener("submit", onSubmit);
    panel.appendChild(form);
    refreshVisibility();
  }

  function submitLabel(mode) {
    if (mode === "image_edit") return "开始处理";
    if (mode === "tileset") return "合成图集";
    if (mode === "ui_extract") return "提取重排";
    if (mode === "anim_pack") return "打包";
    if (mode === "animate") return "生成动画";
    if (mode === "texture") return "生成纹理";
    if (mode === "ui_gen") return "生成 UI";
    return "生成";
  }

  function renderField(spec) {
    var wrap = h("div", { class: "field", "data-field": spec._fid });
    var vals = modeValues(state.mode);

    if (spec.type === "note") {
      wrap.appendChild(h("p", { class: "panel-note", text: spec.text }));
      return wrap;
    }
    if (spec.type !== "toggle" && spec.label) {
      wrap.appendChild(h("label", { class: "field-label", text: spec.label }));
    }
    switch (spec.type) {
      case "composer": {
        var ta = h("textarea", { class: "composer", rows: "3", placeholder: spec.placeholder || "" });
        ta.value = vals[spec.key] || "";
        ta.addEventListener("input", function () { vals[spec.key] = ta.value; });
        wrap.appendChild(ta);
        break;
      }
      case "segmented": {
        if (vals[spec.key] === undefined) vals[spec.key] = spec.value !== undefined ? spec.value : "";
        var options = spec.options.slice();
        if (spec.optionalAuto) options.unshift("");
        var seg = h("div", { class: "segmented", role: "radiogroup" });
        options.forEach(function (opt) {
          var label = opt === "" ? (spec.autoLabel || "自动") : opt;
          seg.appendChild(h("button", {
            type: "button",
            class: vals[spec.key] === opt ? "is-active" : "",
            text: label,
            onclick: function () {
              vals[spec.key] = opt;
              Array.prototype.forEach.call(seg.children, function (b, i) {
                b.classList.toggle("is-active", options[i] === opt);
              });
              refreshVisibility(); // operation 等枚举驱动动态字段区
            }
          }));
        });
        wrap.appendChild(seg);
        break;
      }
      case "number": {
        if (vals[spec.key] === undefined && spec.value !== undefined) vals[spec.key] = spec.value;
        var input = h("input", { class: "input", type: "number", min: spec.min, max: spec.max,
          step: spec.step !== undefined ? spec.step : "1" });
        input.value = vals[spec.key] !== undefined ? vals[spec.key] : "";
        input.addEventListener("input", function () { vals[spec.key] = input.value; });
        wrap.appendChild(input);
        break;
      }
      case "text": {
        var text = h("input", { class: "input", type: "text", placeholder: spec.placeholder || "" });
        text.value = vals[spec.key] || "";
        text.addEventListener("input", function () { vals[spec.key] = text.value.trim(); });
        wrap.appendChild(text);
        break;
      }
      case "toggle": {
        if (vals[spec.key] === undefined && spec.value !== undefined) vals[spec.key] = spec.value;
        var row = h("div", { class: "toggle-row" });
        if (spec.label) row.appendChild(h("span", { class: "field-label", text: spec.label }));
        var cb = h("input", { class: "toggle", type: "checkbox" });
        cb.checked = vals[spec.key] === true;
        cb.addEventListener("change", function () {
          vals[spec.key] = cb.checked;
          refreshVisibility();
        });
        row.appendChild(cb);
        wrap.appendChild(row);
        break;
      }
      case "swatches": {
        if (vals[spec.key] === undefined) vals[spec.key] = spec.value !== undefined ? spec.value : "";
        var options2 = spec.options.slice();
        if (spec.optionalAuto) options2.unshift("");
        var rowSw = h("div", { class: "swatch-row", role: "radiogroup" });
        options2.forEach(function (color) {
          if (color === "") {
            var auto = h("button", {
              type: "button",
              class: "inline-action" + (vals[spec.key] === "" ? " is-active" : ""),
              text: spec.autoLabel || "自动",
              style: "min-height:32px"
            });
            auto.addEventListener("click", function () {
              vals[spec.key] = "";
              Array.prototype.forEach.call(rowSw.children, function (s, i) {
                s.classList.toggle("is-active", options2[i] === "");
              });
            });
            rowSw.appendChild(auto);
            return;
          }
          var b = h("button", {
            type: "button", class: "swatch" + (vals[spec.key] === color ? " is-active" : ""),
            "aria-label": color, title: color
          });
          b.style.background = color;
          b.addEventListener("click", function () {
            vals[spec.key] = color;
            Array.prototype.forEach.call(rowSw.children, function (s, i) {
              s.classList.toggle("is-active", options2[i] === color);
            });
          });
          rowSw.appendChild(b);
        });
        wrap.appendChild(rowSw);
        break;
      }
      case "provider": {
        var select = h("select", { class: "input", "data-provider": "1" });
        (BOOTSTRAP.provider_options || []).forEach(function (o) {
          var opt = h("option", { value: o.value, text: o.label });
          if (o.selected) opt.selected = true;
          select.appendChild(opt);
        });
        wrap.appendChild(select);
        break;
      }
      default:
        break;
    }
    if (spec.hint) wrap.appendChild(h("p", { class: "panel-note", text: spec.hint }));
    return wrap;
  }

  /* ---------- 上传槽位（dropzone + 缩略图 + 计数 + 上下限预校验）---------- */
  function renderFileField(spec) {
    var wrap = h("div", { class: "field", "data-field": "file-" + spec.key });
    var labelRow = h("div", { class: "toggle-row" });
    labelRow.appendChild(h("label", { class: "field-label", text: spec.label }));
    var badge = h("span", { class: "count-badge", "data-count": spec.key, text: "0/" + spec.max });
    labelRow.appendChild(badge);
    wrap.appendChild(labelRow);

    var zone = h("div", { class: "dropzone", text: "点击选择或拖入文件到此处" });
    if (spec.hint) zone.appendChild(h("span", { class: "panel-note", text: spec.hint }));
    var input = h("input", { type: "file", accept: "image/png,image/jpeg,image/webp",
      multiple: spec.max > 1 ? "multiple" : null, style: "display:none" });

    zone.addEventListener("click", function () { input.click(); });
    zone.addEventListener("dragover", function (e) {
      e.preventDefault();
      zone.classList.add("is-over");
    });
    zone.addEventListener("dragleave", function () { zone.classList.remove("is-over"); });
    zone.addEventListener("drop", function (e) {
      e.preventDefault();
      zone.classList.remove("is-over");
      addFiles(spec, Array.prototype.slice.call(e.dataTransfer.files));
    });
    input.addEventListener("change", function () {
      addFiles(spec, Array.prototype.slice.call(input.files));
      input.value = "";
    });

    wrap.appendChild(zone);
    wrap.appendChild(input);
    wrap.appendChild(h("div", { class: "thumb-grid", "data-thumbs": spec.key }));
    renderThumbs(spec);
    return wrap;
  }

  function addFiles(spec, incoming) {
    var bucket = modeFiles(state.mode);
    var list = bucket[spec.key] || (bucket[spec.key] = []);
    var rejected = [];
    incoming.forEach(function (f) {
      if (spec.max === 1) { list.length = 0; }
      if (list.length >= spec.max) { rejected.push("超过上限 " + spec.max + " 张"); return; }
      if (f.size > MAX_UPLOAD_BYTES) { rejected.push(f.name + " 超过 20MB"); return; }
      list.push(f);
    });
    if (rejected.length) formError(rejected[0]);
    renderThumbs(spec);
    updateFileBadge(spec);
  }

  function removeFile(spec, index) {
    var list = modeFiles(state.mode)[spec.key] || [];
    list.splice(index, 1);
    renderThumbs(spec);
    updateFileBadge(spec);
  }

  function renderThumbs(spec) {
    var grid = els.panel.querySelector('[data-thumbs="' + spec.key + '"]');
    if (!grid) return;
    grid.innerHTML = "";
    var list = modeFiles(state.mode)[spec.key] || [];
    list.forEach(function (f, i) {
      var url = URL.createObjectURL(f);
      var cell = h("div", { class: "thumb" });
      var img = h("img", { alt: f.name, src: url });
      img.addEventListener("load", function () { URL.revokeObjectURL(url); });
      cell.appendChild(img);
      cell.appendChild(h("button", {
        type: "button", "aria-label": "移除 " + f.name, text: "×",
        onclick: function () { removeFile(spec, i); }
      }));
      grid.appendChild(cell);
    });
  }

  function fileCountValid(spec, count) {
    if (count < spec.min) return false;
    if (count > spec.max) return false;
    if (spec.even && count % 2 !== 0) return false;
    return true;
  }

  function updateFileBadge(spec) {
    var badge = els.panel.querySelector('[data-count="' + spec.key + '"]');
    if (!badge) return;
    var list = modeFiles(state.mode)[spec.key] || [];
    badge.textContent = list.length + "/" + spec.max;
    badge.classList.toggle("is-invalid", !fileCountValid(spec, list.length));
  }

  function validateFiles() {
    var specs = FILE_SPECS[state.mode] || [];
    for (var i = 0; i < specs.length; i++) {
      var spec = specs[i];
      var list = modeFiles(state.mode)[spec.key] || [];
      if (list.length < spec.min) {
        return spec.label + "至少需要 " + spec.min + " 张，实际 " + list.length + " 张";
      }
      if (list.length > spec.max) {
        return spec.label + "最多 " + spec.max + " 张，实际 " + list.length + " 张";
      }
      if (spec.even && list.length % 2 !== 0) {
        return "帧数必须为偶数（官方约束），实际 " + list.length + " 张";
      }
    }
    return null;
  }

  /* ---------- showIf 可见性重算 ---------- */
  function refreshVisibility() {
    var form = $("job-form");
    if (!form) return;
    var vals = modeValues(state.mode);
    (FIELDS[state.mode] || []).forEach(function (spec) {
      var node = form.querySelector('[data-field="' + spec._fid + '"]');
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
    if (btn) { btn.disabled = busy; btn.textContent = busy ? "提交中…" : submitLabel(state.mode); }
    if (!busy && els.status && !els.status.classList.contains("is-error")) setStatus("就绪", null);
  }

  function formError(message) {
    var line = $("error-line");
    if (line) line.textContent = message || "";
    if (els.status) setStatus("出错", "error");
  }

  function validateForm() {
    var vals = modeValues(state.mode);
    var fields = FIELDS[state.mode] || [];
    for (var i = 0; i < fields.length; i++) {
      var spec = fields[i];
      if (!spec.required) continue;
      if (spec.showIf && !spec.showIf(vals)) continue;
      var v = vals[spec.key];
      if (v === undefined || v === null || String(v).trim() === "") {
        return (spec.label || spec.key) + " 不能为空";
      }
    }
    var checker = VALIDATORS[state.mode];
    var err = checker ? checker(vals) : null;
    if (err) return err;
    return validateFiles();
  }

  function onSubmit(event) {
    event.preventDefault();
    if (state.busy) return;
    var problem = validateForm();
    if (problem) { formError(problem); return; }
    var errLine = $("error-line");
    if (errLine) errLine.textContent = "";

    var request = buildRequest(state.mode);
    setBusy(true);
    if (els.status) setStatus("提交中…", "busy");
    fetch(ROUTES[state.mode].submit, {
      method: "POST",
      headers: request.headers,
      body: request.body
    }).then(function (resp) {
      return resp.json().then(function (data) {
        if (resp.status !== 202) {
          throw new Error(data && data.detail ? String(data.detail) : "HTTP " + resp.status);
        }
        return data;
      });
    }).then(function (data) {
      trackJob(data.job_id);
      var line = $("status-line");
      if (line) line.textContent = "任务已受理（job " + data.job_id.slice(0, 8) + "…），生成中…";
      if (els.status) setStatus("生成中", "busy");
      startPolling(data.job_id, ROUTES[state.mode].status);
    }).catch(function (err) {
      setBusy(false);
      formError("提交失败：" + err.message);
    });
  }

  function buildRequest(mode) {
    var route = ROUTES[mode];
    var payload = BUILDERS[mode](modeValues(mode));
    if (route.transport === "json") {
      return { headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) };
    }
    var fd = new FormData();
    fd.append("payload", JSON.stringify(payload));
    (FILE_SPECS[mode] || []).forEach(function (spec) {
      var list = modeFiles(mode)[spec.key] || [];
      if (spec.key === "files") {
        list.forEach(function (f) { fd.append("files", f, f.name); });
      } else if (list.length) {
        fd.append(spec.key, list[0], list[0].name);
      }
    });
    return { headers: {}, body: fd };
  }

  function startPolling(jobId, statusBase) {
    stopPolling();
    // 状态前缀在提交时固定（ui_gen 双模式同前缀、animations/animate 不同前缀），
    // 轮询期间用户切换模式不改变本任务的查询路径
    state.pollTimer = setInterval(function () { pollStatus(jobId, statusBase); }, 2000);
    pollStatus(jobId, statusBase);
  }
  function stopPolling() {
    if (state.pollTimer) { clearInterval(state.pollTimer); state.pollTimer = null; }
  }

  function pollStatus(jobId, statusBase) {
    fetch(statusBase + "/" + encodeURIComponent(jobId))
      .then(function (resp) {
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.json();
      })
      .then(function (job) {
        updateHistory(jobId, job);
        if (job.status === "succeeded") {
          stopPolling();
          setBusy(false);
          var n = (job.outputs || []).length;
          var line = $("status-line");
          if (line) line.textContent = "完成，共 " + n + " 个产物";
          if (els.status) setStatus("完成，共 " + n + " 个产物", "ok");
          renderResult(job);
        } else if (job.status === "failed") {
          stopPolling();
          setBusy(false);
          formError("任务失败：" + (job.error || "未知错误"));
        } else {
          var txt = job.status === "running" ? "生成中…" : "排队中…";
          var line2 = $("status-line");
          if (line2) line2.textContent = txt;
          if (els.status) setStatus(txt, "busy");
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
    var jsonOutputs = [];
    (job.outputs || []).forEach(function (out) {
      if (out.format === "json") { jsonOutputs.push(out); return; }
      var fig = h("div");
      var img = h("img", { alt: out.filename, src: artifactUrl(job.job_id, out.filename) });
      img.loading = "lazy";
      fig.appendChild(img);
      var size = out.width && out.height ? out.width + "×" + out.height : out.format;
      fig.appendChild(h("div", { class: "result-caption", text: out.filename + " · " + size }));
      grid.appendChild(fig);
    });
    box.appendChild(grid);
    if (jsonOutputs.length) {
      jsonOutputs.forEach(function (out) {
        box.appendChild(h("a", {
          class: "inline-action",
          href: artifactUrl(job.job_id, out.filename),
          download: out.filename,
          text: "下载 " + out.filename
        }));
      });
    }
    renderReports(job);
  }

  /* ---------- 报告渲染（如实交付：passed:false 显式呈现为「未通过」，禁绿勾粉饰）---------- */
  function badge(passed, passText, failText) {
    return h("span", {
      class: "report-badge " + (passed ? "pass" : "fail"),
      text: passed ? (passText || "通过") : (failText || "未通过")
    });
  }
  function reportRow(key, value, fail) {
    return h("div", { class: "report-row" }, [
      h("span", { class: "report-key", text: key }),
      h("span", { class: "report-value" + (fail ? " is-fail" : ""), text: String(value) })
    ]);
  }
  function reportCard(title, badgeNode, cls) {
    var head = h("div", { class: "report-head" }, [
      h("span", { class: "report-title", text: title }),
      badgeNode
    ]);
    return h("div", { class: "report-card glass-panel-soft" + (cls ? " " + cls : "") }, [head]);
  }

  /* 检缝报告（纹理线）：双轴跳变 <6 视为无缝；不过不判 failed，指标如实交付 */
  function seamCard(report) {
    var card = reportCard("检缝报告", badge(report.passed));
    var body = h("div", { class: "report-body" });
    body.appendChild(reportRow("水平接缝最大跳变", report.horizontal_max_step, !report.passed));
    body.appendChild(reportRow("垂直接缝最大跳变", report.vertical_max_step, !report.passed));
    body.appendChild(reportRow("无缝阈值", "< 6"));
    if (!report.passed) {
      body.appendChild(h("p", { class: "panel-note",
        text: "接缝未抹平（常见于高频纹理）：数值如实呈现，是否重生成由你决定。" }));
    }
    card.appendChild(body);
    return card;
  }

  /* 质量门禁报告（UI 线）：巨型粘连 / bbox 重叠逐项显式呈现 */
  function gateCard(gate) {
    var card = reportCard("质量门禁", badge(gate.passed));
    var body = h("div", { class: "report-body" });
    body.appendChild(reportRow("组件数", gate.component_count, !gate.passed));
    (gate.giant_components || []).forEach(function (label) {
      body.appendChild(reportRow("巨型粘连（面积占比超阈值）", label, true));
    });
    (gate.overlaps || []).forEach(function (ov) {
      body.appendChild(reportRow("bbox 重叠", ov.a_label + " ↔ " + ov.b_label + "（占比 " + ov.ratio + "）", true));
    });
    if (!gate.passed) {
      body.appendChild(h("p", { class: "panel-note",
        text: "门禁未通过（多为生成图杂色背景导致粘连）：指标如实呈现，是否重生成由你决定。" }));
    }
    card.appendChild(body);
    return card;
  }

  /* components.json 表格：label/bbox/area_px（extract 线另有 source_index/source_bbox）*/
  function componentsTable(manifest, jobId) {
    var wrap = h("div", { class: "report-card glass-panel-soft" });
    wrap.appendChild(h("div", { class: "report-head" }, [
      h("span", { class: "report-title", text: "组件分割数据" }),
      h("span", { class: "report-key", text: "聚合表实际尺寸 " + manifest.actual_size.join("×") })
    ]));
    var hasSource = (manifest.components || []).some(function (c) {
      return c.source_index !== undefined && c.source_index !== null;
    });
    var table = h("table", { class: "components-table" });
    table.appendChild(h("thead", null, [h("tr", null, (hasSource
      ? ["label", "bbox [x,y,w,h]", "area_px", "src#", "src bbox"]
      : ["label", "bbox [x,y,w,h]", "area_px"]
    ).map(function (t) { return h("th", { text: t }); }))]));
    var tbody = h("tbody");
    (manifest.components || []).forEach(function (c) {
      var cells = [
        h("td", { class: "num", text: c.label }),
        h("td", { class: "num", text: "[" + c.bbox.join(", ") + "]" }),
        h("td", { class: "num", text: c.area_px })
      ];
      if (hasSource) {
        cells.push(h("td", { class: "num", text: c.source_index !== undefined && c.source_index !== null ? c.source_index : "—" }));
        cells.push(h("td", { class: "num", text: c.source_bbox ? "[" + c.source_bbox.join(", ") + "]" : "—" }));
      }
      tbody.appendChild(h("tr", null, cells));
    });
    table.appendChild(tbody);
    wrap.appendChild(h("div", { class: "table-wrap" }, [table]));
    if (jobId) {
      wrap.appendChild(h("a", {
        class: "inline-action", style: "margin-top:8px",
        href: artifactUrl(jobId, "components.json"),
        download: "components.json",
        text: "下载 components.json（原始 JSON）"
      }));
    }
    return wrap;
  }

  /* 打包报告（帧序列线）：loop_report 如实 + sheet_meta（spritesheet 线）*/
  function loopRows(body, loop) {
    body.appendChild(reportRow("首末帧最大通道跳变", loop.first_last_max_step, !loop.passed));
    body.appendChild(reportRow("循环阈值", "< 6"));
    if (!loop.passed) {
      body.appendChild(h("p", { class: "panel-note",
        text: "循环检未闭合：数值如实呈现（生成线逐帧文生图的帧间一致性是已知局限），是否重生成由你决定。" }));
    }
  }
  function sheetRows(body, meta) {
    if (!meta) return;
    body.appendChild(reportRow("网格（列×行）", meta.columns + " × " + meta.rows));
    body.appendChild(reportRow("单帧尺寸", meta.frame_size.join("×")));
    body.appendChild(reportRow("循环播放", meta.loop ? "是" : "否"));
  }
  function animPackCard(report) {
    var card = reportCard("打包报告", badge(report.loop_report.passed));
    var body = h("div", { class: "report-body" });
    body.appendChild(reportRow("帧数 / 帧尺寸", report.frame_count + " × " + report.frame_size.join("×")));
    body.appendChild(reportRow("帧时长", report.duration_ms + " ms"));
    body.appendChild(reportRow("动作类型 / 交付", report.animation_type + " / " + (report.alpha_mode || "—") +
      (report.pixel ? " / pixel" : "")));
    loopRows(body, report.loop_report);
    if (report.sheet_meta) {
      body.appendChild(h("hr", { class: "divider" }));
      body.appendChild(reportRow("spritesheet 元数据", "cols " + report.sheet_meta.columns + " / rows " + report.sheet_meta.rows +
        " / frame " + report.sheet_meta.frame_size.join("×")));
    }
    card.appendChild(body);
    return card;
  }

  /* 生成报告（animate 线）：seeds 序列 + 逐帧 prompt 折叠详情 + 内嵌打包报告 */
  function animateCard(report, jobId) {
    var card = reportCard("生成报告", badge(report.pack.loop_report.passed, "循环通过", "循环未闭合"));
    var body = h("div", { class: "report-body" });
    body.appendChild(reportRow("帧数 / 尺寸", report.frame_count + " × " + report.size.join("×")));
    body.appendChild(reportRow("seeds", report.seeds.join(", ")));
    body.appendChild(reportRow("动作类型", report.animation_type + (report.pixel ? " / pixel" : "") +
      " / alpha " + (report.alpha_mode || "—")));
    if (report.frame_prompts && report.frame_prompts.length) {
      var acc = h("details", { class: "acc" }, [
        h("summary", { text: "逐帧 prompt（" + report.frame_prompts.length + " 条，点击展开）" })
      ]);
      var accBody = h("div", { class: "acc-body" });
      report.frame_prompts.forEach(function (p, i) {
        accBody.appendChild(h("pre", { text: "frame " + i + ": " + p }));
      });
      acc.appendChild(accBody);
      body.appendChild(acc);
    }
    loopRows(body, report.pack.loop_report);
    if (report.pack.sheet_meta) sheetRows(body, report.pack.sheet_meta);
    card.appendChild(body);
    return card;
  }

  function renderReports(job) {
    var stack = h("div", { class: "report-stack" });
    if (job.seam_report) stack.appendChild(seamCard(job.seam_report));
    if (job.ui_components) {
      stack.appendChild(gateCard(job.ui_components.gate));
      stack.appendChild(componentsTable(job.ui_components, job.job_id));
    }
    if (job.anim_report) stack.appendChild(animPackCard(job.anim_report));
    if (job.animate_report) stack.appendChild(animateCard(job.animate_report, job.job_id));
    if (stack.childElementCount) els.result.appendChild(stack);
  }

  /* ---------- 会话任务列表（本轮内存态；跨会话历史待 GET /api/v1/jobs 后再做） ---------- */
  var lastViewed = null;
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
