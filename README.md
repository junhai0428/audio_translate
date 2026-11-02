# 音频翻译工具

把**中文口播音频**做成**越南语配音轨**：分离人声 → 逐句转写 → 校对翻译控长 → 配音对齐。

四步串行，每一步一个角色。本文档是这个项目的唯一说明书，含**背景来历、参数口径、验收标准、实测踩坑记录**——接手的人不用再看别的文件。

---

## 一、边界（先说清，避免误用）

| 做 | 不做 |
|---|---|
| 音频人声/BGM 分离（demucs） | **视频抽轨** —— 用户侧已有视频转音频工具，本链路只吃音频 |
| 逐句 ASR + 起止时间 + 男女判定 | **去字幕** —— 独立项目 `E:/Projects/字幕擦除工具` |
| 中文校对 + 越南语翻译 + 音节预算控长 | **成片合成** —— 配音轨交给下游视频侧 |
| edge-TTS 并行合成 + 逐句锚定原时间轴 | 视频编码/转码 |

输入：`mp3 / wav / m4a / aac / flac / ogg / opus / wma`
输出：`output/<名>/<名>_vi_voice.mp3`

## 二、四步干什么、按什么顺序

| 顺序 | 干什么 | 输入 | 产物 |
|---|---|---|---|
| 第 1 步 | **人声分离** —— demucs 拆出纯口播和 BGM | 源音频 | `01_separate/<名>.mp3`、`<名>_bgm.mp3` |
| 第 2 步 | **语音转写** —— 逐句 ASR + F0 判男女 | 第 1 步人声轨 | `02_asr/asr.json`、`asr.srt` |
| 第 3 步 | **翻译控长** —— 中文校对、译越南语、压到音节预算内 | 第 2 步 asr.json | `03_translate/zh_opt.txt`、`vi.txt`、`segments_vi.json` |
| 第 4 步 | **配音对齐** —— TTS 合成，逐句锚定原起点 | 第 3 步 segments_vi.json | `04_voice/<名>_vi_voice.mp3/.wav` |

```
源音频 → ① 分离人声 → ② 逐句转写+性别 → ③ 校对翻译控长 → ④ 配音对齐 → 越南语配音轨
```

**为什么四步在同一个项目里**：它们串在**同一根时间轴**上。第 2 步的 `segments[].start/end` 就是第 4 步配音的锚点；第 3 步的 `slot` / `max_syl` 由第 2 步时间轴算出、被第 4 步直接消费。这条链的单一事实来源是 `segments_vi.json`，拆开就会各自维护一份口径，必然错位。

---

## 三、来历（为什么长成这样）

### 3.1 最初的形态

起点是一条"把中文短视频做成越南语版本"的完整流水线，五个环节：去字幕、分离人声、转写、翻译、配音。最初实现为单脚本 + 一个 `run.py` 调度器，放在 `E:/Users/96176/WorkBuddy/视频翻译团队`。

业务背景：保健品／个护工厂（朕养堂等品牌）主做视频号直播带货，需要把国内素材做成越南语版本投 TikTok；竞品素材（如 SKT 烟酰胺蚕丝皂、七子白洗脸皂）也要能快速本地化。

### 3.2 关键转折：从"脚本"到"角色"

第一版交付的是**代码**。用户明确纠正："我不是要你创建脚本，我是要你创建独立的 agent 角色，由你来调度他们完成任务、检验完成效果。你不要亲自干活。"

于是改成 **角色卡 + 1 个调度者**：

- 每张卡写死四件事：工单输入、交付契约（路径+字段+硬指标）、验收清单、打回规则与调参边界。
- 调度者（AI）只做派单、抽查验收、打回、汇报，不代替角色干活。
- 这一步的价值在后续批量里验证了：角色卡是"可复用的执行力"，脚本只是角色可选用的工具。

### 3.3 两批真实批量（经验来源）

| 批次 | 内容 | 结果 |
|---|---|---|
| SKT烟酰胺蚕丝皂 | 81 个源片全量去字幕 | 53 个合格产物；>120s 的 28 个按用户要求删除 |
| 七绿七子白洗脸皂 | 97 个源片全量去字幕 | 88 个合格产物，约 20 小时 |

这两批**只跑字幕擦除**，但暴露的问题反过来改写了整体工程约定：safe-delete 预算、VFR 转 CFR、work-dir 落盘位置、帧序列兜底、误擦类型与区域限定。这些全部写进了字幕擦除项目的 README 与角色卡。

