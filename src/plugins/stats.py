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
import time

from nonebot import on_command, get_driver, require
from nonebot.message import event_preprocessor
from nonebot.adapters.onebot.v11 import MessageEvent, Bot, Message, GroupMessageEvent, Event
from nonebot.params import CommandArg
from nonebot.rule import Rule

from .core import JsonStore, get_role, ROLE_GROUP_ADMIN, log_command
from .features import guard

_store = JsonStore("stats.json")

STATS_ROLE = ROLE_GROUP_ADMIN


# 进程退出前把未落盘的统计刷入磁盘，尽量避免丢失
@get_driver().on_shutdown
async def _flush_on_shutdown():
    _flush()

# 批量写入：高频消息不每条都落盘，累计到阈值或超过时间间隔才保存，
# 显著降低磁盘 I/O；进程退出前会 flush 一次，尽量不丢数据。
_FLUSH_EVERY = 100          # 累计 100 次改动落盘一次
_FLUSH_INTERVAL = 60        # 或每 60 秒至少落盘一次
_dirty = 0                  # 自上次落盘以来累计的改动次数
_last_flush = time.time()   # 上次落盘时间


def _mark_dirty() -> None:
    """标记有一次改动；达到阈值或超时则真正落盘"""
    global _dirty, _last_flush
    _dirty += 1
    now = time.time()
    if _dirty >= _FLUSH_EVERY or (now - _last_flush) >= _FLUSH_INTERVAL:
        _flush()


def _flush() -> None:
    """强制把内存中的数据落盘"""
    global _dirty, _last_flush
    _store.save()
    _dirty = 0
    _last_flush = time.time()


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

    # 改写内存副本后标记待落盘（不直接 put，避免每条消息都写文件）
    _store.set_group(gid, {**_store.get_group(gid), "daily": day})
    _mark_dirty()


def record_command(group_id, cmd: str) -> None:
    """记录一次指令调用"""
    if not group_id:
        return
    date = _today_key()
    day = _store.get(group_id, "daily", {}) or {}
    entry = day.setdefault(date, {"messages": 0, "users": [], "commands": {}})
    entry["commands"][cmd] = entry["commands"].get(cmd, 0) + 1
    _store.set_group(group_id, {**_store.get_group(group_id), "daily": day})
    _mark_dirty()


@event_preprocessor
async def _handle_track(event: Event):
    # 阻断型 matcher 会跳过低优先级 tracker；事件预处理保证每条消息只记一次。
    if isinstance(event, GroupMessageEvent):
        record_message(event)


@require("nonebot_plugin_apscheduler").scheduler.scheduled_job(
    "interval", seconds=_FLUSH_INTERVAL, id="luoshu_stats_flush"
)
async def _periodic_flush():
    # 即使最后一条消息后一直没有新消息，也必须在一分钟内落盘。
    if _dirty:
        _flush()


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
    _flush()  # 先把攒着的统计落盘，保证读到的是最新数据
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
