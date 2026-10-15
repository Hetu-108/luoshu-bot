# python3
# -*- coding: utf-8 -*-
# @File    : features.py
# @Usage   : 群级功能开关 —— 每个群可以独立开关功能，互不影响
#
#   /功能                    查看本群功能开关状态
#   /功能 关闭 <功能名>      在本群关闭某个功能
#   /功能 开启 <功能名>      在本群重新开启
#   /功能 重置               恢复本群全部默认（全部开启）
#
# 设计要点：
#   1) 开关按「群」隔离存储（data/features.json），A 群关掉某功能，
#      B 群完全不受影响；
#   2) 只有群主 / 群管理员 / 机器人管理员可以管理本群开关；
#   3) 「管理」类与「帮助」类命令不受开关约束，避免把自己锁死；
#   4) 其他插件通过 guard("功能键") 拿到一条 Rule，拼到自己的匹配器上。
#
# 存储结构： { "<群号>": { "disabled": ["抽签", "运势"] } }
# 采用「记录被关闭项」而非「记录开启项」，这样新增功能默认就是开启的，
# 老群不用做数据迁移。
from typing import Any, Optional, Set

from nonebot import on_command
from nonebot.adapters.onebot.v11 import (
    Bot,
    Event,
    GroupMessageEvent,
    Message,
    MessageEvent,
)
from nonebot.params import CommandArg
from nonebot.rule import Rule

from .core import (
    JsonStore,
    get_role,
    ROLE_GROUP_ADMIN,
    log_command,
)

_store = JsonStore("features.json")

MANAGE_ROLE = ROLE_GROUP_ADMIN  # 群管理员及其以上可管理

# 全局禁用名单：这些功能不允许被群内关闭（防止群被自己锁死）
NEVER_DISABLEABLE = {
    "功能开关",  # 本功能自己
    "帮助",      # /help 与功能总览
    "身份查询",  # whoami，排查权限用
}


# ==================== 可开关功能注册表 ====================
# key     = 用于 storage / guard() 的稳定标识（改名不影响历史数据）
# name    = 展示给用户的中文名（与 help_menu.FEATURES 的 name 对齐）
# aliases = 允许用户输入的其它写法（通常是实际指令词，方便「/功能 关闭 运势」）
# desc    = 菜单里展示的一句话说明
TOGGLEABLE = [
    {"key": "quiz", "name": "科普答题", "aliases": ["答题", "科普", "督师答题"],
     "desc": "每日图文选择题、私聊提交、截止后结算与排行"},
    # ---- AI 对话 ----
    {"key": "ai", "name": "AI对话", "aliases": ["ai", "聊天", "对话"],
     "desc": "和本地大模型聊天（@我 / 喊名字说话）"},
    {"key": "switch", "name": "人设切换", "aliases": ["切换", "人设切换"],
     "desc": "/切换 <人设名> 更换 AI 角色设定"},
    {"key": "persona", "name": "人设列表", "aliases": ["人设", "persona"],
     "desc": "/人设 查看可用人设"},
    {"key": "reset", "name": "重置记忆", "aliases": ["reset", "重置", "清空"],
     "desc": "/reset 清空你与 AI 的对话记忆"},
    {"key": "history", "name": "对话历史", "aliases": ["history", "历史"],
     "desc": "/history 查看已记录轮数"},
    # ---- 娱乐 ----
    {"key": "draw", "name": "抽签", "aliases": ["抽签", "求签", "签"],
     "desc": "/抽签 抽一支今日心情签"},
    {"key": "luck", "name": "今日运势", "aliases": ["运势", "今日运势", "人品", "今日人品"],
     "desc": "/运势 生成本群今日运势卡"},
    {"key": "tarot", "name": "塔罗牌", "aliases": ["塔罗", "tarot", "抽塔罗"],
     "desc": "/塔罗 抽牌并解读"},
    # ---- 群管理（群内可开关，但管理命令本身保持可用）----
    {"key": "stats", "name": "数据统计", "aliases": ["统计", "stats", "数据"],
     "desc": "/统计 查看本群日活与指令排行"},
    {"key": "keywords", "name": "关键词回复", "aliases": ["关键词", "自动回复"],
     "desc": "/关键词 配置本群自动回复"},
    {"key": "keyword_reply", "name": "关键词触发", "aliases": ["关键词触发"],
     "desc": "消息命中关键词时自动回复"},
    {"key": "ban", "name": "自助禁言", "aliases": ["禁言", "ban"],
     "desc": "/禁言 <秒数> 禁言自己"},
    {"key": "whiteboard", "name": "身份查询", "aliases": ["whoami", "身份", "身份查询"],
     "desc": "/whoami 查看自己的权限等级"},
    {"key": "poke", "name": "戳一戳", "aliases": ["戳一戳", "戳"],
     "desc": "被戳一戳时的回应"},
    {"key": "welcome", "name": "入群欢迎", "aliases": ["入群欢迎", "欢迎"],
     "desc": "新人入群时欢迎"},
    {"key": "farewell", "name": "退群提示", "aliases": ["退群提示", "退群"],
     "desc": "有人退群时提示"},
    # ---- 心动专属（管理员的私密互动）----
    {"key": "crush", "name": "心动互动", "aliases": ["心动", "心动互动"],
     "desc": "洛书对「熟人」的专属应答（语气只比别人亲近一点点）"},
]

