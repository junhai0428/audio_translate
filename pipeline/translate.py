"""第 3 步 · 文案翻译与控长
① 中文识别结果纠错 / 断句 / 标点  → zh_opt.txt
② 中文 → 越南语（严格控长，防止配音后超时长）→ vi.txt
③ 同时写出 segments_vi.json 供第 4 步逐句对齐

LLM 走 OpenAI 兼容 /chat/completions 接口（DeepSeek / 通义 / OpenAI / 本地 vLLM 均可）。
key 优先取 config.json 的 translate.api_key，其次取环境变量。
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from .config import Job, llm_key
from .utils import compute_slots, count_vi_syllables, hhmmss, log, read_json, write_json

STAGE = 3

ZH_SYSTEM = """你是资深中文短视频口播文案校对员。输入是一组带时间轴的语音识别结果，请逐条校正后返回。

铁律：
1. 只做校正：改错别字、改同音误识、补标点、优化断句、删掉无意义的重复语气词（如"那个那个""就是就是"）。
2. 严禁改写语义，严禁扩写或压缩内容，严禁合并/拆分条目，严禁增删条目。
3. 输出的条目数量、id 必须与输入完全一致，顺序一致。
4. 保留口语感，不要书面化，不要加书名号/引号/emoji。
5. 单条不要超过 40 个汉字，过长请在自然停顿处断开——但不得改变条目数量。
6. 如果某条内容确实是噪声误识（如孤立的"嗯""啊"），保留为空字符串 ""，不要编造。

只输出纯 JSON：{"segments":[{"id":1,"text":"..."}]}，不要 markdown 代码块，不要解释。"""

VI_SYSTEM = """你是中文→越南语的短视频口播本地化专家，受众是越南 TikTok / 电商直播间观众。

铁律：
1. 地道口语化越南语，符合越南年轻人短视频语感；不要书面语，不要堆砌汉越词。
2. 严格控长：输入给出 max_syl（该句配音允许的最大音节数，越南语一个空格分隔的词≈一个音节）。越南语音节多，必须用最紧凑的表达，绝不能超过 max_syl。宁可砍修饰语，也不要超。
3. 数字用阿拉伯数字；保留品牌名、型号、单位、货币符号。
4. 一一对应输入的 id，不合并、不拆分、不增删、不解释、不加注释。
5. 不加引号、不加字幕符号、不堆叠标点；单条最多两个逗号。
6. 合规改写：祛湿/湿气/健脾/养胃/消肿/排毒等功效词，改成不含医疗承诺的表达（如 "hỗ trợ tiêu hóa"、"thanh lọc cơ thể"、"giảm cảm giác nặng nề"），不得出现治疗、根治、药效含义。
7. 语气自然，像真人在镜头前说话，不要机翻腔。