翻译配音这四步只在早期做过小样本验证：人声分离（65s 音频 141s）、语音转写（52s 音频 19 句，女声 190.5Hz）。**四步端到端尚未完整实跑**，这是当前最大的未验证点。

### 3.4 为什么最终拆成两个项目

1. 先合成一个项目（含去字幕），理由是"共用时间轴契约"。
2. 用户指出 **去字幕不依赖前面任何一步**，只吃视频源文件 → 拆分。
3. 拆分后本项目进一步收窄：用户已自有视频转音频工具，**第 1 步不再需要做抽轨**，于是整条链路改成纯音频输入。

现在：本项目 = 纯音频四步；字幕擦除 = 独立工具。两者唯一的交集是"成片时需要无字幕视频 + 越南语配音轨"，格式对齐即可，不存在数据依赖。

---

## 四、目录

```
音频翻译工具/
├── .workbuddy/agents/
│   ├── TEAM.md              协作协议（派单模板、交接契约、验收、并行策略）
│   ├── 01-人声分离.md
│   ├── 02-语音转写.md
│   ├── 03-翻译控长.md
│   └── 04-配音对齐.md
├── pipeline/                separate / asr / translate / tts
├── tools/step3_helper.py    第 3 步辅助：超长句切分 + 合稿算预算
├── tools/acceptance.py      调度验收：时长/峰值/起句对齐/字符残留
├── tools/export_bundle.py   打包导出：全套产物复制到源视频所在文件夹
├── run.py                   批量调度（断点续跑、并行、心跳）
├── config.json              全部参数（audio_exts / stages = 1..4）
├── README.md                ← 本文档（唯一说明书）
└── input/ work/ output/     输入音频 / 中间产物 / 交付配音轨
```

工作目录约定：`work/<音频名>/01_separate|02_asr|03_translate|04_voice/`

## 五、快速开始

```bash
# 把音频放进 input/，或直接指定目录
E:/Projects/whisper_env/Scripts/python.exe run.py
E:/Projects/whisper_env/Scripts/python.exe run.py -i "E:/素材/0715" --jobs 2

# 先出文案人工审核，确认后再配音
E:/Projects/whisper_env/Scripts/python.exe run.py --stages 1,2,3
E:/Projects/whisper_env/Scripts/python.exe run.py --stages 4

# 只跑某个文件、重做某一步
E:/Projects/whisper_env/Scripts/python.exe run.py -i a.mp3
E:/Projects/whisper_env/Scripts/python.exe run.py --force --stages 4
```

产物：`work/<名>/01..04/`，交付 `output/<名>/<名>_vi_voice.mp3`（中越文稿在 `work/<名>/03_translate/`）。

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

`pipeline.separate` / `pipeline.asr` / `pipeline.translate` / `pipeline.tts` 依次对应第 1–4 步。

### 导出到源视频所在文件夹

```bash
E:/Projects/whisper_env/Scripts/python.exe tools/export_bundle.py            # work/ 下所有工单
E:/Projects/whisper_env/Scripts/python.exe tools/export_bundle.py douyin_051 # 指定工单
E:/Projects/whisper_env/Scripts/python.exe tools/export_bundle.py --extra    # 附带校对说明 + 时间轴 json
```

以 `douyin_051` 为例，产物落在源 `douyin_051.mp3` 同一目录（文件名带后缀，**不会覆盖源音频**）：

| 文件 | 来源 |
|---|---|
| `douyin_051_人声.mp3` | 第 1 步 纯人声 |
| `douyin_051_背景声.mp3` | 第 1 步 纯 BGM |
| `douyin_051_中文.txt` | 第 3 步 校对后中文（带时间轴） |
| `douyin_051_越南语.txt` | 第 3 步 越南语（带时间轴） |
| `douyin_051_越南语配音.mp3` | 第 4 步 配音轨 |

### 第 3 步没有 api_key 时（默认路径）

`translate.py` 需要 api_key，没有就报"未配置 LLM api_key"。此时**由第 3 步角色（LLM 本人）直接翻译**，不走任何外部接口：

```bash
E:/Projects/whisper_env/Scripts/python.exe tools/step3_helper.py split  work/<名>   # 超长句切分 → zh_split.json
# 角色校对中文 + 翻译越南语，写成 work/<名>/03_translate/_translated.json：[{"id":1,"zh":"...","vi":"..."}]
E:/Projects/whisper_env/Scripts/python.exe tools/step3_helper.py build work/<名>   # 合稿 → segments_vi.json/zh_opt.txt/vi.txt + 预算校验
E:/Projects/whisper_env/Scripts/python.exe run.py -i <源音频> --stages 4           # 配音
```

