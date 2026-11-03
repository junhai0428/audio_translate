"""第 4 步 · 越南语配音（edge-TTS 并行 + 时长对齐）
按第 3 步的逐句译文生成语音，音色性别与原口播一致：
  女声 vi-VN-HoaiMyNeural / 男声 vi-VN-NamMinhNeural
每句锚定在原句起始时间，超长则先提高 edge-tts rate 重合成、再 atempo 加速，
最终整轨时长严格等于源音频时长。

产出：<工作目录>/04_voice/<音频名>_vi_voice.mp3 (+ .wav)
"""

from __future__ import annotations

import asyncio

import re
from pathlib import Path

from .config import Job
from .utils import FFMPEG, compute_slots, ensure_dir, log, media_duration, read_json, run, write_json

STAGE = 4

_BAD_CHARS = re.compile(r"[#*_`~^<>\[\]{}|\\/@=+%$&\"“”‘’…·•●▲◆■★☆→←↑↓\U0001F000-\U0001FAFF]")


def clean_text(text: str) -> str:
    text = (text or "").replace("\n", " ").replace("\r", " ")
    text = _BAD_CHARS.sub(" ", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"^[\s,.;:\-]+", "", text)
    return text.strip(" .")


async def _synth_one(text: str, voice: str, rate: str, out: Path, sem, retries: int = 3) -> bool:
    import edge_tts

    async with sem:
        for attempt in range(1, retries + 1):
            try:
                comm = edge_tts.Communicate(text, voice, rate=rate)
                await comm.save(str(out))
                if out.exists() and out.stat().st_size > 800:
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.8 * attempt)
    return False


async def _synth_batch(items, voice: str, rate: str, out_dir: Path, concurrency: int, retries: int):
    sem = asyncio.Semaphore(concurrency)
    tasks = [
        asyncio.create_task(_synth_one(it["text"], voice, rate_for(it, rate), it["raw"], sem, retries))
        for it in items
    ]
    return await asyncio.gather(*tasks)


def rate_for(item, base: str) -> str:
    return item.get("rate") or base


# ------------------------------------------------------------------ 音频处理
def _trim_to_wav(src: Path, dst: Path, sr: int) -> float:
    """去掉首尾静音并统一采样率，返回时长(秒)。"""
    af = (
        "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.02:detection=peak,"
        "areverse,"
        "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.02:detection=peak,"
        "areverse"
    )
    run([FFMPEG, "-y", "-v", "error", "-i", str(src), "-af", af, "-ar", str(sr), "-ac", "1",
         "-c:a", "pcm_s16le", str(dst)], quiet=True)
    return media_duration(dst)


def _tempo(src: Path, dst: Path, tempo: float, sr: int) -> None:
    """tempo > 1 加速（清空溢出），< 1 放慢（铺满槽位）。atempo 单级只吃 0.5~2.0。"""
    filters = []
    t = tempo
    while t > 2.0:
        filters.append("atempo=2.0")
        t /= 2.0
    while t < 0.5:
        filters.append("atempo=0.5")
        t /= 0.5
    if abs(t - 1.0) > 0.005:
        filters.append(f"atempo={t:.4f}")
    cmd = [FFMPEG, "-y", "-v", "error", "-i", str(src)]
    if filters:
        cmd += ["-af", ",".join(filters)]
    cmd += ["-ar", str(sr), "-ac", "1", "-c:a", "pcm_s16le", str(dst)]
    run(cmd, quiet=True)


