"""临时：按工单目录路径补空译文，让第 4 步出等长静音轨。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.utils import media_duration, write_json  # noqa: E402

for raw in sys.argv[1:]:
    wd = Path(raw)
    media = wd.parent.parent / f"{wd.name}.mp4"
    if not media.is_file():
        print(f"skip {wd.name}: 源文件不存在")
        continue
    dur = media_duration(media)
    p = wd / "03_translate" / "segments_vi.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    write_json(p, {"media": str(media), "duration": round(dur, 3), "gender": None,
                   "segments": [], "silent": True})
    print(f"{wd.name}: {dur:.2f}s 空文案已写入 {p.parent.parent.parent.name}")
