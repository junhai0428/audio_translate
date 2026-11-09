# 协作协议

项目根：`E:/Projects/音频翻译工具`

**这是一条纯音频链路**：输入是已提取好的音频文件，输出是越南语配音轨。不做视频抽轨（用户侧工具有现成方案），不做去字幕（独立项目 `E:/Projects/字幕擦除工具`），不做成片合成。

## 四步，按顺序干

| 顺序 | 干什么 | 角色卡 | 输入 | 主产物 |
|---|---|---|---|---|
| 第 1 步 | 人声分离 | `01-人声分离.md` | 源音频 | `01_separate/<名>.mp3`、`<名>_bgm.mp3` |
| 第 2 步 | 语音转写 | `02-语音转写.md` | 第 1 步人声轨 | `02_asr/asr.json`、`asr.srt` |
| 第 3 步 | 翻译控长 | `03-翻译控长.md` | 第 2 步 asr.json | `03_translate/zh_opt.txt`、`vi.txt`、`segments_vi.json` |
| 第 4 步 | 配音对齐 | `04-配音对齐.md` | 第 3 步 segments_vi.json | `04_voice/<名>_vi_voice.mp3/.wav` |

```
源音频 → ① 分离人声 → ② 逐句转写+性别 → ③ 校对翻译控长 → ④ 配音对齐 → 越南语配音轨
```

**字幕擦除是独立项目**（`E:/Projects/字幕擦除工具`），不在本链路内，也不消费本链路任何产物。它只吃视频源文件，产出无字幕视频。

## 每次派单写清这六项

```
【工单号】<音频名>-<日期>
【源音频】<绝对路径>（mp3/wav/m4a/aac/flac/ogg）
【工单目录】E:/Projects/音频翻译工具/work/<音频名>/
【目标语种】越南语
【特殊要求】如"只做前 30 秒"、"男声用 NamMinh"
【交付时限】
```

工单目录由调度者创建，下游只读上游产物，禁止覆盖上游文件。

## 交接契约

1. **第 1 步 → 第 2 步**：`01_separate/<名>.mp3` 必须纯人声（无 BGM、无削波），时长 = 源音频 ±0.2s。第 2 步抽检 F0 与能量，分离不净有权打回。
2. **第 2 步 → 第 3 步**：`02_asr/asr.json` 字段 `segments[].{id,start,end,text}`，时间单调不减、无重叠、覆盖所有有声区间。第 3 步发现语义断裂有权打回。
3. **第 3 步 → 第 4 步**：`03_translate/segments_vi.json` 每条含 `{id,start,end,slot,max_syl,zh,vi}`。第 4 步只负责把 `vi` 念出来，不得改语义；音节超 `max_syl` 有权要求第 3 步压缩。
4. **第 4 步 → 交付**：`04_voice/<名>_vi_voice.mp3` 总时长 = 源音频 ±0.3s，逐句起点锚定原时间轴。这是本项目的最终交付物（配音轨），与画面合成由下游视频侧负责。

## 通用验收（调度者抽查，任一不过打回）

| 项 | 判定 |
|---|---|
| 文件存在且非空 | 大小 > 10KB，ffprobe 可读 |
| 时长 | 与源音频偏差 ≤ 0.3s |
| 逐句对齐 | 抽 3 处（开头/中段/结尾）听：越南语起句落在原句时间点上，偏差 ≤ 0.15s |
| 性别 | 配音音色 = 第 2 步判定性别 |
| 语义 | 抽 5 句对齐中越：越南语含该句核心信息，数字/品牌未丢 |
| 合规 | 无"祛湿/健脾/根治/排毒"直译，无医疗承诺 |
| 无遗留 | 无中文字符混入越南语、无 TTS 读出符号、无重叠爆音 |

## 打回规则

- 一次打回附**具体证据**（时间码 + 实测值 + 期望值），不写"效果不好"。
- 同一步连打回 2 次，调度者亲自定位根因，再决定换方案或降级。
- 上游改产物必须通知下游重跑，禁止静默改动。

## 并行策略

| 步骤 | 能否并行 | 说明 |
|---|---|---|
| 第 1 步 人声分离 | 可跨文件并行 | demucs 走 **GPU**（RTX 3060，0.1 倍实时），显存占用约 0.6GB |
| 第 2 步 语音转写 | 可跨文件并行 | whisper **GPU** float16（无 GPU 时退回 int8 CPU） |
| 第 3 步 翻译控长 | 可跨文件并行 | LLM 调用，无硬件争用 |
| 第 4 步 配音对齐 | 可跨文件并行 | edge-tts 本身已并发（Semaphore 8） |

`run.py -j N` 即按文件级并行。

## 工具箱（可自取，不必从零造）

```bash
cd "E:/Projects/音频翻译工具"
E:/Projects/whisper_env/Scripts/python.exe run.py                # 跑完 4 步
E:/Projects/whisper_env/Scripts/python.exe run.py --stages 1,2,3 # 只出文案，人工审核
E:/Projects/whisper_env/Scripts/python.exe run.py --stages 4     # 文案确认后配音
```

单独调某一步：

```bash
E:/Projects/whisper_env/Scripts/python.exe -c "
import sys, pathlib; sys.path.insert(0, r'E:/Projects/音频翻译工具')
from pipeline.config import Job, load_config
from pipeline.separate import run_stage
job = Job(pathlib.Path(r'<源音频>'), load_config())
print(run_stage(job, log_fn=print))
"
```

| 步骤 | 模块 | 说明 |
|---|---|---|
| 第 1 步 | `pipeline.separate` | demucs htdemucs 两轨分离 → mp3 |
| 第 2 步 | `pipeline.asr` | faster-whisper large-v3 + F0 性别判定 |
| 第 3 步 | `pipeline.translate` | LLM 校对 + 翻译 + 音节控长（需 api_key；无 key 时由角色自己做） |
| 第 4 步 | `pipeline.tts` | edge-tts 并行 + 时隙对齐 + atempo |

## 环境事实（写死，别重新试探）

| 项 | 值 |
|---|---|
| Python | `E:/Projects/whisper_env/Scripts/python.exe`（3.13，装了 demucs 4.1 / faster-whisper 1.2 / edge-tts 7.3） |
| torch | 该 env 是 **CPU 版** `2.14.1+cpu`，demucs 必须 `-d cpu` |
| whisper 模型 | `E:/Projects/whisper_env/models/whisper_models/models--Systran--faster-whisper-large-v3/...`（本地已下载） |
| 装包 | 一律走清华镜像 `-i https://pypi.tuna.tsinghua.edu.cn/simple` |
| 缓存 | `HF_HOME=E:/Projects/whisper_env/models/hf_cache`、`TORCH_HOME=E:/Projects/whisper_env/models/hf_cache/torch`（C 盘长期紧张） |
