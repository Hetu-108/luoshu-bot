# python3
# -*- coding: utf-8 -*-
# @File    : help_menu.py
# @Usage   : 帮助菜单 —— 对应 F-04
#
#   /help           → 分类指令总览
#   /h              → 同 /help（基础功能总览）
#   /help <功能名>  → 查看单个功能的用法与示例
#   @机器人 功能清单 → 同 /help（用户明确要求的用法）
#   @机器人 /h      → 同 /help
#   洛书 /h         → 喊名字 + 帮助词，也能拿到功能总览
#   /帮助 /菜单     → 同 /help

from nonebot import on_command, on_message, get_driver
from nonebot.adapters.onebot.v11 import MessageEvent, Bot, Message
from nonebot.params import CommandArg
from nonebot.rule import Rule

from .core import get_role, ROLE_NAMES, log_command

# ==================== 机器人称呼 / 帮助词（与 ai_chat.py 保持一致） ====================
_cfg = get_driver().config


def _split_names(raw) -> list:
    if isinstance(raw, (list, tuple, set)):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [x.strip() for x in str(raw or "").split(",") if x.strip()]


# 昵称 + 呼叫词，长词优先（避免"洛"抢先匹配"洛书"）
_ALL_NAMES = sorted(
    set(_split_names(getattr(_cfg, "nickname", [])) + _split_names(getattr(_cfg, "call_names", ""))),
    key=len,
    reverse=True,
)

# 帮助词：喊名字 + 这些词 → 返回功能总览
_HELP_WORDS = {
    "h", "help", "菜单", "帮助", "功能", "功能清单", "功能列表",
    "你会什么", "能做什么", "有哪些功能",
}

# 「洛书偷偷喜欢的人」的称呼（与 fun.py / ai_chat.py 一致，来自 .env 的 CRUSH_NAMES）
_CRUSH = _split_names(getattr(_cfg, "crush_names", "")) or ["小美"]
_CRUSH0 = _CRUSH[0]  # 菜单里展示用的默认称呼

# 分类名（集中定义，方便统一改）
CAT_OWNER = "管理员专属"
CAT_CRUSH = "心动专属"

