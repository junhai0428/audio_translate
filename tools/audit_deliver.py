"""交付体检：核对每个产品的「源素材 ↔ 两个交付音轨」，并清理旧命名残留。

目录契约（唯一允许落在素材目录里的派生文件）：
  <素材目录>/<名>_越南语配音.mp3   第 4 步产出
  <素材目录>/<名>_背景声.mp3       第 1 步产出

用法：
  python tools/audit_deliver.py               # 只体检（扫 config.media_roots）
  python tools/audit_deliver.py --fix         # 顺手补 BGM + 删旧命名残留
  python tools/audit_deliver.py --site 07.七绿七子白洗脸皂
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.config import JOB_NAME, iter_job_dirs, load_config  # noqa: E402

VOICE_SUF = "_越南语配音.mp3"
BGM_SUF = "_背景声.mp3"
# 旧命名：能对上正确交付（或同一工单）就删
STALE_SUF = ("_vi.mp3", "_BGM.mp3", "_背景.mp3", "_bgm.mp3", "_vi_vi.mp3")


def site_dirs(cfg: dict, only: str | None) -> list[Path]:
    roots = [Path(r) for r in cfg.get("media_roots") or []]
    out = []
    for r in roots:
        if not r.is_dir():
            continue
        out += [p for p in sorted(r.iterdir()) if p.is_dir() and p.name != str(cfg["dirs"]["temp"])]
    if only:
        out = [p for p in out if p.name == only or only in p.name]
    return out


def sources(site: Path) -> list[Path]:
    return sorted(p for p in site.iterdir() if p.is_file() and JOB_NAME.match(p.stem))


def job_dir_of(cfg: dict, base: str) -> Path | None:
    for d in iter_job_dirs(cfg):
        if d.name == base:
            return d
    return None


def audit_site(cfg: dict, site: Path, fix: bool) -> tuple[int, int, int, int]:
    srcs = sources(site)
    no_voice, no_bgm, fixed_bgm, removed = [], [], 0, 0

    for src in srcs:
        base = src.stem
        voice = site / f"{base}{VOICE_SUF}"
        bgm = site / f"{base}{BGM_SUF}"
        if not voice.is_file():
            no_voice.append(base)
        if bgm.is_file():
            continue
        no_bgm.append(base)
        if not fix:
            continue
        wd = job_dir_of(cfg, base)
        s = (wd / "01_separate" / f"{base}_bgm.mp3") if wd else None
        if s and s.is_file() and s.stat().st_size > 1024:
            shutil.copy2(s, bgm)
            fixed_bgm += 1

    stale = [f for f in site.iterdir() if f.is_file() and f.name.endswith(STALE_SUF)]
    for f in stale:
        if not fix:
            break
        base = f.name
        for suf in STALE_SUF:
            if base.endswith(suf):
                base = base[: -len(suf)]
                break
        # 只有在同工单已有正确交付时才删，避免连唯一的成品一起删掉
        if (site / f"{base}{VOICE_SUF}").is_file() or (site / f"{base}{BGM_SUF}").is_file():
            f.unlink()
            removed += 1

    print(f"{site.name}: 源 {len(srcs)} / 缺配音 {len(no_voice)} / 缺BGM {len(no_bgm)}"
          f" / 错名残留 {len(stale)}")
    if no_voice:
        print("   缺配音:", no_voice[:12])
    if no_bgm:
        print("   缺BGM :", no_bgm[:12])
    if stale:
        print("   旧命名:", [f.name for f in stale][:12])
    if fix:
        print(f"   已补 BGM {fixed_bgm}，已删旧命名 {removed}")
    return len(srcs), len(no_voice), len(no_bgm), len(stale)


def main() -> int:
    ap = argparse.ArgumentParser(description="交付体检 / 补齐")
    ap.add_argument("--fix", action="store_true", help="补 BGM 交付并删除旧命名残留")
    ap.add_argument("--site", default=None, help="只查某个产品目录（支持子串）")
    args = ap.parse_args()

    cfg = load_config()
    sites = site_dirs(cfg, args.site)
    if not sites:
        print("没有找到素材目录，检查 config.media_roots")
        return 1

    total = [0, 0, 0, 0]
    for site in sites:
        for i, v in enumerate(audit_site(cfg, site, args.fix)):
            total[i] += v
    print(f"\n合计：源 {total[0]} / 缺配音 {total[1]} / 缺BGM {total[2]} / 错名残留 {total[3]}")
    return 0 if total[1] == 0 and total[3] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
