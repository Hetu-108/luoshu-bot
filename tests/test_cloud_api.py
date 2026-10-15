import asyncio
import base64
import json

import httpx
import pytest
from nonebot.adapters.onebot.v11 import Message

from src import cloud_api
from src.plugins import ai_chat as ai
from test_chat_regressions import Finished, Matcher, isolated_state


@pytest.fixture
def config(monkeypatch):
    config = cloud_api.CloudConfig(base_url='https://api.example.com/v1/', api_key='private-test-key',
                                   model='cloud-test', enabled=True, vision=True)
    monkeypatch.setattr(ai, '_cloud_config', config)
    monkeypatch.setattr(ai, '_cloud_slots', asyncio.Semaphore(2))
    return config


def mock_http(monkeypatch, handler):
    client = httpx.AsyncClient
    monkeypatch.setattr(cloud_api.httpx, 'AsyncClient', lambda **kw: client(transport=httpx.MockTransport(handler), **kw))


async def test_cloud_text_contract_and_secret_not_in_repr(monkeypatch, config):
    captured = []
    def handler(request):
        captured.append(request)
        return httpx.Response(200, json={'choices': [{'message': {'content': '云端回复'}}]})
    mock_http(monkeypatch, handler)
    assert await cloud_api.complete([{'role': 'user', 'content': '你好'}], 'cloud-test', config) == '云端回复'
    request = captured[0]
    assert str(request.url) == 'https://api.example.com/v1/chat/completions'
    assert request.headers['Authorization'] == 'Bearer private-test-key'
    body = json.loads(request.content)
    assert body['model'] == 'cloud-test' and body['max_tokens'] == 1024 and not body['stream']
    assert 'options' not in body and 'think' not in body
    assert 'private-test-key' not in repr(config)


async def test_cloud_vision_conversion_preserves_source(monkeypatch, config):
    encoded = base64.b64encode(b'\x89PNG\r\n\x1a\nexample').decode()
    messages = [{'role': 'user', 'content': '读图', 'images': [encoded]}]
    captured = []
    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': [{'type': 'text', 'text': '42'}]}}]})
    mock_http(monkeypatch, handler)
    config.token_parameter = 'max_completion_tokens'
    assert await cloud_api.complete(messages, config.model, config) == '42'
    row = captured[0]['messages'][0]
    assert row['content'][1] == {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + encoded}}
    assert 'images' not in row and messages[0]['images'] == [encoded]
    assert captured[0]['max_completion_tokens'] == 1024 and 'max_tokens' not in captured[0]


@pytest.mark.parametrize('status', [400, 401, 403, 429, 500])
async def test_cloud_error_does_not_echo_secret_or_input(monkeypatch, config, status):
    mock_http(monkeypatch, lambda request: httpx.Response(status, text='private-test-key private user input'))
    with pytest.raises(RuntimeError) as error:
        await cloud_api.complete([{'role': 'user', 'content': 'private user input'}], config.model, config)
    assert str(status) in str(error.value)
    assert 'private-test-key' not in str(error.value) and 'private user input' not in str(error.value)


async def test_cloud_failure_allows_next_request(monkeypatch, config):
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ReadTimeout('private-test-key', request=request)
        return httpx.Response(200, json={'choices': [{'message': {'content': '恢复'}}]})
    mock_http(monkeypatch, handler)
    with pytest.raises(RuntimeError, match='超时'):
        await cloud_api.complete([], config.model, config)
    assert await cloud_api.complete([], config.model, config) == '恢复'
    assert len(calls) == 2


async def test_cloud_disabled_and_bad_response(monkeypatch, config):
    captured = []
    def handler(request):
        captured.append(request)
        return httpx.Response(200, json={'unexpected': 'private-test-key'})
    mock_http(monkeypatch, handler)
    config.enabled = False
    with pytest.raises(RuntimeError, match='尚未'):
        await cloud_api.complete([], config.model, config)
    assert not captured
    config.enabled = True
    with pytest.raises(RuntimeError, match='格式不兼容'):
        await cloud_api.complete([], config.model, config)


