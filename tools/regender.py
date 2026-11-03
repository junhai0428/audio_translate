"""用声纹模型（ECAPA + SVM）重算工单性别，可选写回产物。

默认只打印对比、不改文件；加 --apply 才写回 asr.json 与 03_translate/segments_vi.json。

用法：
  python tools/regender.py                      # 全量，干跑，只看变化
  python tools/regender.py --site 01 --apply     # 只处理 01.* 素材目录并写回
  python tools/regender.py --name douyin_030 --apply
  python tools/regender.py --verbose             # 连未变化的也打印
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.asr import detect_gender  # noqa: E402
from pipeline.config import Job, iter_job_dirs, load_config  # noqa: E402
from pipeline.utils import read_json, write_json  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="", help="只处理素材目录名以此开头的工单，如 01")
    ap.add_argument("--name", default="", help="只处理指定工单名（可重复用逗号分隔）")
    ap.add_argument("--apply", action="store_true", help="写回 asr.json / segments_vi.json")
    ap.add_argument("--verbose", action="store_true", help="未变化的也打印")
    args = ap.parse_args()

    cfg = load_config()
    names = {n.strip() for n in args.name.split(",") if n.strip()}
    jobs = []
    for wd in iter_job_dirs(cfg):
        site = wd.parent.parent
        if args.site and not site.name.startswith(args.site):
            continue
        if names and wd.name not in names:
            continue
        if not (wd / "02_asr" / "asr.json").is_file():
            continue
        jobs.append((site, wd))

    if not jobs:
        print("没找到符合条件的工单")
        return 1

    changed, same, skipped = [], [], []
    for site, wd in jobs:
        media = site / f"{wd.name}.mp4"
        job = Job(media, cfg, name=wd.name)
        asr = read_json(job.asr_json, {}) or {}
        old = (asr.get("gender") or {})
        old_label = old.get("label") or "?"
        try:
            new = detect_gender(job.vocal_mp3, job.dir(2), cfg["asr"], log_fn=lambda *_: None)
        except Exception as e:
            skipped.append((site.name, wd.name, f"{type(e).__name__}: {e}"))
            continue
        new_label = new.get("label")
        tag = f"{site.name} / {wd.name}"
        if old_label == new_label:
            same.append((tag, old_label, new_label))
            if args.verbose:
                print(f"  [不变] {tag:34s} {old_label:7s} -> {new_label:7s} "
                      f"(margin {new.get('margin', 0):+.2f})")
            continue
        changed.append((tag, wd.name, str(job.media), old_label, new_label, old, new))
        print(f"  [变更] {tag:34s} {old_label:7s} -> {new_label:7s} "
              f"(旧 {old.get('median_f0')}Hz / 新 margin {new.get('margin', 0):+.2f})")
        if args.apply:
            asr["gender"] = new
            write_json(job.asr_json, asr)
            if job.vi_segments_json.is_file():
                vi = read_json(job.vi_segments_json, {}) or {}
                vi["gender"] = new
                write_json(job.vi_segments_json, vi)

    print()
    print(f"扫描 {len(jobs)} 条：变更 {len(changed)}，未变 {len(same)}，失败 {len(skipped)}")
    if skipped:
        for s in skipped:
            print(f"  [失败] {s[0]} / {s[1]}: {s[2]}")
    if changed:
        print()
        print("这些工单的配音音色要跟着换" + ("（已写回产物，下一步重跑第 4 步即可）" if args.apply else "（加 --apply 写回）") + "：")
        for tag, name, media, ol, nl, _o, _n in changed:
            print(f'  python run.py -i "{media}" --stages 4 --force')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
