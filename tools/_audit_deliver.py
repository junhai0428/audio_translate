"""临时：逐条核对源素材与交付物（正确命名 vs 错名残留）。"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SRC = re.compile(r"^([A-Za-z0-9\u4e00-\u9fff]+_)?\d+$")

SITES = [
    r"E:\001.TTS\000视频素材\01.八珍足贴",
    r"E:\001.TTS\000视频素材\02.BUV小绿泥洗面奶",
    r"E:\001.TTS\000视频素材\03.SKT烟酰胺蚕丝皂",
    r"E:\001.TTS\000视频素材\05.木子言身体素颜霜",
    r"E:\001.TTS\000视频素材\07.七绿七子白洗脸皂",
    r"E:\001.TTS\000视频素材\08.米诺头皮上药器",
]

for s in SITES:
    site = Path(s)
    if not site.is_dir():
        continue
    srcs = sorted(p for p in site.iterdir() if p.suffix.lower() == ".mp4" and SRC.match(p.stem))
    ok = [p for p in srcs if (site / f"{p.stem}_越南语配音.mp3").is_file()]
    miss = [p.stem for p in srcs if not (site / f"{p.stem}_越南语配音.mp3").is_file()]
    stale = [p.name for p in site.glob("*_vi.mp3")] + [p.name for p in site.glob("*_BGM.mp3")]
    print(f"{site.name}: 源 {len(srcs)} / 正确交付 {len(ok)} / 缺 {len(miss)} / 错名残留 {len(stale)}")
    if miss:
        print("   缺:", miss[:12])
