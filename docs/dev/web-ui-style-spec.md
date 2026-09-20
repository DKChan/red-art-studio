# Web UI 样式规格 v2 — 视觉基线对齐 meowa.ai（2026-09-20 实测复核）

> 来源：meowa.ai 生产 CSS bundle `/assets/main-BsPQOrTz.css`（618,762B）。执行日重抓复核：任务书 token 与生产 CSS 逐项一致；两处偏差按实测修正（见 §6 已知偏差）。
> 本文档是 Web UI 实现的唯一视觉依据（v1 的暗色单页规格作废，其 §5「明确不做」中的排除项——亮色主题/双主题切换——本轮全部启用）。
> 铁律不变：只借视觉语言，不做 credit/钱包/计价/登录注册/订阅/多租户；无框架无构建无外部 CDN（字体本地 woff2），**离线可用**；Tailwind「抄值不抄类」。

## 1. 交付形态（v2 架构定稿）

```
server/app/static/
├── app.css          # 自研 CSS：token 两套 + 组件 + 骨架（本规格的落地物）
├── app.js           # 路由表驱动的表单/轮询/渲染逻辑（取值域投影自 jobs/models.py）
└── fonts/           # manrope-latin / ibm-plex-mono-600（本地 woff2，实测与官方 md5 一致：938c6e8019b69313372c47dbb7a7c930）
server/app/api/ui.py # HTML 骨架 + {provider_options} 注入（bootstrap JSON）+ /static/{path} 清单白名单路由
```

- 官方是 React + Tailwind + Vite——**只借视觉值，不借技术栈**。
- 静态路由沿用 fonts 白名单语义：显式清单直读，穿越/清单外一律 404，不用 StaticFiles 挂目录。
- HTML 骨架只承载结构（header / 五模式导航 / 画布区 / 可拖拽设置面板），表单字段由 app.js 配置生成。

## 2. 主题 token（实测原文，两套都必须）

**官方是亮色默认**（`<meta theme-color #ffffff>`、`:root` 为亮色），暗色走 `[data-theme="dark"]`。本项目同构：默认亮色 + header 切换按钮 + localStorage 记忆（key `ras-theme`）。完整变量表见 `server/app/static/app.css`（与生产 bundle 逐值一致），关键项：

```css
:root {
  --lavender:#bdbbff;  --magenta:#ef2cc1;  --orange:#fc4c02;
  --shadow-light:0 4px 10px #0101201a;  --shadow-soft:0 4px 10px #01012014;  --shadow-dark:0 4px 10px #0101203d;
  --theme-canvas:#fff;            --theme-canvas-soft:#f6f7ff;
  --theme-surface:#ffffffd6;      --theme-surface-soft:#ffffffb3;   --theme-surface-strong:#f6f7ffc7;
  --theme-line:#00000014;         --theme-line-strong:#00000029;
  --theme-text-primary:#000;      --theme-text-secondary:#0009;     --theme-text-muted:#0000006b;
  --theme-hero-gradient:linear-gradient(135deg,#020214 0%,#5656a9 36%,#ef2cc1 72%,#2c9cff 100%);
  --theme-action-bg:#010120;      --theme-action-text:#fff;         --theme-action-hover-bg:#15154f;
  /* panel-* / settings-bg / panel-contrast-* 两主题同值（官方设置面板恒深藏青） */
}
[data-theme="dark"] {
  --theme-canvas:#010120;         --theme-canvas-soft:#08082a;
  --theme-surface:#0e0e34d6;      --theme-surface-soft:#ffffff14;   --theme-surface-strong:#0a0a28f5;
  --theme-line:#ffffff1f;         --theme-line-strong:#ffffff38;
  --theme-text-primary:#fff;      --theme-text-secondary:#ffffffb8; --theme-text-muted:#ffffff75;
  --theme-action-bg:#fff;         --theme-action-text:#010120;      --theme-action-hover-bg:#d8d7ff;
}
```

页面底色（实测 `html` 两层：三光斑径向 + 主题纵向渐变）：

```css
html {
  background:
    radial-gradient(circle at 12% 12%, #ef2cc124, transparent 26%),
    radial-gradient(circle at 84% 18%, #bdbbff2e, transparent 28%),
    radial-gradient(circle at 52% 0%,  #7ac2ff2e, transparent 26%),
    linear-gradient(180deg, var(--theme-canvas), var(--theme-canvas-soft) 42%, var(--theme-canvas) 100%);
}
```

