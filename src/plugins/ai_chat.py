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


def _load_sessions() -> dict:
    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_sessions() -> None:
    try:
        tmp = SESSION_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_sessions, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SESSION_FILE)
    except Exception as e:
        logger.error(f"[ai_chat] 保存会话失败: {e}")


_sessions = _load_sessions()


def _session_key(event: MessageEvent, bot: Bot) -> str:
    """群聊按 群号-用户号 隔离，私聊按 私聊-用户号 隔离"""
    if isinstance(event, GroupMessageEvent):
        return f"g{event.group_id}-u{event.user_id}"
    return f"p-{event.user_id}"


def _get_session(key: str) -> dict:
    if key not in _sessions:
        _sessions[key] = {"persona": DEFAULT_PERSONA, "model": OLLAMA_MODEL, "history": []}
    sess = _sessions[key]
    sess.setdefault("persona", DEFAULT_PERSONA)
    sess.setdefault("model", OLLAMA_MODEL)
    sess.setdefault("history", [])
    return sess


# ==================== Ollama 调用 ====================
async def ask_ollama(
    messages: list,
    model: str,
    temperature: float = 0.8,
) -> str:
    """调用 Ollama /api/chat，返回助手回复文本"""
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
            return (data.get("message") or {}).get("content", "").strip()
    except httpx.ConnectError:
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
        history: list = sess["history"]

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

    # 写回历史
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


def _split_message(text: str, limit: int = 800) -> list:
    """按段落把长文本切分成不超过 limit 的片段"""
    if len(text) <= limit:
        return [text]
    parts = []
    buf = ""
    for line in text.split("\n"):
        if len(buf) + len(line) + 1 > limit:
            if buf:
                parts.append(buf)
            # 单行超长再硬切
            while len(line) > limit:
                parts.append(line[:limit])
                line = line[limit:]
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
    models = await _list_models()
    if not models:
        logger.error(f"[ai_chat] 无法连接本地模型服务 {OLLAMA_URL}，AI 对话将不可用")
        log_event("STARTUP", f"Ollama 连接失败 {OLLAMA_URL}")
        await notify_owner(
            f"启动时连不上本地模型服务（{OLLAMA_URL}）。\n"
            f"请确认 Ollama 已启动，然后重启机器人。"
        )
    else:
        logger.info(f"[ai_chat] 本地模型可用：{', '.join(models)}")
        log_event("STARTUP", f"Ollama 正常，模型：{','.join(models)}")
        await notify_owner(f"机器人已上线，本地模型正常（当前默认：{OLLAMA_MODEL}）")


@driver.on_shutdown
async def _on_shutdown():
    log_event("SHUTDOWN", "机器人正常关闭")
