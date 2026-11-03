"""翻译体检：按 config.translate.vi_syllables_per_sec（当前 5.0 音节/秒）重算每句容量，
找出"被压得过头"的句子（信息没铺满新容量）。

判据（两条同时看）：
  r      = 越南语音节 / 中文音节   —— 越南语同义表达通常比中文长 30%~60%
  target = min(新预算, 中文音节 × 1.45)
  现译音节 < target × 0.75  → 该句信息被砍过，进重翻清单

用法：python tools/audit_translate.py [--sps 5.0] [--thr 0.75]   # 默认读 config
输出：reports/_audit.csv（明细） + 控制台汇总
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.config import iter_job_dirs, load_config  # noqa: E402

HAN = re.compile(r"[\u4e00-\u9fff]")
NUM = re.compile(r"\d+")
WORD = re.compile(r"[A-Za-z]{2,}")
HEAD_LINE = 0.2


def zh_syllables(zh: str) -> int:
    """中文音节估算：汉字 1 个 1 音节，数字串每 3 位 1 个，英文词按 1 个算。"""
    zh = zh or ""
    n = len(HAN.findall(zh))
    n += sum(max(1, len(s) // 3) for s in NUM.findall(zh))
    n += len(WORD.findall(zh))
    return max(1, n)


def main() -> None:
    cfg = load_config()
    # 默认口径取 config，工具和流水线不能各算一套
    tcfg = cfg.get("translate", {})
    sps = float(tcfg.get("vi_syllables_per_sec", 5.0))
    thr = 0.75
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--sps":
            sps = float(args[i + 1])
        elif a == "--thr":
            thr = float(args[i + 1])
    rows = []
    per_job = []
    for wd in iter_job_dirs(cfg):
        sp = wd / "03_translate" / "segments_vi.json"
        if not sp.is_file():
            continue
        data = json.loads(sp.read_text(encoding="utf-8"))
        segs = [s for s in data.get("segments", []) if (s.get("vi") or "").strip()]
        if not segs:
            continue
        site = wd.parent.parent.name
        need = 0
        for s in segs:
            zh, vi = s.get("zh") or "", s.get("vi") or ""
            zs, vs = zh_syllables(zh), len(vi.split())
            slot = float(s.get("slot") or 0)
            capacity = max(1, round((slot - HEAD_LINE) * sps))
            target = min(capacity, max(2, round(zs * 1.2)))
            r = round(vs / zs, 2)
            flag = "扩" if vs < target * thr else ("ok" if vs <= capacity else "超")
            rows.append({
                "site": site, "job": wd.name, "id": s["id"], "zh": zh, "vi": vi,
                "zh_syl": zs, "vi_syl": vs, "r": r, "slot": round(slot, 2),
                "syl_old": s.get("syl"), "cap_old": s.get("max_syl"),
                "cap_new": capacity, "target": target,
                "need_add": max(0, target - vs) if flag == "扩" else 0,
                "flag": flag,
            })
            if flag == "扩":
                need += 1
        per_job.append((site, wd.name, len(segs), need))

    out = ROOT / "reports" / "_audit.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    total = len(rows)
    expand = [r for r in rows if r["flag"] == "扩"]
    print(f"句子总数 {total}，需扩容 {len(expand)}（{len(expand)/total:.1%}）")
    print(f"口径：新预算 {sps} 音节/秒，触发阈值 target×{thr}")
    print(f"平均 r = {sum(r['r'] for r in rows)/total:.2f}（越南语音节/中文字）")
    print(f"需补音节合计 {sum(r['need_add'] for r in expand)}，平均每句补 "
          f"{sum(r['need_add'] for r in expand)/max(1,len(expand)):.1f}")
    buckets = {"r<0.7": 0, "0.7-1.0": 0, "1.0-1.3": 0, "1.3-1.6": 0, ">1.6": 0}
    for r in rows:
        v = r["r"]
        if v < 0.7:
            buckets["r<0.7"] += 1
        elif v < 1.0:
            buckets["0.7-1.0"] += 1
        elif v < 1.3:
            buckets["1.0-1.3"] += 1
        elif v < 1.6:
            buckets["1.3-1.6"] += 1
        else:
            buckets[">1.6"] += 1
    print("r 分布：", buckets)
    print("\n按工单（需扩容句数 / 总句数）：")
    per_job.sort(key=lambda x: -x[3])
    for site, name, n, need in per_job:
        if need:
            print(f"  {site[:12]:14}{name:14}{need:>4} / {n}")
    print(f"\n明细 → {out}")


if __name__ == "__main__":
    main()
