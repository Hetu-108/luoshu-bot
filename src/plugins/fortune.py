# python3
# -*- coding: utf-8 -*-
# @File    : fortune.py
# @Usage   : 抽签 + 今日运势（合并为一个功能模块）—— 对应 F-10
#
#   /抽签      → 抽一支趣味心情签
#   /运势      → 今日趣味运势卡（整体运/桃花运/财运/宜/忌）
#   /今日人品  → 与运势同类，保留旧习惯写法
#
# 说明：按「用户 + 日期」做种子，同一天结果固定，避免反复刷新。
# 文案定位为「趣味心情卡」，不涉及算命、预测吉凶等表述。
# 支持 @他人 或 回复某人消息 来帮对方抽。
import hashlib
import random
from datetime import datetime

from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import MessageEvent, Bot, Message, MessageSegment
from nonebot.params import CommandArg
from nonebot.rule import Rule

from .core import log_command, check_cooldown
from .features import guard

# ==================== 签文库 ====================
# 等级由高到低，趣味向描述
SIGNS = [
    ("上上签", "☀️", [
        "万事顺遂，今天你就是最靓的仔。",
        "心想事成，连外卖都不会洒。",
        "出门遇好事，排队都最快轮到你。",
        "诸事皆宜，建议立刻去做那件想了很久的事。",
    ]),
    ("上签", "🌤", [
        "顺风局，今天适合推进计划。",
        "心情在线，做事效率翻倍。",
        "小惊喜可能会来，记得抬头看看。",
        "适合社交，聊什么都聊得开。",
    ]),
    ("中签", "⛅", [
        "平稳一天，不惊不喜刚刚好。",
        "按部就班就行，别瞎折腾。",
        "平常心对待，收获会在意料之中。",
        "适合处理琐事，大事稍等再说。",
    ]),
    ("下签", "🌧", [
        "今天宜低调，少说话多喝水。",
        "小事可能会卡壳，深呼吸再来。",
        "建议早点休息，明天会更好。",
        "别做重大决定，等等看。",
    ]),
    ("下下签", "⛈", [
        "今天主打一个陪跑，稳住别慌。",
        "踩坑预警，走路看路、点餐看单。",
        "适合躺平，努力的事明天再说。",
        "万事小心，尤其是自己的暴脾气。",
    ]),
]

SIGN_WEIGHTS = [10, 25, 35, 22, 8]


def _seed(user_id, date_str: str, salt: str = "") -> int:
    raw = f"{user_id}{date_str}{salt}"
    return int(hashlib.md5(raw.encode()).hexdigest(), 16)


def _today() -> str:
    return datetime.now().strftime("%Y%m%d")


# ==================== 目标解析（支持 @他人 / 回复他人）====================
def _resolve_target(event: MessageEvent) -> tuple:
    """返回 (目标QQ, 目标昵称)。默认是自己。"""
    # 1) 回复某人的消息
    reply = getattr(event, "reply", None)
    if reply is not None:
        try:
            return str(reply.sender.user_id), (reply.sender.card or reply.sender.nickname or "TA")
        except Exception:
            pass
    # 2) @某人
    for seg in event.get_message():
        if seg.type == "at":
            qq = str(seg.data.get("qq"))
            if qq and qq != "all":
                return qq, f"QQ{qq}"
    # 3) 自己
    name = event.sender.card or event.sender.nickname or "你"
    return str(event.get_user_id()), name


def _target_prefix(target_id: str, target_name: str, self_id: str, self_name: str) -> str:
    if target_id == self_id:
        return f"【{self_name}】"
    return f"【{target_name}】"


# ==================== /抽签 ====================
draw = on_command("抽签", aliases={"求签"}, rule=guard("draw"), priority=5, block=True)


@draw.handle()
async def _handle_draw(bot: Bot, event: MessageEvent):
    if not check_cooldown("抽签", event, 5):
        await draw.finish("别急嘛，等几秒再抽~")
    log_command("抽签", event)

    tid, tname = _resolve_target(event)
    sid = str(event.get_user_id())
    sname = event.sender.card or event.sender.nickname or "你"

    seed = _seed(tid, _today(), "sign")
    rng = random.Random(seed)
    level, icon, texts = rng.choices(SIGNS, weights=SIGN_WEIGHTS, k=1)[0]
    line = rng.choice(texts)

    prefix = "" if tid == sid else _target_prefix(tid, tname, sid, sname)
    num = rng.randint(1, 99)

    msg = (
        f"{prefix} 今日心情签\n"
        f"{icon} 第 {num} 签 · {level}\n"
        f"「{line}」\n"
        f"（趣味心情卡，图一乐别当真~）"
    )
    await draw.finish(msg)


# ==================== /运势 ====================
LUCK_FIELDS = {
    "整体运": (60, 100),
    "桃花运": (40, 100),
    "财运": (40, 100),
}

GOOD_THINGS = [
    "早睡早起", "喝一杯热水", "给朋友发条消息", "整理桌面", "散步十分钟",
    "听一首喜欢的歌", "吃顿好的", "把待办清掉一件", "给绿植浇水", "早点下班",
]
BAD_THINGS = [
    "熬夜", "空腹喝咖啡", "跟人抬杠", "冲动购物", "拖延", "刷手机到半夜",
    "一次性做太多事", "在群里发火", "饿着肚子开会", "反复纠结",
]


def _stars(v: int) -> str:
    n = max(1, min(5, round(v / 20)))
    return "★" * n + "☆" * (5 - n)


luck = on_command("运势", aliases={"今日运势", "今日人品", "人品"}, rule=guard("luck"), priority=5, block=True)


# 也支持不带前缀直接说（群里更自然）
def _luck_checker() -> Rule:
    async def _checker(event: MessageEvent) -> bool:
        text = event.get_message().extract_plain_text().strip()
        return text in {"今日人品", "今日运势", "看运势", "查运势"}

    return Rule(_checker)


luck_bare = on_message(rule=_luck_checker() & guard("luck"), priority=6, block=True)


@luck_bare.handle()
async def _handle_luck_bare(bot: Bot, event: MessageEvent):
    await _render_luck(event)


@luck.handle()
async def _handle_luck(bot: Bot, event: MessageEvent, arg: Message = CommandArg()):
    await _render_luck(event)


async def _render_luck(event: MessageEvent):
    if not check_cooldown("运势", event, 5):
        await luck.finish("运势卡刚生成过啦，等几秒~")
    log_command("运势", event)

    tid, tname = _resolve_target(event)
    sid = str(event.get_user_id())
    sname = event.sender.card or event.sender.nickname or "你"

    seed = _seed(tid, _today(), "luck")
    rng = random.Random(seed)

    lines = []
    for field, (lo, hi) in LUCK_FIELDS.items():
        v = rng.randint(lo, hi)
        lines.append(f"  {field}：{_stars(v)}  {v}")

    good = rng.sample(GOOD_THINGS, 2)
    bad = rng.sample(BAD_THINGS, 2)

    prefix = "" if tid == sid else _target_prefix(tid, tname, sid, sname)
    date_str = datetime.now().strftime("%m月%d日")

    msg = (
        f"{prefix} {date_str} 趣味运势卡\n"
        f"{'-' * 18}\n"
        + "\n".join(lines) + "\n"
        f"{'-' * 18}\n"
        f"  宜：{good[0]}、{good[1]}\n"
        f"  忌：{bad[0]}、{bad[1]}\n"
        f"（娱乐向心情卡，开心就好~）"
    )
    await luck.finish(msg)
