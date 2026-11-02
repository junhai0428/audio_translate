"""第 2 步 · 语音转写（ASR）+ 性别判定
用 faster-whisper 转写第 1 步产出的人声轨，保留逐句起止时间，写入 asr.json；
再用基频(F0)中位数判断口播是男声还是女声。

产出：<工作目录>/02_asr/asr.json
{
  "gender": {"label": "female", "median_f0": 213.4, "voiced_frames": 812, "confidence": 0.87},
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
from .utils import ensure_dir, log, media_duration, read_json, to_wav, write_json

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
    threshold = float(cfg.get("gender_f0_threshold", 165))
    lo, hi = cfg.get("gender_f0_range", [70, 350])
    tmp_wav = ensure_dir(work_dir) / "gender_probe.wav"
    to_wav(vocal_mp3, tmp_wav, sr=16000, mono=True)
    median_f0, voiced, f0s = estimate_f0(tmp_wav, 16000, float(lo), float(hi))
    try:
        tmp_wav.unlink(missing_ok=True)
    except Exception:
        pass

    label = "male" if (median_f0 and median_f0 < threshold) else "female"
    if not median_f0:
        label = "female"
    # 置信度：中位数离阈值越远越确信
    conf = 0.0 if not median_f0 else min(1.0, abs(median_f0 - threshold) / 60.0)
    log_fn(f"  [2/4 转写] 声纹基频中位数 {median_f0:.1f}Hz（阈值 {threshold:.0f}）→ {'男声' if label == 'male' else '女声'}，有效帧 {voiced}")
    return {"label": label, "median_f0": round(median_f0, 1), "voiced_frames": voiced, "confidence": round(conf, 2)}


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


# ---------------------------------------------------------------- 主流程
def run_stage(job: Job, logger=None, log_fn=log, force: bool = False) -> dict:
    cfg = job.cfg["asr"]
    out_json = job.asr_json
    if not force and out_json.exists():
        data = read_json(out_json)
        if data and data.get("segments"):
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
