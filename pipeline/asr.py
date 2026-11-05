"""第 2 步 · 语音转写（ASR）+ 性别判定
用 faster-whisper 转写第 1 步产出的人声轨，保留逐句起止时间，写入 asr.json；
再用声纹模型（ECAPA-TDNN 嵌入 + SVM）判断口播是男声还是女声。

产出：<工作目录>/02_asr/asr.json
{
  "gender": {"label": "male", "method": "ecapa_svm", "confidence": 0.93, "margin": -0.83,
             "probe": [12.0, 42.0]},
  "segments": [{"id": 1, "start": 0.02, "end": 2.34, "text": "..."}]
}
"""

from __future__ import annotations

import math
import re
import statistics
import threading
from pathlib import Path

from .config import Job, resolve_whisper_model
from .utils import (FFMPEG, ensure_dir, log, media_duration, read_json, run, to_wav,
                    write_json)

STAGE = 2

# 进程内共享的 faster-whisper 实例：批量跑时每条重新加载要 20s，59 条就白扔 20 分钟
_MODEL = None
_MODEL_KEY = None
_MODEL_LOCK = threading.Lock()


def _get_model(model_path: str, cfg: dict, job: Job, log_fn=log):
    """路径/设备/精度不变时复用已加载模型。调用方需持有 _MODEL_LOCK。"""
    global _MODEL, _MODEL_KEY
    key = (str(model_path), str(cfg.get("device", "cpu")), str(cfg.get("compute_type", "int8")))
    if _MODEL is not None and _MODEL_KEY == key:
        return _MODEL
    from faster_whisper import WhisperModel

    log_fn(f"  [2/4 转写] 加载 faster-whisper：{Path(model_path).name} ({key[2]})")
    _MODEL = WhisperModel(
        model_path,
        device=key[1],
        compute_type=key[2],
        cpu_threads=int(job.cfg.get("threads", {}).get("whisper_cpu", 8)),
        num_workers=1,
    )
    _MODEL_KEY = key
    return _MODEL


