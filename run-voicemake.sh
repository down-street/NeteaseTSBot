#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [[ -f "$ROOT_DIR/tsbot.env" ]]; then
  source "$ROOT_DIR/tsbot.env"
fi

mkdir -p "$ROOT_DIR/logs"

if [[ -z "${TSBOT_TS3_IDENTITY_FILE:-}" ]]; then
  export TSBOT_TS3_IDENTITY_FILE="$ROOT_DIR/logs/identity.json"
elif [[ "${TSBOT_TS3_IDENTITY_FILE}" != /* ]]; then
  export TSBOT_TS3_IDENTITY_FILE="$ROOT_DIR/${TSBOT_TS3_IDENTITY_FILE#./}"
fi

CARGO_BIN="$HOME/.cargo/bin/cargo"
if [[ ! -x "$CARGO_BIN" ]]; then
  CARGO_BIN="$(command -v cargo || true)"
fi

# 预编译二进制优先，避免在低内存机器上重新编译：
#   1) TSBOT_VOICE_BIN 显式指定的路径
#   2) 发布包 / CI 产物中的 bin/voice-service
#   3) 都没有时才本地构建（默认仍构建 debug）
VOICE_BIN="${TSBOT_VOICE_BIN:-}"
if [[ -n "$VOICE_BIN" && "$VOICE_BIN" != /* ]]; then
  VOICE_BIN="$ROOT_DIR/${VOICE_BIN#./}"
fi
if [[ -z "$VOICE_BIN" && -x "$ROOT_DIR/bin/voice-service" ]]; then
  VOICE_BIN="$ROOT_DIR/bin/voice-service"
fi

if [[ -n "$VOICE_BIN" ]]; then
  if [[ ! -x "$VOICE_BIN" ]]; then
    echo "TSBOT_VOICE_BIN 不可执行: $VOICE_BIN" >&2
    exit 1
  fi
  echo "Using prebuilt voice service: $VOICE_BIN"
else
  if [[ -z "${CARGO_BIN:-}" ]]; then
    echo "cargo not found in \$HOME/.cargo/bin or PATH (也可设置 TSBOT_VOICE_BIN 使用预编译二进制)" >&2
    exit 1
  fi

  echo "Building voice service..."
  "$CARGO_BIN" build --manifest-path "$ROOT_DIR/voice-service/Cargo.toml"

  VOICE_BIN="$ROOT_DIR/voice-service/target/debug/voice-service"
  if [[ ! -x "$VOICE_BIN" ]]; then
    echo "voice-service binary not found at $VOICE_BIN" >&2
    exit 1
  fi
fi

export TSBOT_TS3_HOST="${TSBOT_TS3_HOST:-47.113.188.213}"
export TSBOT_TS3_PORT="${TSBOT_TS3_PORT:-9987}"
export TSBOT_TS3_NICKNAME="${TSBOT_TS3_NICKNAME:-tsbot}"
export TSBOT_TS3_CHANNEL_ID="${TSBOT_TS3_CHANNEL_ID:-2}"

echo "Starting voice service..."
echo "Use Ctrl+C to stop gracefully"
exec "$VOICE_BIN" 127.0.0.1:50051
