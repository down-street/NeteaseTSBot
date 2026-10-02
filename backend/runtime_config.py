from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Literal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .config import settings
from .crypto import decrypt_text, encrypt_text
from .models import AppSetting
from .logger import reconfigure_logger
from .managed_assets import asset_path, assets_payload, ASSET_BY_KEY, voice_avatar_config_path


SettingType = Literal["string", "integer", "boolean", "secret", "password", "url", "select", "multiline"]


@dataclass(frozen=True)
class SettingDefinition:
    key: str
    group: str
    label: str
    type: SettingType = "string"
    env: str = ""
    default: Any = ""
    sensitive: bool = False
    restart: str = "none"
    minimum: int | None = None
    maximum: int | None = None
    options: tuple[str, ...] = ()
    help: str = ""
    backend_attr: str = ""


DEFINITIONS: tuple[SettingDefinition, ...] = (
    SettingDefinition("web.app_name", "web", "界面名称", env="TSBOT_WEB_APP_NAME", default="Yumi TSBot", backend_attr="web_app_name"),
    SettingDefinition("web.log_level", "web", "Web 日志等级", type="select", env="VITE_LOG_LEVEL", default="INFO", options=("DEBUG", "INFO", "WARN", "ERROR"), backend_attr="web_log_level"),
    SettingDefinition("backend.netease_api_base", "music", "网易云 API 地址", type="url", env="TSBOT_NETEASE_API_BASE", default="http://127.0.0.1:3000/", backend_attr="netease_api_base"),
    SettingDefinition("backend.voice_grpc_addr", "backend", "Voice gRPC 地址", env="TSBOT_VOICE_GRPC_ADDR", default="127.0.0.1:50051", backend_attr="voice_grpc_addr"),
    SettingDefinition("backend.log_level", "backend", "后端日志等级", type="select", env="TSBOT_LOG_LEVEL", default="INFO", options=("DEBUG", "INFO", "WARNING", "ERROR"), backend_attr="log_level"),
    SettingDefinition("backend.log_file", "backend", "后端日志文件", env="TSBOT_LOG_FILE", default="logs/backend.log", backend_attr="log_file"),
    SettingDefinition("backend.api_tokens", "access", "外部 API Token", type="secret", env="TSBOT_API_TOKENS", sensitive=True, backend_attr="api_tokens", help="多个 Token 使用逗号分隔"),
    SettingDefinition("backend.bilibili_max_duration_minutes", "music", "B站最大点播时长（分钟）", type="integer", env="TSBOT_BILIBILI_MAX_DURATION_MINUTES", default=180, minimum=0, maximum=10080, backend_attr="bilibili_max_duration_minutes"),
    SettingDefinition("backend.bilibili_audio_cache_ttl_hours", "music", "B站缓存保留时间（小时）", type="integer", env="TSBOT_BILIBILI_AUDIO_CACHE_TTL_HOURS", default=72, minimum=0, maximum=8760, backend_attr="bilibili_audio_cache_ttl_hours"),
    SettingDefinition("backend.bilibili_audio_cache_max_mb", "music", "B站缓存上限（MiB）", type="integer", env="TSBOT_BILIBILI_AUDIO_CACHE_MAX_MB", default=2048, minimum=0, maximum=1048576, backend_attr="bilibili_audio_cache_max_mb"),
    SettingDefinition("backend.bilibili_audio_partial_ttl_minutes", "music", "未完成下载保留时间（分钟）", type="integer", env="TSBOT_BILIBILI_AUDIO_PARTIAL_TTL_MINUTES", default=60, minimum=0, maximum=10080, backend_attr="bilibili_audio_partial_ttl_minutes"),
    SettingDefinition("backend.bilibili_browser_subtitle", "music", "用浏览器补抓B站AI字幕", type="boolean", env="TSBOT_BILIBILI_BROWSER_SUBTITLE", default=False, backend_attr="bilibili_browser_subtitle_enabled", help="关闭后不再启动 Chromium，只使用B站接口字幕（匿名 + 管理员登录态）。"),
    SettingDefinition("backend.chat_tts_enabled", "teamspeak", "频道打字转语音", type="boolean", env="TSBOT_CHAT_TTS_ENABLED", default=True, backend_attr="chat_tts_enabled", help="有人在频道打字时，由 TS3AudioBot 念出来；指令消息不会被念。"),
    SettingDefinition("backend.chat_tts_api_base", "teamspeak", "TS3AudioBot API 地址", type="string", env="TSBOT_CHAT_TTS_API_BASE", default="http://ts3audiobot:58913", backend_attr="chat_tts_api_base"),
    SettingDefinition("backend.chat_tts_bot_id", "teamspeak", "TS3AudioBot 实例 ID", type="integer", env="TSBOT_CHAT_TTS_BOT_ID", default=0, minimum=0, maximum=99, backend_attr="chat_tts_bot_id"),
    SettingDefinition("backend.chat_tts_voice", "teamspeak", "语音音色", type="string", env="TSBOT_CHAT_TTS_VOICE", default="zh-CN-XiaoxiaoNeural", backend_attr="chat_tts_voice", help="edge-tts 音色，例如 zh-CN-XiaoxiaoNeural、zh-CN-YunxiNeural、zh-CN-XiaoyiNeural。"),
    SettingDefinition("backend.chat_tts_prefix", "teamspeak", "播报前缀模板", type="string", env="TSBOT_CHAT_TTS_PREFIX", default="{name}说：", backend_attr="chat_tts_prefix", help="支持 {name} 占位符。"),
    SettingDefinition("backend.chat_tts_max_chars", "teamspeak", "单条最长字数", type="integer", env="TSBOT_CHAT_TTS_MAX_CHARS", default=60, minimum=1, maximum=500, backend_attr="chat_tts_max_chars"),
    SettingDefinition("backend.chat_tts_cooldown_s", "teamspeak", "播报最小间隔（秒）", type="integer", env="TSBOT_CHAT_TTS_COOLDOWN_S", default=5, minimum=0, maximum=600, backend_attr="chat_tts_cooldown_s"),
    SettingDefinition("backend.chat_tts_public_base", "teamspeak", "语音文件访问地址", type="string", env="TSBOT_CHAT_TTS_PUBLIC_BASE", default="http://backend:8009", backend_attr="chat_tts_public_base", help="TS3AudioBot 拉取语音文件时使用的后端地址。"),
    SettingDefinition("backend.chat_tts_ignore_names", "teamspeak", "忽略的昵称", type="string", env="TSBOT_CHAT_TTS_IGNORE_NAMES", default="TS3AudioBot", backend_attr="chat_tts_ignore_names", help="多个昵称用逗号分隔，机器人自己的昵称始终忽略。"),
    SettingDefinition("voice.ts3_host", "teamspeak", "TeamSpeak 服务器", env="TSBOT_TS3_HOST", default="127.0.0.1", restart="voice"),
    SettingDefinition("voice.ts3_port", "teamspeak", "TeamSpeak 端口", type="integer", env="TSBOT_TS3_PORT", default=9987, minimum=1, maximum=65535, restart="voice"),
    SettingDefinition("voice.ts3_nickname", "teamspeak", "机器人昵称", env="TSBOT_TS3_NICKNAME", default="tsbot", restart="voice", backend_attr="ts3_nickname"),
    SettingDefinition("voice.ts3_server_password", "teamspeak", "服务器密码", type="password", env="TSBOT_TS3_SERVER_PASSWORD", sensitive=True, restart="voice"),
    SettingDefinition("voice.ts3_channel_id", "teamspeak", "频道 ID", env="TSBOT_TS3_CHANNEL_ID", restart="voice"),
    SettingDefinition("voice.ts3_channel_path", "teamspeak", "频道路径", env="TSBOT_TS3_CHANNEL_PATH", restart="voice"),
    SettingDefinition("voice.ts3_channel_password", "teamspeak", "频道密码", type="password", env="TSBOT_TS3_CHANNEL_PASSWORD", sensitive=True, restart="voice"),
    SettingDefinition("voice.ts3_identity_file", "teamspeak", "Identity 文件", env="TSBOT_TS3_IDENTITY_FILE", default="./logs/identity.json", restart="voice"),
    SettingDefinition("voice.ts3_identity", "teamspeak", "Identity 内容", type="secret", env="TSBOT_TS3_IDENTITY", sensitive=True, restart="voice"),
    SettingDefinition("voice.allow_direct_description", "teamspeak", "允许直接更新客户端简介", type="boolean", env="TSBOT_TS3_ALLOW_DIRECT_CLIENTUPDATE_DESCRIPTION", default=False, restart="voice"),
    SettingDefinition("voice.description_title", "teamspeak", "客户端简介标题", type="multiline", env="TSBOT_TS3_CLIENT_DESCRIPTION_TITLE", default="Yumi TSBot", restart="voice", backend_attr="voice_description_title"),
    SettingDefinition("voice.description_intro", "teamspeak", "客户端简介正文", type="multiline", env="TSBOT_TS3_CLIENT_DESCRIPTION_INTRO", default="TeamSpeak 音乐机器人\\n支持网易云 / QQ 音乐 / B站", restart="voice", backend_attr="voice_description_intro"),
    SettingDefinition("voice.ts3_cover_avatar", "teamspeak", "播放时使用封面作为机器人头像", type="boolean", env="TSBOT_TS3_COVER_AVATAR", default=True, backend_attr="voice_cover_avatar_enabled", help="播放时把机器人头像换成当前歌曲/视频封面，空闲或停止后还原为 Web 设置的头像。"),
    SettingDefinition("voice.ts3_lyric_nickname", "teamspeak", "昵称滚动显示歌词/字幕", type="boolean", env="TSBOT_TS3_LYRIC_NICKNAME", default=True, backend_attr="voice_lyric_nickname_enabled", help="播放时在机器人昵称显示当前歌词/字幕行，暂停或空闲后还原昵称。"),
    SettingDefinition("voice.ts3_lyric_update_interval_ms", "teamspeak", "歌词昵称刷新间隔（毫秒）", type="integer", env="TSBOT_TS3_LYRIC_UPDATE_INTERVAL_MS", default=2000, minimum=500, maximum=30000, backend_attr="voice_lyric_update_interval_ms"),
    SettingDefinition("voice.ts3_filetransfer_port", "teamspeak", "TeamSpeak 文件传输端口", type="integer", env="TSBOT_TS3_FILETRANSFER_PORT", default=30033, minimum=0, maximum=65535, restart="voice", backend_attr="ts3_filetransfer_port", help="用于清理上一张封面头像文件；设为 0 可关闭清理。"),
    SettingDefinition("voice.serverquery_user", "serverquery", "ServerQuery 用户名", env="TSBOT_TS3_SERVERQUERY_USER", restart="voice"),
    SettingDefinition("voice.serverquery_password", "serverquery", "ServerQuery 密码", type="password", env="TSBOT_TS3_SERVERQUERY_PASSWORD", sensitive=True, restart="voice"),
    SettingDefinition("voice.serverquery_host", "serverquery", "ServerQuery 主机", env="TSBOT_TS3_SERVERQUERY_HOST", restart="voice"),
    SettingDefinition("voice.serverquery_port", "serverquery", "ServerQuery 端口", type="integer", env="TSBOT_TS3_SERVERQUERY_PORT", default=10011, minimum=1, maximum=65535, restart="voice"),
    SettingDefinition("voice.serverquery_sid", "serverquery", "虚拟服务器 SID", env="TSBOT_TS3_SERVERQUERY_SID", restart="voice"),
    SettingDefinition("voice.serverquery_use_port", "serverquery", "语音端口映射", type="integer", env="TSBOT_TS3_SERVERQUERY_USE_PORT", default=9987, minimum=1, maximum=65535, restart="voice"),
    SettingDefinition("voice.log_level", "backend", "Voice 日志等级", type="select", env="TSBOT_VOICE_LOG_LEVEL", default="INFO", options=("DEBUG", "INFO", "WARNING", "ERROR"), restart="voice"),
    SettingDefinition("voice.state_file", "backend", "播放状态文件", env="TSBOT_VOICE_STATE_FILE", default="./logs/voice_state.json", restart="voice"),
)


