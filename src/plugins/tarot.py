# python3
# -*- coding: utf-8 -*-
# @File    : tarot.py
# @Usage   : 抽塔罗牌 —— 对应 F-11
#
#   /塔罗        抽单张牌
#   /塔罗 三张   抽过去 / 现在 / 未来 三张牌
#
# 输出牌面名称、正逆位、牌意解读。
# 每日限抽次数由 cooldown 控制；牌库见 tarot_deck.py（完整 78 张）。
# 定位为趣味解读，不做吉凶预测。
import hashlib
import random
from datetime import datetime

from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageEvent, Bot, Message
from nonebot.params import CommandArg

from .core import log_command, check_cooldown
from .features import guard
from .tarot_deck import FULL_DECK

POSITIONS = ["过去", "现在", "未来"]

HINTS = [
    "牌只是镜子，答案还是在你自己心里~",
    "趣味解读，看看就好，别太较真~",
    "把它当作一个换个角度思考的契机吧~",
]


def _seed(*parts) -> int:
    return int(hashlib.md5("".join(str(p) for p in parts).encode()).hexdigest(), 16)


def _draw_cards(seed: int, n: int) -> list:
    rng = random.Random(seed)
    cards = rng.sample(FULL_DECK, n)
    result = []
    for name, up, rev in cards:
        is_rev = rng.random() < 0.35  # 约 35% 概率逆位
        result.append({
            "name": name,
            "reversed": is_rev,
            "meaning": rev if is_rev else up,
        })
    return result


def _resolve_target(event: MessageEvent) -> tuple:
    reply = getattr(event, "reply", None)
    if reply is not None:
        try:
            return str(reply.sender.user_id), (reply.sender.card or reply.sender.nickname or "TA")
        except Exception:
            pass
    for seg in event.get_message():
        if seg.type == "at":
            qq = str(seg.data.get("qq"))
            if qq and qq != "all":
                return qq, f"QQ{qq}"
    return str(event.get_user_id()), (event.sender.card or event.sender.nickname or "你")


tarot = on_command("塔罗", aliases={"tarot", "抽塔罗"}, rule=guard("tarot"), priority=5, block=True)


@tarot.handle()
async def _handle_tarot(bot: Bot, event: MessageEvent, arg: Message = CommandArg()):
    mode = arg.extract_plain_text().strip()
    three = mode in ("三张", "3", "3张", "过去现在未来")

    cooldown_key = "塔罗三张" if three else "塔罗单张"
    if not check_cooldown(cooldown_key, event, 10):
        await tarot.finish("刚抽过啦，歇口气再来~")
    log_command("塔罗三张" if three else "塔罗", event)

    tid, tname = _resolve_target(event)
    sid = str(event.get_user_id())
    sname = event.sender.card or event.sender.nickname or "你"

    # 单张允许每 10 秒一次；三张用日期+分钟做种子，避免无限刷
    if three:
        seed = _seed(tid, datetime.now().strftime("%Y%m%d"), "tarot3")
    else:
        seed = _seed(tid, datetime.now().strftime("%Y%m%d%H%M"), "tarot1")

    n = 3 if three else 1
    cards = _draw_cards(seed, n)

    prefix = "" if tid == sid else f"【{tname}】"
    lines = [f"{prefix} 🎴 塔罗牌解读"]
    if three:
        lines.append("牌阵：过去 / 现在 / 未来")
    lines.append("-" * 18)

    for i, c in enumerate(cards):
        pos = f"{POSITIONS[i]}：" if three else ""
        orient = "逆位" if c["reversed"] else "正位"
        lines.append(f"{pos}{c['name']}（{orient}）")
        lines.append(f"  {c['meaning']}")
        lines.append("")

    lines.append(random.choice(HINTS))
    await tarot.finish("\n".join(lines).rstrip())
