"""把重翻稿合并进 _translated.json 并重新合稿（更新 slot/max_syl/segments_vi.json）。

用法：
  python tools/apply_vi.py douyin_188           # 读 reports/_new_vi/douyin_188.json
  python tools/apply_vi.py douyin_188 --build   # 合并后立即合稿

译文文件格式：{"1": "câu tiếng Việt", "2": "..."}（id → vi）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.config import iter_job_dirs, job_dir, load_config  # noqa: E402
from pipeline.utils import count_vi_syllables, read_json, write_json  # noqa: E402
from tools.step3_helper import cmd_build  # noqa: E402

SRC = ROOT / "reports" / "_new_vi"
SPS = 5.0
HEAD = 0.2


def apply_one(wd: Path, build: bool) -> None:
    site = wd.parent.parent.name
    src = SRC / site / f"{wd.name}.json"
    if not src.is_file():
        src = SRC / f"{wd.name}.json"
    if not src.is_file():
        raise SystemExit(f"缺少译文文件 {src}")
    new = json.loads(src.read_text(encoding="utf-8"))
    tp = wd / "03_translate" / "_translated.json"
    rows = read_json(tp) or []
    zh_map = {int(r["id"]): r.get("zh", "") for r in rows}
    split = read_json(wd / "03_translate" / "zh_split.json") or {}
    for s in split.get("segments", []):
        zh_map.setdefault(int(s["id"]), s["zh"])

    by_id = {int(r["id"]): r for r in rows}
    for k, vi in new.items():
        i = int(k)
        if i in by_id:
            by_id[i]["vi"] = vi
        else:
            by_id[i] = {"id": i, "zh": zh_map.get(i, ""), "vi": vi}
    out = [by_id[i] for i in sorted(by_id)]
    write_json(tp, out)

    seg = {s["id"]: s for s in (read_json(wd / "03_translate" / "segments_vi.json") or {}).get("segments", [])}
    over = []
    for k, vi in new.items():
        s = seg.get(int(k))
        if not s:
            continue
        cap = max(2, round((float(s["slot"]) - HEAD) * SPS))
        n = count_vi_syllables(vi)
        if n > cap:
            over.append((int(k), n, cap, vi))
    print(f"{wd.name}: 覆盖 {len(new)} 句，超新容量 {len(over)} 句")
    for i, n, cap, vi in over[:10]:
        print(f"   [{i}] {n} > {cap} | {vi}")
    if build:
        cmd_build(wd)


def main() -> None:
    argv = sys.argv[1:]
    site = None
    if "--site" in argv:
        site = argv[argv.index("--site") + 1]
    args = [a for i, a in enumerate(argv) if not a.startswith("--") and (i == 0 or not argv[i - 1] == "--site")]
    build = "--no-build" not in argv
    cfg = load_config()
    for name in args:
        wd = job_dir(cfg, name)
        if wd is not None and site and wd.parent.parent.name != site:
            cands = [d for d in iter_job_dirs(cfg) if d.name == name and d.parent.parent.name == site]
            wd = cands[0] if cands else None
        if wd is None:
            print(f"[跳过] 找不到 {name}")
            continue
        apply_one(wd, build)


if __name__ == "__main__":
    main()
