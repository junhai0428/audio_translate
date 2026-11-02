"""翻译质量核验：结构 / 长度 / 语言 / 合规 / 数字一致性。

用法：
  python tools/qc_translate.py                    # 扫 config.media_roots 下全部工单
  python tools/qc_translate.py --site 01.八珍足贴
输出：reports/_qc.md（明细） + 控制台汇总
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.config import iter_job_dirs, load_config  # noqa: E402

HAN = re.compile(r"[\u4e00-\u9fff]")
NUM = re.compile(r"\d+(?:\.\d+)?")
BRAND = re.compile(r"[A-Za-z]{2,}")
MOJIBAKE = re.compile(r"(Ã.|â€|ï¿½|\ufffd)")
# 只统计源素材工单（douyin_012 / bz_051 / 012），跳过 _nosub/_cfr 等历史派生物
SRC = re.compile(r"^([A-Za-z0-9\u4e00-\u9fff]+_)?\d+$")
# 越南广告合规红线（绝对化/医疗承诺/减肥）
FORBIDDEN = ["chữa bệnh", "trị bệnh", "đặc trị", "thần dược", "giảm cân", "giảm béo",
             "tốt nhất", "số 1", "duy nhất", "trị khỏi", "khỏi bệnh"]


def check_job(wd: Path, site: str) -> tuple[list, dict]:
    sp = wd / "03_translate" / "segments_vi.json"
    if not sp.is_file():
        return [], {}
    data = json.loads(sp.read_text(encoding="utf-8"))
    segs = data.get("segments") or []
    issues = []
    prev_vi = None
    tot_syl = tot_cap = 0
    for s in segs:
        sid, zh, vi = s.get("id"), (s.get("zh") or ""), (s.get("vi") or "").strip()
        cap = int(s.get("max_syl") or 0)
        n = len(vi.split())
        tot_syl += n
        tot_cap += cap
        if not vi:
            issues.append((site, wd.name, sid, "空译文", zh, ""))
            continue
        if HAN.search(vi):
            issues.append((site, wd.name, sid, "含中文字符", zh, vi))
        if MOJIBAKE.search(vi):
            issues.append((site, wd.name, sid, "乱码", zh, vi))
        if n > cap + 1:
            issues.append((site, wd.name, sid, f"超预算 {n}>{cap}", zh, vi))
        low = vi.lower()
        for w in FORBIDDEN:
            if w in low:
                issues.append((site, wd.name, sid, f"合规禁词 {w}", zh, vi))
        vi_digits = re.sub(r"\D", "", vi)
        zh_nums = {re.sub(r"\D", "", x) for x in NUM.findall(zh)}
        zh_nums = {x for x in zh_nums if len(x) >= 2 or (x and int(x) >= 2)}
        miss = [x for x in zh_nums if x not in vi_digits]
        if miss:
            issues.append((site, wd.name, sid, f"数字缺失 {miss}", zh, vi))
        brands = {b for b in BRAND.findall(zh) if b.lower() not in ("ok",)}
        missb = [b for b in brands if b.lower() not in vi.lower()]
        if missb:
            issues.append((site, wd.name, sid, f"品牌/英文缺失 {missb}", zh, vi))
        if zh and n < max(2, len(HAN.findall(zh)) * 0.5):
            issues.append((site, wd.name, sid, f"疑似漏译 {n}音节/{len(HAN.findall(zh))}字", zh, vi))
        if prev_vi and vi == prev_vi and n > 2:
            issues.append((site, wd.name, sid, "与上句完全重复", zh, vi))
        prev_vi = vi
    stat = {"job": wd.name, "site": site, "segs": len(segs), "syl": tot_syl, "cap": tot_cap,
            "use": round(tot_syl / max(1, tot_cap), 3)}
    return issues, stat


def main() -> None:
    cfg = load_config()
    site_filter = None
    if "--site" in sys.argv:
        site_filter = sys.argv[sys.argv.index("--site") + 1]

    all_issues, stats = [], []
    for wd in iter_job_dirs(cfg):
        site = wd.parent.parent.name
        if site_filter and site != site_filter:
            continue
        if not SRC.match(wd.name):
            continue
        iss, st = check_job(wd, site)
        if st:
            stats.append(st)
            all_issues += iss

    lines = ["# 翻译质量核验", "",
             f"- 工单 {len(stats)} 条，句子 {sum(s['segs'] for s in stats)} 句",
             f"- 音节 {sum(s['syl'] for s in stats)} / 预算 {sum(s['cap'] for s in stats)}"
             f"（占用 {sum(s['syl'] for s in stats)/max(1,sum(s['cap'] for s in stats)):.2f}）",
             f"- 问题 {len(all_issues)} 条", ""]
    by_kind = {}
    for it in all_issues:
        kind = it[3].split(" ")[0].split(">")[0]
        by_kind.setdefault(kind, []).append(it)
    lines.append("## 问题分类")
    lines.append("")
    lines.append("| 类型 | 数量 |")
    lines.append("|---|---|")
    for k, v in sorted(by_kind.items(), key=lambda x: -len(x[1])):
        lines.append(f"| {k} | {len(v)} |")
    lines.append("")
    lines.append("## 明细")
    lines.append("")
    for it in all_issues[:400]:
        lines.append(f"- `{it[0]}/{it[1]}` #{it[2]} **{it[3]}**")
        lines.append(f"  - zh: {it[4]}")
        lines.append(f"  - vi: {it[5]}")
    out = ROOT / "reports" / "_qc.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"工单 {len(stats)}，句子 {sum(s['segs'] for s in stats)}，问题 {len(all_issues)}")
    for k, v in sorted(by_kind.items(), key=lambda x: -len(x[1])):
        print(f"  {k:16}{len(v)}")
    print(f"\n明细 → {out}")


if __name__ == "__main__":
    main()
