# 功能总览阶段遗留疑问（raw-overview）

- 官网 Gallery 是否有公开 API？本地 CLI/Skill 文档只覆盖生成侧，未见画廊检索/展示接口（上下文：3.5 UGC 画廊仅来自官网抓取）。
- 各具体能力的精确 credit 单价未在本地文档中列出（仅官网换算 1 credit≈1 图、10 credits≈1 动画）；quality/resolution/speed 各档差价未知。
- custom templates 档位上限（5/10/20）对应的「自定义工作流」在 CLI 中仅为 custom-workflow-list/run，模板如何创建（Web 端？）不明。
- Archmage $150 档的具体 credits 数量与并发/模板上限未确认（官网 pricing 抓取信息不完整）。
- pixel-gen preset 的完整清单（32px/64px 具体条目、默认数量）需运行 `pixel-gen-template-info` 才能确认，本地文档未内置。
- 原版帧动画命令（新帧动画 8/16/24/32 帧）与 animate-run 的服务端关系未知（是否同一 backend 工作流的两个版本），复刻时需决定是否合并。
- side-scrolling HD 的 7 种 art_style 与 pixel 版差异、以及横版三层地图的输出分辨率/尺寸契约，本地文档未明确。
- Game Designer 的 planning model 底层模型与「web-research 工具」计费细节不明（game-design.md 仅给出 token 单价与 60 credits/月免费额度）。
- 官网抓取的三大卖点与本地文档能力面高度吻合，但首页是否有「模板市场 / template store」类入口未确认。
- CLI 87 个子命令中别名（如 sfx-run、map-preset-search）占若干个，确切「独立能力数」可能少于 87，精确去重统计未做。
