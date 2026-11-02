"""一次性迁移：项目内 work/output/input → 各素材目录下的 temp + 交付物。

旧布局（v1）：
  项目/work/<工单>/<阶段目录>/...        中间产物
  项目/output/<工单>/<名>_vi_voice.mp3   配音交付
  素材目录/<名>_人声.mp3 / _BGM.mp3 / _中文.txt / _越南语.txt / _vi.mp3

新布局（v2）：
  素材目录/<名>_BGM.mp3               交付物 1
  素材目录/<名>_vi.mp3           交付物 2
  素材目录/temp/<工单>/<阶段目录>/...    其余全部是中间产物

用法：python tools/migrate_layout.py [--apply]     # 默认 dry-run
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work"
OUTPUT = ROOT / "output"
LEGACY = ["_人声.mp3", "_中文.txt", "_越南语.txt", "_校对说明.md", "_时间轴.json"]


def media_of(wd: Path):
    for rel in ("state.json", "02_asr/asr.json"):
        p = wd / rel
        if p.is_file():
            try:
                m = json.loads(p.read_text(encoding="utf-8")).get("media")
            except Exception:
                m = None
            if m:
                return Path(m)
    return None


def main() -> None:
    apply = "--apply" in sys.argv
    wds = sorted(d for d in WORK.iterdir() if d.is_dir()) if WORK.is_dir() else []
    moved = delivered = cleaned = skipped = 0
    plans = []
    for wd in wds:
        media = media_of(wd)
        if media is None:
            plans.append(("skip", wd.name, "无 media 记录"))
            skipped += 1
            continue
        site, base = media.parent, media.stem
        dst = site / "temp" / wd.name
        plans.append(("move", f"{wd.name}", f"→ {dst}"))
        moved += 1

        # 交付物：bgm / 配音（优先 work 内，其次旧 output）
        for src, name in ((wd / "01_separate" / f"{base}_bgm.mp3", f"{base}_BGM.mp3"),
                          (wd / "04_voice" / f"{base}_vi_voice.mp3", f"{base}_vi.mp3"),
                          (OUTPUT / wd.name / f"{base}_vi_voice.mp3", f"{base}_vi.mp3")):
            target = site / name
            if src.is_file() and not target.is_file():
                plans.append(("copy", str(src), f"→ {target.name}"))
                delivered += 1
        # 清掉旧规范留在素材目录的中间文件
        for suf in LEGACY:
            f = site / f"{base}{suf}"
            if f.is_file():
                plans.append(("del", str(f), ""))
                cleaned += 1

    for act, a, b in plans:
        print(f"[{act:6}] {a} {b}")
    print(f"\n工单 {len(wds)} 条：迁移 {moved} / 复制交付 {delivered} / 清理 {cleaned} / 跳过 {skipped}")
    if not apply:
        print("（dry-run，加 --apply 执行）")
        return

    for wd in wds:
        media = media_of(wd)
        if media is None:
            continue
        site, base = media.parent, media.stem
        dst = site / "temp" / wd.name
        if dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(wd), str(dst))
        for src, name in ((dst / "01_separate" / f"{base}_bgm.mp3", f"{base}_BGM.mp3"),
                          (dst / "04_voice" / f"{base}_vi_voice.mp3", f"{base}_vi.mp3"),
                          (OUTPUT / wd.name / f"{base}_vi_voice.mp3", f"{base}_vi.mp3")):
            target = site / name
            if src.is_file() and not target.is_file():
                shutil.copy2(src, target)
        for suf in LEGACY:
            f = site / f"{base}{suf}"
            if f.is_file():
                f.unlink(missing_ok=True)
    print("迁移完成")


if __name__ == "__main__":
    main()
