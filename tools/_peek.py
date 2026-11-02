"""临时：查看某工单的逐句中越对照。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.config import job_dir, load_config  # noqa: E402

cfg = load_config()
name = sys.argv[1]
n = int(sys.argv[2]) if len(sys.argv) > 2 else 20
wd = job_dir(cfg, name)
p = wd / "03_translate" / "segments_vi.json"
data = json.loads(p.read_text(encoding="utf-8"))
print(f"{name}: {len(data['segments'])} 句, {data.get('duration')}s, {data.get('gender')}")
for s in data["segments"][:n]:
    print(f"{s['id']:>3} {s['start']:6.2f}-{s['end']:6.2f} cap{s['max_syl']:<3} | {s.get('zh','')} || {s.get('vi','')}")