## 六、三个硬约束

1. **重型步骤必须走 GPU**：本机 RTX 3060（12GB），`E:/Projects/whisper_env` 的 torch 已换成 `2.14.1+cu130`，第 1 步 demucs `-d cuda`、第 2 步 whisper `device=cuda + float16`。CPU 路线慢 20 倍，只在确认无 GPU 时用。
2. **语音转写不能走 PyAV 解码**：faster-whisper 1.2 与本机 PyAV 19 不兼容，必须自己用 ffmpeg 解 16k 数组喂模型。
3. **第 3 步的音节预算是硬约束**：音节超 `max_syl` 就让第 3 步压，不许让第 4 步提语速硬顶。

---

## 七、参数与验收（按步骤）

### 7.1 第 1 步 · 人声分离

| 事实 | 依据 |
|---|---|
| 本机 RTX 3060 + torch **`2.14.1+cu130`**，`cuda.is_available()=True` | demucs 用 `-d cuda`；59s 音频实测 **6.9s**（约 0.12 倍实时），显存约 0.6GB |
| CPU 路线（无 GPU 时的退路）65s 音频约 **141 秒**（约 2.2 倍实时） | 实测，比 GPU 慢约 20 倍 |
| 两轨分离命令 | `--two-stems=vocals -n htdemucs --other-method minus -d cuda` |
| **不设离线时每次启动要 60~90s** | demucs 会 HEAD huggingface.co 校验模型，国内超时重试。`config.json → hf_offline` 注入 `HF_HUB_OFFLINE=1` |

**不做视频抽轨**：音视频分离由用户侧工具完成，交给这一步的就是音频。收到视频文件请退回上游，不要自己转（会引入二次编码，且不在本链路职责内）。解码命令带 `-vn` 只是为了万一误传视频也能解出音轨。

**验收硬指标**：人声轨 F0 有效帧占比 > 25%（说明真有人说话）；峰值 ≤ -0.5dBFS 无削波；时长与源差 ≤ 0.2s；前后中抽 3 段各 5 秒听，无伴奏旋律残留。

**下游打回信号**：第 2 步反馈"识别出歌词/大面积乱码" → 分离不净，换 `--shifts 2 --overlap 0.5` 或 `mdx_extra` 模型重做。

### 7.2 第 2 步 · 语音转写

**GPU**：ctranslate2 4.8.4 的 Windows 轮子自带 cuDNN 9，`get_cuda_device_count()=1`，直接 `device=cuda + compute_type=float16`。
实测 59s 音频从 CPU 的 100s 降到 34s（其中 22s 是首次模型加载，后续文件 9~12s）。

**超长整段（必踩）**：whisper 单句上限 30 秒，口播素材常出 20~30 秒一整段（实测 29.8s / 28.7s / 14.9s 各一条）。
对策：开 `word_timestamps`，第 3 步用 `tools/step3_helper.py split` 按词边界切成 ≤5s 的小句再翻译。

**initial_prompt 幻觉（必踩）**：音频开头是 BGM/无人声时，whisper 会把 `initial_prompt` 当成语音输出
（实测 douyin_156 首句被幻觉成一句占满 26 秒的假句子，导致后面整条时间轴全错；去掉提示词后从 20 句变成 38 句正常分句）。
对策：`pipeline/asr.py` 已内置自检——命中提示词片段就自动去掉提示词重跑一次。

**PyAV 不兼容（必踩）**：faster-whisper 1.2.1 与本机 PyAV 19.0.2 冲突，`av.open(metadata_errors=)` 抛 `TypeError`。

对策（已落在 `pipeline/asr.py`）：不走 `decode_audio`，自己用 ffmpeg 解成 16k 单声道数组再喂 `transcribe()`。

```bash
ffmpeg -i <音频> -ac 1 -ar 16000 -f f32le -   # 直接拿 float32 数组
```

**长音频内存**：整段读入会 `MemoryError`（114 分钟 ≈ 419MB float32）。按 **5~10 分钟切片**转写，时间戳加偏移合并，峰值内存从 419MB 降到 ~76MB。

**tokenizers 版本**：

```
pip install "tokenizers>=0.21,<1" -i https://pypi.tuna.tsinghua.edu.cn/simple
```

