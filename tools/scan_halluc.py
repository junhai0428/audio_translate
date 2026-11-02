"""幻觉句体检：找出 whisper 在 BGM/画面字幕上编出来的重复短句。

典型形态：整条片子里同一句"BUT"重复十几遍，配音会跟着念十几遍。

用法：
  python tools/scan_halluc.py                 # 全量扫，只报告
  python tools/scan_halluc.py --fix           # 清空幻觉句的译文并重新合稿
  python tools/scan_halluc.py --site 05.xxx
"""

from __future__ import annotations

import json
import re
import sys
from itertools import groupby
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.config import iter_job_dirs, load_config  # noqa: E402
from pipeline.utils import write_json  # noqa: E402

SRC = re.compile(r"^([A-Za-z0-9\u4e00-\u9fff]+_)?\d+$")
HAN = re.compile(r"[\u4e00-\u9fff]")

MIN_REPEAT = 3      # 同一句连续出现次数
MAX_LEN = 8         # 视为幻觉的最大字符数


def find_halluc(segs: list) -> tuple[list[int], int]:
    """返回（应清空的句子 id, 最长连续重复次数）。"""
    ids, max_rep = [], 0
    for text, group in groupby(segs, key=lambda s: (s.get("zh") or "").strip().lower()):
        g = list(group)
        if not text:
            continue
        # 重复 >=3 次的短句
        if len(g) >= MIN_REPEAT and len(text) <= MAX_LEN:
            ids.extend(s["id"] for s in g)
            max_rep = max(max_rep, len(g))
            continue
        # 单句超长但内容极短（例如 11 秒的 "BUT"）
        long_bad = [s for s in g if (s["end"] - s["start"]) > 3.5 and len(text) <= 4]
        if long_bad:
            ids.extend(s["id"] for s in long_bad)
            max_rep = max(max_rep, 1)
    return sorted(set(ids)), max_rep


def main() -> None:
    argv = sys.argv[1:]
    fix = "--fix" in argv
    site_filter = argv[argv.index("--site") + 1] if "--site" in argv else None
    cfg = load_config()

    rows = []
    for wd in iter_job_dirs(cfg):
        site = wd.parent.parent.name
        if site_filter and site != site_filter:
            continue
        if not SRC.match(wd.name):
            continue
        sp = wd / "03_translate" / "segments_vi.json"
        if not sp.is_file():
            continue
        segs = json.loads(sp.read_text(encoding="utf-8")).get("segments") or []
        # 已经清空译文的句子不再重复报（zh 会一直留着）
        segs = [s for s in segs if (s.get("vi") or "").strip()]
        bad, max_rep = find_halluc(segs)
        if not bad:
            continue
        spoken = [s for s in segs if (s.get("zh") or "").strip()]
        ratio = len(bad) / max(1, len(spoken))
        # 整片性幻觉（占比高）或明显的 repetition loop（连续 >=4 次）才清；
        # 零星的"哎哟""订阅转"多半是主播真的在说，不动
        if ratio < 0.25 and not (max_rep >= 4 and ratio >= 0.12):
            continue
        sample = next((s.get("zh", "") for s in segs if s.get("id") == bad[0]), "")
        rows.append((site, wd.name, len(bad), len(segs), ratio, sample))
        if fix:
            # 直接改最终产物（run.py 链路不产 _translated.json）
            data = json.loads(sp.read_text(encoding="utf-8"))
            for s in data.get("segments") or []:
                if s.get("id") in bad:
                    s["vi"] = ""
            write_json(sp, data)
            # 有中转稿的一并同步，免得下次 build 又写回来
            tp = wd / "03_translate" / "_translated.json"
            if tp.is_file():
                rows_json = json.loads(tp.read_text(encoding="utf-8"))
                for r in rows_json:
                    if int(r["id"]) in bad:
                        r["vi"] = ""
                write_json(tp, rows_json)

    rows.sort(key=lambda x: -x[4])
    print(f"{'工单':<22}{'幻觉句':>6}{'总句':>6}{'占比':>8}  内容")
    for site, name, nb, nt, ratio, sample in rows:
        print(f"{name:<22}{nb:>6}{nt:>6}{ratio*100:>7.0f}%  {sample[:16]}")
    print(f"\n受影响工单 {len(rows)} 个，幻觉句合计 {sum(r[2] for r in rows)} 句")
    if fix:
        print("已清空上述句子并重新合稿")


if __name__ == "__main__":
    main()
