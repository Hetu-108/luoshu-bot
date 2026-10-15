import base64
import httpx
import pytest
from src import media
from src.plugins import ai_chat as ai
from test_chat_regressions import Matcher


async def test_private_image_url_is_rejected():
    with pytest.raises(ValueError, match="内网"):
        await media._public_url("http://127.0.0.1/admin")
    with pytest.raises(ValueError, match="HTTP"):
        await media._public_url("file:///etc/passwd")


async def test_download_size_and_type(monkeypatch):
    async def public(url):
        pass
    monkeypatch.setattr(media, "_public_url", public)
    client_class = httpx.AsyncClient
    def response(request):
        return httpx.Response(200, content=b"\x89PNG\r\n\x1a\n" + b"x" * 32)
    monkeypatch.setattr(media.httpx, "AsyncClient", lambda **kw: client_class(transport=httpx.MockTransport(response), **kw))
    with pytest.raises(ValueError, match="大小"):
        await media.download_image("https://example.com/a.png", max_bytes=10)
    assert (await media.download_image("https://example.com/a.png")).startswith(b"\x89PNG")


async def test_images_reach_model_but_not_persistent_history(monkeypatch, bot, event_factory, tmp_path):
    async def download(url):
        return b"fake-image-bytes"
    captured = []
    async def ask(messages, model):
        captured.append(messages)
        return "图片描述"
    monkeypatch.setattr(ai, "download_image", download)
    monkeypatch.setattr(ai, "ask_ollama", ask)
    monkeypatch.setattr(ai, "SESSION_FILE", tmp_path / "session.json")
    event = event_factory(uid=99999001)
    await ai._do_chat(Matcher(), bot, event, "看看图片", ["https://example.com/a.png"])
    assert captured[0][-1]["images"] == [base64.b64encode(b"fake-image-bytes").decode()]
    assert "images" not in ai._sessions[ai._session_key(event, bot)]["history"][0]
