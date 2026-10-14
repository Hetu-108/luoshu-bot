# python3
# -*- coding: utf-8 -*-
# @File    : ai_chat.py
# @Usage   : AI 对话插件 —— 对接本地 Ollama 大模型
#
# 功能：
#   @机器人 说话        → 与该用户进行 AI 对话（带记忆）
#   /ai <内容>          → 同上（命令式触发）
#   /切换 <人设名>      → 切换该用户的 AI 人设
#   /人设               → 查看当前人设和可用人设列表
#   /reset              → 清空该用户的对话历史
#   /history            → 查看该用户的对话历史条数
#   /model <模型名>     → 切换使用的模型（仅管理员）
#
# 记忆按「用户 + 群」隔离，持久化到 data/ai_sessions.json
# 输入输出均做敏感词过滤（见 security.py）
import json
import os
import asyncio
from pathlib import Path

import httpx
from nonebot import on_message, on_command, get_driver, get_bots
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent, Message
from nonebot.rule import Rule
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.log import logger

from .personas import PERSONAS, DEFAULT_PERSONA
from .core import check_cooldown, log_command, log_event, notify_owner, _OWNER_QQ
from .security import has_sensitive, filter_text
from .features import guard

# ==================== 配置读取 ====================
driver = get_driver()
cfg = driver.config

OLLAMA_URL = str(getattr(cfg, "ollama_url", "http://127.0.0.1:11434")).rstrip("/")
OLLAMA_MODEL = str(getattr(cfg, "ollama_model", "qwen2.5:7b"))
OLLAMA_TIMEOUT = float(getattr(cfg, "ollama_timeout", 180))
AI_MAX_HISTORY = int(getattr(cfg, "ai_max_history", 20))
AI_AT_SENDER = str(getattr(cfg, "ai_at_sender", "false")).lower() in ("true", "1", "yes")

# ==================== 会话持久化 ====================
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
SESSION_FILE = DATA_DIR / "ai_sessions.json"

_sessions: dict = {}
_lock = asyncio.Lock()

# 会话过期：超过该天数未活动的会话，在保存时顺手清理（避免文件与内存无限膨胀）
SESSION_TTL_DAYS = int(getattr(cfg, "ai_session_ttl_days", 30))


def _load_sessions() -> dict:
    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _prune_sessions() -> None:
    """清理长期未活动的会话（按 last_active 时间戳）。必须在持锁时调用。"""
    if SESSION_TTL_DAYS <= 0:
        return
    import time

    deadline = time.time() - SESSION_TTL_DAYS * 86400
    stale = [
        k for k, v in _sessions.items()
        if isinstance(v, dict) and v.get("last_active", 0) and v["last_active"] < deadline
    ]
    for k in stale:
        _sessions.pop(k, None)
    if stale:
        logger.info(f"[ai_chat] 已清理 {len(stale)} 个过期会话（>{SESSION_TTL_DAYS} 天未活动）")


def _save_sessions() -> None:
    _prune_sessions()
    try:
        tmp = SESSION_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_sessions, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SESSION_FILE)
    except Exception as e:
        logger.error(f"[ai_chat] 保存会话失败: {e}")
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


_sessions = _load_sessions()


def _session_key(event: MessageEvent, bot: Bot) -> str:
    """群聊按 群号-用户号 隔离，私聊按 私聊-用户号 隔离"""
    if isinstance(event, GroupMessageEvent):
        return f"g{event.group_id}-u{event.user_id}"
    return f"p-{event.user_id}"


def _get_session(key: str) -> dict:
    import time

    if key not in _sessions:
        _sessions[key] = {"persona": DEFAULT_PERSONA, "model": OLLAMA_MODEL, "history": []}
    sess = _sessions[key]
    sess.setdefault("persona", DEFAULT_PERSONA)
    sess.setdefault("model", OLLAMA_MODEL)
    sess.setdefault("history", [])
    sess["last_active"] = time.time()  # 每次访问刷新活动时间
    return sess


# ==================== Ollama 调用 ====================
# 健康状态标志：启动自检失败后置 False，用户请求时据此快速失败，
# 不再干等 180 秒超时；后台成功调用一次后会自动恢复为 True。
_ollama_ok: bool = True


