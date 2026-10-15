# 洛书 QQ 机器人 —— Ubuntu 部署与启动指南

> **本文档用途**：在 Ubuntu 服务器上从零部署并启动「洛书」QQ 机器人。
> **执行方式**：文档按步骤编号，**每一步都是可复制的完整命令**，每步末尾附「验证」。
> 可以由人照着敲，也可以交给 AI 编码助手（Codex / Claude Code 等）在服务器上逐条执行。
> **执行约定**：
> 1. 命令按顺序执行，**不要跳步**；上一步的「验证」通过后再进入下一步。
> 2. 需要系统权限的命令已标 `sudo`。请确保当前账号有 sudo 权限。
> 3. 文档中形如 `<这样>` 的尖括号内容是**占位符，必须替换成真实值**。
> 4. 遇到报错先查第 6 章「故障排查」，不要自行改动项目源码。

---

## 1. 先理解：洛书不是一个程序，是三个

洛书机器人本身只是中间那一环。**至少要运行三个组件**，缺任何一个，QQ 里都不会有回复。

| 组件 | 作用 | 端口 | 是否必须在 Ubuntu 上 |
| --- | --- | --- | --- |
| **NapCat**（登录端） | 用 QQ 小号登录，把 QQ 消息转成 OneBot 协议 | WebUI `6099` | 是（Linux 版）/ 也可留在另一台电脑 |
| **洛书 bot**（本项目） | 接收消息、调用模型、生成回复 | `8080` | 是 |
| **Ollama** | 本地大模型，负责生成回复文字 | `11434` | 是 / 也可在另一台机器 |

数据流向：

```
QQ 服务器  →  NapCat 登录端  →  洛书 bot (:8080)  →  Ollama (:11434)  →  回复文字
```

**关键认知**：`洛书 bot` 必须和 `NapCat` 建立 WebSocket 连接才能工作。连接建立后，日志里会出现
`OneBot V11 | Bot <QQ号> connected`。没有这一行，说明两者没接上，此时群里发什么都没反应。

---

## 2. 前置条件

| 项 | 要求 | 检查命令 |
| --- | --- | --- |
| 系统 | Ubuntu 20.04 / 22.04 / 24.04 或同系 Debian | `lsb_release -a` |
| 权限 | 当前账号可用 sudo | `sudo -v` |
| Python | 3.10 或更高 | `python3 --version` |
| 磁盘 | 至少 10 GB 空闲（模型占约 3.3 GB） | `df -h /` |
| 网络 | 能访问 GitHub 与 ollama.com | `curl -sI https://github.com \| head -1` |

### 2.1 需要提前确认的两个决策项

**决策 A —— NapCat 是否也在这台 Ubuntu 上？** 这决定 `HOST` 怎么配：

| 情况 | `HOST` 配置值 |
| --- | --- |
| NapCat 也在这台 Ubuntu 上（推荐） | `127.0.0.1` |
| NapCat 在**另一台机器**上 | `0.0.0.0` **并且必须在防火墙只放行那台机器的 IP** |

> ⚠️ `8080` 端口等价于「你的 QQ 号的操作权」。配成 `0.0.0.0` 却对公网敞开，等于把 QQ 号送人。
> 如果选择 `0.0.0.0`，务必同步配置安全组/`ufw`，只放行 NapCat 所在机器的 IP。

**决策 B —— 管理员 QQ 号是多少？** 后面 `SUPERUSERS` 要填。填错的话，管理命令（`/say`、`/model` 等）会用不了，但普通聊天不受影响。

---

## 3. 部署步骤

### 步骤 1 —— 进入项目目录，清掉 Windows 留下的虚拟环境

```bash
cd <项目目录>          # 例：cd /opt/luoshu-bot
pwd                    # 确认目录正确，里面应能看到 bot.py、requirements.txt
rm -rf .venv
```

**为什么必须删**：如果项目是从 Windows 拷/压缩包传过来的，`.venv` 里是
`Scripts/Activate.ps1` 这种 Windows 结构，Linux 下没有可执行的 `bin/python`，
`./start.sh` 会直接报错退出。`install.sh` 不会自动覆盖已存在的 `.venv`，必须手动删。

**验证**：

```bash
ls .venv 2>&1        # 预期输出：ls: cannot access '.venv': No such file or directory
```

---

