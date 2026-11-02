"""第 3 步辅助工具（调度者/角色手工翻译时使用）

whisper 单句上限 30 秒，口播类素材常出现 20~30 秒的"超长整段"，
直接配音会导致一句话铺满半条片子、无法逐句对齐。本工具用词级时间戳把它切成可对齐的小句。

用法：
  1) 切句   python tools/step3_helper.py split  work/<音频名>
            → 03_translate/zh_split.json    [{id,start,end,zh}]
  2) 人工/角色翻译后，把结果写成 03_translate/_translated.json：[{"id":1,"zh":"...","vi":"..."}]
  3) 合稿   python tools/step3_helper.py build work/<音频名>
            → 03_translate/segments_vi.json / zh_opt.txt / vi.txt，并打印音节预算占用
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.utils import compute_slots, count_vi_syllables, hhmmss, read_json, write_json  # noqa: E402

PUNCT = "，,。；;：:！!？?、…"
MAX_DUR = 5.0       # 单句目标时长上限（秒）
MAX_CHARS = 30      # 单句字数上限
MIN_DUR = 1.0       # 切出来的句子最小时长，太碎不配音


def _close_at(word: str) -> bool:
    return bool(word) and word[-1] in PUNCT


def split_one(seg: dict) -> list:
    """把一句（可能 30 秒）按词级时间戳切成可对齐的小句。"""
    start, end = float(seg["start"]), float(seg["end"])
    if end - start <= MAX_DUR and len(seg["text"]) <= MAX_CHARS:
        return [{"start": start, "end": end, "zh": seg["text"].strip()}]

    words = seg.get("words") or []
    if not words:
        return _split_by_ratio(seg)

    chunks, cur = [], []
    for w in words:
        cur.append(w)
        c_dur = float(cur[-1]["e"]) - float(cur[0]["s"])
        c_txt = "".join(x["w"] for x in cur)
        gap_next = None
        idx = words.index(w)
        if idx + 1 < len(words):
            gap_next = float(words[idx + 1]["s"]) - float(w["e"])
        if len(c_txt) >= MAX_CHARS or c_dur >= MAX_DUR + 1.0:
            chunks.append(cur)
            cur = []
        elif _close_at(w["w"]) and (len(c_txt) >= 16 or c_dur >= 3.0):
            chunks.append(cur)
            cur = []
        elif gap_next is not None and gap_next >= 0.35 and c_dur >= 2.0:
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)

    out = []
    for c in chunks:
        s = float(c[0]["s"])
        e = float(c[-1]["e"])
        txt = "".join(x["w"] for x in c).strip().strip("，,、。 ")
        if not txt:
            continue
        if e - s < MIN_DUR:
            e = min(s + MIN_DUR, end)
        out.append({"start": round(s, 3), "end": round(e, 3), "zh": txt})
    return out or [{"start": start, "end": end, "zh": seg["text"].strip()}]


def _split_by_ratio(seg: dict) -> list:
    """没有词级时间戳时，按标点 + 字数比例切分（退化方案）。"""
    start, end = float(seg["start"]), float(seg["end"])
    parts = [p for p in re.split(r"(?<=[%s])" % re.escape(PUNCT), seg["text"]) if p.strip()]
    dur = end - start
    total = sum(len(p) for p in parts) or 1
    out, t = [], start
    for p in parts:
        d = dur * len(p) / total
        out.append({"start": round(t, 3), "end": round(min(t + d, end), 3), "zh": p.strip().strip("，,、。 ")})
        t += d
    return out


def cmd_split(work_dir: Path) -> None:
    asr = read_json(work_dir / "02_asr" / "asr.json")
    segs = asr.get("segments") or []
    out = []
    for s in segs:
        for piece in split_one(s):
            out.append({"id": len(out) + 1, **piece})
    dst = work_dir / "03_translate" / "zh_split.json"
    write_json(dst, {"media": asr.get("media"), "duration": asr.get("duration"),
                     "gender": asr.get("gender"), "segments": out})
    print(f"切句完成：{len(segs)} → {len(out)} 条 → {dst}")
    for s in out:
        print(f"  {s['id']:>3} [{hhmmss(s['start'])} - {hhmmss(s['end'])}] {s['zh']}")


def apply_budget(segs, total_dur) -> None:
    """按项目口径给每句算 slot / max_syl / syl（就地写回）。"""
    cfg = read_json(ROOT / "config.json", {})
    tcfg = cfg.get("translate", {})
    sps = float(tcfg.get("vi_syllables_per_sec", 3.2))
    head_line = float(tcfg.get("head_line", 0.2))
    slots = compute_slots(segs, total_dur,
                          gap_use=float(tcfg.get("gap_use", 0.85)), gap=float(tcfg.get("gap", 0.04)))
    for s, (_, slot) in zip(segs, slots):
        s["slot"] = round(slot, 3)
        s["max_syl"] = max(2, int(round((slot - head_line) * sps)))
        s["syl"] = count_vi_syllables(s.get("vi") or "")


def prepare(work_dir: Path) -> dict:
    """切句 + 预算（已切过就复用），返回 zh_split 数据。"""
    p = work_dir / "03_translate" / "zh_split.json"
    data = read_json(p)
    if not data:
        cmd_split(work_dir)
        data = read_json(p)
    apply_budget(data["segments"], float(data.get("duration") or 0))
    return data


def cmd_todo(work_dirs) -> None:
    """批量切句并生成「待翻译清单」work/_zh_all.txt：每行 name|id|max_syl|中文"""
    lines, total = [], 0
    for wd in work_dirs:
        data = prepare(wd)
        total += len(data["segments"])
        for s in data["segments"]:
            lines.append(f"{wd.name}|{s['id']}|{s['max_syl']}|{s['zh']}")
    out = ROOT / "work" / "_zh_all.txt"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"待翻译 {len(work_dirs)} 条 / {total} 句 → {out}")


def cmd_build(work_dir: Path) -> None:
    split = read_json(work_dir / "03_translate" / "zh_split.json")
    trans = read_json(work_dir / "03_translate" / "_translated.json") or []

    by_id = {int(t["id"]): t for t in trans}
    segs = []
    for s in split["segments"]:
        t = by_id.get(int(s["id"]), {})
        segs.append({**s, "zh": (t.get("zh") or s["zh"]).strip(),
                     "vi": (t.get("vi") or "").strip()})

    total_dur = float(split.get("duration") or 0) or (segs[-1]["end"] + 0.5)
    apply_budget(segs, total_dur)

    write_json(work_dir / "03_translate" / "segments_vi.json",
               {"media": split.get("media"), "duration": round(total_dur, 3),
                "gender": split.get("gender"), "segments": segs})

    def render(field):
        return "\n".join(f"[{hhmmss(s['start'])} - {hhmmss(s['end'])}] {s[field]}"
                         for s in segs if s.get(field)) + "\n"

    (work_dir / "03_translate" / "zh_opt.txt").write_text(render("zh"), encoding="utf-8")
    (work_dir / "03_translate" / "vi.txt").write_text(render("vi"), encoding="utf-8")

    vi_syl = sum(s["syl"] for s in segs)
    budget = sum(s["max_syl"] for s in segs) or 1
    over = [s["id"] for s in segs if s["syl"] > s["max_syl"]]
    print(f"合稿完成：{len(segs)} 条")
    print(f"音节 {vi_syl} / 预算 {budget} = 占用 {vi_syl/budget:.2f}（目标 ≤0.95）")
    if over:
        print(f"[!] 超长句 id={over}：")
        for s in segs:
            if s["syl"] > s["max_syl"]:
                print(f"   {s['id']:>3} syl {s['syl']} > max {s['max_syl']} | slot {s['slot']} | {s['vi']}")
    else:
        print("[ok] 无超长句")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    wds = [Path(a) for a in sys.argv[2:]]
    if cmd == "todo":
        cmd_todo(wds)
    else:
        fn = cmd_split if cmd == "split" else cmd_build
        for wd in wds:
            fn(wd)