# key -> 展示名
_KEY_TO_NAME = {f["key"]: f["name"] for f in TOGGLEABLE}

# 输入 -> key 的反查表：中文名、别名、key 本身都能命中
_NAME_TO_KEY: dict = {}
for _f in TOGGLEABLE:
    _NAME_TO_KEY[_f["name"]] = _f["key"]
    _NAME_TO_KEY[_f["key"]] = _f["key"]
    for _a in _f.get("aliases", []):
        _NAME_TO_KEY.setdefault(_a, _f["key"])


def _gk(event: Any) -> Optional[int]:
    """取事件所属群号；私聊返回 None（私聊不做开关，一律放行）"""
    return getattr(event, "group_id", None)


# ==================== 读写 ====================
def get_disabled(group_id: Optional[int]) -> Set[str]:
    """返回该群被关闭的功能 key 集合"""
    raw = _store.get(group_id, "disabled", []) or []
    return {str(x) for x in raw}


def is_enabled(group_id: Optional[int], key: str) -> bool:
    """判断某群某功能是否开启。私聊（group_id=None）视为开启。"""
    if group_id is None:
        return True
    return key not in get_disabled(group_id)


def set_enabled(group_id: Optional[int], key: str, enabled: bool) -> None:
    """开启/关闭某群某功能"""
    if group_id is None:
        return
    disabled = get_disabled(group_id)
    if enabled:
        disabled.discard(key)
    else:
        disabled.add(key)
    _store.put(group_id, "disabled", sorted(disabled))


def reset_group(group_id: Optional[int]) -> None:
    """恢复该群全部默认（清空关闭名单）"""
    if group_id is None:
        return
    _store.put(group_id, "disabled", [])


def resolve_key(flag: str) -> Optional[str]:
    """把用户输入的名字/别名/ key 解析成内部 key"""
    flag = (flag or "").strip()
    if not flag:
        return None
    if flag in _NAME_TO_KEY:
        return _NAME_TO_KEY[flag]
    low = flag.lower()
    for name, key in _NAME_TO_KEY.items():
        if name.lower() == low:
            return key
    return None


# ==================== 供其它插件复用的 Rule ====================
# 用法（在目标插件里）：
#     from .features import guard
#     my_cmd = on_command("抽签", rule=guard("draw"), ...)
#
# 说明：nonebot 允许把 Rule 传给匹配器；这里返回 Rule，
#       在规则校验阶段按「群号 + 功能 key」判断是否放行。
def guard(key: str) -> Rule:
    """生成一条规则：当本群未关闭该功能时通过。

    参数用 event 泛型（不限定 MessageEvent），因为本规则也会挂在
    通知类匹配器（戳一戳 / 入群 / 退群）上——那些事件不是消息事件，
    但同样带 group_id。
    """

    async def _checker(event: Event) -> bool:
        return is_enabled(_gk(event), key)

    return Rule(_checker)


# ==================== 管理命令 ====================
fs = on_command("功能", aliases={"功能开关", "开关"}, priority=4, block=True)


