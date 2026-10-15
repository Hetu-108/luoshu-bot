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
# 注意：「功能」不在这里 —— 它是真实命令（/功能 群功能开关），
#       若列入会被当成帮助词劫持。@我 说「功能」仍走 menu_query 分支。
_HELP_WORDS = {
    "h", "help", "菜单", "帮助", "功能清单", "功能列表",
    "你会什么", "能做什么", "有哪些功能",
}

# 「洛书熟人」的称呼（与 fun.py / ai_chat.py 一致，来自 .env 的 CRUSH_NAMES）
_CRUSH = _split_names(getattr(_cfg, "crush_names", "")) or ["小美"]
_CRUSH0 = _CRUSH[0]  # 菜单里展示用的默认称呼

# 分类名（集中定义，方便统一改）
CAT_OWNER = "管理员专属"
CAT_CRUSH = "心动专属"

# ==================== 功能注册表 ====================
# 每项：
#   "name"    —— 显示名（带真正的命令词，方便对上）
#   "aliases" —— 还能怎么叫它（/help 用它检索）
#   "category"—— 所属分类
#   "usage"   —— 怎么发这条命令
#   "example" —— 一个能直接抄的例子
#   "desc"    —— 总览里的一句话（尽量说"干什么用"，别只重复名字）
#   "what"    —— 详情页的通俗解释（它做什么 / 什么时候用）
FEATURES = [
    # ---- AI 对话 ----
    {
        "name": "AI对话",
        "aliases": ["ai", "聊天", "对话"],
        "category": "AI 对话",
        "usage": "@洛书 <内容>\n  或 直接喊「洛书 <内容>」\n  或 /ai <内容>",
        "example": "@洛书 帮我写个自我介绍",
        "desc": "和洛书聊天，她记得你们聊过什么",
        "what": (
            "让洛书用本地大模型跟你对话，像和群友聊天一样。\n"
            "她会记住上下文（默认最近 20 轮），所以可以接着上一句说。\n"
            "三种喊法：@她、句首喊名字、或 /ai。所有人都能用。"
        ),
    },
    {
        "name": "人设切换 /切换",
        "aliases": ["切换", "人设", "人设切换", "换人设"],
        "category": "AI 对话",
        "usage": "/人设              先看有哪些人设\n  /切换 <人设名>     换成那个人设",
        "example": "/切换 助手",
        "desc": "给洛书换个「性格」，比如变毒舌或变正经",
        "what": (
            "洛书内置好几种性格（人设），比如「洛书」清冷话少、「助手」正经、\n"
            "「傲娇」毒舌、「学姐」温柔，「督师」则是协会科普活动用的口吻。\n"
            "换人设只影响你自己的对话，不会动到别人，默认一直是「洛书」。\n"
            "注意：换人设会清空你和她的聊天记忆。"
        ),
    },
    {
        "name": "洛书称呼我 /称呼",
        "aliases": ["称呼", "叫我", "用户名"],
        "category": "AI 对话",
        "usage": "/称呼 <名字>\n  /称呼 清除     取消自定义称呼",
        "example": "/称呼 会长",
        "desc": "告诉洛书该怎么称呼你，她会一直记住",
        "what": (
            "不设置的话，洛书会按你的群名片 / QQ 昵称来称呼你。\n"
            "如果你想让她换个叫法（小名、外号、职务都行），发 /称呼 <名字>。\n"
            "这个设置跟着「你这个人」走 —— 在哪个群和她说话都生效。"
        ),
    },
    {
        "name": "告诉洛书关于我 /备注",
        "aliases": ["备注", "关于我"],
        "category": "AI 对话",
        "usage": "/备注 <一句话>\n  /备注 清除     删掉这条说明",
        "example": "/备注 天文社的，喜欢拍照",
        "desc": "留一句话让洛书记住你，她会顺着这个和你聊",
        "what": (
            "写一句关于你自己的话（身份、爱好、在意的事都行），洛书会记住，\n"
            "之后和你聊天时会自然地参考，不用每次都重新自我介绍。\n"
            "建议 60 字以内，挑最重要的说。"
        ),
    },
    {
        "name": "我的资料 /资料",
        "aliases": ["资料", "我是谁", "我的资料"],
        "category": "AI 对话",
        "usage": "/资料",
        "example": "/资料",
        "desc": "看看洛书记住了关于你的哪些信息",
        "what": (
            "列出洛书对你的全部印象：昵称、群名片、她平时怎么称呼你、\n"
            "你留的备注，以及你们互动过多少次。\n"
            "想删干净就发 /忘掉我。"
        ),
    },
    {
        "name": "重置记忆 /reset",
        "aliases": ["reset", "重置", "清空", "重置记忆"],
        "category": "AI 对话",
        "usage": "/reset",
        "example": "/reset",
        "desc": "让洛书忘掉你和她的聊天记录，重新开始",
        "what": (
            "洛书会记住你们聊过的话。聊得太多她想换话题、或者你说错话了\n"
            "想翻篇，发 /reset 就能清空这份记忆，之后她当你们刚认识。\n"
            "只清你自己的，别人的记忆不受影响。"
        ),
    },
    {
        "name": "对话历史 /history",
        "aliases": ["history", "历史"],
        "category": "AI 对话",
        "usage": "/history",
        "example": "/history",
        "desc": "看看当前用的是哪个人设、哪个模型、聊了几轮",
        "what": (
            "一条命令告诉你三个信息：你现在用的是哪个人设、走的哪个本地模型、\n"
            "以及洛书已经记住你多少轮对话。排查「她怎么不记得我了」时很有用。"
        ),
    },

    # ---- 娱乐 ----
    {
        "name": "抽签",
        "aliases": ["抽签", "求签", "签", "心情签"],
        "category": "娱乐",
        "usage": "/抽签\n  /抽签 @某人     帮对方抽",
        "example": "/抽签",
        "desc": "抽一支趣味心情签，博个乐子",
        "what": (
            "抽一支「今日心情签」，带上等级和一句签文。同一天抽结果固定，\n"
            "不会反复刷出不一样的。也可以 @某人 帮 TA 抽一支。纯娱乐，别当真。"
        ),
    },
    {
        "name": "今日运势",
        "aliases": ["运势", "人品", "今日人品", "今日运势"],
        "category": "娱乐",
        "usage": "/运势\n  /运势 @某人     帮对方看",
        "example": "/运势",
        "desc": "生成今天的趣味运势卡：桃花、财运、宜忌",
        "what": (
            "生成一张当日运势卡，包含整体运、桃花运、财运的星数，\n"
            "以及「宜做什么、忌做什么」。每人每天结果固定。\n"
            "就是图一乐的心情卡片，不是真预测。"
        ),
    },
    {
        "name": "塔罗牌",
        "aliases": ["塔罗", "tarot", "抽塔罗", "塔罗牌"],
        "category": "娱乐",
        "usage": "/塔罗          抽 1 张\n  /塔罗 三张     抽「过去 / 现在 / 未来」3 张",
        "example": "/塔罗 三张",
        "desc": "抽塔罗牌，带牌意解读（含正逆位）",
        "what": (
            "从完整 78 张塔罗牌里抽牌，给出牌名、正位还是逆位、以及牌意解读。\n"
            "单张适合随手翻，三张牌阵可以看「过去 / 现在 / 未来」。\n"
            "牌只是镜子，当个换个角度想问题的契机就好。"
        ),
    },

    # ---- 心动专属（洛书熟人）----
    {
        "name": "心动打招呼",
        "aliases": ["打招呼"] + _CRUSH,
        "category": CAT_CRUSH,
        "usage": f"直接说「{_CRUSH0}」「{_CRUSH0}在吗」",
        "example": f"{_CRUSH0}",
        "desc": "洛书比较熟的人叫她时，比平时更放松一点的应答",
        "what": (
            f"当「洛书比较熟的人」（配置里叫 {_CRUSH0}）单独喊她名字或找她时，\n"
            "她会用比平时放松、随意一点的语气回应——仅此而已，不会夸张。\n"
            "其他人这样喊，不会触发这个专属语气。"
        ),
    },
    {
        "name": "心动夸奖",
        "aliases": ["夸夸", "真棒"],
        "category": CAT_CRUSH,
        "usage": "消息里包含 真棒 / 好棒 / 夸夸 / 可爱 等词",
        "example": f"{_CRUSH0}真棒",
        "desc": "洛书被熟人夸时，会自然地接一句",
        "what": (
            "如果「洛书比较熟的人」夸她（真棒 / 好棒 / 可爱 / 夸夸…），\n"
            "她会比平时更放松地接一句，语气随意一点（比如「哈哈，你嘴挺甜」）。\n"
            "其他人夸她不会有这个专属反应。"
        ),
    },
    {
        "name": "心动问安",
        "aliases": ["早安", "晚安"],
        "category": CAT_CRUSH,
        "usage": "直接说 早安 / 晚安 / 午安",
        "example": "晚安",
        "desc": "熟人问早晚安时的专属问候",
        "what": (
            "「洛书比较熟的人」说早安 / 晚安 / 午安时，她会用更自然、\n"
            "更关心一点的语气回应（比如叮嘱早点睡、记得吃饭）。\n"
            "这是只给那个人的专属问候。"
        ),
    },
    {
        "name": "洛书心事 /心事",
        "aliases": ["心事", "小心思"] + _CRUSH,
        "category": CAT_CRUSH,
        "usage": "/心事",
        "example": "/心事",
        "desc": "偷偷看一眼洛书的一点小心事",
        "what": (
            "一张小小的「心事卡」——里面写着洛书关注的人是谁，\n"
            "以及她说给自己听的一句话。只有「她比较熟的那位」能看，\n"
            "其他人发会被她挡回去。"
        ),
    },

    # ---- 管理 ----
    {
        "name": "数据统计 /统计",
        "aliases": ["统计", "stats", "数据", "统计数据", "群数据"],
        "category": "管理",
        "usage": "/统计              今天\n  /统计 周           本周\n  /统计 月           本月",
        "example": "/统计 周",
        "desc": "看本群有多少人说话、发了多少条、哪个指令用得最多",
        "what": (
            "想看这个群活不活跃，就用它。会统计：这段时间的消息总数、\n"
            "有多少人发过言、以及各种指令被用了多少次（带排行）。\n"
            "数据按群各自统计，看不到别的群。仅群主 / 群管理员 / 管理员可用。"
        ),
    },
    {
        "name": "关键词回复 /关键词",
        "aliases": ["关键词", "自动回复"],
        "category": "管理",
        "usage": "/关键词                    查看本群已设的关键词\n"
                 "  /关键词 添加 <触发词> <回复>   加一条\n"
                 "  /关键词 删除 <触发词>          删一条",
        "example": "/关键词 添加 早 早上好呀|早安",
        "desc": "教洛书：群里有人发某个词，她就自动回一句",
        "what": (
            "比如设了「早 → 早上好呀」，那群里任何人发「早」，洛书就会自动回。\n"
            "适合做群规提示、固定问答。回复可以写多条，用 | 隔开，\n"
            "触发时随机挑一条（例：早上好呀|早安|起床啦）。\n"
            "每个群各自配置，互不影响。仅群主 / 群管理员 / 管理员可用。"
        ),
    },
    {
        "name": "功能开关 /功能",
        "aliases": ["功能", "开关", "群功能", "功能清单", "功能列表"],
        "category": "管理",
        "usage": "/功能                      查看本群开关状态\n"
                 "  /功能 关闭 <功能名>        在本群关掉（可写多个）\n"
                 "  /功能 开启 <功能名>        在本群重新开\n"
                 "  /功能 重置                 本群全部恢复默认",
        "example": "/功能 关闭 运势 塔罗",
        "desc": "在本群关掉/打开某个功能，不影响别的群",
        "what": (
            "如果这个群不想用「运势」「塔罗」之类的功能，可以单独关掉，\n"
            "别的群照常用。比如 /功能 关闭 运势 塔罗。\n"
            "随时 /功能 开启 恢复，或 /功能 重置 一键全开。\n"
            "仅群主 / 群管理员 / 管理员可改本群。"
        ),
    },
    {
        "name": "自助禁言 /禁言",
        "aliases": ["ban", "禁言", "自助禁言"],
        "category": "管理",
        "usage": "/禁言 <秒数>      （0 表示解除）",
        "example": "/禁言 60",
        "desc": "让自己被禁言一会儿（自我约束用）",
        "what": (
            "给自己关一小时的嘴，比如去写作业/上班时怕自己刷群。\n"
            "发 /禁言 3600 就是把自己禁言一小时，/禁言 0 解除。\n"
            "注意：只能禁言自己，不能用来禁别人。"
        ),
    },
    {
        "name": "身份查询 /whoami",
        "aliases": ["whoami", "身份", "身份查询"],
        "category": "管理",
        "usage": "/whoami      也可以直接打 whoami",
        "example": "/whoami",
        "desc": "看看自己在群里是什么权限等级",
        "what": (
            "查你的权限等级（管理员 / 群主 / 群管理员 / 普通成员）。\n"
            "如果发现某个命令你没权限用，先发这个确认一下自己是什么身份。"
        ),
    },

    # ---- 管理员专属 ----
    {
        "name": "模型切换 /model",
        "aliases": ["model", "模型", "模型切换", "换模型"],
        "category": CAT_OWNER,
        "usage": "/model             看看当前模型和本机有哪些\n  /model <模型名>    切换",
        "example": "/model qwen2.5:14b",
        "desc": "给 AI 换个「脑子」（本地大模型）",
        "what": (
            "洛书的 AI 能力来自本机跑的 Ollama 模型。这条命令可以查当前用的是哪个，\n"
            "以及本机还装了哪些，然后换一个。\n"
            "模型越大越聪明也越吃显存，不确定就先用默认的。仅机器人管理员可用。"
        ),
    },
    {
        "name": "运行状态 /stat",
        "aliases": ["stat", "状态", "运行状态", "运行", "机器人状态"],
        "category": CAT_OWNER,
        "usage": "/stat",
        "example": "/stat",
        "desc": "看看这个机器人自身跑得怎么样（系统/内存/群数）",
        "what": (
            "体检报告：系统版本、Python 版本、机器人加了多少群和好友、\n"
            "当前用的模型与地址、以及它占了多少内存、CPU 多少、\n"
            "这台机器开机多久了。机器人卡顿或掉线时先看这个。\n"
            "仅机器人管理员可用。"
        ),
    },
    {
        "name": "代言 /say",
        "aliases": ["say", "发言", "代言", "代言人", "喊话"],
        "category": CAT_OWNER,
        "usage": "/say <要说的话>",
        "example": "/say 大家好，我是洛书",
        "desc": "借用洛书的嘴发一条消息（她不会当 AI 回复）",
        "what": (
            "想让洛书原样说一句话、而不是让 AI 去生成回答时，用它。\n"
            "比如发通知、群发一句公告。发 /say 大家好，群里就会直接出现\n"
            "「大家好」这条消息，署名是洛书。\n"
            "在群里发就发到那个群，私聊发就发给你自己。仅机器人管理员可用。"
        ),
    },
]

