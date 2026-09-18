# 图像流水线阶段遗留疑问（raw-image-1：文生图素材 / 精灵动画 / 风格一致性）

- style-gen 的 `--variant 121|112|221` 语义是推断（请求字段为 background_variant/reference_variant，疑似"背景/参考变体"组合枚举），确切含义需实测或抓 Web 请求确认。
- character-multi-view 的隐藏参数 `--direction-mode mirror|ninegrid`（argparse SUPPRESS，默认 mirror）含义是推测：镜像复用合成 vs 九宫格布局，官方文档未说明，复刻八方向前需验证。
- 原版的 pixel-engine-v1.1 / frame-engine-v1.1 底层实现未知（自研还是封装第三方视频扩散模型），文档 02 选 AnimateDiff/CogVideoX 只是"最近似开源等价物"，帧间一致性/循环质量可能达不到其商业效果。
- 像素线的"完美像素"输出（官方禁止二次像素化）是模型原生能力还是服务端后处理（下采样+量化）未确认，直接影响自部署像素化管线放在生成层还是后处理层。
- one-click-upgrade-prompts 的变体 prompt 起草是纯 LLM 生成还是带官方模板库/升级阶梯词表，本地文档无信息。
- 八方向"5 真方向 + 3 水平镜像"方案是行业惯例推断，原版实际生成策略未证实（仅 direction-mode 默认 mirror 间接支持）。
- 文档 01 底模对比表中 Qwen-Image 等新指令跟随系模型的像素风 LoRA 生态成熟度未验证（网络受限，未做 web 搜索核实）。
- "Flux.1-dev 出像素风偏伪像素、需配像素 LoRA"是社区经验说法，未实测；像素线底模最终选型需跑分对比。
- video-run 的 `--action`/`--direction` 内置动作模板清单需运行 `video-prompt-list --motion-mode <mode>` 才能确认，本地未内置。
- web 契约中 nano-banana 的模型名 gemini-3.1-flash-lite/flash/pro-image 是原版内部命名还是 Google 官方发布名未验证；自部署接 Gemini API 时需核对当前可用的模型 ID。
- animate-run 通用帧模式"输出上限 480p"与原版帧动画 HD 720p 的能力差异，是两代引擎还是同一后端不同封装，未确认（影响复刻时是否合并为一条管线）。
- keyframes-run 的 `keyframe-strength`（0-1）作用于哪个条件层（IP-Adapter 权重 / ControlNet 权重 / 时间戳软约束）官方未披露，属实现细节猜测。
- 一次 `one-click-upgrade-run` 产出 1-8 张是"一次任务批量"还是"N 次子调用聚合"，计费与失败重试语义（部分失败怎么办）未知。

## 地图瓦片与纹理（文档 05）遗留疑问

- 四个地图 workflow（pixel_isometric_gen 等）后端如何保证瓦片中心锚定与精确几何（128×64 底座、六边 127 步距）未披露：是模板内先验引导、生成后几何矫正，还是两者结合，属推断；直接决定复刻时"生成层保证几何"还是"后处理归一层保证几何"的架构选择。
- `--similar-tiles` 的真实语义（等距默认 False、六边/HD 默认 True）官方未解释，"跨瓦片风格一致性控制"是靠共享种子、参考图链还是后端风格聚类，未确认。
- `--generation-speed normal|fast` 的实现差异（跳过精修步数/低步数采样/不同底模）未披露，影响自部署时 fast 档的成本/质量权衡设计。
- tileset-gen 的"引导色内部推断"（foreground/background guide colors，文档明言故意不暴露）具体机制未知：自部署程序化合成 dual-grid 图集与原版 AI 生成图集的过渡自然度差距需实测评估。
- HD 瓦片命令不暴露 remove-bg（HD 透明边距/背景由后端内部处理）的处理时机（生成时 green-screen 引导还是生成后 matting）未确认，影响 HD 线去背环节放在管线哪一级。
- 参考库 1000+ 预设的来源与版权状态（官方自制、授权还是聚合）完全未知；自部署替代种子库（CC0 打包 + 自建）的覆盖度与风格差距需调研。
- side-scrolling 三层"1K 级 16:9"的精确画布尺寸、三层是否强制同尺寸，本地文档只有模糊口径，需实跑一次产出检视；层间风格/光线一致性靠什么保证（共享参考？一次多区域生成？）也未说明。
- `mode=road/wall`（仅像素等距有）的参考数量契约与布局语义未在文档中给出（官方只给了 standard/edit/tetraploid 的数量），road/wall 是否有额外几何约束（如直路双向锚点）未确认。
- tetraploid 在像素六边（2×2）与 HD 六边（奇偶行两套占据偏移）之间的布局关系文档描述粒度不一，像素六边 tetraploid 的六格中心偏移表未给出，复刻渲染器时需自行推导或实测样本反推。