def _status_lines(group_id: Optional[int]) -> list:
    """按类别输出开关状态"""
    disabled = get_disabled(group_id)
    lines = []
    # 按 TOGGLEABLE 中的顺序分组，用 name 前缀判断过于脆弱，
    # 这里直接按 key 在注册表中的顺序线性展示，用分隔线区分 AI/娱乐/管理/心动。
    groups = [
        ("AI 对话", ["ai", "switch", "persona", "reset", "history"]),
        ("娱乐", ["draw", "luck", "tarot"]),
        ("群管理", ["stats", "keywords", "keyword_reply", "ban", "whiteboard"]),
        ("通知", ["poke", "welcome", "farewell"]),
        ("心动专属", ["crush"]),
    ]
    for title, keys in groups:
        items = [f for k in keys for f in TOGGLEABLE if f["key"] == k]
        if not items:
            continue
        lines.append(f"【{title}】")
        for f in items:
            mark = "❌ 已关闭" if f["key"] in disabled else "✅ 开启  "
            # 中文按显示宽度对齐（中文算 2 格）
            w = sum(2 if ord(ch) > 127 else 1 for ch in f["name"])
            pad = " " * max(1, 12 - w)
            lines.append(f"  {f['name']}{pad}{mark}")
        lines.append("")
    return lines


@fs.handle()
async def _handle_fs(bot: Bot, event: MessageEvent, arg: Message = CommandArg()):
    role = await get_role(bot, event)

    if not isinstance(event, GroupMessageEvent):
        await fs.finish("功能开关是「按群」设置的，请在群里使用哦。")

    gid = event.group_id
    text = arg.extract_plain_text().strip()

    # 无参数：展示状态
    if not text:
        lines = [f"⚙️ 本群（{gid}）功能开关", ""]
        lines.extend(_status_lines(gid))
        lines.append("管理：/功能 关闭 <功能名> 、 /功能 开启 <功能名> 、 /功能 重置")
        lines.append("（仅群主 / 群管理员 / 管理员可修改；只影响本群）")
        log_command("功能开关:查看", event)
        await fs.finish("\n".join(lines).rstrip())

    if role < MANAGE_ROLE:
        await fs.finish("只有群主 / 群管理员 / 管理员可以修改本群的功能开关哦。")

    parts = text.split(maxsplit=1)
    action = parts[0]
    target = parts[1].strip() if len(parts) > 1 else ""

    # 重置
    if action in ("重置", "reset", "全部开启", "恢复"):
        reset_group(gid)
        log_command("功能开关:重置", event)
        await fs.finish("已把本群功能全部恢复为默认（开启）。其他群不受影响。")

    # 开启 / 关闭
    if action in ("关闭", "开", "开启", "禁用", "启用", "关", "off", "on", "disable", "enable"):
        enable = action in ("开启", "开", "启用", "on", "enable")
        if not target:
            await fs.finish(f"用法：/功能 {'开启' if enable else '关闭'} <功能名>\n发 /功能 查看功能名。")

        # 支持一次多个：/功能 关闭 抽签 运势 塔罗
        names = target.replace("，", " ").replace(",", " ").split()
        ok, bad, locked = [], [], []
        for n in names:
            key = resolve_key(n)
            if not key:
                bad.append(n)
                continue
            if not enable and _KEY_TO_NAME.get(key) in NEVER_DISABLEABLE:
                locked.append(_KEY_TO_NAME.get(key, n))
                continue
            set_enabled(gid, key, enable)
            ok.append(_KEY_TO_NAME.get(key, n))
            log_command(f"功能开关:{'开启' if enable else '关闭'}:{key}", event)

        word = "开启" if enable else "关闭"
        out = []
        if ok:
            out.append(f"已在本群{word}：{'、'.join(ok)}")
        if locked:
            out.append(f"「{'、'.join(locked)}」是管理功能，不能关闭，已跳过。")
        if bad:
            # 区分三类：机器人管理员专属命令 / 保护项（不可关） / 纯粹拼错了
            admin_cmds = {"stat", "say", "model", "status", "状态", "代言", "发言", "模型"}
            protect = {"功能", "功能开关", "开关", "帮助", "help", "h", "菜单"}
            advise, lock_bad, wrong = [], [], []
            for n in bad:
                low = n.lower()
                if low in admin_cmds:
                    advise.append(n)
                elif n in protect or low in protect:
                    lock_bad.append(n)
                else:
                    wrong.append(n)
            if advise:
                out.append(f"「{'、'.join(advise)}」是机器人管理员专属命令，不按群开关。")
            if lock_bad:
                out.append(f"「{'、'.join(lock_bad)}」不能被关闭（关了就找不回来了）。")
            if wrong:
                out.append(f"没找到功能：{'、'.join(wrong)}")
            out.append("发 /功能 可以看本群支持开关的全部功能名。")
        if not out:
            out.append("没有可执行的操作。")
        out.append("（仅影响本群）")
        await fs.finish("\n".join(out))

    await fs.finish("未知操作。支持：/功能（查看）、开启、关闭、重置")
