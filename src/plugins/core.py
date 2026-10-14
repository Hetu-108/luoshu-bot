# python3
# -*- coding: utf-8 -*-
# @File    : core.py
# @Usage   : 公共基础模块 —— 权限分级、指令冷却、多群隔离存储、日志
#
# 这个文件不注册任何指令，只给其它插件调用。
# 对应任务清单：F-05 权限分级、F-06 指令冷却、F-07 多群隔离、F-09 日志
import json
import os
import time
from pathlib import Path
from typing import Any, Optional

from nonebot import get_driver
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent
from nonebot.adapters.onebot.v11.permission import GROUP_ADMIN, GROUP_OWNER
from nonebot.log import logger

driver = get_driver()
cfg = driver.config

_OWNER_QQ = [str(q) for q in (getattr(cfg, "superusers", []) or [])]

# ==================== 路径 ====================
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


# ==================== F-05 权限分级 ====================
# 四级：管理员(4) > 群主(3) > 群管理员(2) > 普通成员(1)
ROLE_OWNER = 4
ROLE_GROUP_OWNER = 3
ROLE_GROUP_ADMIN = 2
ROLE_MEMBER = 1

ROLE_NAMES = {
    ROLE_OWNER: "管理员",
    ROLE_GROUP_OWNER: "群主",
    ROLE_GROUP_ADMIN: "群管理员",
    ROLE_MEMBER: "普通成员",
}


async def get_role(bot: Bot, event: MessageEvent) -> int:
    """判定发送者的权限等级。

    优先使用事件里自带的 sender.role（OneBot 协议会填充，无需额外请求），
    缺失时再回退到 get_group_member_info 查询。
    """
    uid = str(event.get_user_id())
    if uid in _OWNER_QQ:
        return ROLE_OWNER
    if not isinstance(event, GroupMessageEvent):
        return ROLE_MEMBER

    # 1) 事件自带 role（最快，无网络请求）
    role = getattr(event.sender, "role", None)
    if role == "owner":
        return ROLE_GROUP_OWNER
    if role == "admin":
        return ROLE_GROUP_ADMIN
    if role == "member":
        return ROLE_MEMBER

    # 2) 回退：调接口查询
    try:
        info = await bot.get_group_member_info(
            group_id=event.group_id, user_id=int(uid), no_cache=True
        )
        r = (info or {}).get("role")
        if r == "owner":
            return ROLE_GROUP_OWNER
        if r == "admin":
            return ROLE_GROUP_ADMIN
    except Exception as e:
        # 查询失败（如适配器不支持、网络异常）时降级为普通成员，
        # 但记录一条日志便于排查，别静默吞掉。
        logger.debug(f"[core] get_group_member_info 失败，降级为普通成员: {e}")
    return ROLE_MEMBER


async def require_role(bot: Bot, event: MessageEvent, need: int) -> bool:
    """校验权限，不足返回 False（调用方负责发提示）"""
    role = await get_role(bot, event)
    return role >= need


# ==================== F-06 指令冷却 ====================
# key: (指令名, 群号, 用户号) -> 上次触发时间戳
_cooldown: dict = {}
# 冷却记录清理：当条目数超过阈值时，顺带清掉已过期的 key，避免无限增长
_COOLDOWN_MAX = 2000


def _prune_cooldown(now: float, keep_seconds: int = 3600) -> None:
    """清理 _cooldown 中超过 keep_seconds 未更新的条目（内存泄漏防护）"""
    if len(_cooldown) <= _COOLDOWN_MAX:
        return
    for k in [k for k, t in _cooldown.items() if now - t > keep_seconds]:
        _cooldown.pop(k, None)


def check_cooldown(cmd: str, event: MessageEvent, seconds: int) -> bool:
    """判断是否已过冷却。True=可以响应，False=还在冷却中。

    seconds<=0 表示不启用冷却。
    """
    if seconds <= 0:
        return True
    gid = event.group_id if isinstance(event, GroupMessageEvent) else 0
    key = (cmd, gid, str(event.get_user_id()))
    now = time.time()
    last = _cooldown.get(key, 0)
    if now - last < seconds:
        return False
    _cooldown[key] = now
    _prune_cooldown(now)
    return True


def cooldown_remain(cmd: str, event: MessageEvent) -> int:
    """返回剩余冷却秒数（用于提示）"""
    gid = event.group_id if isinstance(event, GroupMessageEvent) else 0
    key = (cmd, gid, str(event.get_user_id()))
    return max(0, int(_cooldown.get(key, 0) + 0 - time.time()))


# ==================== F-07 多群隔离存储 ====================
class JsonStore:
    """一个简易的按群隔离的 JSON 存储。

    结构： { "<群号或global>": { ...数据... } }
    每个群的数据互相独立，A 群的改动不影响 B 群。
    """

    def __init__(self, filename: str):
        self.path = DATA_DIR / filename
        self._data: dict = self._load()

    def _load(self) -> dict:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def save(self) -> None:
        # 原子写入：先写临时文件，成功后再 os.replace 覆盖正式文件。
        # 关键点：json.dump 中途若抛异常（磁盘满 / 权限问题），临时文件会残留
        # 一份「半截 JSON」。必须清理，否则下次写入可能读到损坏内容。
        # 另外返回值应能反映成败，方便上层决定是否重试（默认仍保持无返回值）。
        tmp = self.path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception as e:
            logger.error(f"[core] 保存 {self.path.name} 失败: {e}")
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass

    @staticmethod
    def _gk(group_id: Optional[int]) -> str:
        return str(group_id) if group_id else "private"

    def get_group(self, group_id: Optional[int]) -> dict:
        return self._data.setdefault(self._gk(group_id), {})

    def set_group(self, group_id: Optional[int], value: dict) -> None:
        self._data[self._gk(group_id)] = value

    # 便捷读写单个键
    def get(self, group_id: Optional[int], key: str, default: Any = None) -> Any:
        return self.get_group(group_id).get(key, default)

    def put(self, group_id: Optional[int], key: str, value: Any) -> None:
        self.get_group(group_id)[key] = value
        self.save()


# ==================== F-09 日志 ====================
_usage_log = DATA_DIR / "usage.log"


def log_event(kind: str, detail: str) -> None:
    """记录运行事件到文件（指令调用、异常等）"""
    try:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(_usage_log, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {kind} | {detail}\n")
    except Exception:
        pass


def log_command(cmd: str, event: MessageEvent) -> None:
    """记录一次指令调用：写入日志文件，并累加到统计数据。

    统计数据由 stats 模块负责落盘；这里用延迟导入避免循环依赖。
    """
    gid = event.group_id if isinstance(event, GroupMessageEvent) else "private"
    log_event("CMD", f"{cmd} | group={gid} | user={event.get_user_id()}")

    if isinstance(event, GroupMessageEvent):
        try:
            from .stats import record_message, record_command

            # 指令消息同样计入消息总数与活跃人数，保证统计口径完整
            record_message(event)
            record_command(event.group_id, cmd)
        except Exception as e:
            # 统计失败不应影响指令本身，但要留下痕迹便于排查
            logger.debug(f"[core] 记录统计失败: {e}")


async def notify_owner(msg: str) -> None:
    """给管理员发私聊告警（失败不抛异常）"""
    from nonebot import get_bots

    for _, bot in get_bots().items():
        for qq in _OWNER_QQ:
            try:
                await bot.send_private_msg(user_id=int(qq), message=f"【洛书告警】{msg}")
            except Exception as e:
                logger.warning(f"[core] 告警发送失败: {e}")