会让 transformers 4.46.3 的约束告警，但本流水线不用 transformers，可忽略。

**必须做的覆盖率检查**：找出**相邻句间隔 > 4 秒**的位置，逐个人工听源音频确认那里只有 BGM/静音。若有口播被漏，必须重跑并调整：

- 关闭 `vad_filter`
- 提高 `no_speech_threshold`
- 去掉 `initial_prompt`

> 实战案例：样片 12–33 秒整段口播被漏识别，带 `initial_prompt` 时触发；去掉后恢复正常。

**性别判定**：40ms 帧自相关法求 F0 中位数，阈值 **165Hz**（女 ≥165）。中位数离阈值 < 15Hz 时必须标注"临界，建议人工确认"，不要把临界值当结论。

### 7.3 第 3 步 · 翻译控长

**长度预算（核心，别绕）**

```
slot    = min(下一句.start - 0.04, 本句.end + (下一句.start - 本句.end) * 0.85)
max_syl = round((slot - 0.2) * 3.2)      # 3.2 音节/秒
```

- 3.2 音节/秒的口径来自历史项目 4.3 SPS @ rate+18% 的实测反推，在 **rate +0%** 下取 3.2 更稳。
- 越南语按**空格切音节**，一个词≈一个音节；数字串按 3 位 1 音节估。
- 全片总占用 ≤ 0.95（留 10% 余量）。
- 压缩顺序：删重复主语/代词 → 删虚词（thật sự / đúng là / thực ra / nhé / nha / thì / mà）→ 合并同义 → 长定语换短词 → 汉越词换口语词。
- 取舍顺序：寒暄 → 重复强调 → 修饰形容词 → 口语插入语。**永不删**：卖点、成分、价格、CTA。

**合规红线（越南广告法）**

| 中文 | 禁止直译 | 改用 |
|---|---|---|
| 祛湿 / 湿气重 | trừ thấp / loại bỏ thấp khí | `thanh lọc cơ thể` / `giảm cảm giác nặng nề` |
| 健脾 / 养胃 | bổ tỳ / dưỡng vị | `hỗ trợ tiêu hóa` |
| 消肿 | tiêu sưng | `giảm phù nề` |
| 排毒 | thải độc | `thanh lọc` |
| 治疗 / 根治 / 药效 | điều trị / chữa khỏi | 删除或改 `hỗ trợ` |

不得出现疾病名 + 疗效承诺的组合。

**术语一致**：同一术语全片统一（如"香皂"统一 `xà phòng`，不要混 `bánh xà phòng`）。历史术语表可复用：`E:/001.TTS/000视频素材/_压缩翻译规则.md`。

### 7.4 第 4 步 · 配音对齐

| 项 | 值 |
|---|---|
| 女声 | `vi-VN-HoaiMyNeural` |
| 男声 | `vi-VN-NamMinhNeural` |
| 并发 | `asyncio + Semaphore(8)`，单句失败重试 3 次 |

**对齐策略（按顺序，不要跳步）**

1. `rate=+0%` 首轮合成，实测每句时长。
2. 超 `slot × 1.12` 的句子，按比例提 `rate`（最多 +70%）**重合成**——比重采样自然。
3. 仍超 `slot × 1.02`，用 `atempo` 加速，上限 **1.45x**。
4. 硬裁到 `slot`（宁可极轻微截尾，也不要压到下一句头上）。
5. 每句**锚定原句 `start`**，短了留空档，不累积漂移。
6. 整轨补齐/裁切到源音频时长，首尾各 8ms 淡入淡出防爆音。

**验收**：总时长差 ≤0.3s；`avg_tempo ≤1.15`、`max_tempo ≤1.45`；超 1.3 的句子 ≤5%；音色性别与第 2 步一致；峰值 ≤ -0.5dBFS。

---

## 八、环境事实（写死，别重新试探）

