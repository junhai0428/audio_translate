"""把一条视频的全套产物打包到「源视频所在文件夹」。

导出内容（以 douyin_051 为例，全部落在源 mp3 同目录，文件名带后缀，绝不覆盖源音频）：
  douyin_051_人声.mp3        第 1 步 纯人声轨
  douyin_051_背景声.mp3      第 1 步 纯 BGM 轨
  douyin_051_中文.txt        第 3 步 校对后中文文案（带时间轴）
  douyin_051_越南语.txt      第 3 步 越南语文案（带时间轴）
  douyin_051_越南语配音.mp3  第 4 步 越南语配音轨

用法：
  python tools/export_bundle.py                     # 导出 work/ 下所有已完成的工单
  python tools/export_bundle.py douyin_051 douyin_152
  python tools/export_bundle.py --extra             # 额外导出 review.md 与 segments_vi.json
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.utils import read_json  # noqa: E402

WORK = ROOT / "work"
OUTPUT = ROOT / "output"

# (产物相对 work/<工单名>/ 的路径, 目标后缀)。{base} = 源文件基名（导出文件名用它，不带工单前缀）
ITEMS = [
    ("01_separate/{base}.mp3", "_人声.mp3"),          # 纯人声
    ("01_separate/{base}_bgm.mp3", "_背景声.mp3"),     # 纯 BGM
    ("03_translate/zh_opt.txt", "_中文.txt"),
    ("03_translate/vi.txt", "_越南语.txt"),
]
VOICE_FROM_OUTPUT = "{name}/{base}_vi_voice.mp3"
VOICE_FROM_WORK = "04_voice/{base}_vi_voice.mp3"
EXTRA = [("03_translate/review.md", "_校对说明.md"),
         ("03_translate/segments_vi.json", "_时间轴.json")]


def export_one(name: str, extra: bool = False) -> bool:
    wd = WORK / name
    asr = read_json(wd / "02_asr" / "asr.json") or {}
    src = asr.get("media") or read_json(wd / "03_translate" / "zh_split.json", {}).get("media")
    if not src:
        print(f"[skip] {name}：找不到源音频路径（asr.json 缺失）")
        return False
    dst_dir = Path(src).parent
    base = Path(src).stem
    if not dst_dir.is_dir():
        print(f"[skip] {name}：源目录不存在 {dst_dir}")
        return False

    copied, missing = [], []
    for rel, suffix in ITEMS:
        f = wd / rel.format(base=base)
        if not f.exists():
            missing.append(rel.format(base=base))
            continue
        dst = dst_dir / f"{base}{suffix}"
        shutil.copy2(f, dst)
        copied.append(dst.name)

    voice = OUTPUT / VOICE_FROM_OUTPUT.format(name=name, base=base)
    if not voice.exists():
        voice = wd / VOICE_FROM_WORK.format(base=base)
    if voice.exists():
        shutil.copy2(voice, dst_dir / f"{base}_越南语配音.mp3")
        copied.append(f"{base}_越南语配音.mp3")
    else:
        missing.append("越南语配音")

    if extra:
        for rel, suffix in EXTRA:
            f = wd / rel
            if f.exists():
                shutil.copy2(f, dst_dir / f"{base}{suffix}")
                copied.append(f"{base}{suffix}")

    print(f"[ok] {name} → {dst_dir}")
    print(f"     导出 {len(copied)} 个：{', '.join(copied)}")
    if missing:
        print(f"     缺失 {len(missing)} 个：{', '.join(missing)}")
    return not missing


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    extra = "--extra" in sys.argv
    names = args or sorted(p.name for p in WORK.iterdir() if (p / "02_asr").is_dir())
    ok = 0
    for n in names:
        if export_one(n, extra):
            ok += 1
    print(f"\n完成 {ok}/{len(names)} 个工单")


if __name__ == "__main__":
    main()
