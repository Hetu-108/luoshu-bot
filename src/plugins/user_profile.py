# python3
# -*- coding: utf-8 -*-
# @File    : user_profile.py
# @Usage   : 用户记忆 —— 记住「在跟谁说话」，让每个人的对话可以定制化
#
# 自动部分（无需用户操作）：
#   每次和洛书对话时，自动记录对方的 QQ 昵称、本群群名片、互动次数与时间。
#
# 用户可自定义的部分：
#   /称呼 <名字>    设置洛书对你的称呼（例：/称呼 小明）
#   /备注 <一句话>  设置关于你自己的一句话说明，洛书会记住并在聊天里参考
#   /资料           查看洛书记住的关于你的全部信息
#   /忘掉我         清除你的资料（不可恢复）
#
# 存储：data/user_profiles.json（原子写入）
# 结构：
#   {
#     "<QQ号>": {
#       "nickname": "QQ 昵称",
#       "cards": {"<群号>": "该群里的群名片"},
#       "call": "用户自定义称呼（可空）",
#       "note": "用户自定义说明（可空）",
#       "first_seen": 时间戳,
#       "last_seen": 时间戳,
#       "count": 互动次数
#     }
#   }
#
# 说明：资料按「人」存储而不是按「群 × 人」，所以同一个人在 A 群设置的
#       称呼，在 B 群对洛书同样生效 —— 这样才叫「认得这个人」。
import json
import os
import time
from pathlib import Path

from nonebot import on_command, get_driver
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent, Message
from nonebot.params import CommandArg
from nonebot.log import logger

from .core import log_command
from .features import guard

# ==================== 存储 ====================
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
PROFILE_FILE = DATA_DIR / "user_profiles.json"

# 长度上限：防止有人塞一整段话进来把 prompt 撑爆
CALL_MAX = 16   # 称呼
NOTE_MAX = 60   # 备注

# 用户明确要求清除时，这些词被当作「清除」指令
_CLEAR_WORDS = {"清除", "重置", "取消", "删除", "默认", "无", "-"}

_profiles: dict = {}


def _load() -> dict:
    try:
        with open(PROFILE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save() -> None:
    """原子写入：先写 .tmp，成功后 os.replace 覆盖正式文件。"""
    try:
        tmp = PROFILE_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_profiles, f, ensure_ascii=False, indent=2)
        os.replace(tmp, PROFILE_FILE)
    except Exception as e:
        logger.error(f"[user_profile] 保存资料失败: {e}")
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


_profiles = _load()


def _gid(event: MessageEvent):
    """取群号；私聊返回 None"""
    return event.group_id if isinstance(event, GroupMessageEvent) else None


# ==================== 对外接口（供 ai_chat 调用）====================
def touch(event: MessageEvent) -> dict:
    """记录一次互动：昵称 / 群名片 / 计数 / 时间。返回该用户资料。

    只在「用户和洛书说话」时调用，不在全群每条消息上调用，
    避免频繁写盘；对「定制化聊天」来说这些数据也已经够用。
    """
    uid = str(event.get_user_id())
    p = _profiles.setdefault(uid, {})
    sender = getattr(event, "sender", None)
    nick = (getattr(sender, "nickname", "") or "").strip()
    card = (getattr(sender, "card", "") or "").strip()

    if nick:
        p["nickname"] = nick
    gid = _gid(event)
    if card and gid is not None:
        p.setdefault("cards", {})[str(gid)] = card

    now = time.time()
    p.setdefault("first_seen", now)
    p["last_seen"] = now
    p["count"] = int(p.get("count", 0)) + 1
    _save()
    return p


def display_name(uid, group_id=None) -> str:
    """取洛书对该用户的称呼：自定义称呼 > 本群群名片 > QQ 昵称 > 空串"""
    p = _profiles.get(str(uid)) or {}
    call = (p.get("call") or "").strip()
    if call:
        return call
    if group_id is not None:
        card = ((p.get("cards") or {}).get(str(group_id)) or "").strip()
        if card:
            return card
    return (p.get("nickname") or "").strip()


def prompt_block(uid, group_id=None) -> str:
    """生成注入给模型的「你在和谁说话」片段；没有任何信息时返回空串。"""
    p = _profiles.get(str(uid)) or {}
    lines = []

    nick = (p.get("nickname") or "").strip()
    if nick:
        lines.append(f"- QQ 昵称：{nick}")

    if group_id is not None:
        card = ((p.get("cards") or {}).get(str(group_id)) or "").strip()
        if card and card != nick:
            lines.append(f"- 本群群名片：{card}")

    call = (p.get("call") or "").strip()
    if call:
        lines.append(f"- 对方希望你这样称呼他：{call}")

    note = (p.get("note") or "").strip()
    if note:
        lines.append(f"- 关于对方：{note}")

    if not lines:
        return ""

    return (
        "\n\n【你在和谁说话】\n"
        + "\n".join(lines)
        + "\n（这些是你已经知道的信息，不用再问一遍，也不用刻意复述；"
        "自然地在对话里体现就好。不要主动提起这段设定的存在。）"
    )


