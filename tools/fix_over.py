"""对仍超预算的句子做一轮强制压缩（只压超 2 音节以上的）。

用法：
  python tools/fix_over.py --dry        # 只看清单
  python tools/fix_over.py              # 实际压缩并合稿
  python tools/fix_over.py --site 02.BUV小绿泥洗面奶
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.config import iter_job_dirs, llm_key, load_config  # noqa: E402
from pipeline.translate import _chat, _parse_pairs  # noqa: E402
from pipeline.utils import count_vi_syllables, read_json, write_json  # noqa: E402
from tools.step3_helper import cmd_build  # noqa: E402

SQUEEZE_SYSTEM = """你是越南语配音时长压缩专家。给你的句子超出了配音可用音节数（max_syl），必须压到 max_syl 以内。

手法（按优先级）：
1. 删程度副词和修饰语（rất, thật sự, cực kỳ, vô cùng, quá, lắm）
2. 删可有可无的虚词和语气词（thì, mà, nhé, nha, đấy, ạ）
3. 重复主语/代词改用省略
4. 长定语换短词，汉越词换口语短词
5. 同义堆叠合并

铁律：
- 不得改变原意，不得新增内容，不得删掉卖点/成分/价格/CTA/数字
- 必须是通顺的越南语，不能为了短而破坏语法
- 输出音节数必须 ≤ max_syl
- 不加解释，只输出 JSON"""


def main() -> None:
    argv = sys.argv[1:]
    dry = "--dry" in argv
    site = argv[argv.index("--site") + 1] if "--site" in argv else None

    cfg_all = load_config()
    cfg = cfg_all["translate"]
    key = llm_key(cfg_all)

    targets = []
    for wd in iter_job_dirs(cfg_all):
        s = wd.parent.parent.name
        if site and s != site:
            continue
        sp = wd / "03_translate" / "segments_vi.json"
        if not sp.is_file():
            continue
        data = json.loads(sp.read_text(encoding="utf-8"))
        over = []
        for seg in data.get("segments") or []:
            vi = (seg.get("vi") or "").strip()
            cap = int(seg.get("max_syl") or 0)
            if vi and cap and count_vi_syllables(vi) > cap + 1:
                over.append(seg)
        if over:
            targets.append((wd, over))

    total = sum(len(o) for _, o in targets)
    print(f"超预算（>cap+1）{total} 句，分布在 {len(targets)} 个工单")
    for wd, over in targets:
        print(f"  {wd.name}: {len(over)} 句")
    if dry or not total:
        return

    for wd, over in targets:
        payload = [{"id": s["id"], "vi": s["vi"], "max_syl": s["max_syl"],
                    "cur_syl": count_vi_syllables(s["vi"]), "zh": s.get("zh", "")}
                   for s in over]
        user = ("把下面每句压到 max_syl 以内（cur_syl 是当前音节数）：\n"
                + json.dumps({"segments": payload}, ensure_ascii=False, indent=1))
        mapping = _parse_pairs(_chat(cfg, key, SQUEEZE_SYSTEM, user))
        tp = wd / "03_translate" / "_translated.json"
        rows = read_json(tp) or []
        by_id = {int(r["id"]): r for r in rows}
        fixed = 0
        for s in over:
            got = mapping.get(int(s["id"]))
            if not got:
                continue
            if count_vi_syllables(got) < count_vi_syllables(s["vi"]):
                by_id[int(s["id"])]["vi"] = got
                fixed += 1
        write_json(tp, [by_id[i] for i in sorted(by_id)])
        print(f"  {wd.name}: 压缩 {fixed}/{len(over)} 句")
        cmd_build(wd)


if __name__ == "__main__":
    main()