### 步骤 2 —— 装系统依赖

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip curl
```

**验证**：

```bash
python3 --version    # 预期：Python 3.10.x 或更高
python3 -m venv --help > /dev/null && echo "venv 可用"
```

> 如果 `python3 --version` 低于 3.10，需要先升级 Python（Ubuntu 20.04 自带 3.8，可通过
> `sudo apt install python3.10 python3.10-venv` 或 deadsnakes PPA 解决）。

---

### 步骤 3 —— 安装 Ollama 并拉取模型

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama --version
sudo systemctl enable --now ollama     # 若安装脚本未自动启动服务
ollama pull qwen3-vl:4b                # 约 3.3 GB，下载需要几分钟
```

**验证**：

```bash
systemctl is-active ollama                                   # 预期：active
curl -s http://127.0.0.1:11434/api/tags | grep -o qwen3-vl:4b # 预期：输出 qwen3-vl:4b
```

> **模型规格**：`qwen3-vl:4b` 是推荐值（约 3.3 GB，支持图文，速度与效果均衡）。
> 显存/内存紧张可换 `qwen2.5:3b`（约 2 GB）；追求效果可换 `qwen3-vl:8b`（约 6 GB，需更多显存）。
> 换模型时记得同步改 `.env` 的 `OLLAMA_MODEL`。

---

### 步骤 4 —— 安装项目依赖

```bash
chmod +x install.sh start.sh
./install.sh
```

脚本会自动检测 Python 3.10+、创建 `.venv`、安装 `requirements.txt` 里的依赖，
**不会污染系统 Python**。首次执行较慢（需下载 nonebot2 等包），请耐心等待。

**验证**：

```bash
./.venv/bin/python --version                          # 预期：Python 3.1x.x
./.venv/bin/python -c "import nonebot; print('nonebot OK')"
```

---

### 步骤 5 —— 配置 `.env`

> ⚠️ **注意**：`.env` 被 `.gitignore` 排除，**如果你是 `git clone` 下来的，仓库里根本没有这个文件**，
> 必须从模板创建。

```bash
cp .env.example .env
nano .env
```

#### 必须修改的配置项

| 配置项 | 填什么 | 不填的后果 |
| --- | --- | --- |
| `SUPERUSERS` | `["<管理员QQ号>"]` | 管理命令无法使用 |
| `NICKNAME` | `["洛书"]` | **见下方警告** |
| `CALL_NAMES` | `洛书,洛洛,小洛,洛` | 喊名字唤不醒 |
| `OLLAMA_MODEL` | `qwen3-vl:4b` | 与已拉取模型不一致会报错 |
| `HOST` | 见 2.1 决策 A | 配错则 NapCat 连不上 |

> ⚠️ **`NICKNAME` 只能写 `["洛书"]`，不要写成 `["洛书", "洛"]`。**
> 原因：底层框架会把这个列表拼成正则来识别「有人叫机器人」，而列表顺序每个进程随机。
> 同时配「洛书」和「洛」时，有可能先匹配到「洛」，把「洛书 你好」截成「书 你好」，
> 导致**喊名字唤不醒**。名字集合不会因此变少 —— 「洛」已经在 `CALL_NAMES` 里了。

#### 一份可直接替换的完整示例

```ini
ENVIRONMENT=prod
HOST=127.0.0.1
PORT=8080
LOG_LEVEL=INFO
FASTAPI_RELOAD=false

SUPERUSERS=["<管理员QQ号>"]
CRUSH_NAMES=<一个名字，可留默认>

NICKNAME=["洛书"]
CALL_NAMES=洛书,洛洛,小洛,洛

COMMAND_START=["/"]
COMMAND_SEP=["."]

SESSION_EXPIRE_TIMEOUT=PT1M

OLLAMA_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen3-vl:4b
OLLAMA_TIMEOUT=180

AI_MAX_HISTORY=20
AI_SESSION_TTL_DAYS=30
AI_AT_SENDER=false

GREETING_ENABLED=true
GREETING_GROUPS=[]
```

**验证**（确认文件已建且关键项没写错）：

```bash
grep -E '^(SUPERUSERS|NICKNAME|HOST|PORT|OLLAMA_MODEL)=' .env
```

> 补充说明：`.env` 里含真实 QQ 号等隐私信息，**不要提交到公开仓库**。项目默认已在
> `.gitignore` 里排除，不要用 `git add -f` 强制添加。

---

