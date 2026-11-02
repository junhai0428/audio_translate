"""配置加载 + 单个音频任务上下文(Job)。

本链路只处理**已提取好的音频**（mp3/wav/m4a/flac/aac/ogg…），不做视频抽轨。
"""

from __future__ import annotations

import os
from pathlib import Path

from .utils import ensure_dir, read_json, write_json

DEFAULT_CONFIG_NAME = "config.json"


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
    root = project_root()
    for key in ("input", "work", "output"):
        cfg["dirs"][key] = str((root / cfg["dirs"][key]).resolve())
    cfg["_root"] = str(root)
    return cfg


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
        # name：工单目录名，可带前缀避免不同素材目录的编号撞车
        self.base = self.media.stem
        self.name = name or self.base
        self.work = ensure_dir(Path(cfg["dirs"]["work"]) / self.name)
        self.out_dir = ensure_dir(Path(cfg["dirs"]["output"]) / self.name)
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
        """最终交付物：越南语配音轨。"""
        return self.out_dir / f"{self.base}_vi_voice.mp3"

    # ---------- 状态 ----------
    def mark(self, stage: int, ok: bool, **info) -> None:
        entry = self.state.setdefault("stages", {}).setdefault(str(stage), {})
        entry.update({"ok": ok, "ts": __import__("time").strftime("%Y-%m-%d %H:%M:%S"), **info})
        write_json(self.state_path, self.state)

    def done(self, stage: int) -> bool:
        return bool(self.state.get("stages", {}).get(str(stage), {}).get("ok"))
