#!/usr/bin/env python
"""统一调度器 —— 批量中→越语音本地化（纯音频链路）

输入是已提取好的音频（mp3/wav/m4a/flac…），链路只做：分离人声 → 转写 → 翻译控长 → 配音。
不做视频抽轨，也不做去字幕/成片（去字幕见独立项目 E:/Projects/字幕擦除工具）。

用法：
  python run.py                          # 处理 input/ 下全部音频，跑完 4 个阶段
  python run.py -i D:/素材/0715 --jobs 2 # 指定目录 + 2 路并行
  python run.py -i a.mp3,b.wav           # 指定文件
  python run.py --stages 1,2,3           # 只跑到文案，人工审核
  python run.py --stages 4               # 文案确认后只跑配音
  python run.py --force --stages 4       # 重做配音
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# 源素材命名：douyin_012 / bz_051 / 012（只允许"前缀_编号"或纯编号）
_SOURCE_STEM = re.compile(r"^([A-Za-z0-9\u4e00-\u9fff]+_)?\d+$")


# ---------------------------------------------------------------- 线程/进度环境
def bootstrap_env(cfg: dict) -> None:
    """限制 CPU 线程，避免 torch/numpy 在 Windows 上开满线程导致崩溃。"""
    n = str(int(cfg.get("threads", {}).get("omp", 4)))
    for key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        os.environ.setdefault(key, n)
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONWARNINGS", "ignore")
    os.environ.setdefault("HF_HOME", "E:/Projects/whisper_env/models/hf_cache")
    os.environ.setdefault("TORCH_HOME", "E:/Projects/whisper_env/models/hf_cache/torch")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    # 模型已本地缓存；不设离线时 demucs/whisper 每次启动都会联网校验（国内网络超时重试约 60~90s）
    if cfg.get("hf_offline", True):
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")


def reexec_with_pipeline_python() -> None:
    """确保运行在装了 demucs/whisper/edge-tts 的解释器里。"""
    if os.environ.get("VIDTRANS_REEXEC") == "1":
        return
    import json

    cfg_path = ROOT / "config.json"
    try:
        py = json.loads(cfg_path.read_text(encoding="utf-8")).get("python_exe")
    except Exception:
        return
    if not py or not Path(py).exists():
        return
    if Path(sys.executable).resolve() == Path(py).resolve():
        return
    env = os.environ.copy()
    env["VIDTRANS_REEXEC"] = "1"
    proc = subprocess.run([py, str(Path(__file__).resolve()), *sys.argv[1:]], env=env)
    sys.exit(proc.returncode)


STAGES = {
    1: ("人声分离", "separate"),
    2: ("ASR+性别", "asr"),
    3: ("中文校对/翻译", "translate"),
    4: ("越南语配音", "tts"),
}


def load_stage_module(num: int):
    import importlib

    return importlib.import_module(f"pipeline.{STAGES[num][1]}")


def parse_stages(text: str) -> list:
    nums = []
    for part in str(text).replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            nums += list(range(int(a), int(b) + 1))
        else:
            nums.append(int(part))
    nums = sorted({n for n in nums if n in STAGES})
    return nums or list(STAGES)


def collect_medias(inputs: list, exts: list, temp_name: str = "temp") -> list:
    """收集源素材。只认 `名字_编号.扩展名`（douyin_012.mp4 / bz_051.mp3 / 012.mp4）。

    素材目录里常年混着历史产物：_nosub/_clean/_cfr 转码版、_vi/_BGM/_背景 音轨、
    花字多/tmp/output/_vsr_batch 等子目录。这些音轨与源片相同，若当成独立素材
    会重复跑整条流水线并产出重复交付，所以默认只处理命名规范的源文件。
    """
    found = []   # [(文件, 所属根目录)]
    for item in inputs:
        p = Path(item)
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if not (f.is_file() and f.suffix.lower() in exts):
                    continue
                rel = f.relative_to(p)
                if any(part.startswith("_") for part in rel.parts[:-1]):
                    continue
                if any(par.name.lower() == temp_name.lower() for par in f.parents):
                    continue
                if not _SOURCE_STEM.match(f.stem):
                    continue
                found.append((f, p))
        elif p.is_file() and p.suffix.lower() in exts:
            found.append((p, p.parent))
    # 同一根目录下同名只保留路径最浅的（花字多/子目录里的同源副本让位给顶层）；
    # 不同产品目录的同名素材（各自的 douyin_052）必须都保留
    best = {}
    for f, root in found:
        key = (str(root).lower(), f.stem.lower())
        if key not in best or len(f.parts) < len(best[key].parts):
            best[key] = f
    return sorted(best.values(), key=lambda x: str(x).lower())


class Heartbeat(threading.Thread):
    """每 5 分钟打印一次各任务当前进度，避免长任务看起来卡死。"""

    def __init__(self, interval: int = 300):
        super().__init__(daemon=True)
        self.interval = interval
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.active = {}

    def update(self, name: str, stage: int, started: float) -> None:
        with self.lock:
            self.active[name] = (stage, started)

    def clear(self, name: str) -> None:
        with self.lock:
            self.active.pop(name, None)

    def run(self) -> None:
        while not self.stop_event.wait(self.interval):
            with self.lock:
                snapshot = dict(self.active)
            if not snapshot:
                continue
            lines = [f"[心跳] 运行中 {len(snapshot)} 个任务："]
            now = time.time()
            for name, (stage, started) in snapshot.items():
                lines.append(f"    {name} · 阶段{stage} {STAGES[stage][0]} 已 {int(now - started)//60} 分钟")
            print("\n".join(lines), flush=True)

    def stop(self) -> None:
        self.stop_event.set()


def process_one(media: Path, cfg: dict, stages: list, force: bool, heartbeat: Heartbeat,
                prefix: str = "") -> dict:
    from pipeline.config import Job
    from pipeline.utils import Logger, PipelineError, log

    job = Job(media, cfg, name=(prefix + media.stem) if prefix else None)
    logger = Logger(job.work / "run.log")
    log_fn = lambda msg, _l=logger: log(msg, _l)  # noqa: E731
    log_fn(f"\n=== {media.name} ===")

    result = {"media": media.name, "ok": True, "stages": {}, "error": "", "output": "", "seconds": 0.0}
    t_all = time.time()
    try:
        for num in stages:
            label = STAGES[num][0]
            t0 = time.time()
            if job.done(num) and not force:
                log_fn(f"  [{num}] {label} —— 已完成，跳过")
                result["stages"][num] = 0.0
                continue
            heartbeat.update(media.name, num, t0)
            log_fn(f"  [{num}] {label} 开始")
            module = load_stage_module(num)
            info = module.run_stage(job, logger=logger, log_fn=log_fn, force=force)
            cost = time.time() - t0
            result["stages"][num] = round(cost, 1)
            job.mark(num, True, seconds=round(cost, 1), detail=str(info.get("final") or info.get("voice") or ""))
            log_fn(f"  [{num}] {label} 完成 {cost:.1f}s")
        result["seconds"] = round(time.time() - t_all, 1)
        result["output"] = str(job.deliverable) if job.deliverable.exists() else ""
    except PipelineError as exc:
        result["ok"] = False
        result["error"] = str(exc)
        log_fn(f"  ✗ 失败：{exc}")
    except Exception as exc:  # noqa: BLE001
        result["ok"] = False
        result["error"] = f"{type(exc).__name__}: {exc}"
        log_fn(f"  ✗ 异常：{result['error']}")
        log_fn(traceback.format_exc().splitlines()[-1])
    finally:
        heartbeat.clear(media.name)
        logger.close()
    return result


def preflight(cfg: dict, stages: list) -> list:
    from pipeline.config import llm_key
    from pipeline.utils import FFMPEG

    warn = []
    if not Path(str(cfg.get("python_exe", ""))).exists():
        warn.append(f"python_exe 不存在：{cfg.get('python_exe')}")
    # ffmpeg
    try:
        subprocess.run([FFMPEG, "-version"], capture_output=True, timeout=20)
    except Exception:
        warn.append("ffmpeg 不可用，请把 ffmpeg 加入 PATH")
    if 3 in stages and cfg.get("translate", {}).get("enabled", True) and not llm_key(cfg):
        warn.append("第 3 步（翻译）缺少 LLM api_key（config.json → translate.api_key 或环境变量 VIDTRANS_LLM_KEY）")
    return warn


def main() -> int:
    from pipeline.config import load_config
    from pipeline.utils import log, reconfigure_stdout

    reconfigure_stdout()
    parser = argparse.ArgumentParser(description="批量中→越语音本地化调度器（纯音频链路）")
    parser.add_argument("-i", "--input", action="append", default=None,
                        help="素材/产品目录或文件，可多次指定（默认 config.dirs.input，未配置则必填）")
    parser.add_argument("-j", "--jobs", type=int, default=None, help="并行音频数（默认 config.run.jobs=1）")
    parser.add_argument("--stages", default=None, help="阶段，如 1,2,3 或 1-3，默认全部")
    parser.add_argument("--force", action="store_true", help="忽略断点，强制重跑")
    parser.add_argument("--limit", type=int, default=0, help="最多处理几个音频")
    parser.add_argument("--list", action="store_true", help="只列出待处理音频")
    parser.add_argument("-c", "--config", default=None, help="配置文件名")
    parser.add_argument("--prefix", default="", help="工单目录名前缀（默认不需要：temp 归属各自素材目录）")
    args = parser.parse_args()

    cfg = load_config(args.config)
    stages = parse_stages(args.stages) if args.stages else list(cfg.get("run", {}).get("stages", list(STAGES)))
    jobs = int(args.jobs or cfg.get("run", {}).get("jobs", 1))
    temp_name = str(cfg["dirs"]["temp"])
    raw_inputs = args.input or ([cfg["dirs"]["input"]] if cfg["dirs"].get("input") else [])
    # -i 支持逗号分隔多个素材，也支持多次 -i
    inputs = [x.strip() for item in raw_inputs for x in str(item).split(",") if x.strip()]
    medias = collect_medias(inputs, [e.lower() for e in cfg.get("audio_exts", [".mp3", ".wav"])], temp_name)

    if args.limit:
        medias = medias[: args.limit]
    if not medias:
        log("未找到素材。用 -i 指定素材/产品目录，例如："
            "-i \"E:/001.TTS/000视频素材/01.八珍足贴\"")
        return 1
    if args.list:
        for v in medias:
            log(f"  {v}")
        return 0

    bootstrap_env(cfg)
    log(f"待处理 {len(medias)} 个音频，阶段 {stages}，并行 {jobs}")
    for w in preflight(cfg, stages):
        log(f"⚠️  {w}")

    heartbeat = Heartbeat()
    heartbeat.start()

    t0 = time.time()
    results = []
    if jobs <= 1:
        for v in medias:
            results.append(process_one(v, cfg, stages, args.force, heartbeat, args.prefix))
    else:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = {pool.submit(process_one, v, cfg, stages, args.force, heartbeat, args.prefix): v
                       for v in medias}
            for fut in as_completed(futures):
                results.append(fut.result())
    heartbeat.stop()

    # ---------------- 汇总
    total = time.time() - t0
    log("\n" + "=" * 78)
    log(f"{'音频':<34}{'结果':<8}{'阶段耗时':<24}{'产出'}")
    log("-" * 78)
    for r in sorted(results, key=lambda x: x["media"]):
        detail = ",".join(f"{k}:{v}s" for k, v in sorted(r["stages"].items()) if v)
        log(f"{r['media'][:32]:<34}{'OK' if r['ok'] else 'FAIL':<8}{detail[:22]:<24}{Path(r['output']).name if r['output'] else '-'}")
        if not r["ok"]:
            log(f"    错误：{r['error'][:160]}")
    okn = sum(1 for r in results if r["ok"])
    log("-" * 78)
    log(f"完成 {okn}/{len(results)}，总耗时 {total/60:.1f} 分钟")
    return 0 if okn == len(results) else 2


if __name__ == "__main__":
    reexec_with_pipeline_python()
    sys.exit(main())
