"""给「没有口播」的片子写一份空译文，让第 4 步出等长静音轨而不是报错。

适用：纯 BGM 片、ASR 幻觉已被清空的片子、字幕卡。写完后直接
`run.py -i <该素材> --stages 4 --force` 即可交付静音轨。

用法：
  python tools/mksilent.py douyin_033 douyin_106        # 按工单名找（扫 config.media_roots）
  python tools/mksilent.py "E:/…/temp/douyin_033"       # 按工单目录
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.config import job_dir, load_config  # noqa: E402
from pipeline.utils import media_duration, write_json  # noqa: E402


def find_media(site: Path, base: str, exts: list[str]) -> Path | None:
    for ext in exts:
        p = site / f"{base}{ext}"
        if p.is_file():
            return p
    return None


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    cfg = load_config()
    exts = [e.lower() for e in cfg.get("audio_exts", [".mp4", ".mp3"])]
    ok = 0

    for raw in sys.argv[1:]:
        p = Path(raw)
        if p.is_dir():
            wd = p
        else:
            wd = job_dir(cfg, p.name if p.suffix else raw)
        if wd is None or not wd.is_dir():
            print(f"[跳过] {raw}：找不到工单目录")
            continue

        site = wd.parent.parent
        base = wd.name
        media = find_media(site, base, exts)
        if media is None:
            print(f"[跳过] {base}：源素材不在 {site.name}")
            continue

        dur = media_duration(media)
        out = wd / "03_translate" / "segments_vi.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        write_json(out, {"media": str(media), "duration": round(dur, 3), "gender": None,
                         "segments": [], "silent": True})
        print(f"{site.name}/{base}: {dur:.2f}s 空文案 → 03_translate/segments_vi.json")
        ok += 1

    print(f"完成 {ok}/{len(sys.argv) - 1}")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