# ==================== 功能注册表 ====================
# 每项： { "name": 显示名, "aliases": [别名], "category": 分类,
#         "usage": 用法, "example": 示例, "desc": 说明 }
FEATURES = [
    # ---- AI 对话 ----
    {
        "name": "AI对话",
        "aliases": ["ai", "聊天"],
        "category": "AI 对话",
        "usage": "@洛书 <内容>\n  或 直接喊「洛书 <内容>」\n  或 /ai <内容>",
        "example": "@洛书 帮我写个自我介绍",
        "desc": "和本地大模型聊天，会记住上下文。所有人都能用。",
    },
    {
        "name": "人设切换",
        "aliases": ["切换", "人设"],
        "category": "AI 对话",
        "usage": "/人设              查看人设列表\n  /切换 <人设名>     切换人设",
        "example": "/切换 助手",
        "desc": "更换 AI 的角色设定，切换后会清空该用户的对话记忆。",
    },
    {
        "name": "重置记忆",
        "aliases": ["reset", "清空"],
        "category": "AI 对话",
        "usage": "/reset",
        "example": "/reset",
        "desc": "清空你自己和 AI 的对话记忆。",
    },

    # ---- 娱乐 ----
    {
        "name": "抽签",
        "aliases": ["签", "求签"],
        "category": "娱乐",
        "usage": "/抽签",
        "example": "/抽签",
        "desc": "抽一支趣味心情签，每天结果固定。可 @他人 帮对方抽。",
    },
    {
        "name": "今日运势",
        "aliases": ["运势", "人品", "今日人品"],
        "category": "娱乐",
        "usage": "/运势  或  /今日人品",
        "example": "/运势",
        "desc": "生成今日趣味运势卡：整体运、桃花运、财运、宜与忌。每天结果固定。",
    },
    {
        "name": "塔罗牌",
        "aliases": ["塔罗", "tarot"],
        "category": "娱乐",
        "usage": "/塔罗          抽单张牌\n  /塔罗 三张     抽过去/现在/未来三张牌",
        "example": "/塔罗 三张",
        "desc": "抽取塔罗牌并给出牌意解读，含正逆位。娱乐向，仅供参考。",
    },

    # ---- 心动专属（洛书偷偷喜欢的人）----
    {
        "name": "心动打招呼",
        "aliases": ["打招呼"] + _CRUSH,
        "category": CAT_CRUSH,
        "usage": f"直接说「{_CRUSH0}」「{_CRUSH0}在吗」",
        "example": f"{_CRUSH0}",
        "desc": "她喜欢的人出现时，洛书略显慌张的应答。仅该人可见。",
    },
    {
        "name": "心动夸奖",
        "aliases": ["夸夸", "真棒"],
        "category": CAT_CRUSH,
        "usage": "消息里包含 真棒 / 好棒 / 夸夸 / 可爱 等词",
        "example": f"{_CRUSH0}真棒",
        "desc": "被喜欢的人夸奖时，洛书会特别不好意思。仅该人可见。",
    },
    {
        "name": "心动问安",
        "aliases": ["早安", "晚安"],
        "category": CAT_CRUSH,
        "usage": "直接说 早安 / 晚安 / 午安",
        "example": "晚安",
        "desc": "喜欢的人道早安/晚安时的专属问候。仅该人可见。",
    },
    {
        "name": "洛书心事",
        "aliases": ["心事", "小心思"] + _CRUSH,
        "category": CAT_CRUSH,
        "usage": "/心事",
        "example": "/心事",
        "desc": "查看洛书的一点小心事。仅该人可见。",
    },

    # ---- 管理 ----
    {
        "name": "数据统计",
        "aliases": ["统计", "stats"],
        "category": "管理",
        "usage": "/统计              今日数据\n  /统计 周           本周数据\n  /统计 月           本月数据",
        "example": "/统计 周",
        "desc": "查看群日活、消息总数、指令使用排行。仅群主/群管理员/管理员可用。",
    },
    {
        "name": "关键词回复",
        "aliases": ["关键词", "自动回复"],
        "category": "管理",
        "usage": "/关键词                    查看本群关键词\n  /关键词 添加 <词> <回复>    添加\n  /关键词 删除 <词>           删除",
        "example": "/关键词 添加 早 早上好呀",
        "desc": "配置本群的自动回复，支持多条随机。仅管理员以上可用。",
    },
    {
        "name": "禁言",
        "aliases": ["ban"],
        "category": "管理",
        "usage": "/禁言 <秒数>",
        "example": "/禁言 60",
        "desc": "禁言自己指定秒数（0 表示解除）。",
    },
    {
        "name": "身份查询",
        "aliases": ["whoami"],
        "category": "管理",
        "usage": "/whoami",
        "example": "/whoami",
        "desc": "查看自己在群里的权限等级。",
    },

    # ---- 管理员专属 ----
    {
        "name": "模型切换",
        "aliases": ["model"],
        "category": "管理员专属",
        "usage": "/model <模型名>",
        "example": "/model qwen2.5:14b",
        "desc": "切换 AI 使用的本地模型。仅管理员可用。",
    },
    {
        "name": "运行状态",
        "aliases": ["stat", "状态"],
        "category": "管理员专属",
        "usage": "/stat",
        "example": "/stat",
        "desc": "查看机器人运行状态、内存、群数等。仅管理员可用。",
    },
    {
        "name": "代言",
        "aliases": ["say", "发言"],
        "category": "管理员专属",
        "usage": "/say <内容>",
        "example": "/say 大家好",
        "desc": "让洛书以自己身份发言。仅管理员可用。",
    },
]

# 分类顺序
CATEGORY_ORDER = ["AI 对话", "娱乐", CAT_CRUSH, "管理", CAT_OWNER]


def _find_feature(flag: str):
    """按名称或别名查找功能"""
    flag = flag.strip().lower()
    for f in FEATURES:
        if f["name"].lower() == flag or flag in [a.lower() for a in f["aliases"]]:
            return f
    return None


