"""打印指定工单的转写结果（排查幻觉/漏句用）。

用法：python tools/show_asr.py buv_douyin_047 [更多工单...]
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.config import job_dir, load_config  # noqa: E402

cfg = load_config()
for name in sys.argv[1:]:
    wd = job_dir(cfg, name)
    p = (wd / "02_asr" / "asr.json") if wd else None
    if p is None or not p.is_file():
        print(f"=== {name}: 缺 asr.json（未找到 temp/{name}）")
        continue
    d = json.loads(p.read_text(encoding="utf-8"))
    print(f"=== {name}  dur={d.get('duration')}  segs={len(d.get('segments', []))}  "
          f"gender={(d.get('gender') or {}).get('label')}")
    for s in d.get("segments", []):
        print(f"  {s['id']:>3} [{s['start']:7.2f}-{s['end']:7.2f}] "
              f"nsp={s['no_speech_prob']:<6} | {s['text']}")
