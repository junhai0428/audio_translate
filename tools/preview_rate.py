"""按不同基准语速生成试听样本（同一份译文，只改 edge-tts 的 rate）。

用途：edge-tts 原速偏慢，译文被迫压得很短、语义走样。先用现有译文听几档语速，
选定基准后再回头放宽 `translate.vi_syllables_per_sec` 重翻。

用法：
  python tools/preview_rate.py                                  # 默认 3 条样本 × 4 档
  python tools/preview_rate.py douyin_188 douyin_059            # 指定工单
  python tools/preview_rate.py douyin_188 --rates 0,25,50,80,100 --max-sec 30

产出：<素材根>/_语速试听/<产品前缀>_<工单>_<档>.mp3
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.config import iter_job_dirs, job_dir, load_config  # noqa: E402
from pipeline.tts import _load_mono, _synth_batch, _trim_to_wav, clean_text  # noqa: E402
from pipeline.utils import FFMPEG, ensure_dir, media_duration, run  # noqa: E402

SPS_BASE = 3.2          # 当前翻译口径：3.2 音节/秒（rate +0% 下实测）
DEFAULT_JOBS = ["douyin_188", "douyin_199", "douyin_059"]
DEFAULT_RATES = [0, 25, 50, 80, 100]


def parse_args(argv: list[str]):
    jobs, rates, max_sec, concurrency = [], DEFAULT_RATES, 30.0, 8
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--rates":
            i += 1
            rates = [int(x) for x in argv[i].split(",") if x.strip()]
        elif a == "--max-sec":
            i += 1
            max_sec = float(argv[i])
        elif a == "--concurrency":
            i += 1
            concurrency = int(argv[i])
        elif a.startswith("-"):
            raise SystemExit(f"未知参数 {a}")
        else:
            jobs.append(a)
        i += 1
    return (jobs or DEFAULT_JOBS), rates, max_sec, concurrency


def pick_segments(wd: Path, max_sec: float):
    """取前若干句，累计到 max_sec 原片时长为止。"""
    data = json.loads((wd / "03_translate" / "segments_vi.json").read_text(encoding="utf-8"))
    segs = [s for s in data.get("segments", []) if (s.get("vi") or "").strip()]
    first_start = float(segs[0]["start"])
    picked = []
    for s in segs:
        picked.append(s)
        if float(s["end"]) - first_start >= max_sec:
            break
    span = float(picked[-1]["end"]) - first_start
    gender = (data.get("gender") or {}).get("label") or "female"
    return picked, span, gender


def build_track(wd: Path, segs, rate_pct: int, voice: str, sr: int, gap: float, conc: int,
                work: Path):
    tmp = ensure_dir(work / f"r{rate_pct:03d}")
    items = []
    for n, s in enumerate(segs):
        items.append({
            "id": s["id"],
            "text": clean_text(s["vi"]),
            "raw": tmp / f"seg_{n:04d}.mp3",
            "wav": tmp / f"seg_{n:04d}.wav",
            "rate": f"+{rate_pct}%",
        })
    items = [it for it in items if it["text"]]
    ok = asyncio.run(_synth_batch(items, voice, f"+{rate_pct}%", tmp, conc, 3))

    import numpy as np

    parts = []
    gap_sil = np.zeros(int(gap * sr), dtype="float32")
    syl = 0
    for it, good in zip(items, ok):
        if not good:
            continue
        dur = _trim_to_wav(it["raw"], it["wav"], sr)
        if dur <= 0:
            continue
        clip = _load_mono(it["wav"], sr)
        if clip.size == 0:
            continue
        parts.append(clip)
        parts.append(gap_sil)
        syl += len(it["text"].split())
    if not parts:
        return None, 0, 0.0
    track = np.concatenate(parts)
    peak = float(np.max(np.abs(track))) if track.size else 0.0
    if peak > 0.95:
        track *= 0.95 / peak
    return track, syl, len(track) / sr


def main() -> None:
    cfg = load_config()
    sr = int(cfg["tts"].get("sample_rate", 44100))
    gap = float(cfg["tts"].get("gap", 0.04)) * 3  # 试听句间留 3 倍常规间隙，听感更自然
    voices = cfg["tts"]["voices"]
    jobs, rates, max_sec, conc = parse_args(sys.argv[1:])
    out_root = ensure_dir(Path(cfg["media_roots"][0]) / "_语速试听")

    import numpy as np
    import soundfile as sf

    rows = []
    for name in jobs:
        wd = job_dir(cfg, name)
        if wd is None:
            print(f"[跳过] 找不到工单 {name}")
            continue
        segs, span, gender = pick_segments(wd, max_sec)
        voice = voices.get(gender) or voices["female"]
        tag = wd.parent.parent.name.split(".")[0]  # 01 / 02 …
        print(f"\n=== {name}（{gender}，{len(segs)} 句，覆盖原片 {span:.1f}s）===")
        for pct in rates:
            track, syl, dur = build_track(wd, segs, pct, voice, sr, gap, conc, wd / "preview")
            if track is None:
                print(f"  +{pct}% 合成失败")
                continue
            wav = out_root / f"{tag}_{name}_r{pct:03d}.wav"
            mp3 = out_root / f"{tag}_{name}_{1 + pct / 100:.2f}x.mp3"
            sf.write(str(wav), np.stack([track, track], axis=1), sr, subtype="PCM_16")
            run([FFMPEG, "-y", "-v", "error", "-i", str(wav), "-c:a", "libmp3lame",
                 "-b:a", "192k", str(mp3)], quiet=True)
            wav.unlink(missing_ok=True)
            sps = syl / dur if dur else 0
            rows.append((name, f"{1 + pct / 100:.2f}x", dur, span, sps, sps / SPS_BASE, mp3.name))
            print(f"  +{pct:3d}%  → {dur:6.2f}s / 原片 {span:5.1f}s  实测 {sps:.2f} 音节/秒  "
                  f"容量 {sps / SPS_BASE:.2f}x  {mp3.name}")

    print("\n工单          档位     合成时长  原片时长  实测SPS  容量倍数  文件")
    for r in rows:
        print(f"{r[0]:14}{r[1]:>6}{r[2]:>10.2f}{r[3]:>10.1f}{r[4]:>9.2f}{r[5]:>10.2f}  {r[6]}")
    print(f"\n试听文件目录：{out_root}")


if __name__ == "__main__":
    main()
