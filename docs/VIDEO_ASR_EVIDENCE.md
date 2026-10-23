# 视频审核音频证据通道（线路 A · 第一阶段）

给视频审核加一条独立于 VLM 的确定性音频证据通道：本地 ASR 转写整条音轨，产出带毫秒区间与覆盖断档的转写结果，作为冻结证据随每次取证运行保存。**本阶段不参与任何 verdict 计算**——`required_speech` / `forbidden_expression` 的判定逻辑、VLM 有界工具循环、品牌确认闸门全部不变。

## 背景与问题

视频规则组件的判定是"模型供观察、程序按品牌冻结参数算结论"。但音频两类组件的事实源踩在模型自述上：`channel=audio`、`certain=True`、"我完整听过了音轨"（`coverage_complete`）都由模型声明，程序无法核验，`coverageVerified` 因此永远为 False。口播要求逐项覆盖、禁用词要求零漏检，恰恰对完整性最敏感。

## 本阶段做了什么

每次真实视频取证运行（workflow 绑定或独立测试），在源文件哈希核对通过之后、发送模型之前，用本地 faster-whisper 对**完整音轨**转写一次。结果同时写入：

- `evidence/<run_id>/manifest.json` 的 `audioTrack` 字段；
- 运行结果的 `audioTrack` 字段（成功与失败保留部分结果时都写入）。

结构（`backend/app/modules/video_model/evidence/asr.py`）：

```
{ available, reason,                  # 失败显式降级，绝不伪造
  segments: [{startMs, endMs, text, confidence}],
  coverageMs: [[..],..],              # 实际产出文本的区间并集
  gapsMs: [[..],..],                  # 音轨内未被覆盖的断档
  hasAudio, truncated, durationMs, language,
  sourceSha256,                       # 绑定本次冻结的不可变源文件
  engine: {name, model, device, computeType} }
```

诚实性约定：依赖未安装 / 抽音轨失败 / 转写异常 → `available=false` + 真实原因；口播与禁用词检查据此**以人工核对为准**，页面与后续阶段不得把"未就绪"读成"无问题"。音轨超 600s 上限截断时 `truncated=true`，断档不代表未发声。

## 真机端到端实测结论（2026-10-26，36s 真实上传视频）

两个只有真模型才能暴露的问题，均已修复并被回归测试锁定：

1. **返回顺序**：faster-whisper 返回 `(segments, info)`，与直觉相反；假引擎单测曾按错误顺序构造，导致"单测绿、真机炸"。FakeEngine 现按真实契约建模。
2. **音乐/含糊音轨的幻觉**：`base` 模型不加 VAD 时对纯音乐段成段输出 "I'm sorry." 式循环幻觉，语言检测置信度仅 0.58。现在固定 `vad_filter=True, beam_size=5`——非语音区被划入 `gapsMs`（该视频 0-13.8s、30.4-36.1s 两段音乐/无语音正确显示为断档），断档因此成为"未检测到人声"的诚实信号，而不是"转写失败"。
3. **语言提示**：`ASR_LANGUAGE` 环境变量（如 `zh`）或 `transcribe(language=...)` 可指定语言；自动检测时置信度记入 `languageProbability`，低于 0.5 且未指定语言会附带"转写可能不可靠、须人工核对"的 note。**中文素材建议设 `$env:ASR_LANGUAGE='zh'` 后再跑**——base 模型对中文口播被误判成英文时的输出完全不可用。

结论：通道可用，但 `base` 模型对 BGM 覆盖的口语转写精度有限——阶段二做 required_speech 硬定位前，建议用 2-3 条真实达人成片（人声清晰、中文口播）评估是否升到 `small`（约 460MB，CPU 可跑，慢约 3 倍）。

## 使用

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-asr.txt
```

首跑会下载 base 模型权重（约 145MB，huggingface_hub 缓存，可用 `HF_HOME` 指定目录）；之后完全离线、零边际成本、无新增付费调用。不装该依赖不影响现有功能（自动降级为显式不可用）。

## 后续阶段（尚未实施）

- 阶段二：`required_speech` 用 ASR segments 做硬定位——term 命中、时间戳、置信度来自转写结果；模型的语义判断（同义/否定/数值差异）必须引用 segment 索引，程序核验索引存在且区间相符（对齐脚本审核的 verified-quote 门禁）。
- 阶段三：`forbidden_expression` 对全量 segments 扫描，扫描面不再依赖模型自愿报告；无 gap、未截断、有音轨时 `coverageVerified` 才有置真条件。
- 复核 UI 展示转写与断档，人工先积累 ASR 与 VLM 判断的差异样本，再推进上述收口。

## 验证记录（2026-10-21）

`backend/tests/test_asr_evidence.py`：merge/gap 纯函数、假引擎注入的完整转写结构（区间裁剪、空文本丢弃、中间 wav 用后即删）、依赖缺失/抽取失败/引擎崩溃三种降级都显式 `available=false` 不抛穿、段数上限标记截断、manifest 携带 audioTrack。全量后端套件在该改动后通过；契约与前端类型零变化。

## 本地 ASR 默认关闭（2026-10-23）

品牌实测后反馈本地 faster-whisper 的转写质量不足以支撑判定，因此**默认不再运行本地 ASR**：`asr.enabled()` 读 `REVIEW_VIDEO_ASR`，默认 `off`，只有设成 `on/1/true/yes` 才会转写。

关闭时 `audioTrack` 记为不可用，理由写明"本地 ASR 已关闭（REVIEW_VIDEO_ASR=off）：口播以模型听到的原声音轨和画面字幕为准，不生成转写证据"，并保留 `hasAudio` 的真实值（原片确实有音轨）。判定链路不依赖转写：`component_runtime` 的口播/禁用词组件用的是 `session.has_audio` 与模型提交的 audio 观察，`required_speech`/`forbidden_expression` 不受影响。

实测佐证：真实任务里第 10 条「不得使用提神醒脑等功效词」被判 non_compliant，依据是画面字幕里的「提神醒脑」，不需要 ASR；而转写当时因 PermissionError 根本没有生成。

要重新启用：`REVIEW_VIDEO_ASR=on` 并安装 `backend/requirements-asr.txt`。若后续换用云端 ASR，建议保留同一个 `audioTrack` 结构（segments/coverageMs/gapsMs/sourceSha256），判定侧无需改动。
