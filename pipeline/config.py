"""配置加载 + 单个音频任务上下文(Job)。

本链路只处理**已提取好的音频**（mp3/wav/m4a/flac/aac/ogg…），不做视频抽轨。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .utils import ensure_dir, read_json, write_json

DEFAULT_CONFIG_NAME = "config.json"

# 合法工单名 = 源素材基名：douyin_012 / bz_051 / 012
# temp 下常混着调试目录（_语速试听）和历史派生物（douyin_003_nosub / _cfr14188），一律不认
JOB_NAME = re.compile(r"^([A-Za-z0-9\u4e00-\u9fff]+_)?\d+$")


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


_DEFAULTS = read_json(project_root() / DEFAULT_CONFIG_NAME, {}) or {}


def load_config(path=None) -> dict:
    if path:
        cfg = read_json(path, {}) or {}
    else:
        cfg = read_json(project_root() / DEFAULT_CONFIG_NAME, {}) or {}
    cfg = _deep_merge(_DEFAULTS, cfg)
    cfg["dirs"] = dict(cfg.get("dirs") or {})
    # 中间产物目录名：落在**素材目录**下（<素材目录>/temp/<工单>/），不再集中到项目根
    cfg["dirs"]["temp"] = str(cfg["dirs"].get("temp") or "temp")
    cfg["_root"] = str(project_root())
    return cfg


def iter_job_dirs(cfg: dict, roots=None) -> list:
    """扫描素材目录下的 <temp>/<工单> 目录（新目录规范）。

    只返回合法工单：temp 下的调试目录（_语速试听）和历史派生物
    （douyin_003_nosub / _cfr14188 等）不算工单，否则所有统计工具都会把它们算进去。
    """
    temp_name = str(cfg.get("dirs", {}).get("temp", "temp")).lower()
    roots = roots or cfg.get("media_roots") or []
    out = []
    for r in roots:
        rp = Path(r)
        if not rp.is_dir():
            continue
        # 只认「<素材目录>/temp」两层，不深入 temp 内部（里面有 demucs 中间目录）
        cands = [p for p in list(rp.glob(f"*/{temp_name}")) + list(rp.glob(temp_name)) if p.is_dir()]
        for t in sorted(set(cands)):
            out += sorted(p for p in t.iterdir() if p.is_dir() and JOB_NAME.match(p.name))
    return out


def job_dir(cfg: dict, name: str, roots=None):
    """按工单名找中间产物目录。"""
    for d in iter_job_dirs(cfg, roots):
        if d.name == name:
            return d
    return None


def llm_key(cfg: dict) -> str:
    """优先 config.json，其次环境变量。"""
    key = (cfg.get("translate", {}).get("api_key") or "").strip()
    if key:
        return key
    for env_name in cfg.get("translate", {}).get("api_key_env", []):
        val = (os.environ.get(env_name) or "").strip()
        if val:
            return val
    return ""


def resolve_whisper_model(model: str) -> str:
    """把 HF cache 根目录 / snapshot 目录统一解析成可用的模型目录。"""
    p = Path(model)
    if not p.exists():
        return model  # 交给 faster-whisper 当模型名下载
    if (p / "model.bin").exists():
        return str(p)
    snaps = p / "snapshots"
    if snaps.is_dir():
        cands = [d for d in snaps.iterdir() if (d / "model.bin").exists()]
        if cands:
            cands.sort(key=lambda d: (d / "model.bin").stat().st_size, reverse=True)
            return str(cands[0])
    return str(p)


class Job:
    """一次语音本地化任务：路径约定 + 断点信息。输入是**音频文件**。"""

    STAGE_DIRS = {
        1: "01_separate",
        2: "02_asr",
        3: "03_translate",
        4: "04_voice",
    }

    def __init__(self, media: Path, cfg: dict, name: str | None = None):
        self.media = Path(media)
        self.cfg = cfg
        # base：源文件基名，用于产物文件名（落到源目录时保持原名）
        # name：工单目录名（中间产物目录，同一素材目录内不能重名）
        self.base = self.media.stem
        self.name = name or self.base
        self.site = self.media.parent                     # 素材/产品目录
        self.work = ensure_dir(self.site / str(cfg["dirs"]["temp"]) / self.name)
        self.out_dir = self.site                          # 交付物直接落素材目录
        self.state_path = self.work / "state.json"
        self.state = read_json(self.state_path, {"media": str(self.media), "stages": {}}) or {}

    # ---------- 路径 ----------
    def dir(self, stage: int) -> Path:
        return ensure_dir(self.work / self.STAGE_DIRS[stage])

    def path(self, stage: int, filename: str) -> Path:
        return self.dir(stage) / filename

    @property
    def vocal_mp3(self) -> Path:
        return self.path(1, f"{self.base}.mp3")

    @property
    def bgm_mp3(self) -> Path:
        return self.path(1, f"{self.base}_bgm.mp3")

    @property
    def asr_json(self) -> Path:
        return self.path(2, "asr.json")

    @property
    def zh_txt(self) -> Path:
        return self.path(3, "zh_opt.txt")

    @property
    def vi_txt(self) -> Path:
        return self.path(3, "vi.txt")

    @property
    def vi_segments_json(self) -> Path:
        return self.path(3, "segments_vi.json")

    @property
    def vi_voice_mp3(self) -> Path:
        return self.path(4, f"{self.base}_vi_voice.mp3")

    @property
    def vi_voice_wav(self) -> Path:
        return self.path(4, f"{self.base}_vi_voice.wav")

    @property
    def deliverable(self) -> Path:
        """最终交付物：越南语配音轨，直接放视频同目录。"""
        return self.out_dir / f"{self.base}_越南语配音.mp3"

    @property
    def bgm_deliverable(self) -> Path:
        """交付物 2：纯 BGM 轨，直接放视频同目录。"""
        return self.out_dir / f"{self.base}_背景声.mp3"

    # ---------- 状态 ----------
    def mark(self, stage: int, ok: bool, **info) -> None:
        entry = self.state.setdefault("stages", {}).setdefault(str(stage), {})
        entry.update({"ok": ok, "ts": __import__("time").strftime("%Y-%m-%d %H:%M:%S"), **info})
        write_json(self.state_path, self.state)

    def done(self, stage: int) -> bool:
        return bool(self.state.get("stages", {}).get(str(stage), {}).get("ok"))
