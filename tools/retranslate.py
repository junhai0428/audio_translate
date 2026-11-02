"""按新语速口径重翻越南语（走 DeepSeek API，不动第 1/2 步与中文稿）。

基于 03_translate/zh_split.json 的切句，按 config.translate.vi_syllables_per_sec
重新算 max_syl，让模型在容量内完整表达，再合稿。

用法：
  python tools/retranslate.py douyin_188                # 指定工单
  python tools/retranslate.py douyin_188 douyin_199     # 多条
  python tools/retranslate.py --site 01.八珍足贴        # 整个产品目录
  python tools/retranslate.py --site 01.八珍足贴 --skip-done   # 已有新译文的跳过

断点：每完成一条工单就落盘（_translated.json + segments_vi.json），中断可重跑。
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.config import iter_job_dirs, job_dir, llm_key, load_config  # noqa: E402
from pipeline.translate import shorten_vi, translate_vi  # noqa: E402
from pipeline.utils import read_json, write_json  # noqa: E402
from tools.audit_translate import zh_syllables  # noqa: E402
from tools.step3_helper import apply_budget, cmd_build  # noqa: E402

TAG = "v2"   # 标记：本脚本产出的译文版本


def log(msg: str) -> None:
    print(msg, flush=True)


def clean_vi(text: str) -> str:
    """去掉句尾多余标点（保留 ? !），压缩多余空格。"""
    t = " ".join((text or "").split())
    return t.strip().rstrip(" ,;:、。…-").strip()


def build_segments(wd: Path):
    split = read_json(wd / "03_translate" / "zh_split.json")
    if not split or not split.get("segments"):
        raise RuntimeError("缺少 zh_split.json（先跑 step3_helper split）")
    duration = float(split.get("duration") or 0)
    segs = []
    for s in split["segments"]:
        zh = (s.get("zh") or "").strip()
        if not zh:
            continue
        segs.append({"id": s["id"], "start": s["start"], "end": s["end"],
                     "zh": zh, "text": zh, "vi": ""})
    apply_budget(segs, duration)
    for i, s in enumerate(segs):
        s["target_syl"] = min(s["max_syl"], max(2, round(zh_syllables(s["zh"]) * 1.2)))
        prev = segs[i - 1]["zh"] if i > 0 else ""
        nxt = segs[i + 1]["zh"] if i + 1 < len(segs) else ""
        s["ctx"] = f"{prev} / {nxt}".strip(" /")
    return segs


def retranslate_one(wd: Path, cfg: dict, key: str) -> None:
    tp = wd / "03_translate" / "_translated.json"
    bak = wd / "03_translate" / "_translated.bak.json"
    if tp.is_file() and not bak.is_file():
        shutil.copy2(tp, bak)

    segs = build_segments(wd)
    log(f"  {wd.name}: {len(segs)} 句，预算 {sum(s['max_syl'] for s in segs)} 音节")
    segs = translate_vi(cfg, key, segs, log)
    segs = shorten_vi(cfg, key, segs, log)

    rows = [{"id": s["id"], "zh": s["zh"], "vi": clean_vi(s.get("vi") or ""),
             "ver": TAG} for s in segs]
    write_json(tp, rows)
    cmd_build(wd)


def main() -> None:
    cfg_all = load_config()
    cfg = cfg_all["translate"]
    key = llm_key(cfg_all)
    if not key:
        raise SystemExit("缺少 translate.api_key")

    args = sys.argv[1:]
    site, skip_done, names, limit = None, False, [], 0
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--site":
            i += 1
            site = args[i]
        elif a == "--skip-done":
            skip_done = True
        elif a == "--limit":
            i += 1
            limit = int(args[i])
        else:
            names.append(a)
        i += 1

    if site:
        dirs = [d for d in iter_job_dirs(cfg_all) if d.parent.parent.name == site]
    else:
        dirs = [job_dir(cfg_all, n) for n in names]
    dirs = [d for d in dirs if d is not None and (d / "03_translate" / "zh_split.json").is_file()]
    if limit:
        dirs = dirs[:limit]
    if not dirs:
        raise SystemExit("没有可处理的工单")

    log(f"待重翻 {len(dirs)} 条（SPS={cfg.get('vi_syllables_per_sec')}，model={cfg.get('model')}）")
    t0 = time.time()
    done, fail = 0, []
    for n, wd in enumerate(dirs, 1):
        if skip_done:
            tp = wd / "03_translate" / "_translated.json"
            rows = read_json(tp) or []
            if rows and all(r.get("ver") == TAG for r in rows):
                log(f"[{n}/{len(dirs)}] {wd.name} 已是 {TAG}，跳过")
                continue
        log(f"[{n}/{len(dirs)}] {wd.name}")
        try:
            retranslate_one(wd, cfg, key)
            done += 1
        except Exception as exc:  # noqa: BLE001
            fail.append(wd.name)
            log(f"  [!] 失败：{type(exc).__name__}: {exc}")
    log(f"\n完成 {done} 条，失败 {len(fail)} 条 {fail}，耗时 {(time.time()-t0)/60:.1f} 分钟")


if __name__ == "__main__":
    main()
