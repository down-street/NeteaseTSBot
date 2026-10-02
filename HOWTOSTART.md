# TSBot 部署和运行指南

TSBot 是一个基于 TeamSpeak 的音乐机器人，包含 Python 后端、Vue 前端和 Rust 语音服务。

## 系统要求

- **操作系统**: Linux（推荐 Ubuntu 20.04+）或 Windows 10/11（推荐 PowerShell 7+）
- **Python**: 3.8+
- **Node.js**: 16+
- **CMake**: 3.16+
- **Rust**: 1.70+（推荐，默认语音服务实现）
- **TeamSpeak 3 Client SDK**：仅旧版 C++ 语音服务路径需要；默认 Rust `voice-service` 不依赖它

> **内存**：单独编译 `voice-service` 需要 1.2 GB 以上可用内存（见下文 OOM 章节），因此**编译环境建议 ≥2 GB 内存或 ≥1.2 GB 可用内存 + swap**。运行整套服务（voice + backend + web）建议 ≥512 MB 内存；如果机器只有 200 MB 左右，请直接使用预编译二进制/镜像，不要在本机编译（见「内存很小时如何部署」）。

## 快速开始

### 1. 克隆项目
```bash
git clone <repository-url>
cd tsbot
```

### 2. 环境配置
复制环境配置文件并修改：
```bash
cp tsbot.env.example tsbot.env
# 编辑 tsbot.env，至少设置用于加密敏感配置的 TSBOT_COOKIE_KEY
```

### 3. 安装依赖

#### 后端依赖 (Python)
```bash
# 创建虚拟环境
cd backend
python3 -m venv .venv
source .venv/bin/activate

# 安装 Python 依赖
pip install -r requirements.txt
```

#### 前端依赖 (Node.js)
```bash
# 安装前端依赖
cd web
npm install
cd ..
```

#### 语音服务依赖
```bash
# 安装 CMake 和构建工具
sudo apt update
sudo apt install cmake build-essential

# 安装 Rust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source ~/.cargo/env


```

### 4. 构建项目
```bash
# 使用 Makefile 构建所有组件
make all

# 或者分别构建
make backend-setup  # 创建后端虚拟环境并安装依赖
make web-build      # 安装前端依赖并构建生产产物
make voice-build    # 构建语音服务
```

#### 语音服务编译内存不足（OOM / 被 killed）怎么办

`voice-service` 依赖的 `ts-bookkeeping` 会生成约 1.7 MB 的 Rust 源码（单个 `s2c_messages.rs` 就有 895 KB），是整个构建里最吃内存的一步；再加上 cargo 默认并行编译多个 crate，内存较小的机器（1～2 GB）在 `make voice-build` 时很容易出现 `signal: 9, SIGKILL` / `out of memory` / `error: could not compile ts-bookkeeping`。

实测 OOM 现场（2 GB 内存机器）：

- 被杀的 `rustc` 当时 `anon-rss` 只有约 320 MB 且仍在增长，说明**并不是单个进程申请了 1～2 GB**；
- 现场 `Free swap = 4962776kB`（约 4.9 GB swap 完全没用上），而 `inactive_anon` 有 1.45 GB、`Node 0 DMA32 free` 只剩 43 MB（低于 44.6 MB 水位线）；
- 触发者是宿主机上的其他进程（阿里云安全组件 `AliYunDunUpdate`）申请一页内存失败，判定为 `global_oom`，然后按 `oom_score` 杀掉了最大的 `rustc`；
- 根因是 **`vm.swappiness = 0`**：内核几乎不换出匿名页，宁可触发 OOM killer 也不用那 4.9 GB 空闲 swap。

所以除了加 swap，**还必须确认 `vm.swappiness` 不是 0**，否则 swap 形同虚设。

建议直接按下面的顺序做：**先加 swap，再用 `make voice-build-lowmem` 串行编译**。2 GB 内存 + 2～4 GB swap 的机器通常就能顺利编译完。

按下面的顺序尝试，任选一种即可：

**方案 A：加 swap（最稳，推荐 1～2 GB 内存的机器）**