def _load_mono(path: Path, sr: int):
    import numpy as np
    import soundfile as sf

    data, file_sr = sf.read(str(path), dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if file_sr != sr:
        n = int(round(len(data) * sr / file_sr))
        x_old = np.linspace(0, 1, len(data), endpoint=False)
        x_new = np.linspace(0, 1, n, endpoint=False)
        data = np.interp(x_new, x_old, data).astype("float32")
    return data


# ------------------------------------------------------------------ 主流程
def run_stage(job: Job, logger=None, log_fn=log, force: bool = False) -> dict:
    cfg = job.cfg["tts"]
    out_mp3 = job.vi_voice_mp3
    if not force and out_mp3.exists() and out_mp3.stat().st_size > 2048:
        log_fn("  [4/4 配音] 已存在配音，跳过")
        return {"voice": str(out_mp3), "duration": media_duration(out_mp3), "skipped": True}

    data = read_json(job.vi_segments_json)
    if not data:
        raise RuntimeError("缺少 Agent3 的 segments_vi.json")

    segs = [s for s in data["segments"] if (s.get("vi") or "").strip()]
    if not segs:
        # 整条片子没有可念的台词（纯 BGM，或 ASR 幻觉已被清空）：出等长静音轨，
        # 下游仍按同一套命名取文件，不用为这种片子写特例
        log_fn("  [4/4 配音] 无有效口播，生成等长静音轨")
        import shutil

        import numpy as np
        import soundfile as sf

        sr = int(cfg.get("sample_rate", 44100))
        total_dur = float(data.get("duration") or media_duration(job.media) or 0) or 1.0
        out_wav = job.path(STAGE, f"{job.base}_vi_voice.wav")
        out_wav.parent.mkdir(parents=True, exist_ok=True)
        track = np.zeros(max(1, int(total_dur * sr)), dtype="float32")
        sf.write(str(out_wav), np.stack([track, track], axis=1), sr, subtype="PCM_16")
        run([FFMPEG, "-y", "-v", "error", "-i", str(out_wav),
             "-c:a", "libmp3lame", "-b:a", "192k", str(out_mp3)], quiet=True)
        shutil.copy2(out_mp3, job.deliverable)
        log_fn(f"  [4/4 配音] 交付（静音）：{job.deliverable}")
        return {"voice": str(out_mp3), "duration": total_dur, "silent": True}

    gender = (data.get("gender") or {}).get("label") or "female"
    voice = cfg["voices"].get(gender) or cfg["voices"]["female"]
    sr = int(cfg.get("sample_rate", 44100))
    gap = float(cfg.get("gap", 0.08))
    min_slot = float(cfg.get("min_slot", 0.35))
    max_tempo = float(cfg.get("max_tempo", 1.85))
    resynth_thr = float(cfg.get("resynth_threshold", 1.12))
    min_fill = float(cfg.get("min_fill", 0.8))       # 低于此填充率就放慢铺满
    max_slow = float(cfg.get("max_slow", 1.3))       # 最多放慢几倍（越大越慢、越满）
    total_dur = float(data.get("duration") or media_duration(job.media) or 0)
    if total_dur <= 0:
        total_dur = max(float(s["end"]) for s in segs) + 0.5

    log_fn(f"  [4/4 配音] 音色 {voice}（原声 {gender}），共 {len(segs)} 句，目标总长 {total_dur:.2f}s")

    # 1) 每句可用时隙（与 Agent3 预算同一口径）
    slots = compute_slots(segs, total_dur, gap_use=float(cfg.get("gap_use", 0.95)), gap=gap,
                          min_slot=min_slot)
    items = []
    for i, s in enumerate(segs):
        start, slot = slots[i]
        # 优先用 Agent3 落盘的 slot（同一公式），保证预算与实际一致
        slot = float(s.get("slot") or slot)
        items.append({
            "id": s["id"], "start": start, "seg_end": float(s["end"]),
            "text": clean_text(s["vi"]), "slot": slot,
            "max_syl": s.get("max_syl", 0),
        })
    items = [it for it in items if it["text"]]

    tmp_dir = ensure_dir(job.dir(STAGE) / "raw")
    norm_dir = ensure_dir(job.dir(STAGE) / "norm")
    concurrency = int(cfg.get("concurrency", 8))
    retries = int(cfg.get("retries", 3))
    # 基准语速：edge-tts 的 rate 百分比。"+0%" = 原速；"+50%" = 1.5 倍。
    # 提高基准语速后，同样时间槽能容纳更多音节，译文不必压得过短。
    base_rate = str(cfg.get("base_rate") or "+0%")

    # 2) 首轮并行合成
    for n, it in enumerate(items):
        it["raw"] = tmp_dir / f"seg_{n:04d}.mp3"
        it["wav"] = norm_dir / f"seg_{n:04d}.wav"
        it["final"] = norm_dir / f"seg_{n:04d}.fit.wav"
        it["rate"] = base_rate

    log_fn(f"  [4/4 配音] 并行合成（并发 {concurrency}）…")
    ok = asyncio.run(_synth_batch(items, voice, base_rate, tmp_dir, concurrency, retries))
    for it, good in zip(items, ok):
        it["ok"] = bool(good)
    failed = [it["id"] for it in items if not it["ok"]]
    if failed:
        log_fn(f"  [4/4 配音] 警告：{len(failed)} 句合成失败 id={failed[:8]}")

    # 3) 测量时长，超长的再合成一轮（提速）与 atempo 兜底
    need_resynth = []
    for it in items:
        if not it["ok"]:
            continue
        it["raw_dur"] = _trim_to_wav(it["raw"], it["wav"], sr)
        if it["raw_dur"] > it["slot"] * resynth_thr:
            ratio = it["raw_dur"] / max(0.01, it["slot"])
            pct = min(70, int((ratio - 1) * 100 * 0.85))
            if pct >= 6:
                it["rate"] = f"+{pct}%"
                need_resynth.append(it)

    if need_resynth:
        log_fn(f"  [4/4 配音] {len(need_resynth)} 句超长，按更快语速重合成…")
        ok2 = asyncio.run(_synth_batch(need_resynth, voice, base_rate, tmp_dir, concurrency, retries))
        for it, good in zip(need_resynth, ok2):
            if good:
                it["raw_dur"] = _trim_to_wav(it["raw"], it["wav"], sr)

    # 4) 归一化到槽位（必要时 atempo 加速）
    tempos = []
    for it in items:
        if not it.get("ok"):
            continue
        dur = it.get("raw_dur") or 0.0
        if dur <= 0:
            it["ok"] = False
            continue
        tempo = 1.0
        if dur > it["slot"] * 1.02:
            tempo = min(max_tempo, dur / it["slot"])
        elif dur < it["slot"] * min_fill:
            # 偏短：放慢到接近填满槽位（atempo<1，音高不变），把句间空白用掉
            tempo = max(1.0 / max_slow, dur / (it["slot"] * min_fill))
        _tempo(it["wav"], it["final"], tempo, sr)
        it["tempo"] = tempo
        it["fill"] = round(min(1.0, (dur / tempo) / it["slot"]), 3) if it["slot"] else 0.0
        tempos.append(tempo)

    # 5) 拼接时间轴
    import numpy as np

    total_samples = int(round(total_dur * sr))
    track = np.zeros(total_samples, dtype="float32")
    fade = max(1, int(0.008 * sr))
    placed = 0
    for it in items:
        if not it.get("ok") or not it["final"].exists():
            continue
        clip = _load_mono(it["final"], sr)
        if clip.size == 0:
            continue
        # 强制不超过槽位
        max_len = int(round(it["slot"] * sr))
        if clip.size > max_len:
            clip = clip[:max_len]
        if clip.size > fade * 2:
            ramp = np.linspace(0.0, 1.0, fade, dtype="float32")
            clip[:fade] *= ramp
            clip[-fade:] *= ramp[::-1]
        offset = int(round(it["start"] * sr))
        if offset >= total_samples:
            continue
        end = min(total_samples, offset + clip.size)
        track[offset:end] += clip[: end - offset]
        placed += 1

    peak = float(np.max(np.abs(track))) if track.size else 0.0
    # 峰值上限统一为 -1.5dBFS：过响的整轨衰减，安静的片子一律不动
    ceiling = 10 ** (-1.5 / 20)
    if peak > ceiling:
        track *= ceiling / peak
    if peak <= 0:
        log_fn("  [4/4 配音] 警告：合成音频为空")

    out_wav = job.path(STAGE, f"{job.base}_vi_voice.wav")
    import soundfile as sf

    sf.write(str(out_wav), np.stack([track, track], axis=1), sr, subtype="PCM_16")
    run([FFMPEG, "-y", "-v", "error", "-i", str(out_wav), "-c:a", "libmp3lame", "-b:a", "192k", str(out_mp3)], quiet=True)
    # 交付物直接落视频同目录：<名>_越南语配音.mp3
    import shutil

    shutil.copy2(out_mp3, job.deliverable)
    log_fn(f"  [4/4 配音] 交付：{job.deliverable}")

    far_over = sum(1 for t in tempos if t > 1.45)
    done = [it for it in items if it.get("ok")]
    avg_fill = round(sum(float(it.get("fill") or 0) for it in done) / max(1, len(done)), 3)
    stats = {
        "voice": voice,
        "gender": gender,
        "segments": len(items),
        "placed": placed,
        "failed_ids": failed,
        "avg_tempo": round(sum(tempos) / len(tempos), 3) if tempos else 1.0,
        "max_tempo": round(max(tempos), 3) if tempos else 1.0,
        "over_1_45": far_over,
        "avg_fill": avg_fill,
        "duration": media_duration(out_mp3),
        "target_duration": round(total_dur, 3),
    }
    write_json(job.path(STAGE, "tts_stats.json"), stats)
    # 逐句台账：tempo >1 说明译文挤，<1 说明放慢铺满；fill 是配音占槽位比例
    write_json(job.path(STAGE, "tts_manifest.json"), [
        {"id": it.get("id"), "start": it.get("start"), "slot": round(float(it.get("slot", 0)), 3),
         "raw_dur": round(float(it.get("raw_dur") or 0), 3),
         "tempo": round(float(it.get("tempo", 1.0)), 3),
         "fill": it.get("fill"),
         "zh": it.get("zh"), "vi": it.get("vi")}
        for it in done
    ])
    log_fn(
        f"  [4/4 配音] 完成：{placed}/{len(items)} 句，平均倍速 {stats['avg_tempo']}x，"
        f"槽位填充 {avg_fill:.0%}，时长 {stats['duration']:.2f}s / 目标 {total_dur:.2f}s"
    )
    return {"voice": str(out_mp3), "wav": str(out_wav), **stats}