### 步骤 6 —— 前台启动，确认能跑起来

先在前台跑一次，这样报错能直接看到：

```bash
./start.sh
```

**预期输出**（关键两行）：

```
[SUCCESS] nonebot | Succeeded to load plugin "ai_chat"
[INFO] uvicorn | Uvicorn running on http://127.0.0.1:8080
```

如果 NapCat 已在运行，还会紧接着出现：

```
[INFO] nonebot | OneBot V11 | Bot <QQ号> connected
```

确认无误后按 `Ctrl + C` 停掉，进入下一步（改成常驻运行）。

**验证（另开一个终端执行）**：

```bash
ss -lntp | grep 8080     # 预期：能看到 8080 处于 LISTEN 状态
```

---

### 步骤 7 —— 配置成 systemd 服务（开机自启 + 崩溃自动重启）

服务器上长期运行，**推荐用 systemd**，不要用 `nohup`。

```bash
# 1. 复制服务模板
sudo cp luoshu.service.example /etc/systemd/system/luoshu.service

# 2. 编辑，需要改三处：User / Group、WorkingDirectory、ExecStart
sudo nano /etc/systemd/system/luoshu.service
```

需要修改的三处（其余保持模板原样）：

```ini
User=<运行账号>                          # 例：ubuntu
Group=<运行账号>                         # 例：ubuntu
WorkingDirectory=<项目绝对路径>           # 例：/opt/luoshu-bot
ExecStart=<项目绝对路径>/.venv/bin/python <项目绝对路径>/bot.py
```

> 模板默认值写的是 `/opt/luoshu-bot` 和用户 `luoshu`，改成你的实际情况。
> `WorkingDirectory` 和 `ExecStart` **必须用绝对路径**，不能用 `~` 或相对路径。

```bash
# 3. 加载并启用
sudo systemctl daemon-reload
sudo systemctl enable --now luoshu
```

**验证**：

```bash
systemctl status luoshu --no-pager        # 预期：Active: active (running)
journalctl -u luoshu -n 30 --no-pager     # 预期：能看到 uvicorn running 与 Bot connected
```

> **注意**：用了 systemd 之后，**不要再叠加 `nohup ./start.sh &`**，两者会抢占同一个
> `8080` 端口，导致其中一个启动失败。二选一即可。

---

### 步骤 8 —— 端到端验收

按顺序检查，全部通过才算部署成功。

```bash
# ① Ollama 活着吗
curl -s http://127.0.0.1:11434/api/tags | grep -o qwen3-vl:4b
# 预期：qwen3-vl:4b

# ② bot 在监听吗
ss -lntp | grep 8080
# 预期：LISTEN 状态

# ③ NapCat 连上来了吗（关键）
journalctl -u luoshu -n 100 --no-pager | grep "OneBot V11"
# 预期：OneBot V11 | Bot <QQ号> connected
```

然后在 QQ 群里（用另一个账号，或让别人帮忙）发两条消息：

```
@洛书 你好
洛书 你好
```

**验收标准**：机器人有回复，且日志显示是 `ai_chat` 插件在处理：

```bash
journalctl -u luoshu -n 50 --no-pager | grep "handled by"
# 预期：Event will be handled by Matcher(module=src.plugins.ai_chat)
```

> 首次回复可能要等几秒 —— 本地模型需要先加载进内存，属正常现象。

---

### 步骤 9 —— 运行内置自检

```bash
./.venv/bin/python selftest.py
```

**预期**：跑完约 40 秒，输出各功能的测试结果，末尾无 `异常` 计数。
这一步会真实调用模型，可以确认整条链路（bot → Ollama → 回复）是通的。

---

## 4. 日常运维命令

```bash
sudo systemctl restart luoshu      # 改完配置后重启
sudo systemctl stop luoshu         # 停止
sudo systemctl start luoshu        # 启动
sudo systemctl disable luoshu      # 取消开机自启
systemctl status luoshu            # 看状态
journalctl -u luoshu -f            # 实时看日志（排障最常用）
journalctl -u luoshu --since "10 min ago"   # 看最近 10 分钟日志
```

**更新代码后**：`git pull` → 如果 `requirements.txt` 有变动则 `./install.sh` → `sudo systemctl restart luoshu`。

---

## 5. 数据与文件说明