```bash
# 创建 4 GB swap 文件（已存在则跳过）
sudo fallocate -l 4G /swapfile || sudo dd if=/dev/zero of=/swapfile bs=1M count=4096
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
# 开机自动挂载
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# 确认生效（Swap 一行应显示 4.0G）
free -h

# 关键：确认 swappiness 不是 0（0 会让内核宁可 OOM 也不用 swap）
sysctl vm.swappiness
sudo sysctl -w vm.swappiness=60
echo 'vm.swappiness=60' | sudo tee /etc/sysctl.d/99-tsbot.conf
```

加完 swap 后再编译即可（swap 会明显变慢，但不会 OOM）：

```bash
cd voice-service && cargo build -j 1
```

**方案 B：降低编译内存占用（无 swap 或 swap 很小）**

```bash
# 关闭调试符号 + 串行编译，避免多个 rustc 同时占用内存
make voice-build-lowmem
# 等价于：cd voice-service && CARGO_PROFILE_DEV_DEBUG=0 cargo build -j 1
```

关键点是 `-j 1`：默认并行度会同时编译多个 crate，峰值内存成倍上升。如果单靠方案 B 仍然 OOM（例如可用内存不足 1.5 GB），请配合方案 A 一起使用。注意：关闭调试符号后无法再用 `make voice-gdb` 调试；需要调试时请先加 swap 再执行普通 `make voice-build`。

**方案 C：把编译放到内存更大的机器或 CI**

本仓库自带 GitHub Actions 工作流（`.github/workflows/docker-publish.yml`）：fork 或推送代码到 `main`/`master` 后，会自动构建并推送 `backend` / `web` / `voice-service` 三个镜像。服务器上直接拉取预构建镜像即可，不需要本地编译：

```bash
# 默认指向上游 yumi118 的旧镜像；改成你自己 fork 的命名空间才能用到新功能
export TSBOT_IMAGE_NAMESPACE="你的DockerHub或GHCR命名空间"
export TSBOT_IMAGE_TAG="latest"
docker compose -f docker-compose.prebuilt.yml pull
docker compose -f docker-compose.prebuilt.yml up -d
```

也可在任意 4 GB 以上内存的机器上执行 `docker build -f Dockerfile.voice-service -t tsbot-voice .`，再把镜像导入低配服务器。

> 在 Docker / WSL2 中编译时，容器或虚拟机的内存上限同样会导致 OOM：WSL2 可在 `%UserProfile%\.wslconfig` 中增加 `[wsl2]` 与 `memory=8GB`，Docker Desktop 可在设置里调高内存后再构建。

#### 内存很小时如何部署（不本地编译，适合 200 MB 级机器）

**200 MB 内存无法本地编译**（单个依赖就要 1.2 GB 以上），正确做法是在别的机器/CI 上编译，把二进制搬到服务器运行。本仓库已经支持直接使用预编译二进制：`run-voicemake.sh` 会优先使用 `bin/voice-service` 或 `TSBOT_VOICE_BIN`，只有两者都不存在时才会调用 cargo 编译。

**方式一：使用 CI 发布包（推荐）**

`.github/workflows/docker-publish.yml` 的 `package` 任务会构建 `voice-service` 的 **release** 二进制并打包为 `tsbot-<版本>-linux-amd64.tar.gz`：

- 推送到 `main`/`master`：在 GitHub Actions 该次运行的 **Artifacts** 里下载（保留 14 天）。
- 打 `v*` 标签：会作为 **Release 资产**长期保留。

在服务器上解压后直接启动即可，无需 cargo / Rust 工具链：

```bash
tar -xzf tsbot-<版本>-linux-amd64.tar.gz
cd tsbot-<版本>-linux-amd64

# 发布包内已包含 bin/voice-service，脚本会自动识别并跳过编译
./nohup-start.sh          # 或前台：./run-voicemake.sh
```

> 打包出来的二进制在 GitHub 的 Ubuntu 24.04 runner 上编译，已改用 **rustls**（不再依赖宿主机的 OpenSSL/libssl），但仍需要宿主机 **glibc ≥ 2.34**，即 Ubuntu 22.04+/Debian 12+/RHEL 9+ 可直接运行；Alibaba Cloud Linux 3（glibc 2.32）、Ubuntu 20.04、CentOS 7/8 等较旧系统请改用 Docker 镜像运行（镜像自带运行时，与宿主机 glibc 无关）。

**方式二：只搬一个二进制文件**

