# python3
# -*- coding: utf-8 -*-
# @File    : fun.py
# @Usage   : 洛书对「熟人」的专属互动
#
# 说明：机器人名为「洛书」，形象是一个可爱、略显内向的小女孩。
#       洛书和群里每个人都是平等的朋友，没有谁是她「主人」。
#       而「熟人」（.env 的 CRUSH_NAMES，同时也是 SUPERUSERS 里的管理员）
#       是她相处得更自在的朋友 —— 语气只比别人稍微亲近一点点，
#       不是暧昧、不是暗恋，见本文件与 ai_chat.py。
#
#   <名字> / <名字>在吗       → 比平时放松一点的应答
#   <名字> + 夸奖词           → 被熟人夸，会有点不好意思
#   <名字> 说「晚安 / 早安」  → 专属问候
#   /心事                     → 查看她的一点小心思
#
# 注：这个人同时保留管理员权限（/say /model /stat 等），那是技术身份，
#     与洛书「喜欢谁」这件事无关。其余群友触发这些词不会响应。
import random

from nonebot import get_driver, on_command, on_message
from nonebot.adapters.onebot.v11 import MessageEvent, Bot
from nonebot.rule import Rule

from .core import log_command, get_role, ROLE_OWNER, _OWNER_QQ
from .features import guard

# ==================== 「熟人」的称呼 ====================
# SPECIAL_NAMES 从 .env 的 CRUSH_NAMES 读取（逗号分隔）
_DEFAULT_NAME = "小美"  # 未配置时的占位符，改 .env 即可换成你自己
_cfg = get_driver().config
_raw_names = getattr(_cfg, "crush_names", None) or _DEFAULT_NAME
if isinstance(_raw_names, (list, tuple, set)):
    SPECIAL_NAMES = [str(x).strip() for x in _raw_names if str(x).strip()]
else:
    SPECIAL_NAMES = [x.strip() for x in str(_raw_names).split(",") if x.strip()]
if not SPECIAL_NAMES:
    SPECIAL_NAMES = [_DEFAULT_NAME]

# 她称呼对方时用的昵称（不带「主人」这类从属称呼）
NICK_CALLS = SPECIAL_NAMES

# 拿不到对方群名片/昵称时的兜底称呼
_DEFAULT_CALL = SPECIAL_NAMES[0]


def _is_special(event: MessageEvent) -> bool:
    """是否为「洛书熟人」（技术上是管理员）"""
    return str(event.get_user_id()) in _OWNER_QQ


# ==================== 打招呼 ====================
def _special_greet_checker() -> Rule:
    """对方单独说自己的名字 / 找她时触发"""

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


greet_special = on_message(rule=_special_greet_checker() & guard("crush"), priority=6, block=True)


@greet_special.handle()
async def _handle_special_greet(bot: Bot, event: MessageEvent):
    log_command("心动打招呼", event)
    name = event.sender.card or event.sender.nickname or _DEFAULT_CALL
    replies = [
        f"{name}？我在的，怎么啦~",
        f"嗯，是{name}呀，我在~",
        f"{name}找我有事吗？",
        f"在的在的，{name}你说~",
        f"唔，{name}？我在呢~",
    ]
    await greet_special.finish(random.choice(replies), at_sender=True)


# ==================== 被夸奖 / 心动 ====================
def _special_praise_checker() -> Rule:
    async def _checker(event: MessageEvent) -> bool:
        if not _is_special(event):
            return False
        text = event.get_message().extract_plain_text().strip()
        keys = {"真棒", "真乖", "好棒", "真厉害", "夸夸", "表扬", "摸摸头", "乖", "可爱"}
        return any(k in text for k in keys)

    return Rule(_checker)


praise_special = on_message(rule=_special_praise_checker() & guard("crush"), priority=7, block=True)


@praise_special.handle()
async def _handle_special_praise(bot: Bot, event: MessageEvent):
    log_command("心动夸奖", event)
    name = event.sender.card or event.sender.nickname or _DEFAULT_CALL
    replies = [
        f"谢谢{name}~ 不过真的没那么厉害啦",
        f"诶，被{name}夸了…谢谢你呀~",
        f"唔…{name}这么说，我会不好意思的~",
        f"嘿嘿，那就谢谢{name}啦~",
        f"嗯…{name}别乱夸，我会不知道怎么回的~",
    ]
    await praise_special.finish(random.choice(replies), at_sender=True)


# ==================== 道早安 / 晚安 ====================
def _special_daytime_checker() -> Rule:
    async def _checker(event: MessageEvent) -> bool:
        if not _is_special(event):
            return False
        text = event.get_message().extract_plain_text().strip()
        return text in {"早安", "晚安", "早上好", "晚上好", "午安", "睡了", "我睡了"}

    return Rule(_checker)


daytime_special = on_message(rule=_special_daytime_checker() & guard("crush"), priority=6, block=True)


@daytime_special.handle()
async def _handle_special_daytime(bot: Bot, event: MessageEvent):
    log_command("心动问安", event)
    name = event.sender.card or event.sender.nickname or _DEFAULT_CALL
    text = event.get_message().extract_plain_text().strip()
    if text in {"晚安", "睡了", "我睡了"}:
        reply = random.choice([
            f"{name}晚安，明天见~ 🌙",
            f"嗯，{name}早点睡，做个好梦~ 💤",
            f"好，晚安{name}~",
        ])
    else:
        reply = random.choice([
            f"{name}早呀，今天也要好好的哦 ☀️",
            f"早上好{name}，记得吃早饭~",
            f"{name}午安，记得按时吃饭~ 🍚",
        ])
    await daytime_special.finish(reply, at_sender=True)


# ==================== /心事 心事卡 ====================
# 主命令固定为「心事」；同时把 CRUSH_NAMES 里的每个称呼也注册成别名，
# 于是 /心事 和 /<熟人的名字> 都能查看。
special_cmd = on_command(
    "心事",
    aliases=set(SPECIAL_NAMES) | {"小心思"},
    rule=guard("crush"),
    priority=5,
    block=True,
)


@special_cmd.handle()
async def _handle_special_cmd(bot: Bot, event: MessageEvent):
    role = await get_role(bot, event)
    if role < ROLE_OWNER:
        await special_cmd.finish("这是洛书的一点小心事…还是不给你看啦~")

    log_command("心事卡", event)
    names = " / ".join(SPECIAL_NAMES)
    qqs = ", ".join(_OWNER_QQ) if _OWNER_QQ else "（未配置）"
    await special_cmd.finish(
        "💭 洛书的一点小心事\n"
        f"  名字：{names}\n"
        f"  QQ：{qqs}\n"
        "  关系：平等的好朋友（比较熟的那种）\n"
        "\n"
        "「TA 和我说话的时候，我好像会比平时放松一点点…」\n"
        "（@洛书 或喊「洛书」就能找我聊天~）"
    )
