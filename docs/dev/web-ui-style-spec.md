# Web UI 样式规格 — 仿原版（2026-09-10 实测提取）

> 来源：原版官方 CSS/JS bundle 实测分析（下载到 /tmp 的官方 bundle 文件，608KB CSS + 830KB JS）。
> 本文档是 Claude Code 实现的唯一样式依据。铁律不变：只仿视觉，不做 credit/用户系统/多租户；单文件内嵌 HTML/CSS/JS（vanilla，无框架、无构建、无外部 CDN，**离线可用**）。

## 1. 设计 token（实测值，直接采用）

### 1.1 暗主题变量体系（原版用 `data-theme=dark`，整套照搬）

```css
:root, [data-theme="dark"] {
  --theme-canvas: #010120;                  /* 页面底色：深空蓝紫，不是纯黑 */
  --theme-canvas-soft: #08082a;
  --theme-surface: #0e0e34d6;               /* 玻璃面板渐变起点 */
  --theme-surface-strong: #0a0a28f5;        /* 渐变终点 */
  --theme-surface-soft: #ffffff14;
  --theme-line: #ffffff1f;                  /* 主分隔线 */
  --theme-line-strong: #ffffff38;
  --theme-text-primary: #ffffff;
  --theme-text-secondary: #ffffffb8;
  --theme-text-muted: #ffffff75;
  --theme-header-bg: #010120b8;
  --theme-panel-bg: linear-gradient(180deg, #0f0f3aeb, #010120f5);
  --theme-panel-soft-bg: linear-gradient(180deg, #ffffff1a, #ffffff0f);
  --theme-panel-border: #ffffff1f;
  --theme-panel-hover: #ffffff14;
  --theme-panel-text: #ffffff;
  --theme-panel-text-muted: #ffffff75;
  --theme-input-bg: #ffffff14;              /* 输入框：半透明白 */
  --theme-input-text: #ffffff;
  --theme-input-border: #ffffff1f;
  --theme-action-bg: #ffffff;               /* 主按钮：白底深字（原版的反转设计） */
  --theme-action-text: #010120;
  --theme-action-border: #ffffffe0;
  --theme-action-hover-bg: #d8d7ff;         /* hover 淡紫 */
  --theme-inline-action-text: #ffffff;
  --theme-inline-action-hover: #bdbbff;
  /* 氛围光斑（aurora orbs，页面背景装饰，必须要有） */
  --theme-orb-one: #ef2cc129;               /* 粉紫 */
  --theme-orb-two: #bdbbff38;               /* 淡紫 */
  --theme-orb-three: #7dc4ff33;             /* 天蓝 */
  --theme-hero-gradient: linear-gradient(135deg, #020214 0%, #5656a9 36%, #ef2cc1 72%, #2c9cff 100%);
}
```

### 1.2 字体

- 正文/UI：**Manrope**（原版预载 `manrope-latin.woff2`）；中文回落 `"PingFang SC", "Microsoft YaHei"`
- 标签/数值/字段名：**IBM Plex Mono, monospace**（原版控制栏字段标签用 mono 12px/800）
- **离线铁律**：字体不引外链。下载 woff2 到 `server/app/static/fonts/`（Manrope latin 400/600/800 一档即可 + IBM Plex Mono 600），用 `@font-face` 本地引用；拿不到字体文件时用 `font-family` 声明 + 系统回落，不得引入 CDN

### 1.3 标志性视觉元素（缺失任何一项即不算仿原版）

| 元素 | 实测规格 |
|---|---|
| glass-panel | `background: linear-gradient(180deg, var(--theme-surface), var(--theme-surface-strong)); border: 1px solid var(--theme-line); backdrop-filter: blur(24px)` |
| gold-ring（主面板内描边） | `box-shadow: inset 0 0 0 1px #bdbbff33, 0 16px 36px #0101201a` |
| 大圆角 | 主面板 32px，子卡片 24px，按钮 10px，layer 卡 14px，画布 12px |
| 氛围光斑 | 三个 `position:fixed` 大半径模糊圆（粉紫/淡紫/天蓝），z-index 在内容之下 |
| 卡片投影 | `0 24px 80px rgba(0,0,0,0.14)` |
| 面板 hover | `background: var(--theme-panel-hover)` |

