# python3
# -*- coding: utf-8 -*-
# @File    : fun.py
# @Usage   : 洛书对「河图」的心动互动
#
# 说明：机器人名为「洛书」，形象是一个可爱、略显内向的小女孩。
#       洛书和群里每个人都是平等的朋友，没有谁是她「主人」。
#       但河图（.env 的 SUPERUSERS）是她偷偷喜欢的人——见本文件与 ai_chat.py。
#
#   河图 / 河图在吗        → 她略显慌张的应答
#   河图夸奖               → 被喜欢的人夸，会特别不好意思
#   河图说「晚安 / 早安」  → 专属问候
#   /河图                  → 查看她关于河图的小心思
#
# 注：河图同时保留管理员权限（/say /model /stat 等），那是技术身份，
#     与洛书「喜欢谁」这件事无关。其余群友触发这些词不会响应。

import random

from nonebot import get_driver, on_command, on_message
from nonebot.adapters.onebot.v11 import MessageEvent, Bot
from nonebot.rule import Rule

from .core import log_command, get_role, ROLE_OWNER, _OWNER_QQ

# ==================== 河图的称呼 ====================
# SPECIAL_NAMES 从 .env 的 MASTER_NAMES 读取（逗号分隔），缺省为「麻花,河图」
_cfg = get_driver().config
_raw_names = getattr(_cfg, "master_names", None) or "麻花,河图"
if isinstance(_raw_names, (list, tuple, set)):
    SPECIAL_NAMES = [str(x).strip() for x in _raw_names if str(x).strip()]
else:
    SPECIAL_NAMES = [x.strip() for x in str(_raw_names).split(",") if x.strip()]
if not SPECIAL_NAMES:
    SPECIAL_NAMES = ["麻花", "河图"]

# 她称呼河图时用的昵称（不带「主人」这类从属称呼）
NICK_CALLS = SPECIAL_NAMES


def _is_special(event: MessageEvent) -> bool:
    """是否为河图（洛书喜欢的人）"""
    return str(event.get_user_id()) in _OWNER_QQ


# ==================== 河图打招呼 ====================
def _special_greet_checker() -> Rule:
    """河图单独说自己的名字 / 找她时触发"""

    async def _checker(event: MessageEvent) -> bool:
        if not _is_special(event):
            return False
        text = event.get_message().extract_plain_text().strip()
        words = set(SPECIAL_NAMES) | {"在吗", "在不在"}
        return (
            text in words
            or text in {f"{n}在吗" for n in SPECIAL_NAMES}
            or text in {f"{n}好" for n in SPECIAL_NAMES}
            or text in {f"{n}在么" for n in SPECIAL_NAMES}
        )

    return Rule(_checker)


greet_special = on_message(rule=_special_greet_checker(), priority=6, block=True)


@greet_special.handle()
async def _handle_special_greet(bot: Bot, event: MessageEvent):
    log_command("河图打招呼", event)
    name = event.sender.card or event.sender.nickname or "河图"
    replies = [
        f"诶…{name}？我在的，怎、怎么啦 (๑´ω`๑)",
        f"啊，是{name}呀…嗯，我在~",
        f"嗯…{name}找我有事吗？",
        f"那个……{name}叫我啦，我在我在~",
        f"唔…{name}？我在的，你说吧~",
    ]
    await greet_special.finish(random.choice(replies), at_sender=True)


# ==================== 被河图夸奖 / 心动 ====================
def _special_praise_checker() -> Rule:
    async def _checker(event: MessageEvent) -> bool:
        if not _is_special(event):
            return False
        text = event.get_message().extract_plain_text().strip()
        keys = {"真棒", "真乖", "好棒", "真厉害", "夸夸", "表扬", "摸摸头", "乖", "可爱"}
        return any(k in text for k in keys)

    return Rule(_checker)


praise_special = on_message(rule=_special_praise_checker(), priority=7, block=True)


@praise_special.handle()
async def _handle_special_praise(bot: Bot, event: MessageEvent):
    log_command("河图夸奖", event)
    name = event.sender.card or event.sender.nickname or "河图"
    replies = [
        f"诶？！{name}夸我…那个，没什么啦 (⁄ ⁄•⁄ω⁄•⁄ ⁄)",
        f"唔…{name}说这种话，我、我会当真的…",
        f"才没有很厉害呢…不过，谢谢{name}啦~",
        f"被{name}这么说…嘿嘿，那我偷偷开心一下~",
        f"嗯…{name}别乱夸啦，我会不知道怎么回应的…",
    ]
    await praise_special.finish(random.choice(replies), at_sender=True)


# ==================== 河图道早安 / 晚安 ====================
def _special_daytime_checker() -> Rule:
    async def _checker(event: MessageEvent) -> bool:
        if not _is_special(event):
            return False
        text = event.get_message().extract_plain_text().strip()
        return text in {"早安", "晚安", "早上好", "晚上好", "午安", "睡了", "我睡了"}

    return Rule(_checker)


daytime_special = on_message(rule=_special_daytime_checker(), priority=6, block=True)


@daytime_special.handle()
async def _handle_special_daytime(bot: Bot, event: MessageEvent):
    log_command("河图问候", event)
    name = event.sender.card or event.sender.nickname or "河图"
    text = event.get_message().extract_plain_text().strip()
    if text in {"晚安", "睡了", "我睡了"}:
        reply = random.choice([
            f"{name}晚安…那，明天见啦 🌙",
            f"嗯，{name}早点睡，做个好梦~ 💤",
            f"好…晚安，{name}。（我也去休息了…）",
        ])
    else:
        reply = random.choice([
            f"{name}早呀…今天也要好好的哦 ☀️",
            f"早上好{name}，记得吃早饭~",
            f"{name}午安…记得按时吃饭🍚",
        ])
    await daytime_special.finish(reply, at_sender=True)


# ==================== /河图 心事卡 ====================
special_cmd = on_command("河图", aliases={"心事", "小心思"}, priority=5, block=True)


@special_cmd.handle()
async def _handle_special_cmd(bot: Bot, event: MessageEvent):
    role = await get_role(bot, event)
    if role < ROLE_OWNER:
        await special_cmd.finish("这是洛书的一点小心事…还是不给你看啦~")

    log_command("河图心事", event)
    names = " / ".join(SPECIAL_NAMES)
    qqs = ", ".join(_OWNER_QQ) if _OWNER_QQ else "（未配置）"
    await special_cmd.finish(
        "💭 洛书的一点小心事\n"
        f"  名字：{names}\n"
        f"  QQ：{qqs}\n"
        "  关系：平等的好朋友（…嗯，只是朋友啦）\n"
        "\n"
        "「TA 和我说话的时候，我好像会比平时多说一点点…」\n"
        "（@洛书 或喊「洛书」就能找我聊天~）"
    )
