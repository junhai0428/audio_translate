"""定点修补某些句子的译文（字符串替换），改完自动重配音。

用法：
  python tools/patch_vi.py --site 07.七绿七子白洗脸皂 douyin_125 3 "bồ hòn皂角" "bồ hòn"
  python tools/patch_vi.py --site 07.七绿七子白洗脸皂 douyin_019 17 "" "Mua lại chính là câu trả lời rõ nhất"
（old 为空串表示整句替换）
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.config import iter_job_dirs, load_config  # noqa: E402
from pipeline.utils import write_json  # noqa: E402

argv = sys.argv[1:]
site = argv[argv.index("--site") + 1] if "--site" in argv else None
pos = [a for i, a in enumerate(argv) if not a.startswith("--") and (i == 0 or argv[i - 1] != "--site")]
if len(pos) == 3:      # name id new  → 整句替换
    name, sid, old, new = pos[0], int(pos[1]), "", pos[2]
else:                  # name id old new → 子串替换
    name, sid, old, new = pos[0], int(pos[1]), pos[2], pos[3]

cfg = load_config()
wd = next((d for d in iter_job_dirs(cfg)
           if d.name == name and (not site or d.parent.parent.name == site)), None)
if wd is None:
    raise SystemExit(f"找不到工单 {name}")

sp = wd / "03_translate" / "segments_vi.json"
data = json.loads(sp.read_text(encoding="utf-8"))
hit = False
for s in data["segments"]:
    if int(s["id"]) != sid:
        continue
    before = s.get("vi") or ""
    after = new if not old else before.replace(old, new)
    if after == before:
        raise SystemExit(f"未命中：id={sid} 里找不到 {old!r}")
    s["vi"] = after
    hit = True
    print(f"{name} #{sid}\n  - {before}\n  + {after}")
write_json(sp, data)
if not hit:
    raise SystemExit(f"没有 id={sid}")

media = wd.parent.parent / f"{wd.name}.mp4"
subprocess.run([sys.executable, str(ROOT / "run.py"), "-i", str(media),
                "--stages", "4", "--force"], cwd=str(ROOT), check=True)
