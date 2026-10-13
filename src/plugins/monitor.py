# python3
# -*- coding: utf-8 -*-
# @File    : monitor.py
# @Usage   : 运行监控与异常告警 —— 对应 F-09
#
# 职责：
#   1. 捕获未处理异常，写日志并通知管理员
#   2. 记录插件加载情况
#   3. 提供连接断开/重连的日志记录
#
# 不注册任何用户指令。

import os
import traceback

import nonebot
from nonebot import get_driver, get_bots
from nonebot.log import logger

from .core import log_event, notify_owner

driver = get_driver()

# 记录异常上报的时间间隔，避免同一个错误刷屏告警
_last_alert: dict = {}
ALERT_INTERVAL = 300  # 同一类错误 5 分钟内只告警一次


def _should_alert(key: str) -> bool:
    import time

    now = time.time()
    if now - _last_alert.get(key, 0) < ALERT_INTERVAL:
        return False
    _last_alert[key] = now
    return True


@driver.on_startup
async def _log_startup():
    plugins = [p.name for p in nonebot.get_loaded_plugins()]
    logger.info(f"[monitor] 已加载 {len(plugins)} 个插件：{', '.join(sorted(plugins))}")
    log_event("STARTUP", f"插件数={len(plugins)}")


@driver.on_bot_connect
async def _on_bot_connect(bot):
    logger.info(f"[monitor] Bot {bot.self_id} 已连接")
    log_event("BOT_CONNECT", f"self_id={bot.self_id}")


@driver.on_bot_disconnect
async def _on_bot_disconnect(bot):
    """Bot 掉线时通知管理员（F-09 要求）"""
    logger.warning(f"[monitor] Bot {bot.self_id} 断开连接")
    log_event("BOT_DISCONNECT", f"self_id={bot.self_id}")
    if _should_alert(f"disconnect-{bot.self_id}"):
        if os.name == "nt":
            hint = "请检查 Main.exe 是否还在运行；若已关闭，重新打开并登录即可。"
        else:
            hint = "请检查登录程序（OneBot 端）是否还在运行，必要时重启它。"
        await notify_owner(
            f"机器人（{bot.self_id}）与 QQ 的连接断开了。\n{hint}"
        )


def install_exception_hook():
    """把未捕获异常写日志并通知管理员。

    在 bot.py 启动早期调用；即使通知失败也不会影响主流程。
    """
    import asyncio

    def _handle(loop, context):
        exc = context.get("exception")
        msg = context.get("message", "")
        detail = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        ) if exc else msg
        logger.error(f"[monitor] 未捕获异常：{detail}")
        log_event("EXCEPTION", detail.replace("\n", " | ")[:500])
        try:
            asyncio.ensure_future(notify_owner(f"发生未捕获异常：\n{msg or exc}"))
        except Exception:
            pass

    try:
        loop = asyncio.get_event_loop()
        loop.set_exception_handler(_handle)
    except Exception as e:
        logger.warning(f"[monitor] 安装异常钩子失败: {e}")