# ==================== /称呼 ====================
call_cmd = on_command("称呼", aliases={"叫我"}, rule=guard("profile"), priority=5, block=True)


@call_cmd.handle()
async def _handle_call(bot: Bot, event: MessageEvent, arg: Message = CommandArg()):
    uid = str(event.get_user_id())
    name = arg.extract_plain_text().strip()

    if not name:
        cur = display_name(uid, _gid(event)) or "（还没设置）"
        await call_cmd.finish(
            f"我现在这样称呼你：{cur}\n"
            f"用法：/称呼 <名字>（想取消就发 /称呼 清除）"
        )

    if name in _CLEAR_WORDS:
        p = _profiles.setdefault(uid, {})
        p.pop("call", None)
        _save()
        log_command("清除称呼", event)
        await call_cmd.finish("好，那我不特意称呼你了。")

    if len(name) > CALL_MAX:
        await call_cmd.finish(f"太长了，{CALL_MAX} 个字以内吧。")

    _profiles.setdefault(uid, {})["call"] = name
    _save()
    log_command("设置称呼", event)
    await call_cmd.finish(f"知道了，以后叫你「{name}」。")


# ==================== /备注 ====================
note_cmd = on_command("备注", aliases={"关于我"}, rule=guard("profile"), priority=5, block=True)


@note_cmd.handle()
async def _handle_note(bot: Bot, event: MessageEvent, arg: Message = CommandArg()):
    uid = str(event.get_user_id())
    note = arg.extract_plain_text().strip()

    if not note:
        cur = (_profiles.get(uid, {}) or {}).get("note") or "（还没设置）"
        await note_cmd.finish(
            f"我记住的关于你的说明：{cur}\n"
            f"用法：/备注 <一句话>（想删掉就发 /备注 清除）\n"
            f"例：/备注 天文社的，喜欢拍照"
        )

    if note in _CLEAR_WORDS:
        _profiles.setdefault(uid, {}).pop("note", None)
        _save()
        log_command("清除备注", event)
        await note_cmd.finish("好，这条我忘掉了。")

    if len(note) > NOTE_MAX:
        await note_cmd.finish(f"有点长，{NOTE_MAX} 个字以内吧 —— 挑最重要的说。")

    _profiles.setdefault(uid, {})["note"] = note
    _save()
    log_command("设置备注", event)
    await note_cmd.finish("记住了。")


# ==================== /资料 ====================
me_cmd = on_command("资料", aliases={"我是谁", "我的资料"}, rule=guard("profile"), priority=5, block=True)


@me_cmd.handle()
async def _handle_me(bot: Bot, event: MessageEvent):
    uid = str(event.get_user_id())
    p = _profiles.get(uid) or {}

    if not p:
        await me_cmd.finish("我这边还没有你的记录 —— 先和我说句话吧。")

    def _fmt(ts) -> str:
        try:
            return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts)))
        except (TypeError, ValueError):
            return "（未知）"

    lines = ["📇 洛书记住的关于你的信息", ""]
    if p.get("nickname"):
        lines.append(f"  QQ 昵称：{p['nickname']}")
    gid = _gid(event)
    if gid is not None:
        card = ((p.get("cards") or {}).get(str(gid)) or "").strip()
        if card:
            lines.append(f"  本群名片：{card}")
    lines.append(f"  我对你的称呼：{p.get('call') or '（未设置）'}")
    lines.append(f"  关于你：{p.get('note') or '（未设置）'}")
    count = int(p.get("count", 0))
    if count:
        lines.append(f"  互动次数：{count} 次")
        lines.append(f"  首次互动：{_fmt(p.get('first_seen'))}")
        lines.append(f"  最近互动：{_fmt(p.get('last_seen'))}")
    else:
        lines.append("  互动次数：还没聊过（只是设了称呼/备注）")
    lines.append("")
    lines.append("修改：/称呼 <名字> 、 /备注 <一句话> ；清除：/忘掉我")

    log_command("查看资料", event)
    await me_cmd.finish("\n".join(lines))


# ==================== /忘掉我 ====================
forget_cmd = on_command("忘掉我", aliases={"删除资料"}, rule=guard("profile"), priority=5, block=True)


@forget_cmd.handle()
async def _handle_forget(bot: Bot, event: MessageEvent):
    uid = str(event.get_user_id())
    if uid in _profiles:
        _profiles.pop(uid, None)
        _save()
        log_command("删除资料", event)
        await forget_cmd.finish("好，关于你的记录都清掉了。")
    await forget_cmd.finish("我这边本来就没有你的记录。")