# ---------------------------------------------------------------- 性别判定
def estimate_f0(wav_path: Path, sr: int = 16000, fmin: float = 70.0, fmax: float = 350.0):
    """自相关法估计基频，返回 (中位数, 有效帧数, 全部帧)。不依赖 librosa。"""
    import numpy as np
    import soundfile as sf

    data, file_sr = sf.read(str(wav_path), dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if file_sr != sr:
        # 调用方已用 ffmpeg 转成 sr，这里只做兜底
        idx = (np.arange(int(len(data) * sr / file_sr)) * file_sr / sr).astype(np.int64)
        idx = idx[idx < len(data)]
        data = data[idx]

    frame = int(0.04 * sr)
    hop = int(0.02 * sr)
    lag_min = max(2, int(sr / fmax))
    lag_max = min(frame - 2, int(sr / fmin))
    f0s = []
    for start in range(0, max(0, len(data) - frame), hop):
        seg = data[start:start + frame].astype("float64")
        rms = math.sqrt(float((seg ** 2).mean()))
        if rms < 0.006:
            continue
        seg = seg - seg.mean()
        ac = np.correlate(seg, seg, mode="full")[frame - 1:]
        if ac[0] <= 1e-9:
            continue
        ac = ac / ac[0]
        window = ac[lag_min:lag_max]
        if window.size == 0:
            continue
        k = int(np.argmax(window)) + lag_min
        if ac[k] < 0.32:
            continue
        f0 = sr / k
        if fmin <= f0 <= fmax:
            f0s.append(float(f0))
    if not f0s:
        return 0.0, 0, []
    return float(statistics.median(f0s)), len(f0s), f0s


# 声纹性别判定（SpeechBrain ECAPA-TDNN 192 维说话人嵌入 + SVM，
# 与 voice-gender-classification 同款权重，VoxCeleb2 训练、官方 98.9%）。
# 进程内复用：GPU 上首加载约 2s，之后每条约 0.2s。
_GENDER_MODEL = None
_GENDER_KEY = None
_GENDER_LOCK = threading.Lock()


def _gender_dirs(cfg: dict) -> tuple:
    """模型目录：<gender_model_root>/ecapa-voxceleb 与 /gender_cls_svm_ecapa。"""
    root = Path(str(cfg.get("gender_model_root") or "models"))
    if not root.is_absolute():
        root = Path(str(cfg.get("_root") or ".")) / root
    return root / "ecapa-voxceleb", root / "gender_cls_svm_ecapa"


def _get_gender_model(cfg: dict, log_fn=log):
    global _GENDER_MODEL, _GENDER_KEY
    ecapa_dir, svm_dir = _gender_dirs(cfg)
    dev = "cuda:0" if str(cfg.get("device", "cpu")).startswith("cuda") else "cpu"
    key = (str(ecapa_dir), str(svm_dir), dev)
    with _GENDER_LOCK:
        if _GENDER_MODEL is not None and _GENDER_KEY == key:
            return _GENDER_MODEL
        if not (ecapa_dir / "embedding_model.ckpt").is_file():
            raise FileNotFoundError(f"缺 ECAPA 权重：{ecapa_dir}")
        if not (svm_dir / "svm_model.joblib").is_file():
            raise FileNotFoundError(f"缺性别 SVM：{svm_dir}")

        import joblib
        from speechbrain.inference.speaker import EncoderClassifier

        # speechbrain 的 Pretrainer 默认用 symlink 把缓存文件落到 savedir，
        # Windows 没开开发者模式会抛 WinError 1314。文件齐全时它会跳过 fetch，
        # 但 hyperparams 里写的是 label_encoder.txt、落盘名要 .ckpt，先补齐。
        le_txt = ecapa_dir / "label_encoder.txt"
        le_ckpt = ecapa_dir / "label_encoder.ckpt"
        if not le_ckpt.exists() and le_txt.exists():
            le_ckpt.write_bytes(le_txt.read_bytes())

        log_fn(f"  [2/4 转写] 加载声纹模型：ECAPA + SVM（{dev}）")
        enc = EncoderClassifier.from_hparams(source=str(ecapa_dir), savedir=str(ecapa_dir),
                                             run_opts={"device": dev})
        meta = read_json(svm_dir / "config.json", {}) or {}
        _GENDER_MODEL = {
            "enc": enc,
            "svm": joblib.load(str(svm_dir / "svm_model.joblib")),
            "scaler": joblib.load(str(svm_dir / "scaler.joblib")),
            "labels": meta.get("labels") or ["female", "male"],
            "features": meta.get("feature_names") or [],
        }
        _GENDER_KEY = key
        return _GENDER_MODEL


def _probe_window(data, sr: int, max_sec: float = 30.0):
    """取整段里能量最集中的 ≤30 秒，避开纯音乐前奏/纯 BGM 尾巴，也别让长音频拖慢推理。

    直接喂整条 75s 也能跑，但 ECAPA 的注意力池化对整段取平均，混进长段纯音乐
    会把嵌入拉偏；取最响的连续窗更接近「口播段」。
    """
    import numpy as np

    n = int(len(data) / sr)
    span = max(1, min(int(max_sec), n))
    if n <= span:
        return data, 0.0, len(data) / sr
    rms = np.array([float(np.sqrt(float(np.mean(data[i * sr:(i + 1) * sr].astype("float64") ** 2))))
                    for i in range(n)])
    csum = np.concatenate([[0.0], np.cumsum(rms)])
    sums = csum[span:] - csum[:-span]
    i = int(np.argmax(sums))
    a, b = i * sr, min(len(data), (i + span) * sr)
    return data[a:b], a / sr, b / sr


def load_audio_16k(vocal_mp3: Path, work_dir: Path, sr: int = 16000):
    """用 ffmpeg 解码成 16k 单声道 float32 数组。

    不走 PyAV（faster-whisper 默认的 decode_audio 依赖 av.open(metadata_errors=...)，
    与本机 PyAV 版本签名不兼容），同时避免整段音频二次解码。
    """
    import soundfile as sf

    wav = ensure_dir(work_dir) / "asr_input_16k.wav"
    to_wav(vocal_mp3, wav, sr=sr, mono=True)
    data, file_sr = sf.read(str(wav), dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if file_sr != sr:
        raise RuntimeError(f"ffmpeg 重采样失败：期望 {sr}Hz，得到 {file_sr}Hz")
    return data


def _iter_segments(model, audio, cfg, sr: int, chunk_sec: float = 300.0, log_fn=log):
    """长音频切片转写，时间戳加偏移后统一产出。"""
    import numpy as np

    kw = dict(
        # language 留空 = 让 whisper 自动检测（素材可能是英/越等非中文时用）
        language=(str(cfg["language"]) if cfg.get("language") else None),
        beam_size=int(cfg.get("beam_size", 5)),
        vad_filter=bool(cfg.get("vad_filter", True)),
        vad_parameters={"min_silence_duration_ms": int(cfg.get("vad_min_silence_ms", 300))},
        no_speech_threshold=float(cfg.get("no_speech_threshold", 0.55)),
        condition_on_previous_text=False,
        initial_prompt=str(cfg.get("initial_prompt") or None),
        word_timestamps=bool(cfg.get("word_timestamps", False)),
    )
    total = len(audio) / sr
    if total <= chunk_sec:
        segments, info = model.transcribe(audio, **kw)
        for seg in segments:
            yield seg, 0.0, info
        return

    step = int(chunk_sec * sr)
    n_chunks = int(np.ceil(len(audio) / step))
    info = None
    for i in range(n_chunks):
        piece = audio[i * step:(i + 1) * step]
        if len(piece) < sr * 0.5:
            continue
        log_fn(f"  [2/4 转写] 分片转写 {i+1}/{n_chunks}")
        segments, info = model.transcribe(piece, **kw)
        offset = i * chunk_sec
        for seg in segments:
            yield seg, offset, info


# ---------------------------------------------------------------- 性别判定
def detect_gender(vocal_mp3: Path, work_dir: Path, cfg: dict, log_fn=log) -> dict:
    """声纹判性别（ECAPA 192 维嵌入 + SVM）；基频只在模型缺失时兜底。

    为什么不再用 F0 主判：030 的基频中位数 216Hz（女声区），声纹判 male——
    抖音素材里「男声但音高偏高 / 加了变声」很常见，F0 只看音高必错；
    声纹看的是声道长度与频谱包络（音色），不受音高影响。
    257 条工单交叉验证：两法一致 86%，冲突几乎全是「F0 判女 → 声纹判男」。
    """
    import soundfile as sf

    # 优先复用 ASR 已经转好的 16k 单声道，省一次解码
    wav = Path(work_dir) / "asr_input_16k.wav"
    if not wav.is_file():
        wav = ensure_dir(work_dir) / "gender_probe.wav"
        to_wav(vocal_mp3, wav, sr=16000, mono=True)

    try:
        data, file_sr = sf.read(str(wav), dtype="float32", always_2d=False)
        if data.ndim > 1:
            data = data.mean(axis=1)
        if file_sr != 16000:
            raise RuntimeError(f"采样率 {file_sr}Hz")

        import pandas as pd
        import torch

        m = _get_gender_model(cfg, log_fn=log_fn)
        piece, off_a, off_b = _probe_window(data, 16000)
        dev = "cuda:0" if str(cfg.get("device", "cpu")).startswith("cuda") else "cpu"
        w = torch.from_numpy(piece.copy()).float().unsqueeze(0).to(dev)
        with torch.no_grad():
            emb = m["enc"].encode_batch(w, torch.tensor([1.0]).to(dev)).squeeze(1).cpu().numpy()
        feats = m["features"] or [f"{i}_speechbrain_embedding" for i in range(emb.shape[1])]
        x = m["scaler"].transform(pd.DataFrame(emb, columns=feats))
        label = m["labels"][int(m["svm"].predict(x)[0])]
        try:
            margin = float(m["svm"].decision_function(x)[0])
        except Exception:
            margin = 0.0
        conf = 1.0 / (1.0 + math.exp(-abs(margin))) if margin else 0.5
        log_fn(f"  [2/4 转写] 声纹判定：{'男声' if label == 'male' else '女声'}"
               f"（ECAPA+SVM，margin {margin:+.2f}，取样 {off_a:.1f}-{off_b:.1f}s）")
        return {"label": label, "method": "ecapa_svm", "confidence": round(conf, 2),
                "margin": round(margin, 3), "probe": [round(off_a, 1), round(off_b, 1)],
                "median_f0": None}
    except Exception as e:
        log_fn(f"  [2/4 转写] 声纹判定不可用（{type(e).__name__}: {e}），回退基频判定")

    threshold = float(cfg.get("gender_f0_threshold", 165))
    lo, hi = cfg.get("gender_f0_range", [70, 350])
    median_f0, voiced, _ = estimate_f0(wav, 16000, float(lo), float(hi))
    label = "male" if (median_f0 and median_f0 < threshold) else "female"
    conf = 0.0 if not median_f0 else min(1.0, abs(median_f0 - threshold) / 60.0)
    log_fn(f"  [2/4 转写] 基频中位数 {median_f0:.1f}Hz（阈值 {threshold:.0f}）→ "
           f"{'男声' if label == 'male' else '女声'}，有效帧 {voiced}")
    return {"label": label, "method": "f0_fallback", "median_f0": round(median_f0, 1),
            "voiced_frames": voiced, "confidence": round(conf, 2)}


def _mk_item(seg, offset, idx):
    text = (seg.text or "").strip()
    if not text:
        return None
    item = {
        "id": idx,
        "start": round(float(seg.start) + offset, 3),
        "end": round(float(seg.end) + offset, 3),
        "text": text,
        "no_speech_prob": round(float(getattr(seg, "no_speech_prob", 0.0) or 0.0), 3),
    }
    words = getattr(seg, "words", None) or []
    if words:
        item["words"] = [
            {"w": (w.word or "").strip(),
             "s": round(float(w.start) + offset, 3),
             "e": round(float(w.end) + offset, 3)}
            for w in words if (w.word or "").strip()
        ]
    return item


def _transcribe(model, audio, cfg, log_fn=log):
    """跑一遍转写，返回 (segments, info)。"""
    segments, info = [], None
    for seg, offset, info_ in _iter_segments(model, audio, cfg, 16000, log_fn=log_fn):
        info = info_ or info
        item = _mk_item(seg, offset, len(segments) + 1)
        if item:
            segments.append(item)
    return segments, info


def _prompt_leaked(prompt: str, segments: list) -> bool:
    """判断 initial_prompt 是否被 whisper 当成语音输出（幻觉）。"""
    text = "".join(s["text"] for s in segments)
    frags = [f for f in re.split(r"[，,。；;！!？?、\s]+", prompt) if len(f) >= 5]
    return any(f in text for f in frags)


# whisper 在「纯音乐段」或「口播被 BGM 盖住」的段落上会吐固定套话
# （中文 large-v3 最典型的是 YouTube 字幕组鸣谢）。这些词带货口播不会说，命中即判幻觉。
_HALLUC_RE = re.compile(
    r"请不吝点赞|點贊訂閱|订阅转发|订阅\s*转发|订阅频道|打赏支持|打赏|明镜与点点|点点栏目|"
    r"請不吝點贊|明鏡與點點|點點欄目|打賞支持|訂閱\s*轉發|"
    r"字幕志愿者|字幕组|字幕由.{0,8}(提供|制作|翻译)|Amara\.org|优优独播|YoYo\s*Television|"
    r"感谢观看|谢谢收看|本视频由.{0,10}制作|優優獨播|獨播劇場|感謝觀看|謝謝收看",
    re.I,
)


def _chars(text: str) -> int:
    return len(re.sub(r"\s+", "", text or ""))


def _is_halluc(item: dict, cfg: dict | None = None) -> bool:
    """判断一条转写结果是不是 whisper 幻觉（音乐段固定套话），命中任一即算：

    1. 命中固定套话词表——"打赏支持""字幕志愿者"这类词不出现在带货口播里；
    2. no_speech_prob 高（>= halluc_nsp）且文字密度极低；
    3. 段落很长（>= halluc_long_sec）却每秒不到 2 个字——真人连说 10 秒不可能这么稀。

    026 的实例：61.88-89.08 一条 27.2s 只 12 个字（0.44 字/秒）、no_speech_prob=0.95，
    被判 no_speech 却照样输出，把尾巴 30 秒真口播顶掉了。
    """
    cfg = cfg or {}
    text = item.get("text") or ""
    dur = max(0.01, float(item["end"]) - float(item["start"]))
    dens = _chars(text) / dur
    nsp = float(item.get("no_speech_prob") or 0.0)
    if _HALLUC_RE.search(text):
        return True
    if nsp >= float(cfg.get("halluc_nsp", 0.6)) and dens < float(cfg.get("halluc_min_density", 2.0)):
        return True
    if dur >= float(cfg.get("halluc_long_sec", 10.0)) and dens < 2.0:
        return True
    # 片头 2 秒内的孤立短段：开头是纯音乐，whisper 常在这里编一句问候语
    # （026 的"你能再说一遍吗?"，no_speech_prob 0.53）。真口播的 nsp 一般在 0.3 以下。
    if nsp >= 0.5 and dur <= 2.5 and float(item["start"]) < 2.0:
        return True
    return False


def halluc_segments(segments: list, cfg: dict | None = None) -> list:
    """筛出判定为幻觉的段。

    ASR 自身的跳过分支、以及第 3 步（翻译）的入口都复用这里做兜底：
    老产物若残留"请不吝点赞…明镜与点点栏目"这类套话，翻译出来就是与原文
    完全无关的内容（026 的实例），必须在进入翻译前拦下来。
    """
    return [s for s in (segments or []) if _is_halluc(s, cfg)]


# 句首幻觉套话：整条判定抓不到它，因为它和真口播被并成了同一条
# （026 的 0.00-3.14s「你能再说一遍吗，睡觉睡得好」）。
_HALLUC_LEAD_RE = re.compile(r"^(?:你能再说一遍吗|请不吝点赞|欢迎收看|感谢观看)[?？]?[，,。.、；;\s]*")


def _clean_text(items: list, log_fn=None) -> list:
    """清句首幻觉套话与切分残渣（孤立"的"），清过的段丢掉 words 以免时间轴对不上。"""
    n = 0
    for it in items:
        text = it.get("text") or ""
        m = _HALLUC_LEAD_RE.match(text)
        if m and _chars(text[m.end():]) >= 2:
            text = text[m.end():]
        if text[:1] == "的" and _chars(text) > 3 and text[1:2] != "话":
            text = text[1:]
        if text != (it.get("text") or ""):
            it["text"] = text.strip("，,。.、；; ")
            it.pop("words", None)   # 词级时间戳已和正文错位，交由第 3 步按标点比例切
            n += 1
    if n and log_fn:
        log_fn(f"  [2/4 转写] 清理 {n} 条句首幻觉/残渣")
    return items


def _drop_halluc(segments: list, cfg: dict, log_fn=None) -> tuple:
    """剔除疑似幻觉，返回 (保留, 剔除)。剔除项要留痕，方便人工复核。"""
    keep, drop = [], []
    for s in segments:
        (drop if _is_halluc(s, cfg) else keep).append(s)
    if drop and log_fn:
        sample = "；".join(f"{d['start']:.1f}-{d['end']:.1f}s「{d['text'][:20]}」" for d in drop[:3])
        log_fn(f"  [2/4 转写] 剔除 {len(drop)} 条疑似幻觉：{sample}")
    return keep, drop


def _gaps(segments: list, total_dur: float, min_sec: float = 0.0) -> list:
    """所有 >= min_sec 的空白间隔（含首尾），按从大到小排：[(间隔, 起, 止), ...]。"""
    spans, prev = [], 0.0
    for s in sorted(segments, key=lambda x: float(x["start"])):
        spans.append((prev, float(s["start"])))
        prev = max(prev, float(s["end"]))
    spans.append((prev, total_dur))
    return sorted([(b - a, a, b) for a, b in spans if b - a >= min_sec], key=lambda g: -g[0])


def _max_gap(segments: list, total_dur: float) -> tuple:
    """转写结果里最大的空白间隔（含首尾），返回 (间隔, 起, 止)。"""
    gaps = _gaps(segments, total_dur)
    return gaps[0] if gaps else (0.0, 0.0, 0.0)


def _loud(path: Path, a: float, b: float, thr_db: float = -45.0) -> bool:
    """[a,b) 这段有没有明显声音，用来区分「真空白」和「ASR 漏识别」。"""
    dur = max(0.1, b - a)
    proc = run([FFMPEG, "-hide_banner", "-nostats", "-ss", f"{a:.2f}", "-t", f"{dur:.2f}",
                "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
               quiet=True, check=False)
    m = re.search(r"mean_volume:\s*(-?[\d.]+)", (proc.stderr or "") + (proc.stdout or ""))
    return bool(m) and float(m.group(1)) > thr_db


def _merge_short(items: list, cfg: dict) -> list:
    """相邻短段合并：小窗口会在句子中间切开，合回去减少碎片（否则一句口播碎成三段）。"""
    gap_max = float(cfg.get("regap_merge_gap", 0.4))
    span_max = float(cfg.get("regap_merge_span", 7.0))
    out = []
    for it in sorted(items, key=lambda x: float(x["start"])):
        it = dict(it)
        if out:
            prev = out[-1]
            gap = float(it["start"]) - float(prev["end"])
            span = float(it["end"]) - float(prev["start"])
            if gap <= gap_max and span <= span_max:
                prev["text"] = prev["text"].rstrip("，,。.、；; ") + "，" + it["text"].lstrip("，,。.、；; ")
                prev["end"] = it["end"]
                prev["no_speech_prob"] = max(float(prev.get("no_speech_prob") or 0.0),
                                             float(it.get("no_speech_prob") or 0.0))
                # 词级时间戳必须跟着合并：第 3 步靠 words 判断句子边界和时长，
                # 只拼 text 不拼 words 会让 words 只覆盖前半段，切分时后半句直接丢
                # （026 的 id 2/3/4/18/19 就是这么被砍掉的）。
                prev["words"] = list(prev.get("words") or []) + list(it.get("words") or [])
                continue
        out.append(it)
    return out


def _dedup_overlap(items: list) -> list:
    """同一句话被相邻窗口各识别一次时只留最完整的那个。

    判重看时间重叠：重叠超过较短一方的一半即视为同一句，保留字数多的版本
    （窗口边界会把句子切成"其实判断身体好不好"和"不判断身体好不好"两份）。
    """
    out = []
    for it in sorted(items, key=lambda x: (float(x["start"]), -_chars(x.get("text", "")))):
        hit = None
        for o in out:
            ov = min(float(it["end"]), float(o["end"])) - max(float(it["start"]), float(o["start"]))
            if ov > 0:
                short = min(float(it["end"]) - float(it["start"]), float(o["end"]) - float(o["start"]))
                if ov >= 0.5 * short:
                    hit = o
                    break
        if hit is None:
            out.append(it)
        elif _chars(it.get("text", "")) > _chars(hit.get("text", "")):
            out[out.index(hit)] = it
    return out


def _fill_gap(model, audio, cfg, ga: float, gb: float, sr: int = 16000, log_fn=log) -> list:
    """把漏识别空档切成小窗口（默认 5s，步进 2s 有重叠）逐段转写，只留落在空档内的结果。

    为什么要切小：whisper 一次喂 30 秒「口播被 BGM 压低」的内容，会直接编出一整段
    固定套话幻觉（026 尾部那条 27 秒的"请不吝点赞 订阅 转发 打赏支持明镜与点点栏目"，
    no_speech_prob 0.95）。同样的音频切成 5 秒喂进去，模型就没有空间编长段，
    12 句真口播全能出来；切 8 秒则 75-83s 那一段仍然幻觉，所以默认取 5。
    窗口之间有重叠，避免句子正好落在切点上被切掉（无重叠时会丢"其实""常说男人靠睡"）。
    """
    win = max(2.0, float(cfg.get("regap_window_sec", 5.0)))
    hop = max(0.5, min(win, float(cfg.get("regap_hop_sec", 2.0))))
    a_all = max(0, int((ga - 0.4) * sr))
    b_all = min(len(audio), int((gb + 0.4) * sr))
    if b_all - a_all < sr * 0.5:
        return []
    # 补漏要最宽松的判定：这段本来就是被判成"无人声"的地方，
    # 阈值不放开、还带着 initial_prompt，whisper 会继续只吐一两条。
    kw = dict(cfg)
    kw["vad_filter"] = False
    kw["no_speech_threshold"] = float(cfg.get("regap_no_speech", 0.4))
    kw["initial_prompt"] = ""
    kw["condition_on_previous_text"] = False
    out = []
    win_n = int(round(win * sr))
    hop_n = int(round(hop * sr))
    t = a_all
    while t < b_all:
        piece = audio[t:min(b_all, t + win_n)]
        if len(piece) >= sr * 0.4:
            start_off = t / sr
            stop_off = start_off + len(piece) / sr
            for seg, off, _info in _iter_segments(model, piece, kw, sr, log_fn=lambda *_: None):
                item = _mk_item(seg, start_off + off, len(out) + 1)
                if not item:
                    continue
                # 短音频会被 whisper padding 到 30s，幻觉时间戳能越过窗口尾巴，按窗口硬裁
                item["start"] = max(float(item["start"]), start_off)
                item["end"] = min(float(item["end"]), stop_off)
                if item["end"] - item["start"] < 0.2 or item["start"] < ga - 0.4:
                    continue
                # 先剔幻觉再去重：否则一条长套话会在去重时把同区间的真识别结果顶掉，
                # 幻觉随后被过滤，那段真口播就凭空消失了。
                if _is_halluc(item, cfg):
                    continue
                out.append(item)
        t += hop_n
    return _merge_short(_dedup_overlap(out), cfg)


# ---------------------------------------------------------------- 主流程
def run_stage(job: Job, logger=None, log_fn=log, force: bool = False) -> dict:
    cfg = job.cfg["asr"]
    out_json = job.asr_json
    if not force and out_json.exists():
        data = read_json(out_json)
        if data and data.get("segments"):
            bad = halluc_segments(data["segments"], cfg)
            if bad:
                sample = "；".join(
                    f"{float(b['start']):.1f}s「{(b.get('text') or '')[:16]}」" for b in bad[:2])
                log_fn(f"  [2/4 转写] 已有结果含 {len(bad)} 条幻觉套话（{sample}），判为无效，重跑转写")
            else:
                log_fn(f"  [2/4 转写] 已存在 {len(data['segments'])} 条字幕，跳过")
                return data

    from faster_whisper import WhisperModel

    vocal = job.vocal_mp3
    if not vocal.exists():
        raise RuntimeError("缺少 Agent1 的人声音轨：" + str(vocal))

    model_path = resolve_whisper_model(str(cfg.get("model", "large-v3")))
    log_fn("  [2/4 转写] 解码人声（ffmpeg → 16k 单声道）…")
    audio = load_audio_16k(vocal, job.dir(STAGE))
    log_fn(f"  [2/4 转写] 开始转写（{len(audio)/16000:.1f}s 音频）…")

    kw = dict(cfg)
    with _MODEL_LOCK:  # 多线程共享一个模型，转写串行（GPU 本就在排队，省掉重复加载的 20s/条）
        model = _get_model(model_path, cfg, job, log_fn=log_fn)
        segments, info = _transcribe(model, audio, kw, log_fn=log_fn)
        segments, info = segments, info

        # 幻觉自检：initial_prompt 被当成语音输出时（常见于开头是 BGM/无人声），去掉提示词重跑
        if str(kw.get("initial_prompt") or "").strip() and _prompt_leaked(str(kw["initial_prompt"]), segments):
            log_fn("  [2/4 转写] 检测到 initial_prompt 泄漏到转写结果（whisper 幻觉），去掉提示词重跑")
            kw["initial_prompt"] = ""
            segments, info = _transcribe(model, audio, kw, log_fn=log_fn)

        total_dur = media_duration(job.media)
        # 先剔幻觉：whisper 的套话幻觉会把自己伪装成"有内容"，把真实空档填满，
        # 让下面的漏识别自检看不见那个洞（026 尾巴 30 秒真口播就是这么丢的）。
        segments, dropped = _drop_halluc(segments, cfg, log_fn=log_fn)
        segments = _clean_text(segments, log_fn=log_fn)

        # 漏识别自检：中间出现大段空档、可那段人声里明明有话，说明 VAD/no_speech 把整段滤掉了。
        # 这种漏识别比配音音色问题严重得多——观众会直接听丢整段卖点，所以关掉 VAD 切片重转。
        # 多轮：剔掉幻觉后尾部常常才暴露出大空档，一轮只补最大的一处不够。
        min_sec = float(cfg.get("regap_min_sec", 8.0))
        rounds = max(1, int(cfg.get("regap_max_rounds", 3)))
        for i, (gap, ga, gb) in enumerate(_gaps(segments, total_dur, min_sec)):
            if i >= rounds:
                break
            if not _loud(vocal, ga, gb):
                log_fn(f"  [2/4 转写] {ga:.1f}-{gb:.1f}s 空了 {gap:.1f}s，该段无声音，按真空白处理")
                continue
            log_fn(f"  [2/4 转写] {ga:.1f}-{gb:.1f}s 空了 {gap:.1f}s 但该段有人声，判为漏识别，切片补回")
            extra = _fill_gap(model, audio, kw, ga, gb, 16000, log_fn=log_fn)
            extra, extra_halluc = _drop_halluc(extra, cfg, log_fn=None)
            extra = _clean_text(extra)
            if extra_halluc:
                log_fn(f"  [2/4 转写] 补回结果里再剔 {len(extra_halluc)} 条幻觉")
                dropped += extra_halluc
            if not extra:
                log_fn(f"  [2/4 转写] 该段补不出有效内容（纯音乐/无口播），跳过")
                continue
            log_fn(f"  [2/4 转写] 空档补回 {len(extra)} 条（{len(segments)} → {len(segments) + len(extra)}）")
            segments = sorted(segments + extra, key=lambda s: float(s["start"]))
            for idx, s in enumerate(segments, 1):
                s["id"] = idx

    if not segments:
        raise RuntimeError("ASR 未识别到任何文本，请检查人声音轨（可能整段是 BGM 或被判为静音）")

    gender = detect_gender(vocal, job.dir(STAGE), cfg, log_fn=log_fn)

    data = {
        "media": str(job.media),
        "duration": round(media_duration(job.media), 3),
        "language": getattr(info, "language", cfg.get("language", "zh")),
        "language_probability": round(float(getattr(info, "language_probability", 0.0) or 0.0), 3),
        "gender": gender,
        "segment_count": len(segments),
        "segments": segments,
        # 被剔除的疑似幻觉，留档便于人工复核（不影响下游，只是证据）
        "dropped": dropped,
    }
    write_json(out_json, data)

    srt = render_srt(segments)
    (job.path(STAGE, "asr.srt")).write_text(srt, encoding="utf-8")
    log_fn(f"  [2/4 转写] 完成：{len(segments)} 条，语种 {data['language']}({data['language_probability']})")
    return data


def render_srt(segments) -> str:
    def fmt(t: float) -> str:
        ms = int(round(t * 1000))
        h, ms = divmod(ms, 3600000)
        m, ms = divmod(ms, 60000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines = []
    for seg in segments:
        lines.append(str(seg["id"]))
        lines.append(f"{fmt(seg['start'])} --> {fmt(seg['end'])}")
        lines.append(seg["text"])
        lines.append("")
    return "\n".join(lines)
