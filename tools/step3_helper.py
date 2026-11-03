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
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.utils import (compute_slots, count_vi_syllables, hhmmss, read_json,  # noqa: E402
                            split_long_segments, write_json)

PUNCT = "，,。；;：:！!？?、…"
MAX_DUR = 5.0       # 单句目标时长上限（秒）
MAX_CHARS = 30      # 单句字数上限
MIN_DUR = 1.0       # 切出来的句子最小时长，太碎不配音


def cmd_split(work_dir: Path) -> None:
    asr = read_json(work_dir / "02_asr" / "asr.json")
    segs = asr.get("segments") or []
    # 切分逻辑在 pipeline.utils（第 3 步翻译前也走同一份），这里只做字段名转换
    out = [{"id": p["id"], "start": p["start"], "end": p["end"], "zh": p["text"]}
           for p in split_long_segments(segs, MAX_DUR, MAX_CHARS, MIN_DUR)]
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
    sps = float(tcfg.get("vi_syllables_per_sec", 5.6))
    head_line = float(tcfg.get("head_line", 0.08))
    slots = compute_slots(segs, total_dur,
                          gap_use=float(tcfg.get("gap_use", 0.95)), gap=float(tcfg.get("gap", 0.04)))
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
    out = ROOT / "reports" / "_zh_all.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
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
