"""调度验收：时长/峰值/起点对齐/语义残留检查（临时脚本）"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"E:/Projects/音频翻译工具")
FFMPEG = "ffmpeg"
CJK = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff]")


def dur(p):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(p)], capture_output=True, text=True)
    return float(out.stdout.strip())


def vol(p, ss=None, t=None):
    cmd = ["ffmpeg", "-hide_banner", "-v", "info"]
    if ss is not None:
        cmd += ["-ss", str(ss)]
    if t is not None:
        cmd += ["-t", str(t)]
    cmd += ["-i", str(p), "-af", "volumedetect", "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    mean = mx = -99.0
    for line in (r.stderr or "").splitlines():
        if "mean_volume" in line:
            mean = float(line.split(":")[1].split("dB")[0])
        if "max_volume" in line:
            mx = float(line.split(":")[1].split("dB")[0])
    return mean, mx


def main():
    names = sys.argv[1:] or ["douyin_051", "douyin_152", "douyin_165"]
    for n in names:
        j = ROOT / "work" / n
        src = json.loads((j / "03_translate" / "segments_vi.json").read_text(encoding="utf-8"))
        src_media = Path(src["media"])
        base = src_media.stem  # 工单目录名可能带前缀，产物文件名始终用源文件基名
        wav = j / "04_voice" / f"{base}_vi_voice.wav"
        mp3 = j / "04_voice" / f"{base}_vi_voice.mp3"
        d_src, d_out = dur(src_media), dur(mp3)
        _, peak = vol(wav)
        print(f"\n===== {n} =====")
        print(f"时长 源 {d_src:.2f}s / 配音 {d_out:.2f}s  差 {abs(d_src-d_out):.3f}s  "
              f"{'OK' if abs(d_src-d_out) <= 0.3 else 'FAIL'}")
        print(f"峰值 {peak:.2f}dBFS  {'OK' if peak <= -0.5 else 'FAIL(需查削波)'}")
        print(f"性别 {src['gender']['label']} median_f0={src['gender']['median_f0']} "
              f"conf={src['gender']['confidence']}")

        segs = src["segments"]
        picks = [segs[0], segs[len(segs)//2], segs[-1]]
        for s in picks:
            st = s["start"]
            before, _ = vol(wav, ss=max(0.0, st - 0.6), t=0.45) if st > 0.7 else (-99, -99)
            onset, _ = vol(wav, ss=st, t=min(0.5, max(0.2, s["slot"] * 0.5)))
            print(f"  id{s['id']:>3} start={st:6.2f}s  前段 {before:6.1f}dB / 起句 {onset:6.1f}dB  "
                  f"{'OK' if onset > -45 else 'FAIL(该点无配音)'}")
            print(f"        zh: {s['zh']}")
            print(f"        vi: {s['vi']}")

        bad = [s["id"] for s in segs if CJK.search(s.get("vi") or "")]
        print(f"越南语含中日文字符：{bad or '无'}")
        stats = json.loads((j / "04_voice" / "tts_stats.json").read_text(encoding="utf-8"))
        print(f"音色 {stats['voice']} / 句数 {stats['placed']}/{stats['segments']} / "
              f"avg_tempo {stats['avg_tempo']} / max_tempo {stats['max_tempo']}")

        mf = j / "04_voice" / "tts_manifest.json"
        if mf.exists():
            rows = json.loads(mf.read_text(encoding="utf-8"))
            hot = [r for r in rows if r["tempo"] >= 1.2]
            if hot:
                print(f"  ⚠ 提速 ≥1.2x 的 {len(hot)} 句：")
                for r in sorted(hot, key=lambda x: -x["tempo"]):
                    print(f"     id{r['id']:>3} tempo {r['tempo']}  实际 {r['raw_dur']}s / 槽位 {r['slot']}s | {r['vi']}")
            else:
                print("  提速均 <1.2x，语速自然")


if __name__ == "__main__":
    main()
