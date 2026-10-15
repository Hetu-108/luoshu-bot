import tempfile
import nonebot
import pytest
from nonebot.adapters.onebot.v11 import Adapter, Bot, GroupMessageEvent, PrivateMessageEvent

nonebot.init(
    driver="~fastapi", nickname={"洛书", "洛"}, call_names="洛书,洛洛,小洛,洛",
    superusers={"10000000"}, familiar_users=["20000000"], greeting_enabled=False,
    data_dir=tempfile.mkdtemp(prefix="luoshu-tests-"),
)
driver = nonebot.get_driver()
driver.register_adapter(Adapter)
nonebot.load_from_toml("pyproject.toml")


@pytest.fixture
def bot():
    return Bot(driver._adapters["OneBot V11"], "10000")


def message_event(text="你好", uid=55555555, gid=123456, mid=1, to_me=False):
    data = dict(
        time=1700000000, self_id=10000, post_type="message", user_id=uid,
        message_type="group" if gid else "private", sub_type="normal" if gid else "friend",
        message_id=mid, raw_message=text, message=text, font=0, to_me=to_me,
        sender={"user_id": uid, "nickname": "测试成员", "card": "", "role": "member"},
    )
    if gid:
        data["group_id"] = gid
    return (GroupMessageEvent if gid else PrivateMessageEvent).model_validate(data)


@pytest.fixture
def event_factory():
    return message_event