async def test_legacy_session_stays_local_and_cloud_model_recovers(monkeypatch, config):
    monkeypatch.setattr(ai, 'DEFAULT_BACKEND', 'cloud')
    ai._sessions['old'] = {'persona': '洛书', 'model': 'local-custom', 'history': [], 'cloud_model': ''}
    assert ai._get_session('old')['backend'] == 'ollama'
    assert ai._get_session('old')['model'] == 'local-custom'
    assert ai._get_session('old')['cloud_model'] == 'cloud-test'
    assert ai._get_session('new')['backend'] == 'cloud'


async def test_backend_permission_and_switch_history(monkeypatch, bot, event_factory, config):
    matcher = Matcher()
    monkeypatch.setattr(ai, 'backend_cmd', matcher)
    member = event_factory()
    with pytest.raises(Finished):
        await ai._handle_backend(bot, member, Message('云端'))
    assert '仅机器人管理员' in matcher.messages[-1] and not ai._sessions
    owner = event_factory(uid=10000000)
    key = ai._session_key(owner, bot)
    sess = ai._get_session(key)
    sess['history'] = [{'role': 'user', 'content': '旧的本地私密信息'}]
    with pytest.raises(Finished):
        await ai._handle_backend(bot, owner, Message('云端'))
    assert sess['backend'] == 'cloud' and not sess['history']
    assert '云端服务' in matcher.messages[-1]
    assert ai._get_session(ai._session_key(member, bot))['backend'] == 'ollama'
    monkeypatch.setattr(ai, 'model_cmd', matcher)
    with pytest.raises(Finished):
        await ai._handle_model(bot, owner, Message('cloud-other'))
    assert sess['cloud_model'] == 'cloud-other' and sess['model'] == ai.OLLAMA_MODEL
    with pytest.raises(Finished):
        await ai._handle_backend(bot, owner, Message('本地'))
    assert sess['backend'] == 'ollama' and sess['cloud_model'] == 'cloud-other'


async def test_cloud_chat_and_vision_go_to_cloud_only(monkeypatch, bot, event_factory, config, tmp_path):
    captured = []
    async def ask(messages, model):
        captured.append((messages, model))
        return '<think>private reasoning</think>图中42'
    async def local(*args, **kwargs):
        pytest.fail('Cloud requests must not silently change providers')
    async def download(url):
        return b'\x89PNG\r\n\x1a\nexample'
    monkeypatch.setattr(ai, 'cloud_complete', lambda messages, model, cfg: ask(messages, model))
    monkeypatch.setattr(ai, 'ask_ollama', local)
    monkeypatch.setattr(ai, 'download_image', download)
    event = event_factory()
    key = ai._session_key(event, bot)
    ai._get_session(key)['backend'] = 'cloud'
    matcher = Matcher()
    await ai._do_chat(matcher, bot, event, '看看图片', ['https://example.com/image.png'])
    assert captured[0][1] == 'cloud-test' and captured[0][0][-1]['images']
    assert matcher.messages == ['图中42']
    serialized = ai.SESSION_FILE.read_text(encoding='utf-8')
    assert 'private-test-key' not in serialized and 'images' not in serialized


async def test_cloud_text_only_rejects_image_before_download(monkeypatch, bot, event_factory, config):
    config.vision = False
    async def download(*args):
        pytest.fail('Image must be rejected before download')
    monkeypatch.setattr(ai, 'download_image', download)
    event = event_factory()
    ai._get_session(ai._session_key(event, bot))['backend'] = 'cloud'
    matcher = Matcher()
    with pytest.raises(Finished):
        await ai._do_chat(matcher, bot, event, '识图', ['https://example.com/image.png'])
    assert '未启用识图' in matcher.messages[-1]


async def test_switch_backend_invalidates_inflight_reply(monkeypatch, bot, event_factory, config):
    entered, release = asyncio.Event(), asyncio.Event()
    async def ask(messages, model):
        entered.set()
        await release.wait()
        return '过期云端回复'
    monkeypatch.setattr(ai, 'ask_cloud', ask)
    event = event_factory(uid=10000000)
    key = ai._session_key(event, bot)
    ai._get_session(key)['backend'] = 'cloud'
    matcher = Matcher()
    task = asyncio.create_task(ai._do_chat(matcher, bot, event, '问题'))
    await entered.wait()
    monkeypatch.setattr(ai, 'backend_cmd', Matcher())
    with pytest.raises(Finished):
        await ai._handle_backend(bot, event, Message('本地'))
    release.set()
    await task
    assert not matcher.messages and not ai._sessions[key]['history']
