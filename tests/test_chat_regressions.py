import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from nonebot.adapters.onebot.v11 import Message
from nonebot.adapters.onebot.v11.bot import _check_at_me, _check_nickname
from src.plugins import ai_chat as ai, security, core, stats


class Finished(Exception):
    pass


class Matcher:
    def __init__(self):
        self.messages = []

    async def send(self, message):
        self.messages.append(str(message))

    async def finish(self, message):
        self.messages.append(str(message))
        raise Finished


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path):
    ai._sessions.clear()
    core._cooldown.clear()
    core._cooldown_duration.clear()
    monkeypatch.setattr(ai, "SESSION_FILE", tmp_path / "sessions.json")
    monkeypatch.setattr(ai, "_lock", asyncio.Lock())
    monkeypatch.setattr(ai, "_inference_slots", asyncio.Semaphore(2))
    monkeypatch.setattr(ai, "check_cooldown", lambda *args: True)
    monkeypatch.setattr(ai, "log_command", lambda *args: None)
    monkeypatch.setattr(ai, "_turn_locks", __import__("weakref").WeakValueDictionary())


@pytest.mark.parametrize("kind", ["nickname", "at"])
async def test_wakeup_after_real_adapter_preprocessing(bot, event_factory, kind):
    event = event_factory("洛书 你好" if kind == "nickname" else "你好")
    if kind == "at":
        event.message = Message("[CQ:at,qq=10000] 你好")
        _check_at_me(bot, event)
    else:
        _check_nickname(bot, event)
    assert event.to_me
    assert ai._strip_call_name(core.addressed_text(event, ai.ALL_NAMES)) == "你好"
    assert await ai._called_me()(bot, event, {})


@pytest.mark.parametrize("text", ["傻 逼", "傻*逼", "傻.逼", "傻　逼"])
def test_normalized_filter_replaces_original_span(text):
    assert security.has_sensitive(text)
    assert security.filter_text("前" + text + "后") == "前***后"
    assert not security.has_sensitive(security.filter_text(text))


async def test_failed_service_can_retry_and_recover(monkeypatch):
    calls = []
    def transport(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ConnectError("down", request=request)
        return httpx.Response(200, json={"message": {"content": "恢复了"}})
    client_class = httpx.AsyncClient
    monkeypatch.setattr(ai.httpx, "AsyncClient", lambda **kw: client_class(transport=httpx.MockTransport(transport), **kw))
    with pytest.raises(RuntimeError, match="连接不上"):
        await ai.ask_ollama([], "test")
    assert not ai._ollama_ok
    assert await ai.ask_ollama([], "test") == "恢复了"
    assert ai._ollama_ok


async def test_same_session_serializes_context(bot, event_factory, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    prompts = []
    async def ask(messages, model):
        prompts.append(list(messages))
        if len(prompts) == 1:
            entered.set()
            await release.wait()
        return "回复" + messages[-1]["content"]
    monkeypatch.setattr(ai, "ask_ollama", ask)
    event = event_factory()
    first = asyncio.create_task(ai._do_chat(Matcher(), bot, event, "A"))
    await entered.wait()
    second = asyncio.create_task(ai._do_chat(Matcher(), bot, event, "B"))
    await asyncio.sleep(0)
    assert len(prompts) == 1
    release.set()
    await asyncio.gather(first, second)
    assert [m["content"] for m in prompts[1][1:]] == ["A", "回复A", "B"]
    assert [m["content"] for m in ai._sessions[ai._session_key(event, bot)]["history"]] == ["A", "回复A", "B", "回复B"]


@pytest.mark.parametrize("command", ["reset", "switch", "model"])
async def test_session_change_discards_inflight_reply(bot, event_factory, monkeypatch, command):
    entered, release = asyncio.Event(), asyncio.Event()
    async def ask(messages, model):
        entered.set()
        await release.wait()
        return "旧回复"
    monkeypatch.setattr(ai, "ask_ollama", ask)
    event, matcher = event_factory(), Matcher()
    task = asyncio.create_task(ai._do_chat(matcher, bot, event, "A"))
    await entered.wait()
    target = Matcher()
    monkeypatch.setattr(ai, command if command != "model" else "model_cmd", target)
    with pytest.raises(Finished):
        if command == "reset":
            await ai._handle_reset(bot, event)
        elif command == "switch":
            await ai._handle_switch(bot, event, Message("助手"))
        else:
            await ai._handle_model(bot, event, Message("test-other"))
    release.set()
    await task
    assert matcher.messages == []
    assert ai._sessions[ai._session_key(event, bot)]["history"] == []


async def test_familiar_member_has_no_admin_privilege(bot, event_factory):
    member = event_factory(uid=20000000)
    assert core.is_familiar(member)
    assert await core.get_role(bot, member) == core.ROLE_MEMBER
    assert not core.is_familiar(event_factory(uid=10000000))


def test_cooldown_remaining_uses_duration(monkeypatch, event_factory):
    monkeypatch.setattr(core.time, "time", lambda: 100)
    assert core.check_cooldown("test", event_factory(), 10)
    monkeypatch.setattr(core.time, "time", lambda: 104.2)
    assert core.cooldown_remain("test", event_factory()) == 6


async def test_stats_flush_when_no_further_messages(monkeypatch):
    saved = []
    monkeypatch.setattr(stats, "_dirty", 1)
    monkeypatch.setattr(stats._store, "save", lambda: saved.append(True))
    await stats._periodic_flush()
    assert saved == [True] and stats._dirty == 0


def test_json_save_reports_failure(monkeypatch, tmp_path):
    store = core.JsonStore("unused-test.json")
    store.path = tmp_path / "state.json"
    monkeypatch.setattr(core.os, "replace", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        store.put(1, "value", 2)
    assert not store.path.with_suffix(".tmp").exists()


@pytest.mark.parametrize("raw", ['["123", "456"]', [123, 456], "123,456"])
def test_custom_id_config(raw):
    assert core.parse_ids(raw) == ["123", "456"]