| 项 | 值 |
|---|---|
| Python | `E:/Projects/whisper_env/Scripts/python.exe`（3.13，demucs 4.1 / faster-whisper 1.2 / edge-tts 7.3） |
| GPU | **NVIDIA RTX 3060 12GB**，驱动 617.14 / CUDA UMD 13.4；桌面程序常驻占用约 2.7GB |
| torch | **`2.14.1+cu130`**（`torch.cuda.is_available()=True`，`torch.version.cuda=13.0`） |
| ctranslate2 | **4.8.4，自带 cuDNN 9，`get_cuda_device_count()=1`** —— 第 2 步可直接 `device=cuda` |
| 装 torch CUDA | `pip install --index-url https://download.pytorch.org/whl/cu130 --extra-index-url https://pypi.tuna.tsinghua.edu.cn/simple torch==2.14.1+cu130`（约 2GB，`--cache-dir E:/pip_cache` 别落 C 盘） |
| whisper 模型 | `E:/Projects/whisper_env/models/whisper_models/models--Systran--faster-whisper-large-v3/snapshots/<hash>/`（本地已下载） |
| 装包 | 一律清华镜像 `-i https://pypi.tuna.tsinghua.edu.cn/simple`（官方源被限速 ~14KB/s） |
| 缓存 | `HF_HOME=E:/Projects/whisper_env/models/hf_cache`、`TORCH_HOME=E:/Projects/whisper_env/models/hf_cache/torch` |
| C 盘 | 长期紧张（300G 用掉 90%），缓存/模型/venv 一律落 E 盘 |
| edge-tts | 需要外网；越南语音色只有 HoaiMy（女）与 NamMinh（男）两个 Neural |

## 九、并行策略

| 步骤 | 能否并行 | 说明 |
|---|---|---|
| 第 1 步 人声分离 | 可跨文件并行 | demucs 走 **GPU**，显存约 0.6GB，59s 音频实测 6.9s |
| 第 2 步 语音转写 | 可跨文件并行 | whisper **GPU float16**（无 GPU 时退回 int8 CPU） |
| 第 3 步 翻译控长 | 可跨文件并行 | LLM 调用，无硬件争用 |
| 第 4 步 配音对齐 | 可跨文件并行 | edge-tts 本身已并发（Semaphore 8） |

`run.py -j N` 即按文件级并行。

## 十、踩过的协作坑

- **第 3 步可以不走 API**：`pipeline/translate.py` 需要 api_key（config.json 的 `translate.api_key` 或环境变量）；没有 key 时由角色自己翻译，链路照常能跑。
- **上游改产物必须通知下游重跑**，禁止静默改动——曾经因为第 2 步时间轴改了没通知，第 4 步配音整体错位 1.2 秒。
- **打回必须带证据**：时间码 + 实测值 + 期望值。写"效果不好"无法定位，只会来回两轮。

## 十一、已确定的决策（不要推翻，除非有新证据）

| 决策 | 理由 |
|---|---|
| 第 3 步由 LLM 角色直接翻译，不强制走 API | 角色本身有语言能力；`translate.py` 只是批量化备用 |
| 长度预算用音节口径 `(slot-0.2)*3.2` | 越南语按空格切音节，3.2 SPS 是 edge-TTS +0% 语速下的实测口径 |
| 配音先提速重合成、再 atempo、最后硬裁 | 提 rate 比重采样自然；atempo>1.45 明显失真 |
| 每句锚定原起点而非均匀铺满 | 短了就留空档，避免误差累积漂移 |
| 转写自己用 ffmpeg 解 16k 数组 | PyAV 19 与 faster-whisper 1.2 不兼容，绕开它 |
| demucs 强制 `-d cuda`、whisper 强制 `device=cuda` | 本机有 RTX 3060 且 torch 已换 cu130；实测第 1 步快 20 倍 |
| 必须设 `HF_HUB_OFFLINE=1` | 否则 demucs 每次启动联网校验模型，国内超时重试 60~90s，比推理本身还慢 |
| 第 2 步开 `word_timestamps` | whisper 单句上限 30s，口播素材必有超长整段，没有词级时间戳就切不准句 |
| 第 1 步不做抽轨 | 用户侧已有视频转音频工具，重复实现无收益 |

## 十二、下游：配音轨怎么合回画面

本项目只出 `_vi_voice.mp3`。合成由下游视频侧做，标准做法（无字幕画面 + 越南语配音 + 原 BGM）：

```bash
ffmpeg -y -i <无字幕视频> -i <vi_voice.mp3> -i <原BGM.mp3> \
  -filter_complex "[1:a]volume=1.0,aresample=44100,apad[v];\
                   [2:a]volume=0.28,aresample=44100[b];\
                   [v][b]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0[a]" \
  -map 0:v:0 -map "[a]" -c:v copy -c:a aac -b:a 192k -ar 44100 \
  -movflags +faststart <输出.mp4>
```

要点：`normalize=0`（否则 amix 会自动压音量）、BGM 音量 0.28、`apad` 补足时长后按视频长度截断。

## 十三、未完成 / 待验证