## UI 生成（文档 06）遗留疑问

- UI 分割数据的具体格式与字段完全未知（runner 下载投影只有 output_path/url，原版 CLI 源码 L1593，本地拿不到样本）；影响 components.json schema 只能自定，未来对齐官方格式需实测线上产物。
- UI 1K/2K 档位的精确像素矩阵未披露（官方明言"档位非承诺"）；影响自部署必须自定映射表，跨官方对图时尺寸不可比。
- UI extract 模式"重排"是否重新渲染元素未披露（只说 reorganized/reusable-looking）；影响默认路径选确定性装箱还是生成式重排。
- UI `split_components=false` 的实际效果未知（仅省略分割数据还是改变出图内容）；影响该开关只能按假设实现并标注。
- UI `--remove-bg-method` 在 web 参数契约中缺失、standard/advanced 背后真实模型未知；影响只能沿用 04 篇的 BiRefNet/rembg 两档近似。
- UI 生成 credit 单价未在本地资料出现（自部署无计费，仅调研记录）。
- 原版 CLI 源码 argparse 残留默认 `template="hd_retro_rpg"`（L6608）不出现在请求体中（L5182-5194 无 template 字段），是否对应隐藏后端模板机制未知；复刻时不要照抄。

## 音频生成（文档 07）遗留疑问

- 音效后端 `elevenlabs_generator`（原版 CLI 源码 L4715，全 CLI 唯一厂商命名泄露）与 ElevenLabs 确切关系未知（是否直连其 SFX API、模型版本）；影响音效底模无法对齐内部实现，只能契约等价选型。
- 音效 pack/variants 的服务端实现机制未知（多采样？LLM 分解？多次调用？）；影响自部署一致性策略无官方基准可对照。
- 音乐底模完全未披露（workflow 名中性）；影响 demo/pro 质量差距无法归因，开源选型只能赌。
- 音乐 pro 档精确时长上限未知（"three-minute"仅为官方示例措辞）；影响 pro 管线目标长度需自定。
- 音效 `--loop` 的服务端保证程度未知（真无缝/淡化/仅标注 loop 点）；影响自部署 crossfade 方案有效性需自行验收。
- 音效 `--temperature` 映射的采样器参数未知（黑盒 API）；影响只能做语义等价映射。
- 音乐参考图→音乐的消费机制未知（VLM 转述？专用图像编码器？）；影响该管线纯自研、效果无锚点。
- `audio_generate=false` 纯方向稿模式的服务端输出形态未知（CLI 恒传 true）；影响仅确认不复刻该暗门。
- 音频输出格式/采样率/声道未声明（runner 仅按 audio/* MIME 校验）；影响交付格式需自定（建议 wav 母带）。
- sound/music 的 credit 单价零披露（自部署无计费，仅调研记录）。

## 游戏设计 Agent（文档 08）遗留疑问

- planning model 底层模型未披露（仅知按轮增量计费、断点可恢复）；影响自部署选型无法对齐官方"策划深度"，只能按本地 LLM 自定。
- web-research 工具的实现机制未知（检索源、正文抽取、引用格式）；影响自部署需自建（SearXNG+trafilatura 类方案），效果无官方锚点。
- autoExecuteTools（Agent 自动执行付费素材工具）的具体工具清单与触发条件未披露；影响策划→素材链路的接缝设计只能按文件树约定。
- ask_user 之外是否还有其他 Agent 停机/等待形态未知；影响 manifest 状态机设计可能不完整。
- 设计文档树 `design_docs/` 的完整目录结构与 frontmatter 字段未在本地资料给出；影响复刻文档树 schema 只能自定。
- game_design 无独立 web_parameter_contract.json 段（契约全文仅 policy 一处提及，features 枚举覆盖 L5-321），参数契约覆盖不全的原因未知。
