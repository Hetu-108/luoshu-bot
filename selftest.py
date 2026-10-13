"""开发自检脚本：模拟收到的群消息，验证各插件响应是否正常。

不连 QQ，纯本地跑通逻辑。改完代码想快速验证时用这个。
运行： .venv\\Scripts\\python.exe selftest.py
"""
import asyncio
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OB11Adapter
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from nonebot.adapters.onebot.v11.message import Message

nonebot.init()
driver = nonebot.get_driver()
driver.register_adapter(OB11Adapter)
nonebot.load_from_toml("pyproject.toml")

SELF_ID = "10000"
GROUP_ID = 123456

# 管理员 QQ 从配置读取（.env 的 SUPERUSERS），不把真实 QQ 写死在源码里。
# 新用户复制 .env.example 后无需改这里，测试会自动对上自己的配置。
_superusers = sorted(getattr(driver.config, "superusers", []) or [])
USER_ID = int(_superusers[0]) if _superusers else 10000000

# 「心动的人」的称呼，从 .env 的 CRUSH_NAMES 读取（逗号分隔，默认 小美）
_crush_raw = getattr(driver.config, "crush_names", None) or "小美"
if isinstance(_crush_raw, (list, tuple, set)):
    _CRUSH = [str(x).strip() for x in _crush_raw if str(x).strip()]
else:
    _CRUSH = [x.strip() for x in str(_crush_raw).split(",") if x.strip()]
if not _CRUSH:
    _CRUSH = ["小美"]
CRUSH1 = _CRUSH[0]
CRUSH2 = _CRUSH[1] if len(_CRUSH) > 1 else ""


class FakeBot(Bot):
    """把发送的消息打印出来，而不是真的发到 QQ"""

    def __init__(self, as_admin: bool = False):
        super().__init__(self_id=SELF_ID, adapter=driver._adapters["OneBot V11"])
        self.as_admin = as_admin

    async def send(self, event=None, message=None, **kwargs):
        text = str(message).strip()
        print(f"  [回复] {text}")
        return {"message_id": 1}

    async def call_api(self, api, **kwargs):
        return {"message_id": 1}

    async def get_group_list(self):
        return [{"group_id": GROUP_ID}]

    async def get_friend_list(self):
        return []

    async def get_group_member_info(self, **kwargs):
        return {"role": "admin" if self.as_admin else "member"}


def make_group_event(text: str, user_id: int = USER_ID, at_bot: bool = False,
                     reply: bool = False, role: str = "member") -> GroupMessageEvent:
    if at_bot:
        msg = Message(f"[CQ:at,qq={SELF_ID}] {text}")
    else:
        msg = Message(text)
    data = {
        "time": 1700000000,
        "self_id": SELF_ID,
        "post_type": "message",
        "message_type": "group",
        "sub_type": "normal",
        "message_id": 123,
        "group_id": GROUP_ID,
        "user_id": user_id,
        "raw_message": str(msg),
        "font": 0,
        "sender": {"user_id": user_id, "nickname": "测试用户", "card": "", "role": role},
        "message": msg,
        "original_message": msg,
    }
    if reply:
        data["reply"] = {
            "time": 1700000000,
            "message_type": "group",
            "message_id": 99,
            "real_id": 99,
            "sender": {"user_id": 777, "nickname": "被回复的人", "card": "被回复的人"},
            "message": Message("原始消息"),
        }
    return GroupMessageEvent.parse_obj(data)


async def send(text: str, at_bot: bool = False, reply: bool = False,
               note: str = "", as_admin: bool = False, user_id: int = USER_ID):
    role = "admin" if as_admin else "member"
    bot = FakeBot(as_admin=as_admin)
    event = make_group_event(text, user_id=user_id, at_bot=at_bot, reply=reply, role=role)
    prefix = "@我 " if at_bot else ""
    tag = f"（{note}）" if note else ""
    print(f"\n>>> {prefix}{text}{tag}")
    from nonebot.message import handle_event
    try:
        await handle_event(bot, event)
    except Exception as e:
        print(f"  [异常] {type(e).__name__}: {e}")


async def main():
    print("\n========== 基础必备层 ==========")
    await send("/help", note="F-04 帮助总览")
    await send("/h", note="F-04 帮助总览（短别名）")
    await send("/help 塔罗", note="F-04 单功能用法")
    await send("功能清单", at_bot=True, note="用户要求的 @我 查清单")
    await send("洛书 /h", note="喊名字 + /h 应返回功能总览")
    await send("洛书 h", note="喊名字 + h（无斜杠）")
    await send("小洛 /help", note="别的称呼 + /help")
    await send("/h", at_bot=True, note="@我 + /h")
    await send("whoami", note="F-05 权限分级（无前缀）")
    await send("/关键词 添加 早 早上好呀|早安|起床啦", note="F-08 添加关键词", as_admin=True)
    await send("早", note="F-08 关键词触发")

    print("\n========== 娱乐核心层 ==========")
    await send("/抽签", note="F-10 抽签")
    await send("/运势", note="F-10 运势")
    await send("今日人品", note="F-10 兼容旧写法（无前缀）")
    await send("/塔罗", note="F-11 单张")
    await send("/塔罗 三张", note="F-11 三张")

    print("\n========== 心动专属互动（洛书喜欢的人）==========")
    print(f"  （称呼取自 .env 的 CRUSH_NAMES：{CRUSH1}{'、' + CRUSH2 if CRUSH2 else ''}）")
    await send(CRUSH1, note="打招呼（昵称1，应略显慌张）")
    if CRUSH2:
        await send(CRUSH2, note="打招呼（昵称2）")
    await send("晚安", note="道晚安")
    await send(f"{CRUSH1}真棒", note="被夸，洛书应特别不好意思")
    await send("/心事", note="洛书心事卡")
    await send("/心事", user_id=99999999, note="非心动对象应被拒")
    await send(CRUSH1, user_id=99999999, note="非心动对象打招呼应无响应")
    await send("/心事", user_id=88888888, note="非管理员 QQ 同样应被拒")

    print("\n========== 喊名字对话（所有人可用）==========")
    await send("洛书 你好呀", user_id=55555555, note="普通成员喊名字应能聊天")
    await asyncio.sleep(3.5)
    await send("洛洛 在吗", user_id=55555555, note="普通成员用另一种称呼")
    await asyncio.sleep(3.5)

    print("\n========== 管理辅助层 ==========")
    await send("/统计", note="F-12 普通成员应被拒")
    await send("/统计", note="F-12 管理员可查", as_admin=True)

    print("\n========== AI 对话 ==========")
    await send("你好呀，简单介绍一下你自己，一句话", at_bot=True, note="F-13 普通成员 @我")
    await asyncio.sleep(3.5)  # 等过冷却
    await send("/history")
    await send("/人设")
    await send(f"{CRUSH1} 今天过得怎么样", at_bot=True, note="心动对象对话（应显露心动，且不叫主人）")
    await asyncio.sleep(3.5)

    print("\n=== 自检完成，以上若无 [异常] 即为通过 ===")


if __name__ == "__main__":
    asyncio.run(main())
