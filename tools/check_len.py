"""检查译文音节是否超过槽位预算，输出可直接定位的超长句清单。

预算（max_syl）直接读第 3 步落盘的 03_translate/segments_vi.json，
老工单缺该字段时退回 reports/_zh_all.txt（name|id|max_syl|中文）。

用法：python tools/check_len.py            # 全量
输出：reports/_over_len.txt（工单|id|音节/预算|中文|越南语）
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.config import iter_job_dirs, load_config  # noqa: E402

REPORTS = ROOT / "reports"
OUT = REPORTS / "_over_len.txt"
VI_SPLIT = r"[\s\.,;:!?…\-—/\"'()\[\]]+"


def count_vi_syllables(text: str) -> int:
    """与 pipeline.utils.count_vi_syllables 同口径：数字串按 3 位 1 音节。"""
    t = re.sub(r"(\d)", r" \1", text or "")
    n = 0
    for w in re.split(VI_SPLIT, t):
        if not w:
            continue
        n += max(1, (len(w) + 2) // 3) if w.isdigit() else 1
    return n


def load_budget_from_segments(cfg: dict) -> dict:
    """(工单, 句号) → max_syl，直接取第 3 步落盘的 segments_vi.json。"""
    out = {}
    for wd in iter_job_dirs(cfg):
        sp = wd / "03_translate" / "segments_vi.json"
        if not sp.is_file():
            continue
        try:
            data = json.loads(sp.read_text(encoding="utf-8"))
        except Exception:
            continue
        for s in data.get("segments") or []:
            if s.get("max_syl") is not None:
                out[(wd.name, int(s["id"]))] = int(s["max_syl"])
    return out


def load_budget_fallback() -> dict:
    """老工单没有 max_syl 时，退回 reports/_zh_all.txt（name|id|max_syl|中文）。"""
    p = REPORTS / "_zh_all.txt"
    out = {}
    if not p.is_file():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        parts = line.split("|")
        if len(parts) >= 4:
            out[(parts[0], int(parts[1]))] = int(parts[2])
    return out


def main() -> None:
    cfg = load_config()
    budget = load_budget_from_segments(cfg)
    if not budget:
        budget = load_budget_fallback()
    if not budget:
        print("找不到 max_syl：先跑第 3 步，或 step3_helper build 生成 segments_vi.json")
        return
    rows, total, over, tight = [], 0, 0, []
    for wd in iter_job_dirs(cfg):
        tp = wd / "03_translate" / "_translated.json"
        if not tp.is_file():
            continue
        recs = json.loads(tp.read_text(encoding="utf-8"))
        used = bsum = 0
        for r in recs:
            mx = budget.get((wd.name, int(r["id"])), 0)
            syl = count_vi_syllables(r.get("vi") or "")
            used += syl
            bsum += mx
            if mx and syl > mx:
                over += 1
                rows.append(f"{wd.name}|{r['id']}|{syl}/{mx}|{r.get('zh','')}|{r.get('vi','')}")
        total += used
        if bsum and used / bsum > 0.95:
            tight.append(f"{wd.name} 占用 {used/bsum:.2f}（{used}/{bsum}）")
    rows.extend(f"【紧】{t}" for t in tight)
    REPORTS.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(rows), encoding="utf-8")
    print(f"总音节 {total}，超长句 {over} 句，占用>0.95 的工单 {len(tight)} 条 → {OUT}")


if __name__ == "__main__":
    main()