DEFINITION_BY_KEY = {definition.key: definition for definition in DEFINITIONS}
PENDING_EFFECTS_KEY = "__runtime.pending_effects"


def _env_value(definition: SettingDefinition) -> Any:
    if definition.key == "backend.api_tokens":
        tokens = settings.get_api_tokens()
        return ",".join(tokens)
    if definition.key == "voice.log_level":
        return os.getenv("TSBOT_VOICE_LOG_LEVEL") or os.getenv("TSBOT_LOG_LEVEL") or definition.default
    raw = os.getenv(definition.env) if definition.env else None
    if raw is None and definition.backend_attr:
        raw = getattr(settings, definition.backend_attr, None)
    if raw is None or raw == "":
        return definition.default
    if definition.type == "integer":
        try:
            return int(raw)
        except (TypeError, ValueError):
            return definition.default
    if definition.type == "boolean":
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}
    return str(raw)


def _serialize(definition: SettingDefinition, value: Any) -> str:
    serialized = json.dumps(value, ensure_ascii=False)
    return encrypt_text(serialized) if definition.sensitive else serialized


def _deserialize(definition: SettingDefinition, value: str) -> Any:
    raw = decrypt_text(value) if definition.sensitive else value
    return json.loads(raw)


def initialize_runtime_settings(session: Session) -> None:
    changed = False
    for definition in DEFINITIONS:
        if session.get(AppSetting, definition.key) is not None:
            continue
        value = _env_value(definition)
        if definition.sensitive and not value:
            continue
        session.add(AppSetting(key=definition.key, value=_serialize(definition, value)))
        changed = True
    pending = session.get(AppSetting, PENDING_EFFECTS_KEY)
    if pending is not None:
        session.delete(pending)
        changed = True
    if changed:
        session.commit()
    apply_backend_settings(session)
    write_voice_config(session)


