"""导出某工单的逐句清单（供重翻用）：id|slot|max_syl|zh|现vi

用法：python tools/dump_job.py douyin_188
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.config import job_dir, load_config  # noqa: E402
from pipeline.utils import count_vi_syllables  # noqa: E402

SPS = 5.0
HEAD = 0.2


def main() -> None:
    cfg = load_config()
    for name in sys.argv[1:]:
        wd = job_dir(cfg, name)
        if wd is None:
            print(f"[跳过] 找不到 {name}")
            continue
        data = json.loads((wd / "03_translate" / "segments_vi.json").read_text(encoding="utf-8"))
        segs = [s for s in data.get("segments", []) if (s.get("vi") or "").strip()]
        print(f"### {name} | {len(segs)} 句 | {data.get('duration')}s | "
              f"{(data.get('gender') or {}).get('label')}")
        tot_new = tot_old = 0
        for s in segs:
            cap = max(2, round((float(s["slot"]) - HEAD) * SPS))
            old = count_vi_syllables(s.get("vi") or "")
            tot_new += cap
            tot_old += old
            print(f"{s['id']}|{s['slot']}|{cap}|{s['zh']}|{s.get('vi')}")
        print(f"# 合计 现 {tot_old} 音节 / 新容量 {tot_new}")


if __name__ == "__main__":
    main()
