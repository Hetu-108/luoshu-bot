# python3
# -*- coding: utf-8 -*-
# @File    : tools.py
# @Usage   : 实用工具 —— 身份查询、禁言、状态、代言
#
#   /whoami        → 显示你的权限等级
#   /禁言 <秒数>   → 禁言自己
#   /stat          → 机器人状态（仅管理员）
#   /say <内容>    → 让机器人发言（仅管理员）
#
# 注：科学计算 /e 已按要求移除。

import platform
import time

import psutil
import nonebot
from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    MessageEvent,
    Message,
    MessageSegment,
)
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.rule import Rule

from .core import (
    get_role,
    ROLE_NAMES,
    log_command,
)


# ==================== 身份查询 ====================
whoami = on_command("whoami", aliases={"身份"}, priority=5, block=True)


# 也支持不带前缀（群友习惯直接打 whoami）
def _whoami_checker() -> Rule:
    async def _checker(event: MessageEvent) -> bool:
        return event.get_message().extract_plain_text().strip() in {"whoami", "我的身份"}

    return Rule(_checker)


whoami_bare = on_message(rule=_whoami_checker(), priority=6, block=True)


@whoami_bare.handle()
async def _handle_whoami_bare(bot: Bot, event: MessageEvent):
    await _render_whoami(bot, event)


@whoami.handle()
async def _handle_whoami(bot: Bot, event: MessageEvent):
    await _render_whoami(bot, event)


async def _render_whoami(bot: Bot, event: MessageEvent):
    log_command("whoami", event)
    role = await get_role(bot, event)
    uid = str(event.get_user_id())
    await whoami.finish(
        f"你的 QQ：{uid}\n"
        f"权限等级：{ROLE_NAMES.get(role, '未知')}\n"
        f"（四级：管理员 > 群主 > 群管理员 > 普通成员）"
    )


# ==================== 禁言自己 ====================
ban = on_command("禁言", priority=5, block=True)


@ban.handle()
async def _handle_ban(bot: Bot, event: MessageEvent, arg: Message = CommandArg()):
    if not isinstance(event, GroupMessageEvent):
        await ban.finish("禁言功能需要在群里使用。")
    raw = arg.extract_plain_text().strip()
    if not raw:
        await ban.finish("用法：/禁言 秒数（0 表示解除禁言）")
    try:
        duration = int(raw)
    except ValueError:
        await ban.finish("请输入一个整数秒数哦")
    log_command("禁言", event)
    try:
        await bot.set_group_ban(
            group_id=event.group_id,
            user_id=int(event.get_user_id()),
            duration=duration,
        )
    except Exception as e:
        await ban.finish(f"禁言失败：{e}")
    await ban.finish(
        Message(
            MessageSegment.at(event.user_id) + MessageSegment.text(" 已执行！")
            + MessageSegment.face(20)
        )
    )


# ==================== 机器人状态（仅管理员）====================
stat = on_command("stat", aliases={"状态"}, priority=5, block=True, permission=SUPERUSER)


@stat.handle()
async def _handle_stat(bot: Bot):
    proc = psutil.Process()
    mem = proc.memory_info().rss / 1024 / 1024
    cpu = psutil.cpu_percent(interval=0.3)
    sys_mem = psutil.virtual_memory()
    uptime = time.time() - psutil.boot_time()

    try:
        groups = len(await bot.get_group_list())
        friends = len(await bot.get_friend_list())
    except Exception:
        groups = friends = -1

    from .ai_chat import OLLAMA_URL, OLLAMA_MODEL

    await stat.finish(
        f"🖥 系统：{platform.system()} {platform.release()}\n"
        f"🐍 Python：{platform.python_version()}\n"
        f"🤖 群数：{groups}　好友数：{friends}\n"
        f"🧠 本地模型：{OLLAMA_MODEL}\n"
        f"🔗 模型地址：{OLLAMA_URL}\n"
        f"💾 本进程内存：{mem:.1f} MB\n"
        f"⚙️ CPU 使用率：{cpu:.1f}%\n"
        f"🧮 系统内存：{sys_mem.percent:.1f}%\n"
        f"⏱ 开机时长：{uptime / 3600:.1f} 小时"
    )


# ==================== 代言（仅管理员）====================
say = on_command("say", aliases={"发言"}, priority=5, block=True, permission=SUPERUSER)


@say.handle()
async def _handle_say(event: MessageEvent, arg: Message = CommandArg()):
    content = arg.extract_plain_text().strip()
    if not content:
        await say.finish("用法：/say 要说的话")
    log_command("say", event)
    if isinstance(event, GroupMessageEvent):
        await nonebot.get_bot().send_group_msg(group_id=event.group_id, message=content)
    else:
        await nonebot.get_bot().send_private_msg(user_id=event.user_id, message=content)
    await say.finish()