先把二进制放到服务器任意位置（例如 `/opt/tsbot/voice-service`），然后在 `tsbot.env` 里指定：

```bash
export TSBOT_VOICE_BIN="/opt/tsbot/voice-service"
```

`run-voicemake.sh`、`nohup-start.sh` 都会读取该变量并直接运行它，不再尝试编译。手动指定时记得 `chmod +x`。

**方式三：搬 Docker 镜像**

在大内存机器上 `docker build -f Dockerfile.voice-service -t tsbot-voice .`，再 `docker save tsbot-voice | gzip > tsbot-voice.tar.gz`，传到服务器 `docker load < tsbot-voice.tar.gz` 后使用 `docker-compose.prebuilt.yml`。

**运行时内存提示（200 MB 机器）**

即使不编译，整套服务在 200 MB 内存上也非常紧张：

- `voice-service` 解码音频时会调用 `ffmpeg` 子进程，峰值几十 MB；
- Python 后端（uvicorn + SQLAlchemy + httpx）通常需要 80～150 MB；
- B 站字幕/AI 字幕抓取会启动 Playwright Chromium，单个进程就需要数百 MB，200 MB 机器上必然 OOM，建议关闭或避免使用 B 站字幕功能；
- 前端建议用 nginx 直接托管 `web/dist`（比 `npm run preview` 省内存）。

强烈建议至少加 1～2 GB swap 作为运行时兜底，或把整机升级到 512 MB～1 GB 内存：

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
free -h
```

## 运行项目

### Linux

#### 方法一：前台启动（生产方式）
```bash
# 启动语音服务
./run-voicemake.sh

# 启动后端（新终端）
./run-backend.sh

# 启动前端生产预览（新终端）
./run-web.sh
```

以上脚本会自动读取项目根目录下的 `tsbot.env`。其中 `run-web.sh` 会先构建前端产物，再以 preview 方式监听 `TSBOT_WEB_PORT`（默认 `8080`）。

#### 方法二（远程推荐）：使用 nohup 一键启动/停止（不依赖 screen/yum）
```bash
# 第一次使用需要赋予执行权限
chmod +x ./nohup-start.sh ./nohup-stop.sh ./nohup-status.sh

# 停止（按端口兜底清理，避免重复进程）
./nohup-stop.sh

# 启动（会分别启动 voice/backend/web，并写日志到 logs/）
./nohup-start.sh

# 查看状态（端口 + 日志路径）
./nohup-status.sh
```

#### 方法三：本地开发启动
开 3 个终端分别运行：

```bash
./run-voicemake.sh
```

```bash
backend/.venv/bin/uvicorn backend.main:app --reload --reload-exclude "backend/_generated/*" --host 127.0.0.1 --port 8009
```

```bash
npm --prefix web run dev
```

本地开发默认访问 `http://127.0.0.1:5173`，并通过 `/api` 反向代理到 backend。

### Windows（PowerShell）

Windows 下建议先启动后端和前端；如需真正播放音频，再补齐语音服务依赖并启动 `voice-service`。

#### 1. 复制环境配置
```powershell
Copy-Item tsbot.env.example tsbot.env
# 编辑 tsbot.env，至少设置用于加密敏感配置的 TSBOT_COOKIE_KEY
```

#### 2. 安装后端依赖
```powershell
python -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```

#### 3. 安装前端依赖
```powershell
npm.cmd --prefix web install
```

#### 4. 前台启动（生产方式）
分别打开两个 PowerShell 窗口执行：

```powershell
.\run-backend.ps1
```

```powershell
.\run-web.ps1
```

这两个脚本会自动读取项目根目录下的 `tsbot.env`。其中 `run-web.ps1` 会先构建前端产物，再以 preview 方式在 `TSBOT_WEB_PORT`（默认 `8080`）启动。

#### 5. 本地开发启动
分别打开两个 PowerShell 窗口执行：

```powershell
backend\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8009
```

```powershell
npm.cmd --prefix web run dev
```

#### 6. 启动语音服务
打开第三个 PowerShell 窗口执行：

```powershell
.\run-voicemake.ps1
```

`run-voicemake.ps1` 在 Windows 上默认使用 `MinGW-w64` 工具链构建 Rust 语音服务，额外需要：

- `Rust` / `cargo`
- `CMake`
- `MinGW-w64`（需提供 `gcc.exe`、`g++.exe`、`mingw32-make.exe`）
- `ffmpeg` 并加入 `PATH`