async def ask_ollama(
    messages: list,
    model: str,
    temperature: float = 0.8,
) -> str:
    """调用 Ollama /api/chat，返回助手回复文本"""
    global _ollama_ok
    # 已知服务不可用时，直接快速失败，避免每次请求都卡满超时
    if not _ollama_ok:
        raise RuntimeError(
            f"本地模型服务当前不可用（{OLLAMA_URL}）。请启动 Ollama 后稍等片刻再试。"
        )
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_ctx": 4096,
        },
    }
    url = f"{OLLAMA_URL}/api/chat"
    try:
        async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            _ollama_ok = True  # 成功即恢复健康标志
            return (data.get("message") or {}).get("content", "").strip()
    except httpx.ConnectError:
        _ollama_ok = False
        raise RuntimeError(f"连接不上本地模型服务 {OLLAMA_URL}，请确认 Ollama 已启动")
    except httpx.ReadTimeout:
        raise RuntimeError(f"模型响应超时（{OLLAMA_TIMEOUT:.0f}秒），可能问题太复杂或显存不足")
    except httpx.HTTPStatusError as e:
        body = e.response.text[:200]
        raise RuntimeError(f"模型服务返回错误 {e.response.status_code}：{body}")
    except Exception as e:
        raise RuntimeError(f"调用模型失败：{e}")


# ==================== 触发：@机器人 或 喊名字 对话 ====================
# 机器人称呼（从 .env 的 NICKNAME / CALL_NAMES 读取）
_NICKNAMES = [str(n) for n in (getattr(cfg, "nickname", []) or [])]
_raw_call = getattr(cfg, "call_names", None) or ""
if isinstance(_raw_call, (list, tuple, set)):
    _CALL_NAMES = [str(x).strip() for x in _raw_call if str(x).strip()]
else:
    _CALL_NAMES = [x.strip() for x in str(_raw_call).split(",") if x.strip()]
# 合并昵称与呼叫词，去重，长词优先（避免"洛"抢先匹配"洛书"）
ALL_NAMES = sorted(set(_NICKNAMES + _CALL_NAMES), key=len, reverse=True)

# 「洛书偷偷喜欢的人」的称呼（从 .env 的 CRUSH_NAMES 读取，逗号分隔）
_raw_crush = getattr(cfg, "crush_names", None) or ""
if isinstance(_raw_crush, (list, tuple, set)):
    _CRUSH_NAMES = [str(x).strip() for x in _raw_crush if str(x).strip()]
else:
    _CRUSH_NAMES = [x.strip() for x in str(_raw_crush).split(",") if x.strip()]
_CRUSH_NAME = _CRUSH_NAMES[0] if _CRUSH_NAMES else "TA"

# 帮助类命令词：喊名字 + 这些词时，交给 help_menu.py 处理，AI 不要抢答
# （覆盖 "洛书 /h"、"洛书 /help"、"洛书 h"、"洛书 菜单" 等写法）
# 注意：「功能」不再列入 —— 它现在是一个真实命令（/功能 群功能开关），
#       若列入会被当成帮助词劫持，导致 /功能 关不掉功能。
HELP_WORDS = {"h", "help", "菜单", "帮助", "功能清单", "功能列表", "你会什么", "能做什么"}


def _is_help_cmd(text: str) -> bool:
    """判断「喊名字 + 帮助词」的组合，例：洛书 /h 、洛书 help 、洛 /菜单"""
    rest = _strip_call_name(text)
    rest = rest.lstrip("/／-— ").strip().lower()
    return rest in HELP_WORDS


def _is_master(event: MessageEvent) -> bool:
    """是否为「洛书喜欢的人」（技术上是管理员）"""
    return str(event.get_user_id()) in _OWNER_QQ


def _strip_call_name(text: str) -> str:
    """去掉句首的机器人称呼，返回真正要对话的内容。

    例："洛书你好呀" -> "你好呀"；"洛,在吗" -> "在吗"
    """
    t = text.strip()
    for name in ALL_NAMES:
        if not name:
            continue
        if t.startswith(name):
            rest = t[len(name):].lstrip("，,、:： 　!！?？~～")
            return rest
    return t