# 分类顺序
CATEGORY_ORDER = ["AI 对话", "娱乐", CAT_CRUSH, "管理", CAT_OWNER]


def _name_keys(name: str) -> list:
    """把显示名拆成若干可检索的关键字。

    例：「运行状态 /stat」 → ["运行状态 /stat", "运行状态", "stat"]
         「抽签」           → ["抽签"]
    这样 /help stat 和 /help 运行状态 都能命中同一条。
    """
    keys = [name.lower()]
    # 去掉「/ 、」之类的分隔符后逐段拆
    parts = name.replace("/", " ").replace("、", " ").split()
    keys.extend(p.lower() for p in parts if p)
    return keys


def _find_feature(flag: str):
    """按名称、别名或命令词查找功能"""
    flag = flag.strip().lower().lstrip("/").strip()
    if not flag:
        return None
    for f in FEATURES:
        keys = _name_keys(f["name"])
        keys.append(f["name"].lower())
        keys.extend(a.lower().lstrip("/") for a in f["aliases"])
        if flag in keys:
            return f
    return None


def _overview(is_owner: bool) -> str:
    lines = ["📖 洛书 功能总览", "", "查某个功能到底怎么用：/help 功能名", ""]
    for cat in CATEGORY_ORDER:
        if cat in (CAT_OWNER, CAT_CRUSH) and not is_owner:
            continue
        items = [f for f in FEATURES if f["category"] == cat]
        if not items:
            continue
        lines.append(f"【{cat}】")
        for f in items:
            lines.append(f"  · {f['name']}")
            lines.append(f"      {f['desc']}")
        lines.append("")
    lines.append("想聊天：直接 @我，或喊一声「洛书 + 内容」（所有人都可以）。")
    lines.append("看不懂某个功能？发「/help 功能名」看它的详细说明，")
    lines.append("比如 /help 统计 、 /help say 。")
    return "\n".join(lines).rstrip()


def _detail(f: dict) -> str:
    out = [
        f"📖 {f['name']}",
        f"  分类：{f['category']}",
        "",
        f"  【一句话】{f['desc']}",
    ]
    if f.get("what"):
        out.append("")
        out.append("  【它做什么】")
        for ln in f["what"].split("\n"):
            out.append(f"    {ln}")
    out.append("")
    out.append("  【怎么用】")
    for ln in f["usage"].split("\n"):
        out.append(f"    {ln}")
    out.append("")
    out.append(f"  【例子】{f['example']}")
    return "\n".join(out)


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
        # 是否在叫我：优先用 nonebot 的 event.to_me（适配器已把 at 段删掉并把
        # 以 NICKNAME 开头的文字也剥掉，所以不能再自己找 at 段）。
        # 兜底：部分实现（实测 NapCat 4.18）会整段丢掉 at，此时 to_me 也不会置位，
        # 就再查一次 raw_message 里的 CQ 码。
        at_me = bool(getattr(event, "to_me", False)) or (
            f"[CQ:at,qq={bot.self_id}]" in (getattr(event, "raw_message", "") or "")
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