1. ~~四步端到端完整实跑~~ —— **2026-10-29 已跑通**（douyin_051/152/165 三条，见下"实测记录"）。
2. **第 3 步的音节预算需更多样本校准**：本次占用 0.92/0.92/0.95，`avg_tempo=1.001`，3.2 SPS 口径暂时够用；
   但 `Minoxidil` 这类多音节借词按 1 音节计数会低估，密集出现时需下调 SPS 或单独留余量。
3. ~~第 4 步的男女音色只验过女声~~ —— 本次 051 用 **NamMinh（男声）** 已实测通过。
4. **新暴露**：`E:/Projects/whisper_env` 依赖的 workbuddy 基础解释器 `...\versions\3.13.12\python.exe` 曾被删除导致 venv 起不来，
   已把 `pyvenv.cfg` 的 `home/executable` 指到 `C:\Users\96176\AppData\Local\Programs\WorkBuddy\resources\vendor\python`（同版本 3.13.14）。
   workbuddy 再次清理时可能复发，复发时按此修。
5. **新暴露**：`site-packages` 里有 `~orch`、`~orch-2.6.0+cu124.dist-info`、`~orchgen`、`~unctorch` 四个旧 torch 残留
   （pip 会打印 "Ignoring invalid distribution"），删除被环境安全策略拦截，需手动清理。
6. **合规词表需要维护**：目前是越南广告法常见功效词的静态映射，遇到新品（如"叶黄素缓解视疲劳"）要补条目。
7. **配音轨与画面合成的衔接**：见第十二章的标准做法，尚未写成脚本，需下游视频侧实现。

---

## 十四、实测记录（2026-10-29，米诺头皮上药器 3 条随机样本）

样本：`douyin_051`(59.0s) / `douyin_152`(40.3s) / `douyin_165`(20.8s)

### 耗时（GPU vs CPU，同一批素材）

| 步骤 | CPU | GPU | 加速 |
|---|---|---|---|
| 第 1 步 分离 | 122.8 / 107.3 / 97.1 s | **6.9 / 5.7 / 4.8 s** | ~18x |
| 第 2 步 转写 | 100.0 / 60.6 / 24.2 s | **34.3 / 9.0 / 5.8 s**（含首次模型加载 22s） | ~3.5x |
| 第 4 步 配音 | — | 14.2 / 7.9 / 2.5 s | 网络为主，与 GPU 无关 |

### 验收结果

| 项 | 051 | 152 | 165 |
|---|---|---|---|
| 时长差 | 0.000s | 0.000s | 0.000s |
| 峰值 | -2.40dBFS | -2.90dBFS | -2.90dBFS |
| 句数（切分后） | 29 | 16 | 6 |
| 音节占用 | 0.92 | 0.92 | 0.95 |
| avg / max tempo | 1.001 / 1.041 | 1.001 / 1.023 | 1.000 / 1.000 |
| 音色 | NamMinh（男） | HoaiMy（女） | HoaiMy（女） |
| 起句对齐抽 3 处 | 全部命中 | 全部命中 | 全部命中 |

**唯一待人工确认**：051 的性别判定 152.4Hz，距阈值 165Hz 仅 12.6Hz（<15Hz 属临界），已按男声配音，需人工听一遍确认。

### 全量批量（同日，该文件夹全部 39 条）

| 项 | 结果 |
|---|---|
| 素材 | 39 条 / 28.2 分钟音频 |
| 句数 | 822 句（切分后，原 ASR 约 500 句，大量 20~30s 超长段被切开） |
| 第 1+2 步总耗时 | 约 6 分钟（GPU 并行 2 路） |
| 第 4 步总耗时 | 约 8 分钟（edge-tts 网络为主） |
| 音色 | 女声 35 条 / 男声 3 条（051、163、169） |
| 验收 | 39/39 时长差 0.000s；峰值 -2.3 ~ -3.6dBFS；起句对齐抽 3 处全绿；无中日文残留 |
| 语速 | 全局最大提速 **1.136x**（远低于 1.45 的告警线），平均 1.001x |
| 音节占用 | 0.27 ~ 0.95，全部 ≤0.95 |

**两条特殊素材**：
- `douyin_150`（15.3s）：纯英文 BGM，无中文口播 → 不编造文案，配音轨输出等长静音占位，已在 `douyin_150_校对说明.md` 说明。
- `douyin_155` / `douyin_170`：含真实片尾引导语"请不吝点赞、订阅、转发、打赏，支持明镜与点点栏目"，已正常翻译。
