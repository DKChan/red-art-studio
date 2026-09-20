# Web UI 样式规格 v2 — 视觉基线对齐 meowa.ai（2026-09-20/21 实测复核 + 工作台实拍对齐）

> 来源：meowa.ai 生产 CSS bundle `/assets/main-BsPQOrTz.css`（618,762B）token 实测 + **官方工作台登录后实拍截图**（2026-09-21 用户提供：像素美术工作台 + 首页功能栅格）。
> 本文档是 Web UI 实现的唯一视觉依据（v1 的暗色单页规格作废）。
> **重要修正（2026-09-21）**：landing bundle 里的深藏青 `--theme-settings-bg` 是营销页语言；官方工作台实拍参数面板为**白底浅色**，画布为**浅灰绿点阵场**。本规格以工作台实拍为准，深藏青仅保留在暗色主题。
> 铁律不变：只借视觉语言，不做 credit/钱包/计价/登录注册/订阅/多租户（官方 composer 的积分黄 pill 不复刻）；无框架无构建无外部 CDN（字体本地 woff2），**离线可用**；Tailwind「抄值不抄类」。

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

### 4.3 设置面板（2026-09-21 工作台实拍修正）

- 亮色：`--theme-settings-bg:#ffffff` 白底通栏面板，左缘 1px `--theme-line` 分隔，无卡片圆角；文字用 `--theme-panel-text:#0f172a` / `--theme-panel-text-muted`。
- 暗色：回深藏青渐变玻璃（`linear-gradient(180deg,#0e0e34,#010120)`），文字白。
- **面板 intro 头**（官方形态）：居中小 mono 标签（uppercase）+ 模式名大标题（22px/800）+ 一句描述（muted）。
- **composer 沉底**：prompt 输入区在面板最底部，其后为右下角对齐的黑色胶囊主按钮（暗色反转白底），如官方发送键位。

### 4.4 动效与状态（实测）

- 过渡：`transform .22s, color .22s, border-color .22s, background-color .22s, box-shadow .22s`（**color 必须在列**：否则主题切换/换 tab 时背景与文字过渡脱同步，出现白底白字错帧闪烁）
- hover 抬升：`translateY(-1px)`；焦点环：`:focus-visible { box-shadow:0 0 0 4px #bfdbfe57 }`
- 收起/展开：`width .52s cubic-bezier(.22,1,.36,1)`（设置面板 flex-basis 同步）
- 面板调宽：`--settings-panel-width`（缺省 392px，域 320-560px），拖拽手柄 `col-resize; 12px`
- `color-scheme`: 亮色 `:root{color-scheme:light}` / 暗色 `[data-theme="dark"]{color-scheme:dark}`（原生 select/滚动条随主题）

## 5. 布局骨架（官方 workspace 同款）

```
┌──────────────────────────────────────────────────────────────┐
│ header: 标题(纯色深字) + 状态点 + 主题切换（底部 1px line）    │
├──────────────────────────────────────────────────────────────┤
│ 模式导航：白胶囊组，激活黑底白字（官方首页同款）               │
├────────────────────────────────┬─────────────────────────────┤
│  左画布 = 页面本体              │ 右参数面板（白底通栏,392px   │
│  浅灰绿点阵场(#eef0ea+点阵18px) │ 可拖拽 320-560,可收起）      │
│  - 左下浮层工具条(占位)         │ ├ intro 头(标签+标题+描述)   │
│  - 空态(虚线框+muted)          │ ├ 组内二级分段(双模式组)      │
│  - 会话任务 chips              │ ├ 参数字段(分段/数值/开关…)   │
│  - 产物图 pixelated            │ ├ dropzone(multipart 线)     │
│  - 报告卡(如实) 组件表          │ └ composer 沉底+黑胶囊按钮    │
└────────────────────────────────┴─────────────────────────────┘
响应式 <1024px：单栏，面板移到画布下方，手柄隐藏
```

- 五模式承载八表单：纹理与瓦片={texture, tileset}、UI 聚合表={ui_gen, ui_extract}、精灵动画={anim_pack, animate}（组内二级分段切换）。
- 产物图 `image-rendering: pixelated`（像素素材工作台关键体验）。
- 字段标签 mono 11px/800 uppercase；主按钮 `--theme-action-*` min-height 38px radius 10px 字重 800。

## 6. 目视核对表 + 已知偏差

### 6.1 目视核对表

| 轮次 | 基线 | 结果 |
|---|---|---|
| 2026-09-20 第一轮 | meowa.ai landing 截图 | visual-judge 5/5 通过（但对照标准放宽为"组件语言同源"，整页观感未对照——教训记录） |
| 2026-09-21 第二轮 | **用户提供的官方工作台登录后实拍** | 首轮对照发现 5 处差异（面板深藏青/画布卡片化/无 intro 头/方角按钮/渐变标题）→ 全部修复 → visual-judge 复裁 **3/3 通过，对齐达成** |

### 6.2 已知偏差（不许静默掩饰，逐条注明处置）

1. **landing token ≠ 工作台观感（本轮核心修正）**：生产 bundle 的深藏青 `--theme-settings-bg`、`--theme-panel-text:#fff` 等变量在官方工作台默认视图中并未用作面板样式（实拍为白面板）。处置：亮色按实拍（白面板），深藏青仅保留给暗色主题；教训=**token 同源不等于观感一致，目视对照必须用渲染后的页面**。
2. **分段控件规格取实测真身**：任务书转写为 grid 版；bundle 实测真身 `.game-designer-segmented-control{#f1f5f9b8 / border #e2e8f0eb / radius 12 / padding 3px}` + 按钮 radius 8 + active `#0f172a` 白字。处置：按实测；按钮 min-height 28→32px（中文标签可读性）。
3. **点阵画布取值为近似采样**：官方截图点阵底/点色按目视取样（`#eef0ea`/`#d9ddd2`，18px 栅格），bundle 中未挖到对应规则，属推断取值。
4. **画布浮层工具条为装饰占位**：官方有缩放/图层功能，本轮只还原观感（禁用态 + title 说明），功能后续轮。
5. **暗色主题为推断适配**：官方工作台仅见亮色实拍；暗色按同一设计语言反转（深藏青面板回归、点阵变暗、胶囊反转白底），无官方参照。
6. **官方 composer 的积分黄 pill 不复刻**（铁律：无计价）；「创意智能体」标签以自有品牌 "red art studio" 代位。
7. **截图管线伪影（非产品缺陷，备查）**：本环境嵌入式截图管线对导航胶囊类切换有秒级合成滞后，已用「强制重绘后再截 + 同帧 DOM 激活态核对」排除；真实浏览器无此现象。
8. **多语言/教程/快捷键/画布缩放平移/跨会话历史**：官方有，本轮明确不做（跨会话历史需 `GET /api/v1/jobs`，另轮）。