只输出纯 JSON：{"segments":[{"id":1,"text":"..."}]}，不要 markdown 代码块，不要解释。"""


# ------------------------------------------------------------------ HTTP
def _chat(cfg: dict, key: str, system: str, user: str, log_fn=log) -> str:
    base = str(cfg.get("base_url", "")).rstrip("/")
    if not base:
        raise RuntimeError("未配置 translate.base_url")
    url = base + "/chat/completions"
    payload = {
        "model": cfg.get("model", "deepseek-chat"),
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": float(cfg.get("temperature", 0.3)),
        "stream": False,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
    retries = int(cfg.get("max_retries", 3))
    timeout = float(cfg.get("timeout", 180))

    last_err = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                obj = json.loads(resp.read().decode("utf-8"))
            return obj["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            last_err = f"HTTP {exc.code} {detail}"
            if exc.code in (400, 401, 403, 404):
                break
        except Exception as exc:  # 网络抖动 / 超时
            last_err = f"{type(exc).__name__}: {exc}"
        if attempt < retries:
            time.sleep(min(20, 2 ** attempt))
    raise RuntimeError(f"LLM 调用失败：{last_err}")


def _parse_pairs(content: str) -> dict:
    """从模型输出里抠出 {id: text}，容忍代码块和多余文字。"""
    if not content:
        return {}
    text = content.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        return {}
    try:
        obj = json.loads(m.group(0))
    except Exception:
        # 常见退化：单引号 / 尾逗号
        try:
            obj = json.loads(m.group(0).replace("'", '"').replace(",}", "}").replace(",]", "]"))
        except Exception:
            return {}
    segs = obj.get("segments") if isinstance(obj, dict) else obj
    out = {}
    if isinstance(segs, list):
        for i, item in enumerate(segs, 1):
            if isinstance(item, dict):
                sid = item.get("id", i)
                txt = item.get("text", item.get("vi", item.get("zh", "")))
                try:
                    out[int(sid)] = str(txt).strip()
                except Exception:
                    out[i] = str(txt).strip()
    return out


# ------------------------------------------------------------------ 分块
def _chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _apply(results: dict, target: list, field: str) -> list:
    """把模型返回按 id 回填，缺的保留原文。"""
    for seg in target:
        got = results.get(int(seg["id"]))
        if got:
            seg[field] = got
    return target


# ------------------------------------------------------------------ 阶段
def fix_zh(cfg, key, segments, log_fn) -> list:
    size = int(cfg.get("batch_size", 25))
    out = []
    for idx, chunk in enumerate(_chunks(segments, size), 1):
        payload = [{"id": s["id"], "text": s["text"]} for s in chunk]
        user = "请校正以下语音识别条目：\n" + json.dumps({"segments": payload}, ensure_ascii=False)
        content = _chat(cfg, key, ZH_SYSTEM, user, log_fn)
        mapping = _parse_pairs(content)
        fixed = _apply(mapping, [dict(s) for s in chunk], "text")
        out.extend(fixed)
        log_fn(f"  [3/4 翻译] 中文校对 {idx} 批（{len(chunk)} 条）返回 {len(mapping)} 条")
    # 兜底：空文本回落到原文
    by_id = {s["id"]: s for s in segments}
    for s in out:
        if not s.get("text", "").strip():
            s["text"] = by_id[s["id"]]["text"]
    return out


def translate_vi(cfg, key, segments, log_fn) -> list:
    size = int(cfg.get("batch_size", 25))
    out = []
    for idx, chunk in enumerate(_chunks(segments, size), 1):
        payload = [
            {"id": s["id"], "zh": s["text"], "slot_sec": round(s["slot"], 2), "max_syl": s["max_syl"]}
            for s in chunk
        ]
        user = (
            "把下面每句中文翻译成越南语，严格遵守 max_syl（音节数上限）：\n"
            + json.dumps({"segments": payload}, ensure_ascii=False, indent=1)
        )
        content = _chat(cfg, key, VI_SYSTEM, user, log_fn)
        mapping = _parse_pairs(content)
        out.extend(_apply(mapping, [dict(s) for s in chunk], "vi"))
        log_fn(f"  [3/4 翻译] 越语翻译 {idx} 批（{len(chunk)} 条）返回 {len(mapping)} 条")
    return out


def shorten_vi(cfg, key, segments, log_fn) -> list:
    rounds = int(cfg.get("shorten_rounds", 2))
    tolerance = float(cfg.get("vi_max_ratio", 1.0))
    for r in range(1, rounds + 1):
        over = [
            s for s in segments
            if s.get("vi") and count_vi_syllables(s["vi"]) > max(2, s["max_syl"] * tolerance)
        ]
        if not over:
            break
        log_fn(f"  [3/4 翻译] 第 {r} 轮压缩：{len(over)} 条超长")
        payload = [
            {"id": s["id"], "vi": s["vi"], "max_syl": s["max_syl"],
             "cur_syl": count_vi_syllables(s["vi"]), "zh": s.get("zh", "")}
            for s in over
        ]
        user = (
            "下列越南语句子音节数超限，配音会变慢。请保留核心卖点，压到 max_syl 以内：\n"
            "手法：删重复主语/代词、删虚词（thật sự, đúng là, thực ra, nhé, nha, thì, mà 等）、"
            "合并同义堆叠、长定语换短词、汉越词换口语短词。不要丢卖点/成分/价格/CTA，不要写成中文：\n"
            + json.dumps({"segments": payload}, ensure_ascii=False, indent=1)
        )
        mapping = _parse_pairs(_chat(cfg, key, VI_SYSTEM, user, log_fn))
        for s in over:
            got = mapping.get(int(s["id"]))
            if got and count_vi_syllables(got) < count_vi_syllables(s["vi"]):
                s["vi"] = got
    return segments


# ------------------------------------------------------------------ 主流程
def run_stage(job: Job, logger=None, log_fn=log, force: bool = False) -> dict:
    cfg = job.cfg["translate"]
    if not force and job.vi_segments_json.exists() and job.vi_txt.exists() and job.zh_txt.exists():
        data = read_json(job.vi_segments_json)
        if data and data.get("segments"):
            log_fn(f"  [3/4 翻译] 已存在 {len(data['segments'])} 条译文，跳过")
            return data

    asr = read_json(job.asr_json)
    if not asr or not asr.get("segments"):
        raise RuntimeError("缺少 Agent2 的 asr.json")

    total_dur = float(asr.get("duration") or 0) or (float(asr["segments"][-1]["end"]) + 0.5)
    slots = compute_slots(
        asr["segments"], total_dur,
        gap_use=float(cfg.get("gap_use", 0.85)), gap=float(cfg.get("gap", 0.04)),
    )
    sps = float(cfg.get("vi_syllables_per_sec", 3.2))
    head_line = float(cfg.get("head_line", 0.2))

    if not bool(cfg.get("enabled", True)):
        log_fn("  [3/4 翻译] LLM 已禁用，仅落原文")
        segs = []
        for s, (start, slot) in zip(asr["segments"], slots):
            segs.append({**s, "zh": s["text"], "vi": "", "slot": round(slot, 3),
                         "max_syl": max(2, int(round((slot - head_line) * sps)))})
        data = {"gender": asr.get("gender"), "segments": segs, "llm": "disabled"}
        write_json(job.vi_segments_json, data)
        write_text_safe(job.zh_txt, render_txt(segs, "zh"))
        write_text_safe(job.vi_txt, "")
        return data

    key = llm_key(job.cfg)
    if not key:
        raise RuntimeError(
            "未配置 LLM api_key。请在 config.json 的 translate.api_key 填入（DeepSeek/OpenAI/通义兼容均可），"
            "或设置环境变量 VIDTRANS_LLM_KEY"
        )

    segments = []
    for s, (start, slot) in zip(asr["segments"], slots):
        segments.append({
            **s,
            "zh": s["text"],
            "vi": "",
            "slot": round(slot, 3),
            "max_syl": max(2, int(round((slot - head_line) * sps))),
        })

    log_fn(f"  [3/4 翻译] 开始中文校对，共 {len(segments)} 条（{cfg.get('model')}）")
    segments = fix_zh(cfg, key, segments, log_fn)
    write_text_safe(job.zh_txt, render_txt(segments, "zh"))

    log_fn("  [3/4 翻译] 开始翻译成越南语")
    segments = translate_vi(cfg, key, segments, log_fn)
    segments = shorten_vi(cfg, key, segments, log_fn)

    missing = [s["id"] for s in segments if not s.get("vi", "").strip()]
    if missing:
        log_fn(f"  [3/4 翻译] 警告：{len(missing)} 条译文缺失（id={missing[:10]}），下游将跳过这些句子")

    write_text_safe(job.vi_txt, render_txt(segments, "vi"))
    overlong = sum(1 for s in segments if s.get("vi") and count_vi_syllables(s["vi"]) > s["max_syl"])
    data = {
        "media": str(job.media),
        "duration": round(total_dur, 3),
        "gender": asr.get("gender"),
        "model": cfg.get("model"),
        "segment_count": len(segments),
        "overlong": overlong,
        "segments": segments,
    }
    write_json(job.vi_segments_json, data)

    vi_syl = sum(count_vi_syllables(s.get("vi", "")) for s in segments)
    budget = sum(s["max_syl"] for s in segments) or 1
    log_fn(f"  [3/4 翻译] 完成：越南语 {vi_syl} 音节 / 预算 {budget}（占用 {vi_syl/budget:.2f}），超长 {overlong} 条")
    return data


def render_txt(segments, field: str) -> str:
    lines = []
    for s in segments:
        content = s.get(field, "") or ""
        if not content:
            continue
        lines.append(f"[{hhmmss(s['start'])} - {hhmmss(s['end'])}] {content}")
    return "\n".join(lines) + "\n"


def write_text_safe(path: Path, text: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text, encoding="utf-8")
