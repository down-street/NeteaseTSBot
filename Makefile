.PHONY: all backend backend-setup web web-build voice voice-build voice-build-lowmem voice-run voice-gdb voice-test-server

all: backend-setup web-build voice-build

backend:
	backend/.venv/bin/uvicorn backend.main:app --reload --host 127.0.0.1 --port 8009

backend-setup:
	python3 -m venv backend/.venv
	backend/.venv/bin/pip install -r backend/requirements.txt

web:
	cd web && npm run dev

web-build:
	npm --prefix web ci
	npm --prefix web run build

voice:
	cd voice-service && $$HOME/.cargo/bin/cargo build

voice-build: voice

# 低内存构建：关闭调试符号并串行编译，适合 2 GB 左右内存或会触发 OOM 的机器。
voice-build-lowmem:
	cd voice-service && CARGO_PROFILE_DEV_DEBUG=0 $$HOME/.cargo/bin/cargo build -j 1

voice-run:
	TSBOT_TS3_IDENTITY_FILE="$${TSBOT_TS3_IDENTITY_FILE:-$(CURDIR)/logs/identity.json}" \
	cd voice-service && $$HOME/.cargo/bin/cargo run -- 127.0.0.1:50051

voice-gdb:
	cd voice-service && $$HOME/.cargo/bin/cargo build
	TSBOT_TS3_IDENTITY_FILE="$${TSBOT_TS3_IDENTITY_FILE:-$(CURDIR)/logs/identity.json}" \
	gdb --args voice-service/target/debug/voice-service 127.0.0.1:50051

voice-test-server:
	TSBOT_TS3_IDENTITY_FILE="$${TSBOT_TS3_IDENTITY_FILE:-$(CURDIR)/logs/identity.json}" \
	cd voice-service && $$HOME/.cargo/bin/cargo run -- 127.0.0.1:50051
