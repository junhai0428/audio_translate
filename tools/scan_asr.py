"""批量体检转写结果：抓 whisper 幻觉、无人声素材、句数异常。

用法：
  python tools/scan_asr.py                 # 扫 work/ 下全部工单
  python tools/scan_asr.py bz_ buv_        # 只扫指定前缀
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work"

# whisper 在静音/纯音乐上最常吐出来的模板句（中文训练集水印 + 北欧语水印）
HALLUC = [
    "优优独播剧场", "YoYo", "Television Series Exclusive", "请不吝点赞",
    "支持明镜与点点栏目", "字幕由", "Takk for", "Teksting av", "Amara",
    "字幕志愿者", "订阅我的频道", "請不吝點贊",
]


def main() -> None:
    prefixes = sys.argv[1:] or [""]
    rows = []
    for wd in sorted(WORK.iterdir()):
        if not (wd / "02_asr" / "asr.json").is_file():
            continue
        if prefixes != [""] and not any(wd.name.startswith(p) for p in prefixes):
            continue

        d = json.loads((wd / "02_asr" / "asr.json").read_text(encoding="utf-8"))
        segs = d.get("segments", [])
        dur = float(d.get("duration") or 0)
        hits = []
        for s in segs:
            t = s.get("text", "")
            if any(h in t for h in HALLUC):
                hits.append((s["id"], t[:34]))
        span = sum(s["end"] - s["start"] for s in segs)
        rows.append((wd.name, dur, len(segs), round(span / dur, 2) if dur else 0, hits))

    print(f"{'工单':<16}{'时长':>7}{'句数':>5}{'语音占比':>9}  异常")
    print("-" * 88)
    bad, empty = [], []
    for name, dur, n, ratio, hits in rows:
        marks = []
        if hits:
            marks.append(f"幻觉{len(hits)}句")
            for i, t in hits[:3]:
                marks.append(f"id{i}:{t}")
        if n <= 3:
            marks.append("句数过少")
            empty.append(name)
        if ratio < 0.35:
            marks.append(f"语音占比低 {ratio}")
        if marks:
            bad.append(name)
        print(f"{name:<16}{dur:>7.1f}{n:>5}{ratio:>9}  {' | '.join(marks)}")

    print("\n---- 需人工确认 ----")
    print(f"疑似无人声/幻觉素材 {len(bad)} 条：{', '.join(bad)}")


if __name__ == "__main__":
    main()
