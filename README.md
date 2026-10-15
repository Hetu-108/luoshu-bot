<div align="center">

# 洛书 · Luoshu Bot

**一个安静慢热、聊开了话很多的 QQ 群聊机器人**

基于 [NoneBot2](https://nonebot.dev) + OneBot v11 ｜ 本地大模型驱动 ｜ 数据不出本机

</div>

---

## 这是什么

洛书是一个住在 QQ 群里的女生：平时安静、慢热、说话有分寸，
但话题对路时会明显话多起来，会接梗、会吐槽，也有自己的喜好和看法。
她和群里每个人都是**平等的朋友**，谁都可以喊她的名字和她聊天。

```
洛书 讲个笑话
洛书 今天心情不太好，陪我说说话
```

如果说话的人是她比较熟的那一位，她的语气会比平时放松、亲近一点点（仅此而已）。

**开箱即用**：本地大模型驱动，不依赖任何付费接口，聊天数据不出本机。

---

## 安装

### 前置条件

需要准备三样东西：**Python**、**Ollama**（本地大模型服务）、**登录端**（让机器人登入 QQ）。

| 项目 | 要求 | 获取方式 |
| --- | --- | --- |
| Python | **3.10 或更高** | [python.org](https://www.python.org/downloads/)；安装时记得勾选 **Add to PATH** |
| Ollama | 最新版 | 见下方「① 安装 Ollama」 |
| 登录端 | 任意 [OneBot v11](https://onebot.dev) 实现 | 见下方「② 准备登录端」 |

#### ① 安装 Ollama

从官网下载并安装：[https://ollama.com/download](https://ollama.com/download)

- **Windows**：下载 `OllamaSetup.exe`，双击安装（装完自动常驻，开机自启）
- **macOS**：下载 `.dmg`，或 `brew install ollama`
- **Linux**：

  ```bash
  curl -fsSL https://ollama.com/install.sh | sh
  ```

装好后验证（能打印版本号即可）：

```bash
ollama --version
```

#### ② 准备登录端

登录端（OneBot 实现）负责让机器人登入 QQ，**不包含在本仓库内**，需要单独准备。常见选择：

| 登录端 | 说明 |
| --- | --- |
| **LLOneBot / LLNT** | QQ 插件形式（如 LiteLoaderQQNT），配置简单，许多人的首选 |
| **NapCat** | 同样基于 QQNT，社区活跃，文档完善 |
| **Lagrange.Core** | 独立程序，无需装 QQ 客户端 |
| **go-cqhttp** | 老牌方案（现已基本停更，仅作了解） |

任选其一，按它自己的文档装好并登录账号，然后把它配置成 **ws-reverse（反向 WebSocket）**，地址填：

```
ws://127.0.0.1:8080/onebot/v11/ws
```

> 端口取 `.env` 里的 `PORT`（默认 `8080`）。
> 如果登录端和 bot **不在同一台机器**，把地址里的 `127.0.0.1` 换成 bot 所在机器的 IP，
> 并把 `.env` 的 `HOST` 改成 `0.0.0.0`（详见下方 Linux 一节）。

### 1. 获取代码

```bash
git clone https://github.com/Hetu-108/luoshu-bot.git
cd luoshu-bot
```

> 不想用 git，也可以直接点仓库页面的 **Code → Download ZIP** 下载解压。
> 想装某个已发布版本，见 [Releases](https://github.com/Hetu-108/luoshu-bot/releases)。

### 2. 拉取模型

确保 Ollama 已安装（见上方「① 安装 Ollama」），然后下载模型：

```bash
ollama pull qwen3-vl:4b
```

**模型怎么选**（拉完在 `.env` 里改 `OLLAMA_MODEL` 即可切换）：

| 模型 | 大小 | 说明 |
| --- | --- | --- |
| `qwen3-vl:4b` | 约 3.3 GB | **推荐**，新一代，支持图文，速度与效果均衡 |
| `qwen3-vl:8b` | 约 6 GB | 效果更好，需要更多显存 |
| `qwen2.5:7b` | 约 4.7 GB | 旧版纯文本模型，效果一般，可作备选 |
| `qwen2.5:3b` | 约 2 GB | 快，效果一般，老机器 / 纯 CPU 可用 |

> 查已装模型：`ollama list`。
> 注：`qwen3-vl` 是「思考型」模型，默认会先推理再作答。机器人会自动剥离推理过程，
> 只把最终回复发出去，无需额外配置。

### 3. Windows

```text
1. 双击「安装依赖.bat」   （首次，创建虚拟环境并安装依赖）
2. 双击「启动bot.bat」
```

### 4. Linux

**① 装好 Python 与 venv**

```bash
# Ubuntu / Debian
sudo apt install python3 python3-venv python3-pip

# CentOS / RHEL
sudo yum install python3 python3-pip
```

**② 安装依赖**

```bash
chmod +x install.sh start.sh
./install.sh
```

脚本会自动找 Python 3.10+、创建 `.venv`、装依赖，**不会污染系统 Python**。

**③ 准备配置**

```bash
cp .env.example .env
```

重点确认两项（其余见下方「配置」）：

| 配置 | 什么时候改 |
| --- | --- |
| `HOST` | 登录端也在本机 → 保持 `127.0.0.1`；登录端在**另一台机器** → 改成 `0.0.0.0`，并**务必在防火墙只放行可信来源** |
| `OLLAMA_URL` | Ollama 在同机 → `http://127.0.0.1:11434`；在别的机器 → 填那台的地址 |

登录端那边要连 `ws://<本机IP>:8080/onebot/v11/ws`（端口取 `.env` 的 `PORT`）。

**④ 启动**

```bash
./start.sh
```

临时后台运行：

```bash
nohup ./start.sh > luoshu.log 2>&1 &
```

**⑤ 常驻运行（可选，推荐）**

想开机自启 + 崩溃自动重启，用仓库里的 systemd 模板：

```bash
# 1. 建一个专用账号（别用 root）
sudo useradd -r -m -d /opt/luoshu-bot -s /usr/sbin/nologin luoshu

# 2. 把项目放到 /opt/luoshu-bot 并给权限
sudo chown -R luoshu:luoshu /opt/luoshu-bot

# 3. 复制服务文件，改 User / WorkingDirectory / ExecStart 三处
sudo cp luoshu.service.example /etc/systemd/system/luoshu.service
sudo nano /etc/systemd/system/luoshu.service

# 4. 启用
sudo systemctl daemon-reload
sudo systemctl enable --now luoshu

# 5. 看状态与日志
systemctl status luoshu
journalctl -u luoshu -f
```

常用操作：`sudo systemctl restart|stop luoshu`。

> ⚠️ 两个坑：
> - **`.venv` 不能跨系统拷贝** —— Windows 建的 `.venv` 在 Linux 下用不了，必须在 Linux 上重新跑 `./install.sh`。
> - **用了 systemd 就别再叠加 `nohup`** —— 二选一即可。

启动成功会看到：

```
[SUCCESS] nonebot | Succeeded to load plugin "ai_chat"
```

---

## 配置

所有配置集中在 `.env`（从 `.env.example` 复制一份，等号两边不要加空格，JSON 数组用双引号）。

### 最常改的两项

```ini
# 管理员 QQ（可多个）—— 谁拥有管理权限
SUPERUSERS=["100000000"]

# 洛书「熟人」的称呼（可多个，逗号分隔）——语气会比别人稍微亲近一点点
CRUSH_NAMES=小美
```

**想让洛书对别人更亲近？** 改上面两行就行，不用碰代码：

```ini
SUPERUSERS=["他的QQ号"]
CRUSH_NAMES=他的名字
```

### 其余常用项

```ini
NICKNAME=["洛书", "洛"]          # 机器人昵称
CALL_NAMES=洛书,洛洛,小洛,洛      # 喊名字唤醒用的称呼
OLLAMA_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen3-vl:4b          # 使用的模型
AI_MAX_HISTORY=20                # 记住多少轮对话
AI_AT_SENDER=false               # AI 回复是否 @ 对方
GREETING_ENABLED=true            # 是否开启定时问好
```

改完保存，**重启机器人**生效。

> 完整配置项、维护方法和常见问题见 [`使用说明.txt`](使用说明.txt)。

---

## 功能

> 记不住命令？发 `/help 功能名` 就能看到某个功能**到底是干什么的**——
> 它做什么、怎么用、给个例子。中文名、命令词、常用叫法都能查到
> （`/help 统计`、`/help stat`、`/help say` 都行）。

### 💬 AI 对话（所有人）
`@洛书 <内容>` 或 `洛书 <内容>` 直接开聊，带上下文记忆。
另有 `/人设`（看有哪些性格）、`/切换`（换性格）、`/reset`（忘记你）、`/history`（看会话状态）。

### 🎲 娱乐
`/抽签`（抽心情签）、`/运势`（今日运势卡：桃花/财运/宜忌）、`/塔罗`（抽牌解读，可加"三张"）。
都可 @某人 帮对方抽。

### 💗 心动专属
仅「她比较熟的那位」可用：喊名字、被夸、早晚问安、`/心事`。
语气只比别人亲近一点点，不会肉麻，也不涉及任何暧昧表达。

### 🛠 管理（群主 / 群管理员 / 管理员）
- `/统计` —— 看本群多少人说话、发了多少条、哪个指令最热门（日/周/月）
- `/关键词` —— 教洛书自动回复：有人发某个词，她就回一句
- `/功能` —— 在本群关掉/打开某个功能，别的群不受影响
- `/禁言` —— 把自己禁言一会儿；`whoami` —— 看自己什么权限

### 🎛 群功能开关
每个群可以**独立**开关功能，互不影响：`/功能` 看状态，
`/功能 关闭 运势 塔罗` 在本群关掉，`/功能 开启` 再打开，`/功能 重置` 全部恢复。
仅群主 / 群管理员 / 管理员可改本群；帮助与管理命令不可关闭，防止误锁。

### 🔧 管理员专属
- `/model` —— 给 AI 换个本地模型（先发 `/model` 看本机有哪些）
- `/stat` —— 机器人自身体检：系统 / 内存 / CPU / 群数 / 开机时长
- `/say` —— 借洛书的嘴原样发一句话（发通知用，不走 AI 生成）

### ✨ 自动
新人进群欢迎、退群提示、戳一戳回应、每日定时问好、掉线私聊告警。

---

## 技术特性

- **本地大模型**：AI 对话走 Ollama，不依赖付费接口，**聊天数据不出本机**
- **四级权限**：管理员 > 群主 > 群管理员 > 普通成员，越权直接拦截
- **多群隔离**：关键词、统计数据、功能开关均按群独立存放
- **内容安全**：敏感词库过滤 AI 的输入与输出，且能识别**插空格/符号的变形**写法
- **稳定可靠**：会话并发安全、JSON 原子写入、长期运行自动回收过期数据
- **跨平台**：Windows / Linux 均可

代码规模：14 个插件模块，约 2400 行。

---

## 项目结构

```text
洛书bot/
├── bot.py                      程序入口
├── pyproject.toml              NoneBot 项目配置
├── requirements.txt            依赖清单
├── .env.example                配置模板
├── install.sh / start.sh       Linux 安装与启动脚本
├── luoshu.service.example      systemd 服务模板
├── 使用说明.txt                 完整使用手册
├── CHANGELOG.md                更新日志
├── 启动bot.bat / 安装依赖.bat    Windows 脚本
├── selftest.py                 自检脚本
└── src/plugins/                各功能插件（ai_chat / fun / fortune …）
```

---

## 更新日志

支持 `/识图 问题` 随消息附图、回复图片提问，也可在群内 @机器人附图或私聊直接发图。
可描述图片、读取清晰截图文字，共用AI对话开关；使用方式见 [识图说明](docs/reliability.md#识图)。

新增督师科普答题：人工录题、JSON/Excel导入、每日定时发题、私聊作答、截止结算、成绩导出和腾讯文档待同步队列。
活动默认关闭，配置、命令、隐私与验收范围见 [答题使用说明](docs/quiz.md)。

各版本的变更记录见 [`CHANGELOG.md`](CHANGELOG.md)。当前版本：**v1.3.3**。

---

## 由来与致谢

这个项目是从 **lhcbot** 学出来的。

lhcbot 是一套很完整的 QQ 机器人方案，洛书 bot 正是在学习它的过程中逐渐成型的 —— 沿用了它「OneBot 账号登录 + NoneBot 主机」的整体思路，参考了它组织插件、处理权限与消息的方式，并在此基础上重新梳理结构、重写功能：把原本依赖的**云端大模型接口**换成了**本地 Ollama**，重整了权限分级与多群隔离，并为洛书设计了完整的人格设定。

感谢 lhcbot 提供的灵感与基础。

---

<div align="center">

*嗯……谢谢你看到这里。有空来群里找洛书聊天呀。*

</div>
