"""对比重翻前后的越南语（旧稿取 _translated.bak.json）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.config import job_dir, load_config  # noqa: E402


def main() -> None:
    cfg = load_config()
    for name in sys.argv[1:]:
        wd = job_dir(cfg, name)
        if wd is None:
            print(f"[跳过] {name}")
            continue
        seg = json.loads((wd / "03_translate" / "segments_vi.json").read_text(encoding="utf-8"))["segments"]
        bp = wd / "03_translate" / "_translated.bak.json"
        old = {}
        if bp.is_file():
            old = {r["id"]: r.get("vi", "") for r in json.loads(bp.read_text(encoding="utf-8"))}
        print(f"\n=== {name} ===")
        for s in seg:
            n = len(s["vi"].split())
            o = len(old.get(s["id"], "").split())
            print(f"{s['id']:>3} 新{n:>2}/{s['max_syl']:<2} {s['vi']}")
            print(f"    旧{o:>2}    {old.get(s['id'], '')}")


if __name__ == "__main__":
    main()
