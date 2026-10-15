# python3
# -*- coding: utf-8 -*-
# @File    : notice.py
# @Usage   : 通知类功能 —— 入群欢迎、退群提示、戳一戳、定时问好
#
# 戳一戳机器人        → 随机回应
# 新人入群           → 欢迎
# 有人退群           → 提示
# 每天 7:00 / 12:00 / 22:00 → 定时问好（可在 .env 关闭）
import random

import nonebot
from nonebot import on_notice, on_request, get_driver, require
from nonebot.rule import Rule
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupIncreaseNoticeEvent,
    GroupDecreaseNoticeEvent,
    PokeNotifyEvent,
    Message,
    MessageSegment,
)
from nonebot.log import logger

from .features import guard
from .core import parse_ids

scheduler = require("nonebot_plugin_apscheduler").scheduler

driver = get_driver()
cfg = driver.config

GREETING_ENABLED = str(getattr(cfg, "greeting_enabled", "true")).lower() in ("true", "1", "yes")
GREETING_GROUPS = [int(g) for g in parse_ids(getattr(cfg, "greeting_groups", []))]


# ==================== 戳一戳 ====================
def poke_checker():
    async def _checker(bot: Bot, event: PokeNotifyEvent) -> bool:
        return event.target_id == event.self_id

    return Rule(_checker)


poke = on_notice(rule=poke_checker() & guard("poke"), priority=0, block=True)


@poke.handle()
async def _handle_poke(event: PokeNotifyEvent):
    msgs = [
        "你再戳！",
        "？再戳试试？",
        "别戳了别戳了再戳就坏了555",
        "我爪巴爪巴，球球别再戳了",
        "那...那里...那里不能戳...",
        "(。´・ω・)ん?",
        "有事儿你叫我，别天天一个劲戳戳戳！",
        "欸很烦欸！你戳🔨呢",
        "差不多得了😅",
        "我希望你耗子尾汁",
        "戳我也不会对你好的",
    ]
    await poke.finish(random.choice(msgs), at_sender=True)


# ==================== 入群欢迎 ====================
inc = on_notice(rule=guard("welcome"), priority=5, block=True)


@inc.handle()
async def _handle_increase(bot: Bot, event: GroupIncreaseNoticeEvent):
    if event.user_id == event.self_id:
        await bot.send_group_msg(
            group_id=event.group_id,
            message=Message(
                MessageSegment.text("这是哪里？哦？让我康康！\n") + MessageSegment.face(269)
            ),
        )
    else:
        await bot.send_group_msg(
            group_id=event.group_id,
            message=Message(
                MessageSegment.at(event.user_id)
                + MessageSegment.text(" 欢迎新朋友加入本群！\n")
                + MessageSegment.face(338)
            ),
        )


# ==================== 退群提示 ====================
dec = on_notice(rule=guard("farewell"), priority=5, block=True)


@dec.handle()
async def _handle_decrease(bot: Bot, event: GroupDecreaseNoticeEvent):
    if event.user_id == event.self_id:
        return  # 自己被踢/被移出，不提示
    await bot.send_group_msg(
        group_id=event.group_id,
        message=Message(
            MessageSegment.text(f"{event.user_id} 退群了") + MessageSegment.face(5)
        ),
    )


# ==================== 定时问好 ====================
async def _broadcast(text: str):
    """向配置的群（或所有群）发送问候"""
    try:
        bot = nonebot.get_bot()
    except Exception as e:
        # 拿不到 bot 通常意味着连接还没建立或已断开，记清楚原因便于排查
        logger.error(f"[notice] 定时问候取 bot 失败（可能未连接）: {type(e).__name__}: {e}")
        return

    try:
        if GREETING_GROUPS:
            targets = [int(g) for g in GREETING_GROUPS]
        else:
            group_list = await bot.get_group_list()
            targets = [g["group_id"] for g in group_list]
    except Exception as e:
        logger.error(
            f"[notice] 定时问候获取群列表失败: {type(e).__name__}: {e}"
        )
        return

    sent = 0
    failed = 0
    for gid in targets:
        try:
            await bot.send_group_msg(group_id=gid, message=text)
            sent += 1
        except Exception as e:
            failed += 1
            logger.warning(
                f"[notice] 向群 {gid} 发送问候失败: {type(e).__name__}: {e}"
            )
    logger.info(f"[notice] 定时问候完成：成功 {sent} 个群，失败 {failed} 个群")


MORNING = [
    "☀️ 早上好！新的一天开始啦~",
    "早安呀，今天也要加油哦！",
    "太阳晒屁股啦，快起床~",
    "早上好，祝你今天心情美美的！",
]

NOON = [
    "🍚 中午啦，该吃饭了！",
    "午饭时间到，今天吃什么呀？",
    "中午好，记得按时吃饭哦~",
    "午饭时间，祝你好胃口！",
]

NIGHT = [
    "🌙 夜深了，早点休息哦",
    "晚安，祝大家做个好梦💤",
    "该睡觉啦，熬夜伤身哦~",
    "夜深了，明天见~",
]

if GREETING_ENABLED:
    @scheduler.scheduled_job("cron", hour=7, minute=0, id="luoshu_morning")
    async def _morning():
        await _broadcast(random.choice(MORNING))

    @scheduler.scheduled_job("cron", hour=12, minute=0, id="luoshu_noon")
    async def _noon():
        await _broadcast(random.choice(NOON))

    @scheduler.scheduled_job("cron", hour=22, minute=30, id="luoshu_night")
    async def _night():
        await _broadcast(random.choice(NIGHT))