本项目自补 token（官方无对应，中文 UI 需要）：`--ui-error/--ui-warn/--ui-ok`（报告卡状态色，亮暗各档）与 `--segmented-*`（分段控件实测值，见 §4）。

## 3. 字体（无需改动）

- 正文/UI：**Manrope**（`/static/fonts/manrope-latin.woff2`，可变字重 200-800）；中文回落 `"PingFang SC","Microsoft YaHei"`。
- 标签/数值/字段名：**IBM Plex Mono** 600（`/static/fonts/ibm-plex-mono-600-latin.woff2`）。
- 离线铁律：零外链，@font-face 本地引用。

## 4. 标志性元素（实测规格）

| 元素 | 实测值 |
|---|---|
| `.glass-panel` | `linear-gradient(180deg,var(--theme-surface),var(--theme-surface-strong))` + `border:1px solid var(--theme-line)` + `box-shadow:var(--shadow-light)` + `backdrop-filter:blur(24px)` |
| `.glass-panel-soft` | 同上但 `surface-soft→surface`、`blur(18px)`、`--shadow-soft` |
| `.gold-ring` | **已非金色**：`box-shadow: inset 0 0 0 1px #bdbbff33, 0 16px 36px #0101201a`（淡紫内描边） |
| `.ambient-page::before` | 三光斑 `radial-gradient(circle at 14% 14%,#ef2cc114,…24%) / (78% 16%,#bdbbff2e,…22%) / (50% 100%,#55a6ff14,…26%)`，`absolute inset:0; z-index:-2` |
| `.ambient-page::after` | 顶部柔光洗白 `linear-gradient(#ffffff85,#ffffff1f), radial-gradient(circle at 50% 0,#ffffffa6,transparent 56%)`，`z-index:-1`；暗色下调低白洗（`#ffffff0a` + 淡紫光） |
| `.section-label` | `IBM Plex Mono; color:var(--theme-text-secondary); letter-spacing:.055em; uppercase` |
| 圆角 | 主面板 32px / 卡片 24px / 次卡片 18px / 输入·按钮 10-12px / 图标按钮 6-8px / 胶囊·计数徽章 999px |
| 分割线 | `linear-gradient(90deg,transparent,var(--theme-line-strong),transparent)` 1px |
| 卡片投影 | `0 24px 80px rgba(0,0,0,.14)`；小元素 `0 10px 24px #0f172a0d` |

### 4.1 分段控件（实测 `.game-designer-segmented-control`，抄值不抄类）

```css
.segmented { background:#f1f5f9b8; border:1px solid #e2e8f0eb; border-radius:12px;
  display:inline-flex; align-items:center; gap:3px; padding:3px; }
.segmented button { border-radius:8px; min-height:28px→本项目 32px（见偏差②）; }
.segmented button.is-active { color:#fff; background:#0f172a; }   /* 亮色实测 */
```

暗色适配（官方面板恒深故控件区域观感不变，本项目工作台导航在画布侧随主题）：`--segmented-bg:#0f172a8c; --segmented-border:#ffffff1f; active:白底深字`。

### 4.2 Prompt 输入区（实测 composer）

`background:#fff（两主题恒定=官方对比面板设计）; border:1px solid #e2e8f0f2; border-radius:12px; min-height:104px; padding:16px; box-shadow:0 10px 26px #0f172a0d`。
官方 `padding-right:72px` 变体是 `has-credit-cost` 计价 pill 专属——**剔除**（铁律：无计价）。占位符固定石板灰 `#94a3b8`（composer 恒白，占位符不随主题翻色）。

### 4.3 设置面板（实测）

`border:1px solid var(--theme-panel-border); background:var(--theme-settings-bg)`（深藏青渐变玻璃，**两主题同值**）；面板内文字用 `--theme-panel-text/-muted`；圆角 24px。

### 4.4 动效与状态（实测）

- 过渡：`transform .22s, color .22s, border-color .22s, background-color .22s, box-shadow .22s`（**color 必须在列**：否则主题切换/换 tab 时背景与文字过渡脱同步，出现白底白字错帧闪烁）
- hover 抬升：`translateY(-1px)`；焦点环：`:focus-visible { box-shadow:0 0 0 4px #bfdbfe57 }`
- 收起/展开：`width .52s cubic-bezier(.22,1,.36,1)`（设置面板 flex-basis 同步）
- 面板调宽：`--settings-panel-width`（缺省 392px，域 320-560px），拖拽手柄 `col-resize; 12px`
- `color-scheme`: 亮色 `:root{color-scheme:light}` / 暗色 `[data-theme="dark"]{color-scheme:dark}`（原生 select/滚动条随主题）