def _overview(is_owner: bool) -> str:
    lines = ["📖 洛书 功能总览", "", "查单个功能用法：/help 功能名", ""]
    for cat in CATEGORY_ORDER:
        if cat in (CAT_OWNER, CAT_CRUSH) and not is_owner:
            continue
        items = [f for f in FEATURES if f["category"] == cat]
        if not items:
            continue
        lines.append(f"【{cat}】")
        for f in items:
            lines.append(f"  {f['name']:<6} {f['desc']}")
        lines.append("")
    lines.append("直接用 @我，或喊一声「洛书 + 内容」就能聊天（所有人都可以）；")
    lines.append("发「/help 功能名」看具体用法。")
    return "\n".join(lines).rstrip()


def _detail(f: dict) -> str:
    return (
        f"📖 {f['name']}\n"
        f"  分类：{f['category']}\n"
        f"  用法：{f['usage']}\n"
        f"  示例：{f['example']}\n"
        f"  说明：{f['desc']}"
    )


# ==================== /help ====================
help_cmd = on_command("help", aliases={"h", "帮助", "菜单"}, priority=5, block=True)


@help_cmd.handle()
async def _handle_help(bot: Bot, event: MessageEvent, arg: Message = CommandArg()):
    log_command("help", event)
    role = await get_role(bot, event)
    is_owner = role >= 4

    flag = arg.extract_plain_text().strip()
    if flag:
        f = _find_feature(flag)
        if f:
            if f["category"] in (CAT_OWNER, CAT_CRUSH) and not is_owner:
                await help_cmd.finish("这个功能仅「心动专属」可见哦。")
            await help_cmd.finish(_detail(f))
        await help_cmd.finish(f"没找到「{flag}」这个功能。\n发 /help 查看功能总览。")

    await help_cmd.finish(_overview(is_owner))


# ==================== @机器人 功能清单 ====================
def _is_menu_query() -> Rule:
    """@机器人 并说「功能清单 / 功能列表 / 菜单」等词时，返回功能清单。

    优先级设为 98（低于 AI 对话的 97，即先让 AI 对话判断，
    但这里用 rule 精确拦截，避免被 AI 抢走）。
    """

    async def _checker(bot: Bot, event: MessageEvent) -> bool:
        # 必须 @ 了机器人
        at_me = any(
            seg.type == "at" and str(seg.data.get("qq")) == str(bot.self_id)
            for seg in event.get_message()
        )
        if not at_me:
            return False
        text = event.get_message().extract_plain_text().strip()
        return text in {
            "功能清单", "功能列表", "功能", "菜单", "帮助",
            "有哪些功能", "你会什么", "能做什么",
        }

    return Rule(_checker)


menu_query = on_message(rule=_is_menu_query(), priority=96, block=True)


@menu_query.handle()
async def _handle_menu_query(bot: Bot, event: MessageEvent):
    log_command("功能清单", event)
    role = await get_role(bot, event)
    await menu_query.finish(_overview(role >= 4))


# ==================== 喊名字 + /h（例：洛书 /h） ====================
def _called_help() -> Rule:
    """「名字 + 帮助词」也算调用帮助菜单。

    支持：洛书 /h 、洛书 h 、洛书 help 、@机器人 /h 、@机器人 菜单
    这样即使命令没写在句首（前面有名字），也能正确返回功能清单。
    """

    async def _checker(bot: Bot, event: MessageEvent) -> bool:
        text = event.get_message().extract_plain_text().strip()

        # 去掉句首的机器人称呼（洛书 / 洛洛 / 小洛 / 洛）
        rest = text
        for name in _ALL_NAMES:
            if name and rest.startswith(name):
                rest = rest[len(name):]
                break

        # 去掉可能存在的 @机器人（@ 后文本会拼接，这里分两种来源都判断）
        rest = rest.lstrip("，,、:： 　!！?？~～").strip()
        rest = rest.lstrip("/／-—").strip().lower()

        if not rest:
            # 纯 @机器人 不带文字 → 不算帮助
            return False
        return rest in _HELP_WORDS

    return Rule(_checker)


called_help = on_message(rule=_called_help(), priority=95, block=True)


@called_help.handle()
async def _handle_called_help(bot: Bot, event: MessageEvent):
    log_command("功能清单", event)
    role = await get_role(bot, event)
    await called_help.finish(_overview(role >= 4))