## 2. 布局骨架（原版 studio 同款）

```
┌────────────────────────────────────────────────────┐
│ header: 标题 red-art-studio + 副标题（底部 1px line） │
├──────────────────────────────┬─────────────────────┤
│                              │ 右侧控制栏          │
│   左侧：画布/预览区          │ width: 408px 固定   │
│   - 空态提示 / 生成图展示    │ background: panel-bg│
│   - 生成图 max-height 撑满   │ border-left: 1px    │
│   - 图 image-rendering:      │ padding: 16px       │
│     pixelated（像素风还原）  │ overflow-y: auto    │
│                              │ ┌─────────────────┐ │
│                              │ │ prompt textarea │ │
│                              │ ├─────────────────┤ │
│                              │ │ 尺寸/张数 字段  │ │
│                              │ ├─────────────────┤ │
│                              │ │ provider select │ │
│                              │ ├─────────────────┤ │
│                              │ │ [生成] 主按钮   │ │
│                              │ └─────────────────┘ │
└──────────────────────────────┴─────────────────────┘
响应式（<1024px）：改单栏，控制栏移到画布下方（原版: grid-template-columns:1fr + overflow-y:auto）
```

- 主体 grid：`grid-template-columns: minmax(0,1fr) 408px`（实测值）
- 控制栏按 **section 分组**：`section + section { border-top: 1px solid var(--theme-line); margin-top: 16px; padding-top: 16px }`
- 字段标签：mono 字体 11-12px / 800，颜色 `--theme-text-secondary`
- 生成按钮：`is-generate` 风格 — `background:#2563eb; border-color:#2563eb; color:#fff`，hover `#1d4ed8`；或用主题反转白底 `--theme-action-*`（二选一，保持一致）
- 按钮 min-height 38px、radius 10px、font-weight 700-800、13px
- 输入框：`background: var(--theme-input-bg); border: 1px solid var(--theme-input-border); color: var(--theme-input-text); min-height: 36px; border-radius: 10px`
- 画布区：空态居中提示（muted 文案 + 虚线框）；出图后图片居中 `border-radius:12px; box-shadow: 0 22px 48px #02061757`，`image-rendering: pixelated`（512 像素图放大显示时不糊，这正是游戏素材工作台的关键体验）
- 状态/错误文案：生成中显示在画布区顶部或控制栏按钮下方，错误用 `#f87171` 系红（暗主题适配）

## 3. 交互逻辑（不变，已验收）

- 提交 POST `/api/v1/generations` → 202 → 2s 轮询 GET → succeeded 用 `/api/v1/artifacts/...` 展示；failed 显示错误
- Provider 下拉仍由服务端 `{provider_options}` 拼入，默认 pollinations
- 现有 JS 提交/轮询逻辑可保留，只重构视图层；不得改变 API 契约

## 4. 交付与验收标准

1. `uv run pytest` 全绿（现有 60 项不破，UI 测试同步更新断言新结构/样式类）
2. `uv run ruff check .` 通过
3. `curl -s localhost:8600/` 返回的 HTML 中可验证：`data-theme="dark"`、`--theme-canvas:#010120`、`backdrop-filter: blur(24px)`、`grid-template-columns: minmax(0,1fr) 408px`、`image-rendering: pixelated`、Manrope/IBM Plex Mono 声明、三个 orb 元素、glass-panel 与 gold-ring 类
4. 页面无任何外链资源（grep 无 `http://`、`https://` 的 src/href 资源引用；字体本地化或系统回落）
5. 8600 真实启动，浏览器/截图人工核验：深空蓝紫底 + 玻璃面板 + 右 408px 控制栏 + 光斑氛围（与原版观感一致）
6. STATE.md 迭代日志新增条目 + ROADMAP 若涉及 checkbox 勾选 + git commit（feat(web-ui): 仿原版暗主题样式复刻）
7. 不改 `server/app/api/generations.py`、`artifacts.py`、providers、jobs 任何一行（纯 UI 层改动；测试里 UI 断言文件除外）

## 5. 范围外（明确不做）

- 登录页 / 导航多工具页 / gallery / pricing / credit 任何元素
- 明亮主题切换（原版有亮色 token，但单页工作台只做暗主题）
- React/组件库/构建链引入
