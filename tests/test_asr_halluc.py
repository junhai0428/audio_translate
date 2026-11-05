# -*- coding: utf-8 -*-
"""ASR 幻觉判定回归测试（不依赖音频，纯逻辑）。

跑法：E:/Projects/whisper_env/Scripts/python.exe tests/test_asr_halluc.py

覆盖的真实案例：
- 088 首句 0.00-2.46s、nsp=0.99，但紧接下一句 → **不是**幻觉（曾被误杀）
- 026 首句 0.00-1.36s、nsp=0.53，后面很远才有下一句 → 是幻觉
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.asr import _is_halluc, halluc_segments  # noqa: E402

CFG = {"halluc_nsp": 0.6, "halluc_min_density": 2.0, "halluc_long_sec": 10.0}


def seg(start, end, text, nsp=0.0, sid=1):
    return {"id": sid, "start": start, "end": end, "text": text, "no_speech_prob": nsp}


CASES = [
    # (说明, 是否幻觉, 参数)
    ("088 片头真口播（紧接下一句）", False,
     seg(0.00, 2.46, "之前花大几时买的身体素颜霜可以直接退了。", nsp=0.99), 2.48),
    ("026 片头孤立幻觉（隔 18s 才有下一句）", True,
     seg(0.00, 1.36, "你能再说一遍吗?", nsp=0.53), 20.0),
    ("片头短句但是最后一段（无法判孤立，放行）", False,
     seg(0.00, 1.80, "这个真的很好用", nsp=0.90), None),
    ("片头短句紧接下一句，但 nsp 低", False,
     seg(0.50, 2.00, "姐妹们看过来", nsp=0.10), 2.10),
    ("字幕组套话（任意位置都算幻觉）", True,
     seg(30.0, 33.0, "请不吝点赞 订阅 转发 打赏支持明镜与点点栏目", nsp=0.1), 40.0),
    ("长段但字太稀（27.2s 只 12 字）", True,
     seg(61.9, 89.1, "这个产品真的非常好用啊", nsp=0.95), 92.0),
    ("正常口播段", False,
     seg(10.0, 14.0, "它是这种牛奶的质地，轻轻一抹就可以抹开", nsp=0.05), 14.2),
]


def main():
    fails = 0
    for name, expect, item, nxt in CASES:
        got = _is_halluc(item, CFG, next_start=nxt)
        ok = got == expect
        fails += 0 if ok else 1
        print("[%s] %-36s 期望=%-5s 实得=%s" % ("ok" if ok else "FAIL", name, expect, got))

    # 列表版：上下文按排序后的相邻段取，且不改动入参顺序
    segs = [
        seg(10.0, 13.0, "它是牛奶质地", nsp=0.05, sid=2),
        seg(0.00, 2.46, "之前花大几十买的身体素颜霜可以直接退了。", nsp=0.99, sid=1),
        seg(0.00, 1.36, "你能再说一遍吗?", nsp=0.53, sid=3),
    ]
    bad = halluc_segments(segs, CFG)
    got = [s["id"] for s in bad]
    ok = got == [3]
    fails += 0 if ok else 1
    print("[%s] %-36s 期望=[3] 实得=%s" % ("ok" if ok else "FAIL", "halluc_segments 只挑出片头孤立幻觉", got))

    # 088 现状（已救回的首段）应当零命中，否则第 3 步会被入口守卫拦住
    live = [seg(0.00, 2.46, "之前花大几十买的身体素颜霜可以直接退了。", nsp=0.01),
            seg(2.48, 7.64, "木子岩的这个身体素颜霜被我谈破价了", nsp=0.99)]
    got = halluc_segments(live, CFG)
    ok = got == []
    fails += 0 if ok else 1
    print("[%s] %-36s 期望=[] 实得=%s" % ("ok" if ok else "FAIL", "088 修复后的 asr.json 不再被拦", got))

    print("\n%d 失败 / %d 项" % (fails, len(CASES) + 2))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