| 路径 | 内容 | 是否需要备份 |
| --- | --- | --- |
| `.env` | 真实配置（QQ 号、群号） | **要**，且不能外泄 |
| `data/` | 聊天记忆、统计、功能开关 | **要** |
| `data/ai_sessions.json` | AI 对话上下文 | 可选 |
| `.venv/` | 虚拟环境 | 不用，可随时重建 |
| `bot.out.log` | 前台运行的输出日志 | 不用 |

> ⚠️ `.env` 和 `data/` 都被 `.gitignore` 排除。**如果项目是从 `git clone` 拿的，
> 这两个都是空的**：
> - `.env` 需要按步骤 5 自己创建；
> - `data/` 里是空的，之前 Windows 上的聊天记忆、统计、功能开关**不会跟过来**。
>   想保留旧数据，需要把原机器上的 `data/` 整个目录拷到项目根目录下。

---

## 6. 故障排查

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `[错误] 未找到虚拟环境 .venv/bin/python` | 用的是 Windows 建的 `.venv` | `rm -rf .venv && ./install.sh` |
| 启动报端口占用 | 重复启动了（systemd + nohup 同时开） | 任选一种方式；`ss -lntp \| grep 8080` 找到占用进程后停掉 |
| `ss -lntp \| grep 8080` 无输出 | 进程没起来或已崩溃 | `journalctl -u luoshu -n 50 --no-pager` 看报错 |
| 群里发消息**完全没反应**，日志里也没有消息记录 | NapCat 没连上 | 检查 `HOST` 配置、NapCat 的 WebSocket 地址是否为 `ws://<本机IP>:8080/onebot/v11/ws`；看日志有没有 `Bot connected` |
| 日志有消息记录，但只有 `stats` 在处理 | 唤醒词没匹配上 | 检查 `.env` 的 `NICKNAME` 是否为 `["洛书"]`、`CALL_NAMES` 是否包含喊法；改完重启 |
| 日志出现 `[notice.bot_offline] 你的账号当前登录已失效` | QQ 账号被踢下线（第三方客户端风控，或同号在其他设备登录） | 在 NapCat 里重新扫码登录；**登录期间不要用手机/其他设备登同一个号** |
| 回复报模型相关错误 | 模型名和已拉取的不一致 | `ollama list` 核对，然后改 `.env` 的 `OLLAMA_MODEL` 并重启 |
| Ollama 连不上 | 服务没起，或不在本机 | `systemctl status ollama`；不在本机则改 `OLLAMA_URL` |
| 回复很慢（十几秒以上） | 首次加载模型 / 机器性能不足 | 首次属正常；持续慢可换更小的模型（`qwen2.5:3b`） |
| 机器人偶尔答非所问 | 小参数模型的固有能力上限 | 换 `qwen3-vl:8b` 可改善，非配置问题 |

**排障通用顺序**：

```bash
systemctl status luoshu --no-pager            # ① 进程活着吗
journalctl -u luoshu -n 50 --no-pager         # ② 日志报什么错
ss -lntp | grep 8080                          # ③ 端口在听吗
curl -s http://127.0.0.1:11434/api/tags       # ④ 模型服务在吗
```

---

## 7. 附录：项目文件速查

```
<项目目录>/
├── bot.py                    主程序（入口）
├── install.sh                Linux 依赖安装脚本
├── start.sh                  Linux 启动脚本
├── luoshu.service.example    systemd 服务模板
├── selftest.py               自检脚本
├── requirements.txt          Python 依赖清单
├── .env                      实际配置（需自己创建，不上传）
├── .env.example              配置模板（可上传）
├── src/plugins/              各功能插件（ai_chat 负责 AI 对话）
├── data/                     运行数据（不上传，需单独备份）
├── docs/                     文档
└── .venv/                    虚拟环境（不上传，可重建）
```

---

## 8. 一句话总结部署流程

```
删掉旧 .venv  →  装 Python/Ollama  →  拉模型  →  ./install.sh
   →  配 .env（尤其 NICKNAME 只留「洛书」、SUPERUSERS 填真实 QQ）
   →  前台 ./start.sh 跑通  →  配 systemd 常驻
   →  群里 @洛书 你好，日志出现 ai_chat 即成功
```

**最后提醒**：部署完成后如果 QQ 里没反应，**先看日志有没有 `OneBot V11 | Bot ... connected`**。
没有这一行，问题一定在 NapCat 连接上，而不是洛书本身。