## 5. 布局骨架（官方 workspace 同款）

```
┌──────────────────────────────────────────────────────────────┐
│ header: 标题(hero渐变字) + 状态点 + 主题切换（底部 1px line）  │
├──────────────────────────────────────────────────────────────┤
│ 模式导航：图像生成 │ 图像处理 │ 纹理与瓦片 │ UI 聚合表 │ 精灵动画 │
├────────────────────────────────┬─────────────────────────────┤
│  左画布/产物区 glass-panel      │ 右设置面板 settings-bg       │
│  (32px 圆角 + gold-ring)       │ 392px 可拖拽(320-560) 可收起 │
│  - 空态(虚线框+muted)          │ ├ 组内二级分段(双模式组)      │
│  - 会话任务 chips              │ ├ prompt composer            │
│  - 产物图 pixelated            │ ├ 分段/数值/开关/色板字段     │
│  - 报告卡(如实) 组件表          │ ├ dropzone(multipart 线)     │
│  - JSON 下载链接               │ └ [提交] 主按钮+状态/错误行   │
└────────────────────────────────┴─────────────────────────────┘
响应式 <1024px：单栏，设置面板移至画布下方，手柄隐藏
```

- 五模式承载八表单：纹理与瓦片={texture, tileset}、UI 聚合表={ui_gen, ui_extract}、精灵动画={anim_pack, animate}（组内二级分段切换）。
- 产物图 `image-rendering: pixelated`（像素素材工作台关键体验）。
- 字段标签 mono 11px/800 uppercase；主按钮 `--theme-action-*` min-height 38px radius 10px 字重 800。

## 6. 目视核对表 + 已知偏差

### 6.1 目视核对表（2026-09-20，visual-judge 两轮裁决 5/5 通过）

| 核对项 | 结果 |
|---|---|
| 骨架：header+五模式导航+左画布+右深藏青面板 | ✅ 与官方 workspace 同构 |
| 亮色默认 + 暗色切换（localStorage 记忆，冷加载直入） | ✅ 两套 token 均可读 |
| 配色/光斑/玻璃/圆角/字体层级 vs meowa.ai 首屏 | ✅ 视觉语言一致（功能布局天然不同） |
| 暗色控件可读性（激活态白底深字/占位符灰字/select 深色） | ✅（第一轮 fail→修复→复审 pass） |
| 报告卡/组件表/产物图（pixelated）呈现 | ✅ 数值完整无截断 |
| 表单 8 线（分组二级分段/动态字段/dropzone/计数徽章） | ✅ 浏览器逐面板核验 |

### 6.2 已知偏差（不许静默掩饰，逐条注明处置）

1. **分段控件规格取实测真身**：任务书转写为 `.segmented{background:#f1f5f9; grid; radius 10; min-height:46px}`；生产 bundle 实测真身是 `.game-designer-segmented-control{background:#f1f5f9b8; border:#e2e8f0eb; radius 12; padding 3px; gap 3px; inline-flex}` + 按钮 `radius 8` + active `#0f172a` 底白字，bundle 内无 `.segmented` 类、无 `grid-auto-flow:column`。**处置：按实测真身实现**；按钮 min-height 28→32px（中文枚举标签可读性，仅此一值放大）。
2. **分段控件按钮高度 32px**（实测 28px）：中文双字标签在 28px 下局促；观感差异 4px，记偏差。
3. **`ambient-page::before` 光斑位置**：实测 `14%/14%`、`78%/16%`、`50%/100%`（第三枚在页底）；任务书未给出 ::before 具体位置（其 12%/84%/52% 是 html 底色的光斑）。**处置：按实测。**
4. **暗色主题下画布侧组件的自适配**：官方 bundle 未披露暗色下分段控件等画布侧组件的变量映射（其设置面板恒深藏青，分段控件恒亮）。本项目按 token 语义自适配（`--segmented-*` 暗色档、`::after` 暗色减白洗、报告状态色双档），观感与官方暗色工作台一致，属**推断适配**非实测值。
5. **截图管线伪影（非产品缺陷，记录备查）**：IAB 截图管线在此环境有秒级合成滞后，曾截到主题切换 220ms 过渡中帧与类切换前的旧帧；均已用「冷加载直入目标主题 + 同帧 DOM 激活态核对」排除。真实浏览器无此现象。
6. **多语言/教程/快捷键/画布缩放平移/跨会话历史**：官方有，本轮明确不做（任务书 §7；跨会话历史需新增 `GET /api/v1/jobs`，后端扩展另轮）。