def _called_me() -> Rule:
    """判断消息是否 @ 了机器人 或 以机器人名字开头。

    所有人（含普通群友）都可以用这种方式唤醒对话。
    """

    async def _checker(bot: Bot, event: MessageEvent) -> bool:
        text = event.get_message().extract_plain_text().strip()
        # 「喊名字 + 帮助词」交给 help_menu.py，AI 不抢答（例：洛书 /h）
        if _is_help_cmd(text):
            return False
        for seg in event.get_message():
            if seg.type == "at" and str(seg.data.get("qq")) == str(bot.self_id):
                return True
        # 以名字/呼叫词开头才算（避免正常聊天里偶然包含名字被误触发）
        return any(text.startswith(n) for n in ALL_NAMES if n)

    return Rule(_checker)


chat = on_message(rule=_called_me() & guard("ai"), priority=97, block=True)


async def _do_chat(matcher, bot: Bot, event: MessageEvent, text: str):
    """AI 对话公共逻辑：@触发 / 喊名字 / /ai 命令 共用"""
    text = (text or "").strip()
    if not text:
        await matcher.finish("唔…你想聊什么呀？直接说内容就行~")

    # 冷却防刷屏（F-06）
    if not check_cooldown("ai", event, 3):
        await matcher.finish("说得太快啦，我缓一口气~")

    # 输入敏感词过滤（F-13）
    if has_sensitive(text):
        log_event("SAFE", f"AI输入命中敏感词 user={event.get_user_id()}")
        await matcher.finish("这个话题我不太方便聊哦，我们换个别的吧~")

    key = _session_key(event, bot)
    async with _lock:
        sess = _get_session(key)
        persona_name = sess["persona"]
        model = sess["model"]
        # 快照一份历史副本：后续在锁外调用模型期间，别处可能改写 sess["history"]，
        # 直接引用列表会读到"半新半旧"的数据。这里复制成独立列表再拼消息。
        history: list = list(sess["history"])

    system_prompt = PERSONAS.get(persona_name, PERSONAS[DEFAULT_PERSONA])

    # 检测到是「心动的人」（你心里喜欢的人）时，追加心动关系设定
    # 注意：不是上下级，不叫「主人」，你们是平等的，只是你偷偷喜欢 TA
    if _is_master(event):
        _who = event.sender.card or event.sender.nickname or _CRUSH_NAME
        master_note = (
            f"\n\n【重要】正在和你说话的是「{_who}」，是你偷偷喜欢的人。"
            "你们是平等的朋友，没有任何主从关系——绝对不要叫 TA「主人」，也不要表现得卑微讨好。"
            "你对 TA 的心动是藏不住的：会更在意 TA 说的话、回得更认真，"
            "偶尔会不小心流露出一点小心思，然后马上不好意思地掩饰过去。"
            "但依然保持你内向腼腆的样子，不会直接告白，也不会肉麻——"
            "是那种「嘴上说没什么，心里却偷偷开心」的感觉。"
        )
        system_prompt = system_prompt + master_note

    # 组装消息：system + 历史 + 本次
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history[-AI_MAX_HISTORY * 2:])
    messages.append({"role": "user", "content": text})

    log_command(f"AI对话({persona_name}{'·心动' if _is_master(event) else ''})", event)

    try:
        reply = await ask_ollama(messages, model=model)
    except RuntimeError as e:
        log_event("AI_ERR", str(e))
        await matcher.finish(str(e))
        return

    if not reply:
        reply = "（模型返回了空内容，换个说法再试试？）"

    # 输出敏感词过滤（F-13）
    if has_sensitive(reply):
        log_event("SAFE", "AI输出命中敏感词，已过滤")
        reply = filter_text(reply)

    # 写回历史。
    # 注意：模型调用在锁外进行，期间同一会话可能有另一个请求也调完模型回来了。
    # 若这里直接覆盖 sess["history"]，就会把对方的对话挤掉（竞争条件）。
    # 因此改为「追加 + 按时间裁剪」，只在锁内做最小粒度的追加，不整表替换。
    async with _lock:
        sess = _get_session(key)
        sess["history"].append({"role": "user", "content": text})
        sess["history"].append({"role": "assistant", "content": reply})
        # 只保留最近 N 轮，避免无限增长
        if len(sess["history"]) > AI_MAX_HISTORY * 2:
            sess["history"] = sess["history"][-AI_MAX_HISTORY * 2:]
        _save_sessions()

    # 长回复自动分段，避免被平台截断
    for chunk in _split_message(reply, 800):
        if AI_AT_SENDER:
            await matcher.send(Message(f"[CQ:at,qq={event.user_id}] ") + chunk)
        else:
            await matcher.send(chunk)
        await asyncio.sleep(0.3)