如工具未加入 `PATH`，也可以在 `tsbot.env` 中额外配置这些路径：

- `TSBOT_CARGO`
- `TSBOT_CMAKE`
- `TSBOT_MINGW_BIN`
- `TSBOT_FFMPEG`

如果暂时没有这些工具，后端和前端仍然可以正常启动，但播放控制相关接口会因为 gRPC 语音服务未启动而不可用。

## Docker 运行

项目根目录已提供：

- `docker-compose.yml`
- `docker-compose.prebuilt.yml`
- `Dockerfile.backend`
- `Dockerfile.voice-service`
- `Dockerfile.web`

### 1. 准备配置

```bash
cp tsbot.env.example tsbot.env
```

> 如果 `NeteaseCloudMusicApi` 跑在宿主机，请在容器启动后通过 Web 系统配置将地址设为 `http://host.docker.internal:3000/`。

### 2. 构建并启动

```bash
docker compose up -d --build
```

### 3. 查看运行状态

```bash
docker compose ps
docker compose logs -f backend
docker compose logs -f web
```

### 4. 停止并清理容器

```bash
docker compose down
```

默认端口映射：

- `50051:50051`（voice-service gRPC）
- `8009:8009`（backend）
- `8080:8080`（web，Nginx 托管生产前端产物，并将 `/api/*` 反向代理到 backend）

### 5. 使用预构建镜像（Docker Hub / GHCR）

如果你不想在本机构建，也可以直接使用仓库发布的预构建镜像。项目额外提供了 `docker-compose.prebuilt.yml`：

```bash
# Docker Hub（默认 latest）
docker compose -f docker-compose.prebuilt.yml up -d

# 固定版本，例如 v0.4.0
TSBOT_IMAGE_TAG=v0.4.0 docker compose -f docker-compose.prebuilt.yml up -d

# 改用 GHCR
TSBOT_IMAGE_REGISTRY=ghcr.io \
TSBOT_IMAGE_NAMESPACE=yumi118 \
docker compose -f docker-compose.prebuilt.yml up -d
```

补充说明：

- GitHub **Packages** 页面显示的是 GHCR 包；如果只推 Docker Hub，这里会是空的。
- 现在看到 `backend` / `web` / `voice-service` 三个镜像仓库是正常的，因为当前发布策略就是按三个服务分别构建。
- GitHub **Releases** 页面里的 `tar.gz` 与 `SHA256SUMS.txt` 是软件包归档，不是 Docker 镜像。

## 启动配置与 Web 系统配置

编辑 `tsbot.env` 文件：

```env
# 后端服务配置
TSBOT_HOST=127.0.0.1
TSBOT_PORT=8009
TSBOT_VOICE_GRPC_ADDR=127.0.0.1:50051
TSBOT_COOKIE_KEY=change_me_to_a_random_string
TSBOT_VOICE_CONFIG_FILE=./logs/voice-service.json
TSBOT_INITIAL_PASSWORD_FILE=./logs/initial-admin-password.txt

# 前端生产服务配置（run-web.sh / nohup-start.sh 使用）
TSBOT_WEB_HOST=127.0.0.1
TSBOT_WEB_PORT=8080
# TSBOT_WEB_API_PROXY_TARGET=http://127.0.0.1:8009
# TSBOT_WEB_ALLOWED_HOSTS=dev.example.com,.example.com

# 前端开发服务配置（npm run dev 使用）
VITE_DEV_HOST=127.0.0.1
VITE_DEV_PORT=5173

# 前端 API Base（推荐默认 /api，由 dev / preview / Docker 反向代理到 backend）
VITE_API_BASE=/api
# VITE_WEB_PUBLIC_URL=https://music.example.com
# 数据库配置 (可选)
DATABASE_URL=sqlite:///./tsbot.db
```

说明：

