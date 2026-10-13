# python3
# -*- coding: utf-8 -*-
# @File    : keywords.py
# @Usage   : 关键词自动回复 —— 对应 F-08
#
#   /关键词                      查看本群关键词列表
#   /关键词 添加 <触发词> <回复>  添加（管理员以上）
#   /关键词 删除 <触发词>         删除（管理员以上）
#
# 回复内容用 | 分隔可以配置多条，触发时随机选一条。
# 匹配支持精确匹配与包含匹配（包含匹配即"模糊匹配"）。

import random

from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import MessageEvent, Bot, Message
from nonebot.params import CommandArg
from nonebot.rule import Rule

from .core import JsonStore, get_role, ROLE_GROUP_ADMIN, log_command

_store = JsonStore("keywords.json")

MANAGE_ROLE = ROLE_GROUP_ADMIN


def _get_map(event: MessageEvent) -> dict:
    gid = event.group_id if hasattr(event, "group_id") else None
    return _store.get(gid, "keywords", {}) or {}


def _save_map(event: MessageEvent, data: dict) -> None:
    gid = event.group_id if hasattr(event, "group_id") else None
    _store.put(gid, "keywords", data)


# ==================== 管理命令 ====================
kw = on_command("关键词", aliases={"自动回复"}, priority=5, block=True)


@kw.handle()
async def _handle_kw(bot: Bot, event: MessageEvent, arg: Message = CommandArg()):
    role = await get_role(bot, event)
    if role < MANAGE_ROLE:
        await kw.finish("只有群主 / 群管理员 / 管理员可以管理关键词哦。")

    text = arg.extract_plain_text().strip()
    data = _get_map(event)

    # 无参数：列出
    if not text:
        if not data:
            await kw.finish(
                "本群还没有配置关键词。\n"
                "添加：/关键词 添加 <触发词> <回复内容>\n"
                "多条回复用 | 分隔，例如：/关键词 添加 早 早上好|早安|起床啦"
            )
        lines = ["📝 本群关键词："]
        for k, v in data.items():
            replies = " | ".join(v) if isinstance(v, list) else str(v)
            lines.append(f"  {k} → {replies}")
        await kw.finish("\n".join(lines))

    parts = text.split(maxsplit=2)
    action = parts[0]

    # 添加
    if action in ("添加", "add", "+"):
        if len(parts) < 3:
            await kw.finish("用法：/关键词 添加 <触发词> <回复内容>\n多条回复用 | 分隔。")
        trigger, reply = parts[1], parts[2]
        replies = [r.strip() for r in reply.split("|") if r.strip()]
        if not replies:
            await kw.finish("回复内容不能为空。")
        data[trigger] = replies
        _save_map(event, data)
        log_command(f"关键词添加:{trigger}", event)
        await kw.finish(
            f"已添加关键词「{trigger}」，共 {len(replies)} 条回复，触发时随机选一条。"
        )

    # 删除
    if action in ("删除", "del", "-"):
        if len(parts) < 2:
            await kw.finish("用法：/关键词 删除 <触发词>")
        trigger = parts[1]
        if trigger in data:
            data.pop(trigger)
            _save_map(event, data)
            log_command(f"关键词删除:{trigger}", event)
            await kw.finish(f"已删除关键词「{trigger}」。")
        await kw.finish(f"没有找到关键词「{trigger}」。")

    await kw.finish("未知操作。支持：添加 / 删除 / 直接发送查看列表")


# ==================== 自动匹配 ====================
def _keyword_rule() -> Rule:
    """消息命中本群关键词时触发"""

    async def _checker(event: MessageEvent) -> bool:
        if not hasattr(event, "group_id"):
            return False
        text = event.get_message().extract_plain_text().strip()
        if not text:
            return False
        data = _get_map(event)
        if not data:
            return False
        # 精确匹配优先
        if text in data:
            return True
        # 包含匹配（模糊）
        return any(k in text for k in data)

    return Rule(_checker)


kw_reply = on_message(rule=_keyword_rule(), priority=60, block=True)


@kw_reply.handle()
async def _handle_kw_reply(event: MessageEvent):
    text = event.get_message().extract_plain_text().strip()
    data = _get_map(event)

    # 先看精确匹配
    matched = data.get(text)
    if not matched:
        # 再按包含匹配，取第一个命中的
        for k, v in data.items():
            if k in text:
                matched = v
                break

    if not matched:
        return

    replies = matched if isinstance(matched, list) else [str(matched)]
    log_command("关键词触发", event)
    await kw_reply.finish(random.choice(replies))
