"""Bounded QQ image downloads shared by chat and the quiz image archive."""
import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import httpx

MAX_IMAGE_BYTES = 8 * 1024 * 1024


def image_extension(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    raise ValueError("仅接受 PNG、JPEG、GIF 或 WebP 图片")


async def _public_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("图片地址必须是公开的 HTTP/HTTPS 地址")
    try:
        addresses = await asyncio.get_running_loop().getaddrinfo(
            parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM
        )
    except OSError as exc:
        raise ValueError("无法解析图片地址") from exc
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError("不允许从本机或内网下载图片")


async def download_image(url: str, max_bytes: int = MAX_IMAGE_BYTES) -> bytes:
    """Validate redirects as well as the initial URL; never fetch file:// or private hosts."""
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        for _ in range(4):
            await _public_url(url)
            async with client.stream("GET", url) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("图片重定向缺少目标地址")
                    url = urljoin(url, location)
                    continue
                response.raise_for_status()
                if int(response.headers.get("content-length", "0")) > max_bytes:
                    raise ValueError("图片超过大小限制")
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("图片超过大小限制")
                    chunks.append(chunk)
                data = b"".join(chunks)
                image_extension(data)
                return data
    raise ValueError("图片重定向次数过多")
