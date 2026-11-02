"""打印指定工单的转写结果（排查幻觉/漏句用）。

用法：python tools/show_asr.py buv_douyin_047 [更多工单...]
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for name in sys.argv[1:]:
    p = ROOT / "work" / name / "02_asr" / "asr.json"
    if not p.exists():
        print(f"=== {name}: 缺 asr.json")
        continue
    d = json.loads(p.read_text(encoding="utf-8"))
    print(f"=== {name}  dur={d.get('duration')}  segs={len(d.get('segments', []))}  "
          f"gender={(d.get('gender') or {}).get('label')}")
    for s in d.get("segments", []):
        print(f"  {s['id']:>3} [{s['start']:7.2f}-{s['end']:7.2f}] "
              f"nsp={s['no_speech_prob']:<6} | {s['text']}")
