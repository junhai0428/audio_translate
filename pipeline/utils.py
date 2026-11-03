"""通用工具：子进程执行、ffmpeg/ffprobe 封装、JSON 读写、日志。"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

FFMPEG = os.environ.get("VIDTRANS_FFMPEG") or shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = os.environ.get("VIDTRANS_FFPROBE") or shutil.which("ffprobe") or "ffprobe"

# 隐藏子进程黑窗（Windows）
_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class PipelineError(RuntimeError):
    pass


def ts() -> str:
    return time.strftime("%H:%M:%S")


def log(msg: str, logger=None) -> None:
    line = f"[{ts()}] {msg}"
    print(line, flush=True)
    if logger is not None:
        logger(line)


def ensure_dir(path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def run(cmd, cwd=None, timeout=None, env=None, check=True, capture=True, quiet=False) -> subprocess.CompletedProcess:
    """执行外部命令，失败时抛出带 stderr 尾巴的异常。"""
    cmd = [str(c) for c in cmd]
    if not quiet:
        log("  $ " + " ".join(cmd))
    full_env = os.environ.copy()
    if env:
        full_env.update({k: str(v) for k, v in env.items()})
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            timeout=timeout,
            env=full_env,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_CREATE_NO_WINDOW if os.name == "nt" and capture else 0,
        )
    except FileNotFoundError as exc:
        raise PipelineError(f"找不到可执行文件: {cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise PipelineError(f"命令超时({timeout}s): {' '.join(cmd[:4])} ...") from exc

    if check and proc.returncode != 0:
        tail = ((proc.stderr or "") + (proc.stdout or "")).strip().splitlines()[-8:]
        raise PipelineError(
            "命令失败 rc=%s: %s\n%s" % (proc.returncode, " ".join(cmd[:6]), "\n".join(tail))
        )
    return proc


def ffprobe_json(path) -> dict:
    proc = run(
        [FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        quiet=True,
    )
    return json.loads(proc.stdout or "{}")


def media_duration(path) -> float:
    try:
        return float(ffprobe_json(path)["format"]["duration"])
    except Exception:
        return 0.0


def has_video_stream(path) -> bool:
    try:
        return any(s.get("codec_type") == "video" for s in ffprobe_json(path).get("streams", []))
    except Exception:
        return False


def read_json(path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path, obj) -> Path:
    p = Path(path)
    ensure_dir(p.parent)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)
    return p


def write_text(path, text: str) -> Path:
    p = Path(path)
    ensure_dir(p.parent)
    p.write_text(text, encoding="utf-8")
    return p


def to_wav(src, dst, sr=16000, mono=True, extra=None) -> Path:
    dst = Path(dst)
    ensure_dir(dst.parent)
    af = ["-ac", "1"] if mono else ["-ac", "2"]
    cmd = [FFMPEG, "-y", "-v", "error", "-i", str(src), "-vn", "-ar", str(sr), *af, "-c:a", "pcm_s16le"]
    if extra:
        cmd += list(extra)
    cmd.append(str(dst))
    run(cmd, quiet=True)
    return dst


def hhmmss(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    if h:
        return f"{h:02d}:{m:02d}:{s:05.2f}"
    return f"{m:02d}:{s:05.2f}"


_VI_SPLIT = r"[\s\.,;:!?…\-—/\"'()\[\]]+"


def count_vi_syllables(text: str) -> int:
    """越南语按空格切音节（一个词≈一个音节），数字串按 3 位 1 音节粗算。"""
    import re

    t = re.sub(r"(\d)", r" \1", text or "")
    n = 0
    for w in re.split(_VI_SPLIT, t):
        if not w:
            continue
        if w.isdigit():
            n += max(1, (len(w) + 2) // 3)
        else:
            n += 1
    return n


def compute_slots(segments, total_dur: float, gap_use: float = 0.85,
                  gap: float = 0.04, min_slot: float = 0.2) -> list:
    """按「下一句开始前可用时间」计算每句配音时隙。

    口径与历史项目 _vi_check.py 一致：
      slot_end = min(next_start - gap, end + (next_start - end) * gap_use)
    返回 [(start, slot), ...]
    """
    out = []
    for i, s in enumerate(segments):
        start = float(s["start"])
        end = float(s["end"])
        nxt = float(segments[i + 1]["start"]) if i + 1 < len(segments) else max(total_dur, end)
        slot_end = min(nxt - gap, end + max(0.0, nxt - end) * gap_use, max(total_dur, end) - 0.02)
        out.append((start, max(min_slot, slot_end - start)))
    return out


PUNCT_BREAK = "，,。；;：:！!？?、…"


def _split_one(seg: dict, max_dur: float, max_chars: int, min_dur: float) -> list:
    """一句 ASR 结果 → 若干可对齐小句（优先用词级时间戳，无则按标点比例切）。"""
    start, end = float(seg["start"]), float(seg["end"])
    text = (seg.get("text") or "").strip()
    if end - start <= max_dur and len(text) <= max_chars:
        return [{"start": start, "end": end, "text": text}]

    words = seg.get("words") or []
    # 词级时间戳覆盖率校验：words 明显没盖住整段时（末尾差 1 秒以上）不可信，
    # 否则按词切出来的块只到 words 末尾，后半句文本随之丢掉。
    if words and (float(words[-1]["e"]) < end - 1.0 or float(words[0]["s"]) > start + 1.0):
        words = []
    if not words:
        parts = [p for p in re.split(r"(?<=[%s])" % re.escape(PUNCT_BREAK), text) if p.strip()]
        dur = end - start
        total = sum(len(p) for p in parts) or 1
        out, t = [], start
        for p in parts:
            d = dur * len(p) / total
            out.append({"start": round(t, 3), "end": round(min(t + d, end), 3),
                        "text": p.strip().strip("，,、。 ")})
            t += d
        return out or [{"start": start, "end": end, "text": text}]

    chunks, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        c_dur = float(w["e"]) - float(cur[0]["s"])
        c_txt = "".join(x["w"] for x in cur)
        gap_next = float(words[i + 1]["s"]) - float(w["e"]) if i + 1 < len(words) else None
        if len(c_txt) >= max_chars or c_dur >= max_dur + 1.0:
            chunks.append(cur)
            cur = []
        elif (w.get("w") or "")[-1:] in PUNCT_BREAK and (len(c_txt) >= 16 or c_dur >= 3.0):
            chunks.append(cur)
            cur = []
        elif gap_next is not None and gap_next >= 0.35 and c_dur >= 2.0:
            chunks.append(cur)
            cur = []

    if cur:
        chunks.append(cur)
    out = []
    for c in chunks:
        s, e = float(c[0]["s"]), float(c[-1]["e"])
        txt = "".join(x["w"] for x in c).strip().strip("，,、。 ")
        if not txt:
            continue
        if e - s < min_dur:
            e = min(s + min_dur, end)
        out.append({"start": round(s, 3), "end": round(e, 3), "text": txt})
    return out or [{"start": start, "end": end, "text": text}]


def split_long_segments(segments, max_dur: float = 5.0, max_chars: int = 30,
                        min_dur: float = 1.0) -> list:
    """把 ASR 的超长段切成 ≤max_dur / ≤max_chars 的小句，id 从 1 重排。

    第 3 步翻译前必须切：中文口播一句话 20 多秒不切，就成了一整条 130+ 音节的
    巨句，配音无法逐句对齐，字幕也散架。
    """
    out = []
    for seg in segments:
        for piece in _split_one(seg, max_dur, max_chars, min_dur):
            out.append({"id": len(out) + 1, **piece})
    return out


def safe_stem(name: str) -> str:
    keep = []
    for ch in name:
        if ch.isalnum() or ch in "-_ .()（）[]【】":
            keep.append(ch)
        else:
            keep.append("_")
    out = "".join(keep).strip(" ._")
    return out or "video"


def human_size(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num_bytes < 1024 or unit == "TB":
            return f"{num_bytes:.1f}{unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f}TB"


class Logger:
    """控制台 + 文件双写日志。"""

    def __init__(self, path: Path):
        ensure_dir(Path(path).parent)
        self.path = Path(path)
        self.fh = open(self.path, "a", encoding="utf-8")

    def __call__(self, line: str) -> None:
        self.fh.write(line + "\n")
        self.fh.flush()

    def close(self) -> None:
        try:
            self.fh.close()
        except Exception:
            pass


def reconfigure_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
