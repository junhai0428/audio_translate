"""验收：时长对齐 / 峰值 / 逐句有声 / 越南语无中文字符 / 语速。

用法：
  python tools/acceptance.py                  # 全量（扫 config.media_roots 下所有 temp/<工单>）
  python tools/acceptance.py bz_ buv_         # 按工单名前缀
  python tools/acceptance.py bz_douyin_137    # 指定工单
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

from pipeline.config import iter_job_dirs, load_config  # noqa: E402

CJK = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff]")


def dur(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(p)], capture_output=True, text=True)
    return float(r.stdout.strip())


def check_one(wd: Path):
    seg_p = wd / "03_translate" / "segments_vi.json"
    if not seg_p.is_file():
        return (wd.name, "无译文(静音占位)", "", "", "")
    data = json.loads(seg_p.read_text(encoding="utf-8"))
    base = Path(data["media"]).stem
    mp3 = wd / "04_voice" / f"{base}_vi_voice.mp3"
    wav = wd / "04_voice" / f"{base}_vi_voice.wav"
    if not mp3.is_file() or not wav.is_file():
        return (wd.name, "FAIL 缺配音", "", "", "")
    d_src = float(data.get("duration") or 0)
    d_out = dur(mp3)
    x, sr = sf.read(str(wav), dtype="float32", always_2d=False)
    if x.ndim > 1:
        x = x.mean(axis=1)
    peak_db = 20 * np.log10(max(1e-9, float(np.max(np.abs(x)))))
    ok_n, bad = 0, []
    for s in data["segments"]:
        if not (s.get("vi") or "").strip():
            continue
        a = int(max(0, s["start"] * sr))
        b = int(min(len(x), (s["start"] + min(float(s["slot"]), 1.0)) * sr))
        seg = x[a:b]
        rms = 20 * np.log10(max(1e-9, float(np.sqrt(np.mean(seg ** 2) + 1e-12)))) if seg.size else -99
        if rms > -45:
            ok_n += 1
        else:
            bad.append(s["id"])
    cjk = [s["id"] for s in data["segments"] if CJK.search(s.get("vi") or "")]
    stats_p = wd / "04_voice" / "tts_stats.json"
    stats = json.loads(stats_p.read_text(encoding="utf-8")) if stats_p.is_file() else {}
    ok = abs(d_src - d_out) <= 0.3 and peak_db <= -0.5 and not bad and not cjk
    tag = "OK" if ok else "FAIL"
    detail = ""
    if not ok:
        detail = f"时长差{d_src-d_out:+.2f} 峰值{peak_db:.1f} 无声{bad[:5]} 中文{cjk[:5]}"
    return (wd.name, tag, f"{d_out:.2f}/{d_src:.2f}", f"{peak_db:.1f}dB",
            f"{ok_n}/{ok_n+len(bad)}句 tempo{stats.get('avg_tempo')}/{stats.get('max_tempo')}", detail)


def main() -> None:
    args = sys.argv[1:]
    wds = iter_job_dirs(load_config())
    if args:
        wds = [d for d in wds if any(d.name == a or d.name.startswith(a) for a in args)]
    fails = 0
    for wd in wds:
        row = check_one(wd)
        print(" | ".join(str(c) for c in row[:-1]))
        if row[1] == "FAIL":
            fails += 1
            print(f"     {row[-1]}")
    print(f"\n合计 {len(wds)} 条，FAIL {fails}")


if __name__ == "__main__":
    main()
