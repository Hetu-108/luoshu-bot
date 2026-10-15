"""OpenAI-compatible Chat Completions transport; secrets never enter chat history."""
import base64
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from .media import image_extension


@dataclass
class CloudConfig:
    base_url: str = ''
    api_key: str = field(default='', repr=False)
    model: str = ''
    enabled: bool = False
    vision: bool = False
    timeout: float = 90
    max_tokens: int = 1024
    token_parameter: str = 'max_tokens'

    def require(self):
        if not self.enabled or not all((self.base_url.strip(), self.api_key.strip(), self.model.strip())):
            raise RuntimeError('云端API尚未启用或配置完整，请管理员在服务器填写地址、Key和模型名。')
        url = urlsplit(self.base_url)
        if url.scheme not in ('https', 'http') or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise RuntimeError('云端Base URL格式不正确，请管理员检查服务器配置。')
        if self.timeout <= 0 or self.max_tokens <= 0 or self.token_parameter not in ('max_tokens', 'max_completion_tokens'):
            raise RuntimeError('云端超时或输出长度参数配置不正确。')


def cloud_messages(messages, vision):
    result = []
    for message in messages:
        row = {'role': message['role'], 'content': message['content']}
        images = message.get('images') or []
        if images:
            if not vision:
                raise RuntimeError('当前云端模型未启用识图，请切回本地视觉模型，或请管理员配置支持图片的云端模型。')
            parts = [{'type': 'text', 'text': row['content']}]
            for encoded in images:
                try:
                    extension = image_extension(base64.b64decode(encoded, validate=True))
                except ValueError:
                    raise RuntimeError('图片格式无法转换，请重新发送有效图片。') from None
                mime = {'.jpg': 'image/jpeg', '.png': 'image/png', '.gif': 'image/gif', '.webp': 'image/webp'}[extension]
                parts.append({'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{encoded}'}})
            row['content'] = parts
        result.append(row)
    return result


async def complete(messages, model, config: CloudConfig):
    config.require()
    body = {'model': model, 'messages': cloud_messages(messages, config.vision), 'stream': False,
            config.token_parameter: config.max_tokens}
    url = config.base_url.rstrip('/') + '/chat/completions'
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(config.timeout, connect=5), follow_redirects=False) as client:
            response = await client.post(url, headers={'Authorization': 'Bearer ' + config.api_key}, json=body)
            response.raise_for_status()
            data = response.json()
            message = data['choices'][0]['message']
            content = message.get('content') or message.get('refusal') or ''
            if isinstance(content, list):
                content = '\n'.join(part['text'] for part in content if part.get('type') == 'text')
            if not isinstance(content, str):
                raise ValueError('Invalid content')
            return content
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        reason = {401: 'API Key无效或已过期', 403: '没有调用权限', 429: '请求限流或额度不足'}.get(status, '请检查服务、模型名和接口参数')
        # Provider bodies can echo credentials or user input. Do not expose them in QQ/logs.
        raise RuntimeError(f'云端API返回HTTP {status}：{reason}。') from None
    except httpx.TimeoutException:
        raise RuntimeError('云端API响应超时，请稍后重试。') from None
    except httpx.HTTPError:
        raise RuntimeError('无法连接云端API，请管理员检查地址和网络。') from None
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        raise RuntimeError('云端返回格式不兼容，需要Chat Completions格式的接口。') from None
