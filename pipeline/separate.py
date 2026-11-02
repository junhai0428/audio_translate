"""第 1 步 · 人声分离
输入是已提取好的音频文件（mp3/wav/m4a/flac…），用 demucs 去掉 BGM 保留口播人声。
不做视频抽轨：音频由上游工具产出。
产出：
  <素材目录>/temp/<工单>/01_separate/<音频名>.mp3       口播人声（中间产物）
  <素材目录>/temp/<工单>/01_separate/<音频名>_bgm.mp3   伴奏（中间产物）
  <素材目录>/<音频名>_背景声.mp3                        交付物：纯 BGM
"""

from __future__ import annotations

import shutil
import sys

from .config import Job
from .utils import FFMPEG, ensure_dir, log, media_duration, run

STAGE = 1


def _find(path_dir, filename):
    for p in path_dir.rglob(filename):
        return p
    return None


def run_stage(job: Job, logger=None, log_fn=log, force: bool = False) -> dict:
    cfg = job.cfg["separate"]
    vocal_mp3, bgm_mp3 = job.vocal_mp3, job.bgm_mp3
    if not force and vocal_mp3.exists() and bgm_mp3.exists() and vocal_mp3.stat().st_size > 1024:
        log_fn(f"  [1/4 分离] 已存在，跳过：{vocal_mp3.name}")
        _deliver_bgm(job, bgm_mp3, log_fn)
        return {"vocal": str(vocal_mp3), "bgm": str(bgm_mp3), "skipped": True}

    work = job.dir(STAGE)
    src_wav = work / f"{job.base}_source.wav"
    log_fn(f"  [1/4 分离] 统一解码为 44.1k 立体声 wav → {src_wav.name}")
    run(
        [FFMPEG, "-y", "-v", "error", "-i", str(job.media), "-vn", "-ac", "2", "-ar", "44100",
         "-c:a", "pcm_s16le", str(src_wav)],
        quiet=True,
    )
    duration = media_duration(job.media)
    device = str(cfg.get("device", "cuda"))
    log_fn(f"  [1/4 分离] 音频时长 {duration:.1f}s，开始 demucs 分离（device={device}）")

    demucs_out = ensure_dir(work / "demucs")
    py = cfg_python(job)
    cmd = [
        py, "-m", "demucs.separate",
        "--two-stems=vocals",
        "-n", str(cfg.get("demucs_model", "htdemucs")),
        "--other-method", str(cfg.get("other_method", "minus")),
        "-d", str(cfg.get("device", "cpu")),
        "-j", str(int(cfg.get("jobs", 1))),
        "-o", str(demucs_out),
        str(src_wav),
    ]
    run(cmd, timeout=None)

    vocals = _find(demucs_out, "vocals.wav")
    # demucs 的 --other-method 决定伴奏文件名：minus → minus_vocals.wav，
    # 默认(none) → no_vocals.wav。旧版只认 no_vocals.wav，导致伴奏一直是静音占位。
    no_vocals = _find(demucs_out, "minus_vocals.wav") or _find(demucs_out, "no_vocals.wav")
    if vocals is None:
        raise RuntimeError("demucs 未产出 vocals.wav，请检查 demucs 模型是否下载完整")

    bitrate = str(cfg.get("mp3_bitrate", "192k"))
    run([FFMPEG, "-y", "-v", "error", "-i", str(vocals), "-c:a", "libmp3lame", "-b:a", bitrate, str(vocal_mp3)], quiet=True)
    if no_vocals is not None:
        run([FFMPEG, "-y", "-v", "error", "-i", str(no_vocals), "-c:a", "libmp3lame", "-b:a", bitrate, str(bgm_mp3)], quiet=True)
        log_fn(f"  [1/4 分离] 伴奏轨：{no_vocals.name}")
    else:
        # 没有 BGM 时生成等长静音，保证下游逻辑统一
        log_fn("  [1/4 分离] ⚠ demucs 未产出伴奏轨（minus_vocals/no_vocals 均缺失），BGM 用等长静音占位")
        run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
             "-t", f"{duration:.3f}", "-c:a", "libmp3lame", "-b:a", bitrate, str(bgm_mp3)], quiet=True)

    if not cfg.get("keep_source_wav", False):
        try:
            src_wav.unlink(missing_ok=True)
            for f in (vocals, no_vocals):
                if f is not None:
                    f.unlink(missing_ok=True)
        except Exception:
            pass

    vocal_dur = media_duration(vocal_mp3)
    log_fn(f"  [1/4 分离] 完成：人声 {vocal_dur:.1f}s / BGM {media_duration(bgm_mp3):.1f}s")
    _deliver_bgm(job, bgm_mp3, log_fn)
    return {"vocal": str(vocal_mp3), "bgm": str(bgm_mp3), "duration": vocal_dur}


def _deliver_bgm(job: Job, bgm_mp3, log_fn) -> None:
    """把纯 BGM 复制到视频同目录（交付物之一，只留这一份在素材目录）。"""
    try:
        shutil.copy2(bgm_mp3, job.bgm_deliverable)
        log_fn(f"  [1/4 分离] 交付：{job.bgm_deliverable.name}")
    except Exception as exc:  # noqa: BLE001
        log_fn(f"  [1/4 分离] 警告：BGM 交付失败 {exc}")


def cfg_python(job: Job) -> str:
    return str(job.cfg.get("python_exe") or sys.executable)