- 首次启动后，从后端日志或 `logs/initial-admin-password.txt` 取得 `admin` 的初始密码。
- 第一次登录会强制更换密码，成功后初始密码文件自动删除。
- TeamSpeak / TS6、网易云 API、缓存、日志、外部 Token、界面名称都在 Web 的“系统配置”中维护。
- 界面图标和 TeamSpeak 机器人头像在 Web 设置页直接上传，固定保存在数据库目录旁的 `uploads/` 中；不再填写服务器图片路径。
- “保存配置”只将表单持久化到数据库；“应用配置”才会更新运行服务。应用 TeamSpeak 或 Voice 改动后，voice-service 会优雅断开并自动用新配置重启，Web 在新进程返回后提示“已重启成功”。
- “TeamSpeak 配置”包含连接、频道、身份和客户端简介；“Voice 服务”包含后端到 Voice 的连接与 Voice 运行参数。
- 旧部署的 `TSBOT_TS3_*` 等变量会在数据库缺少对应值时导入一次，迁移后可从环境文件删除。
- QQ 音乐能力由后端内建提供，不需要额外部署独立的 QQ 音乐 API 服务。
- 网易云、QQ 音乐和 B 站授权统一位于“系统配置 → 音乐会员登录”；管理员 Cookie 写入数据库并使用 `TSBOT_COOKIE_KEY` 加密存储。
- 忘记密码可在服务器本地执行 `.venv/bin/python -m backend.admin_cli reset-password`。

## 音乐源支持

### 网易云音乐

- 依赖外部 `NeteaseCloudMusicApi` 服务。
- 需要在 Web 系统配置中填写该服务地址。

### QQ 音乐

- 搜索、歌单、歌词等能力由后端直接提供。
- 播放链接、用户歌单等登录态能力建议在 Web 控制台中扫码登录 QQ 音乐。
- 管理员平台授权接口使用 Web 登录会话保护，不再使用长期 `x-admin-token`。

## 访问应用

启动成功后，访问：
- **前端界面（生产脚本 / Docker）**: http://127.0.0.1:8080 (可通过 `TSBOT_WEB_PORT` 修改；Docker Compose 默认也使用 `8080`)
- **前端界面（本地开发）**: http://127.0.0.1:5173 (可通过 `VITE_DEV_PORT` 修改)
- **后端 API**: http://127.0.0.1:8009 (可通过 `TSBOT_PORT` 环境变量修改端口)
- **API 文档**: http://127.0.0.1:8009/docs

## 故障排除

### 常见问题

1. **Python 依赖安装失败**
   ```bash
   # 更新 pip
   pip install --upgrade pip
   
   # 安装系统依赖
   sudo apt install python3-dev python3-pip
   ```

2. **Node.js 依赖安装失败**
   ```bash
   # 清理缓存
   npm cache clean --force
   rm -rf web/node_modules web/package-lock.json
   cd web && npm install
   ```

3. **语音服务构建失败**
   ```bash
   # 安装缺失的依赖
   sudo apt install libssl-dev pkg-config
   
   # 重新构建
   cargo clean --manifest-path voice-service/Cargo.toml
   make voice-build
   ```

4. **TeamSpeak 连接失败**
   - 检查 TeamSpeak 服务器地址、端口、频道配置和密码
   - 默认 Rust `voice-service` 不需要 TS3 Client SDK；只有旧版 C++ 路径才依赖它
   - 检查防火墙设置

### 日志查看
```bash
# 查看后端日志
tail -f logs/backend.log

# 查看语音服务日志
tail -f logs/voice.log
```

## 开发模式

### 热重载开发
```bash
# 后端热重载
source backend/.venv/bin/activate
uvicorn backend.main:app --reload

# 前端热重载
cd web && npm run dev
```

### 代码生成
```bash
# 重新生成 gRPC 代码
python backend/grpc_codegen.py
```

## 生产部署

### 使用 systemd 服务
当前仓库**未内置** `systemd` service 模板。若你需要以 systemd 托管，请自行创建 service，并分别调用：

- `run-voicemake.sh`
- `run-backend.sh`
- `run-web.sh`

如果只是单机或轻量部署，优先使用上面的 `nohup-start.sh` / `nohup-stop.sh` / `nohup-status.sh`。

### 使用 Nginx 反向代理
```nginx
server {
    listen 80;
    server_name your-domain.com;
    
    location / {
        proxy_pass http://127.0.0.1:8080;
    }
    
    location /api {
        proxy_pass http://127.0.0.1:8009;
    }
}
```

## 更多信息

- 查看 `TODO` 文件了解开发计划
- 查看 `LICENSE` 文件了解许可证信息
- 遇到问题请提交 Issue
