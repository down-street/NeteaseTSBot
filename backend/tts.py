from __future__ import annotations

import asyncio
import hashlib
import time
from pathlib import Path

from .config import settings
from .logger import logger
from .managed_assets import ASSET_DIR

# 语音文件与 uploads 放在同一数据卷下：<data>/tts/
TTS_DIR = ASSET_DIR.parent / "tts"

_TTS_TTL_SECONDS = 3600.0
_TTS_MAX_FILES = 200

_synth_lock = asyncio.Lock()


def tts_audio_path(token: str) -> Path:
    """Resolve a token to a file inside the TTS directory (path-traversal safe)."""
    safe = "".join(ch for ch in str(token or "") if ch in "0123456789abcdefABCDEF")[:64]
    return TTS_DIR / f"{safe}.mp3"


def _prune_tts_dir() -> None:
    try:
        if not TTS_DIR.is_dir():
            return
        entries = [p for p in TTS_DIR.glob("*.mp3") if p.is_file()]
        now = time.time()
        for path in entries:
            try:
                if now - path.stat().st_mtime > _TTS_TTL_SECONDS:
                    path.unlink(missing_ok=True)
            except OSError:
                continue
        remaining = sorted(
            (p for p in TTS_DIR.glob("*.mp3") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
        )
        for path in remaining[:-_TTS_MAX_FILES] if len(remaining) > _TTS_MAX_FILES else []:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                continue
    except Exception as exc:  # pragma: no cover - best effort cleanup
        logger.debug("tts prune skipped: %s", exc)


async def synthesize_tts_file(text: str, *, voice: str = "") -> Path | None:
    """Synthesize `text` with edge-tts and return the cached mp3 path."""
    payload = " ".join(str(text or "").split())
    if not payload:
        return None

    use_voice = (voice or str(getattr(settings, "chat_tts_voice", "") or "")).strip() or "zh-CN-XiaoxiaoNeural"
    token = hashlib.sha256(f"{use_voice}\n{payload}".encode("utf-8")).hexdigest()[:32]
    target = tts_audio_path(token)
    if target.is_file() and target.stat().st_size > 0:
        return target

    try:
        import edge_tts
    except Exception as exc:
        logger.warning("edge-tts 不可用（请重新构建后端镜像以安装依赖）: %s", exc)
        return None

    async with _synth_lock:
        if target.is_file() and target.stat().st_size > 0:
            return target
        TTS_DIR.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(".part")
        try:
            communicate = edge_tts.Communicate(payload, use_voice)
            await communicate.save(str(partial))
            if not partial.is_file() or partial.stat().st_size <= 0:
                raise RuntimeError("edge-tts produced no audio")
            partial.replace(target)
        except Exception as exc:
            logger.warning("edge-tts 合成失败: %s", exc)
            partial.unlink(missing_ok=True)
            return None

    _prune_tts_dir()
    return target