@chat.handle()
async def _handle_chat(bot: Bot, event: MessageEvent):
    # 提取纯文本（@机器人 的部分会被 extract_plain_text 去掉）
    text = event.get_message().extract_plain_text().strip()
    # 若是喊名字唤醒（如「洛书你好」），去掉开头的称呼，只把正文发给模型
    text = _strip_call_name(text)
    await _do_chat(chat, bot, event, text)


# ==================== /ai 命令式触发 ====================
ai_cmd = on_command("ai", aliases={"提问"}, rule=guard("ai"), priority=5, block=True)


@ai_cmd.handle()
async def _handle_ai_cmd(bot: Bot, event: MessageEvent, arg: Message = CommandArg()):
    await _do_chat(ai_cmd, bot, event, arg.extract_plain_text())


# 句子结束符：切分时优先在这些字符后面断开，避免把一句话劈成两半
_SENT_END = "。！？!?…；;\n"
# 段落/分行符优先级最高


def _split_message(text: str, limit: int = 800) -> list:
    """把长文本切分成不超过 limit 的片段。

    切分优先级：段落换行 > 句子结束符（。！？等）> 逗号 > 硬切。
    这样尽量不会把一句话、一个代码块从中间截断。
    """
    if len(text) <= limit:
        return [text]

    # 先按换行切成"行"，再逐行塞进缓冲区；单行超长时按句子边界再切
    parts = []
    buf = ""

    def _flush_soft(line: str) -> list:
        """把一条超长行按句子边界切成若干不超过 limit 的块"""
        chunks = []
        cur = ""
        for ch in line:
            if len(cur) + 1 > limit:
                # 到上限了，尝试回退到最近的句子结束符
                cut = _last_sentence_break(cur)
                if cut > 0:
                    chunks.append(cur[:cut])
                    cur = cur[cut:]
                else:
                    chunks.append(cur)
                    cur = ""
            cur += ch
        if cur:
            chunks.append(cur)
        return chunks

    def _last_sentence_break(s: str) -> int:
        """在 s 中找最后一个句子结束符的位置（返回其后一位），找不到返回 0"""
        for i in range(len(s) - 1, max(-1, len(s) - 120), -1):
            if s[i] in "。！？!?…；;":
                return i + 1
        return 0

    for line in text.split("\n"):
        if len(buf) + len(line) + 1 > limit:
            if buf:
                parts.append(buf)
                buf = ""
            if len(line) > limit:
                parts.extend(_flush_soft(line))
            else:
                buf = line
        else:
            buf = f"{buf}\n{line}" if buf else line
    if buf:
        parts.append(buf)
    return parts


# ==================== 人设切换 ====================
switch = on_command("切换", aliases={"人设切换"}, rule=guard("switch"), priority=5, block=True)


@switch.handle()
async def _handle_switch(bot: Bot, event: MessageEvent, arg: Message = CommandArg()):
    key = _session_key(event, bot)
    name = arg.extract_plain_text().strip()

    if not name:
        async with _lock:
            cur = _get_session(key)["persona"]
        await switch.finish(
            f"当前人设：{cur}\n"
            f"用法：/切换 人设名\n"
            f"可用人设：{', '.join(PERSONAS.keys())}"
        )

    if name not in PERSONAS:
        await switch.finish(
            f"没有「{name}」这个人设哦。\n可用人设：{', '.join(PERSONAS.keys())}"
        )

    async with _lock:
        sess = _get_session(key)
        sess["persona"] = name
        sess["history"] = []  # 换人设清空历史，避免人设串味
        _save_sessions()

    await switch.finish(f"好嘞，已切换为「{name}」，之前聊的就不记得啦～")


