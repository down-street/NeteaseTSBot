import re
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TSBOT_",
        env_file=str(Path(__file__).resolve().parent / ".env"),
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = 8009
    voice_grpc_addr: str = "127.0.0.1:50051"
    
    cookie_key: str = "dev-cookie-key"
    netease_api_base: str = "http://47.113.188.213:3000/"
    
    # 日志配置
    log_level: str = "INFO"
    log_file: str = "logs/backend.log"

    api_token: str = ""
    api_tokens: str = ""

    admin_token: str = ""
    initial_admin_password: str = ""
    initial_password_file: str = "./logs/initial-admin-password.txt"
    voice_config_file: str = "./logs/voice-service.json"
    web_app_name: str = "Yumi TSBot"
    web_app_icon: str = ""
    web_log_level: str = "INFO"
    voice_description_title: str = "Yumi TSBot"
    voice_description_intro: str = "TeamSpeak 音乐机器人\\n支持网易云 / QQ 音乐 / B站"
    voice_cover_avatar_enabled: bool = True
    voice_lyric_nickname_enabled: bool = True
    voice_lyric_update_interval_ms: int = 2000
    ts3_filetransfer_port: int = 30033
    ts3_nickname: str = "tsbot"
    bilibili_max_duration_minutes: int = 180
    bilibili_audio_cache_ttl_hours: int = 72
    bilibili_audio_cache_max_mb: int = 2048
    bilibili_audio_partial_ttl_minutes: int = 60
    bilibili_browser_subtitle_enabled: bool = False

    # 频道打字转语音（edge-tts + TS3AudioBot）
    chat_tts_enabled: bool = True
    chat_tts_api_base: str = "http://ts3audiobot:58913"
    chat_tts_bot_id: int = 0
    chat_tts_voice: str = "zh-CN-XiaoxiaoNeural"
    chat_tts_prefix: str = "{name}说："
    chat_tts_max_chars: int = 60
    chat_tts_cooldown_s: int = 5
    chat_tts_public_base: str = "http://backend:8009"
    chat_tts_ignore_names: str = "TS3AudioBot"

    def get_api_tokens(self) -> list[str]:
        tokens: list[str] = []
        for raw in (self.api_token, self.api_tokens):
            for part in re.split(r"[\s,]+", raw or ""):
                token = part.strip()
                if token and token not in tokens:
                    tokens.append(token)
        return tokens


settings = Settings()