def get_value(session: Session, definition: SettingDefinition) -> Any:
    row = session.get(AppSetting, definition.key)
    if row is None:
        return _env_value(definition)
    try:
        return _deserialize(definition, row.value)
    except Exception as exc:
        raise RuntimeError(f"无法读取配置 {definition.key}") from exc


def validate_value(definition: SettingDefinition, value: Any) -> Any:
    if definition.type == "integer":
        if isinstance(value, bool):
            raise ValueError("必须是整数")
        value = int(value)
        if definition.minimum is not None and value < definition.minimum:
            raise ValueError(f"不能小于 {definition.minimum}")
        if definition.maximum is not None and value > definition.maximum:
            raise ValueError(f"不能大于 {definition.maximum}")
    elif definition.type == "boolean":
        if not isinstance(value, bool):
            raise ValueError("必须是布尔值")
    else:
        value = str(value).strip()
        if definition.key == "web.app_name" and not value:
            raise ValueError("界面名称不能为空")
        if definition.type == "url" and not re.match(r"^https?://", value, re.IGNORECASE):
            raise ValueError("必须是 http:// 或 https:// 地址")
        if definition.options and value.upper() not in definition.options:
            raise ValueError("不支持的选项")
        if definition.options:
            value = value.upper()
    return value


def _pending_effects(session: Session) -> set[str]:
    row = session.get(AppSetting, PENDING_EFFECTS_KEY)
    if row is None:
        return set()
    try:
        values = json.loads(row.value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return set()
    if not isinstance(values, list):
        return set()
    return {str(value) for value in values if str(value) in {"none", "voice", "backend"}}


def _store_pending_effects(session: Session, effects: set[str]) -> None:
    row = session.get(AppSetting, PENDING_EFFECTS_KEY)
    if not effects:
        if row is not None:
            session.delete(row)
        return
    value = json.dumps(sorted(effects), ensure_ascii=False)
    if row is None:
        session.add(AppSetting(key=PENDING_EFFECTS_KEY, value=value))
    else:
        row.value = value


def update_settings(session: Session, values: dict[str, Any], *, apply: bool = True) -> set[str]:
    effects: set[str] = set()
    errors: dict[str, str] = {}
    normalized: dict[str, Any] = {}
    for key, value in values.items():
        definition = DEFINITION_BY_KEY.get(key)
        if definition is None:
            errors[key] = "未知配置项"
            continue
        if definition.sensitive and value == "":
            continue
        if value is None:
            normalized[key] = None
            continue
        try:
            normalized[key] = validate_value(definition, value)
        except (TypeError, ValueError) as exc:
            errors[key] = str(exc)
    if errors:
        raise HTTPException(status_code=422, detail={"fields": errors})

    for key, value in normalized.items():
        definition = DEFINITION_BY_KEY[key]
        row = session.get(AppSetting, key)
        if value is None:
            if definition.sensitive:
                if row is None:
                    session.add(AppSetting(key=key, value=_serialize(definition, "")))
                elif get_value(session, definition) != "":
                    row.value = _serialize(definition, "")
                else:
                    continue
            elif row is not None:
                session.delete(row)
            else:
                continue
        elif row is None:
            session.add(AppSetting(key=key, value=_serialize(definition, value)))
        else:
            if get_value(session, definition) == value:
                continue
            row.value = _serialize(definition, value)
        effects.add(definition.restart)
    if apply:
        effects.update(_pending_effects(session))
        _store_pending_effects(session, set())
    else:
        _store_pending_effects(session, _pending_effects(session) | effects)
    session.commit()
    if not apply:
        return effects

    apply_backend_settings(session)
    if "voice" in effects:
        write_voice_config(session, force_restart=True)
    return effects


def apply_backend_settings(session: Session) -> None:
    for definition in DEFINITIONS:
        if definition.backend_attr:
            setattr(settings, definition.backend_attr, get_value(session, definition))
    settings.api_token = ""
    reconfigure_logger(str(settings.log_level or "INFO"), str(settings.log_file or ""))


def _voice_env(session: Session) -> dict[str, str]:
    result: dict[str, str] = {}
    for definition in DEFINITIONS:
        if not definition.key.startswith("voice.") or not definition.env:
            continue
        value = get_value(session, definition)
        if isinstance(value, bool):
            result[definition.env] = "1" if value else "0"
        else:
            result[definition.env] = str(value or "")
    # The voice logger historically reads TSBOT_LOG_LEVEL.
    result["TSBOT_LOG_LEVEL"] = result.pop("TSBOT_VOICE_LOG_LEVEL", "INFO")
    avatar = ASSET_BY_KEY["teamspeak-avatar"]
    result["TSBOT_TS3_AVATAR_FILE"] = voice_avatar_config_path() if asset_path(avatar).is_file() else ""
    result["TSBOT_TS3_AVATAR_DIR"] = ""
    return result


def write_voice_config(session: Session, *, force_restart: bool = False) -> str:
    path = Path(settings.voice_config_file)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent.parent / path
    path.parent.mkdir(parents=True, exist_ok=True)
    values = _voice_env(session)
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        previous = {}
    revision = str(previous.get("TSBOT_VOICE_CONFIG_REVISION") or "")
    if force_restart:
        revision = str(time.time_ns())
    if revision:
        values["TSBOT_VOICE_CONFIG_REVISION"] = revision
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    return revision


def voice_config_revision() -> str:
    path = Path(settings.voice_config_file)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent.parent / path
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return ""
    return str(values.get("TSBOT_VOICE_CONFIG_REVISION") or "")


def settings_payload(session: Session) -> dict[str, Any]:
    fields: list[dict[str, Any]] = []
    for definition in DEFINITIONS:
        value = get_value(session, definition)
        fields.append({
            "key": definition.key,
            "group": definition.group,
            "label": definition.label,
            "type": definition.type,
            "value": None if definition.sensitive else value,
            "configured": bool(value) if definition.sensitive else True,
            "sensitive": definition.sensitive,
            "restart": definition.restart,
            "minimum": definition.minimum,
            "maximum": definition.maximum,
            "options": list(definition.options),
            "help": definition.help,
        })
    return {
        "fields": fields,
        "assets": assets_payload(),
        "apply_pending": bool(_pending_effects(session)),
        "bootstrap": {
            "backend_host": settings.host,
            "backend_port": settings.port,
            "database": os.getenv("DATABASE_URL") or os.getenv("TSBOT_DATABASE_URL") or "sqlite:///./tsbot.db",
            "cookie_key_configured": bool(settings.cookie_key and settings.cookie_key != "dev-cookie-key"),
            "voice_config_file": settings.voice_config_file,
            "initial_password_file": settings.initial_password_file,
            "web_host": os.getenv("TSBOT_WEB_HOST", "127.0.0.1"),
            "web_port": os.getenv("TSBOT_WEB_PORT", "8080"),
            "web_api_proxy_target": os.getenv("TSBOT_WEB_API_PROXY_TARGET", "自动推导"),
            "web_allowed_hosts": os.getenv("TSBOT_WEB_ALLOWED_HOSTS", "未限制"),
        },
    }