persona_cmd = on_command("人设", aliases={"persona"}, rule=guard("persona"), priority=5, block=True)


@persona_cmd.handle()
async def _handle_persona(bot: Bot, event: MessageEvent):
    key = _session_key(event, bot)
    async with _lock:
        cur = _get_session(key)["persona"]
    lines = [f"当前人设：{cur}", "", "可用人设："]
    for name, prompt in PERSONAS.items():
        brief = prompt.split("\n")[0][:30]
        lines.append(f"  · {name} —— {brief}")
    lines.append("")
    lines.append("用 /切换 人设名 来更换")
    await persona_cmd.finish("\n".join(lines))


# ==================== 重置 / 查看历史 ====================
reset = on_command("reset", aliases={"重置", "清空"}, rule=guard("reset"), priority=5, block=True)


@reset.handle()
async def _handle_reset(bot: Bot, event: MessageEvent):
    key = _session_key(event, bot)
    async with _lock:
        sess = _get_session(key)
        sess["history"] = []
        _save_sessions()
    await reset.finish("对话记忆已清空，我们重新开始吧～")


history = on_command("history", aliases={"历史"}, rule=guard("history"), priority=5, block=True)


@history.handle()
async def _handle_history(bot: Bot, event: MessageEvent):
    key = _session_key(event, bot)
    async with _lock:
        sess = _get_session(key)
        n = len(sess["history"]) // 2
        persona = sess["persona"]
        model = sess["model"]
    await history.finish(f"当前人设：{persona}\n使用模型：{model}\n已记录对话：{n} 轮")


# ==================== 模型切换（仅管理员）====================
model_cmd = on_command("model", aliases={"模型"}, priority=5, block=True, permission=SUPERUSER)


@model_cmd.handle()
async def _handle_model(bot: Bot, event: MessageEvent, arg: Message = CommandArg()):
    global OLLAMA_MODEL
    key = _session_key(event, bot)
    name = arg.extract_plain_text().strip()

    if not name:
        async with _lock:
            cur = _get_session(key)["model"]
        # 顺便查一下本地有哪些模型
        models = await _list_models()
        await model_cmd.finish(
            f"当前模型：{cur}\n"
            f"用法：/model 模型名\n"
            f"本地已有模型：{', '.join(models) if models else '（查询失败）'}"
        )

    async with _lock:
        sess = _get_session(key)
        sess["model"] = name
        _save_sessions()
    await model_cmd.finish(f"已切换模型为「{name}」（只对你自己生效）")


async def _list_models() -> list:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{OLLAMA_URL}/api/tags")
            resp.raise_for_status()
            return [m["name"] for m in resp.json().get("models", [])]
    except Exception:
        return []


# ==================== 启动自检与掉线告警（F-09）====================
driver = get_driver()


@driver.on_startup
async def _check_ollama_on_startup():
    """启动时检查本地模型是否可用，不可用则告警（不阻断启动）"""
    global _ollama_ok
    models = await _list_models()
    if not models:
        _ollama_ok = False
        logger.error(f"[ai_chat] 无法连接本地模型服务 {OLLAMA_URL}，AI 对话将不可用")
        log_event("STARTUP", f"Ollama 连接失败 {OLLAMA_URL}")
        await notify_owner(
            f"启动时连不上本地模型服务（{OLLAMA_URL}）。\n"
            f"请确认 Ollama 已启动，然后重启机器人。\n"
            f"（在你恢复之前，AI 对话会直接提示不可用，不会让你干等超时）"
        )
    else:
        _ollama_ok = True
        logger.info(f"[ai_chat] 本地模型可用：{', '.join(models)}")
        log_event("STARTUP", f"Ollama 正常，模型：{','.join(models)}")
        await notify_owner(f"机器人已上线，本地模型正常（当前默认：{OLLAMA_MODEL}）")


@driver.on_shutdown
async def _on_shutdown():
    log_event("SHUTDOWN", "机器人正常关闭")
