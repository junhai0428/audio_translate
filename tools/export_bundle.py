"""把交付物补齐到素材目录，并清掉旧规范留下的多余文件。

目录规范（新）：
  <素材目录>/<视频>.mp4
  <素材目录>/<视频>_背景声.mp3        ← 交付物 1（纯 BGM）
  <素材目录>/<视频>_越南语配音.mp3    ← 交付物 2（越南语人声轨）
  <素材目录>/temp/<工单>/...          ← 其余全是中间产物

用法：
  python tools/export_bundle.py                 # 全量（扫 config.media_roots）
  python tools/export_bundle.py bz_douyin_012   # 指定工单
  python tools/export_bundle.py --dry           # 只看要做什么
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.config import iter_job_dirs, load_config  # noqa: E402
from pipeline.utils import read_json  # noqa: E402

# 旧规范残留在素材目录里的中间文件（应清理）
LEGACY = ["_人声.mp3", "_中文.txt", "_越南语.txt", "_校对说明.md", "_时间轴.json", "_vi_voice.mp3"]


def _media_of(wd: Path):
    state = read_json(wd / "state.json", {}) or {}
    if state.get("media"):
        return Path(state["media"])
    asr = read_json(wd / "02_asr" / "asr.json", {}) or {}
    if asr.get("media"):
        return Path(asr["media"])
    return None


def export_one(wd: Path, dry: bool = False) -> bool:
    media = _media_of(wd)
    if media is None:
        print(f"[skip] {wd.name}：找不到源素材路径（state.json/asr.json 缺失）")
        return False
    site, base = media.parent, media.stem
    if not site.is_dir():
        print(f"[skip] {wd.name}：素材目录不存在 {site}")
        return False

    pairs = [
        (wd / "01_separate" / f"{base}_bgm.mp3", site / f"{base}_背景声.mp3"),
        (wd / "04_voice" / f"{base}_vi_voice.mp3", site / f"{base}_越南语配音.mp3"),
    ]
    done, missing = [], []
    for src, dst in pairs:
        if not src.is_file():
            missing.append(dst.name)
            continue
        if not dry:
            shutil.copy2(src, dst)
        done.append(dst.name)

    removed = []
    for suf in LEGACY:
        f = site / f"{base}{suf}"
        if f.is_file():
            if not dry:
                f.unlink(missing_ok=True)
            removed.append(f.name)

    print(f"[ok] {wd.name} → {site.name}/")
    print(f"     交付 {len(done)}：{', '.join(done) or '-'}"
          + (f" | 清理 {len(removed)}：{', '.join(removed)}" if removed else ""))
    if missing:
        print(f"     缺失：{', '.join(missing)}")
    return not missing


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    dry = "--dry" in sys.argv
    cfg = load_config()
    wds = iter_job_dirs(cfg)
    if args:
        wds = [d for d in wds if d.name in set(args)]
    ok = sum(1 for wd in wds if export_one(wd, dry))
    print(f"\n完成 {ok}/{len(wds)} 个工单{'（dry-run，未落盘）' if dry else ''}")


if __name__ == "__main__":
    main()
