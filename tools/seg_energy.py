"""逐句测人声能量，用来区分「真实口播」和「whisper 在静音段吐出的幻觉句」。

用法：python tools/seg_energy.py bz_ buv_     # 按工单前缀批量
输出：reports/_energy.txt（工单|句号|起止|RMS|nsp|文本）
"""
import json
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.config import iter_job_dirs, load_config  # noqa: E402

SR = 16000
OUT = ROOT / "reports" / "_energy.txt"


def rms_db(chunk: np.ndarray) -> float:
    if chunk.size == 0:
        return -99.0
    return float(20 * np.log10(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)) + 1e-9))


def main() -> None:
    prefixes = sys.argv[1:] or [""]
    lines = []
    for wd in iter_job_dirs(load_config()):
        if prefixes != [""] and not any(wd.name.startswith(p) for p in prefixes):
            continue
        wav = wd / "02_asr" / "asr_input_16k.wav"
        js = wd / "02_asr" / "asr.json"
        if not (wav.is_file() and js.is_file()):
            continue
        with wave.open(str(wav), "rb") as f:
            data = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        d = json.loads(js.read_text(encoding="utf-8"))
        for s in d.get("segments", []):
            a, b = max(0, int(s["start"] * SR)), min(len(data), int(s["end"] * SR))
            lines.append(
                f"{wd.name}|{s['id']}|{s['start']:.2f}-{s['end']:.2f}|{rms_db(data[a:b]):.1f}"
                f"|nsp={s['no_speech_prob']}|{s['text']}"
            )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    vals = [float(l.split("|")[3]) for l in lines]
    print(f"共 {len(lines)} 句 → {OUT}")
    if vals:
        arr = np.array(vals)
        print(f"RMS 分布：min {arr.min():.1f} / p10 {np.percentile(arr,10):.1f} / "
              f"median {np.median(arr):.1f} / p90 {np.percentile(arr,90):.1f} / max {arr.max():.1f}")
        print(f"低于 -45dB 的 {int((arr < -45).sum())} 句，低于 -50dB 的 {int((arr < -50).sum())} 句")


if __name__ == "__main__":
    main()
