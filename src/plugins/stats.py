# python3
# -*- coding: utf-8 -*-
# @File    : stats.py
# @Usage   : 数据统计 —— 对应 F-12
#
#   /统计        今日数据
#   /统计 周     本周数据
#   /统计 月     本月数据
#
# 统计维度：群日活人数、消息总数、指令使用次数排行。
# 权限：仅群主 / 群管理员 / 管理员可查。
# 数据按群隔离（F-07），存放在 data/stats.json。
from collections import defaultdict
from datetime import datetime, timedelta

from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import MessageEvent, Bot, Message, GroupMessageEvent
from nonebot.params import CommandArg
from nonebot.rule import Rule

from .core import JsonStore, get_role, ROLE_GROUP_ADMIN, log_command
from .features import guard

_store = JsonStore("stats.json")

STATS_ROLE = ROLE_GROUP_ADMIN


# ==================== 记录 ====================
def _today_key() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def record_message(event: GroupMessageEvent) -> None:
    """记录一条群消息：按日期统计消息数与活跃用户"""
    gid = event.group_id
    date = _today_key()
    day = _store.get(gid, "daily", {}) or {}

    entry = day.setdefault(date, {"messages": 0, "users": [], "commands": {}})
    entry["messages"] += 1

    uid = str(event.user_id)
    if uid not in entry["users"]:
        entry["users"].append(uid)

    _store.put(gid, "daily", day)


def record_command(group_id, cmd: str) -> None:
    """记录一次指令调用"""
    if not group_id:
        return
    date = _today_key()
    day = _store.get(group_id, "daily", {}) or {}
    entry = day.setdefault(date, {"messages": 0, "users": [], "commands": {}})
    entry["commands"][cmd] = entry["commands"].get(cmd, 0) + 1
    _store.put(group_id, "daily", day)


# 自动记录每条群消息（优先级最低，不影响其它功能）
def _track_rule() -> Rule:
    async def _checker(event: MessageEvent) -> bool:
        return isinstance(event, GroupMessageEvent)

    return Rule(_checker)


tracker = on_message(rule=_track_rule(), priority=99, block=False)


@tracker.handle()
async def _handle_track(event: GroupMessageEvent):
    try:
        record_message(event)
    except Exception:
        pass


# ==================== 日期范围 ====================
def _date_range(scope: str) -> list:
    today = datetime.now().date()
    if scope in ("周", "week", "本周"):
        start = today - timedelta(days=today.weekday())
        return [(start + timedelta(days=i)).strftime("%Y-%m-%d")
                for i in range((today - start).days + 1)]
    if scope in ("月", "month", "本月"):
        start = today.replace(day=1)
        return [(start + timedelta(days=i)).strftime("%Y-%m-%d")
                for i in range((today - start).days + 1)]
    return [today.strftime("%Y-%m-%d")]


# ==================== /统计 ====================
stats = on_command("统计", aliases={"stats", "数据"}, rule=guard("stats"), priority=5, block=True)


@stats.handle()
async def _handle_stats(bot: Bot, event: MessageEvent, arg: Message = CommandArg()):
    role = await get_role(bot, event)
    if role < STATS_ROLE:
        await stats.finish("只有群主 / 群管理员 / 管理员可以查看统计数据哦。")

    if not isinstance(event, GroupMessageEvent):
        await stats.finish("统计功能需要在群里使用。")

    scope_raw = arg.extract_plain_text().strip()
    scope = scope_raw or "日"
    dates = _date_range(scope)

    gid = event.group_id
    day = _store.get(gid, "daily", {}) or {}

    total_msgs = 0
    active_users = set()
    cmd_counter = defaultdict(int)

    for d in dates:
        entry = day.get(d)
        if not entry:
            continue
        total_msgs += entry.get("messages", 0)
        active_users.update(entry.get("users", []))
        for c, n in (entry.get("commands") or {}).items():
            cmd_counter[c] += n

    label = {"周": "本周", "week": "本周", "本周": "本周",
             "月": "本月", "month": "本月", "本月": "本月"}.get(scope, "今日")

    if not day:
        await stats.finish("本群还没有统计数据。让我多在群里待一会儿吧~")

    lines = [
        f"📊 本群{label}数据",
        "-" * 18,
        f"  消息总数：{total_msgs}",
        f"  活跃人数：{len(active_users)}",
        f"  统计天数：{len(dates)} 天",
    ]

    if cmd_counter:
        top = sorted(cmd_counter.items(), key=lambda x: -x[1])[:8]
        lines.append("")
        lines.append("  指令使用排行：")
        for i, (c, n) in enumerate(top, 1):
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, "  ")
            lines.append(f"   {medal} {c}　{n} 次")
    else:
        lines.append("")
        lines.append("  指令使用排行：（暂无记录）")

    await stats.finish("\n".join(lines))
