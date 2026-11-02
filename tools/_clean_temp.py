"""临时：清理 temp 下非源素材的工单目录（_nosub/_cfr 等历史派生物）。"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

SRC = re.compile(r"^([A-Za-z0-9\u4e00-\u9fff]+_)?\d+$")

for s in sys.argv[1:]:
    temp = Path(s) / "temp"
    if not temp.is_dir():
        print(s, "无 temp")
        continue
    keep, drop = [], []
    for d in sorted(temp.iterdir()):
        if d.is_dir() and not SRC.match(d.name):
            drop.append(d)
        else:
            keep.append(d)
    for d in drop:
        shutil.rmtree(d)
    print(f"{Path(s).name}: 保留 {len(keep)}，删除 {len(drop)}")
    if drop:
        print("   删除:", [d.name for d in drop][:10])
