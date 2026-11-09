"""直播链接/分享文本 → 房间号。

parse_live_input 是纯函数（可直接单测）；resolve_room 涉及短链跳转（IO）。
支持输入：
  1. 纯房间号：745961234
  2. 网页链接：https://live.douyin.com/745961234?enter_from=...
  3. 分享口令文本：'7长按复制... https://v.douyin.com/iRNBho6u/ @抖音直播'
"""
from __future__ import annotations

import re

import httpx

_URL_RE = re.compile(r"https?://[^\s，。、：；！？\"'<>（）()【】]+")
_LIVE_PATH_RE = re.compile(r"live\.douyin\.com/([A-Za-z0-9_-]+)")
_ROOM_QUERY_RE = re.compile(r"room_id[=:](\d+)")
_SHORT_HOSTS = ("v.douyin.com", "www.iesdouyin.com", "iesdouyin.com")
_MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
              "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")


def parse_live_input(text: str) -> dict:
    """从用户输入解析直播间标识。

    返回 {"room": <房间号>} 或 {"short_url": <短链>}（需网络解析）；无法解析返回 {}。
    """
    t = (text or "").strip()
    if not t:
        return {}
    # 纯房间号（douyinLive ValidateLiveID 允许字母数字下划线短横线）
    if re.fullmatch(r"[A-Za-z0-9_-]{1,32}", t):
        return {"room": t}
    for url in _URL_RE.findall(t):
        m = _LIVE_PATH_RE.search(url)
        if m:
            return {"room": m.group(1)}
        host = (re.match(r"https?://([^/]+)/?", url) or [None, ""])[1].lower()
        if host in _SHORT_HOSTS:
            return {"short_url": url}
    return {}


async def resolve_room(text: str) -> str:
    """房间号/链接/分享文本 → 房间号。短链会发起网络请求跟跳转。"""
    parsed = parse_live_input(text)
    if parsed.get("room"):
        return parsed["room"]
    url = parsed.get("short_url")
    if not url:
        raise ValueError("无法识别直播间：请粘贴房间号、直播间链接或分享口令")
    async with httpx.AsyncClient(follow_redirects=True, timeout=10.0,
                                 headers={"User-Agent": _MOBILE_UA}) as cli:
        r = await cli.get(url)
    final = str(r.url)
    reparsed = parse_live_input(final)
    if reparsed.get("room"):
        return reparsed["room"]
    m = _ROOM_QUERY_RE.search(final)
    if m:
        return m.group(1)
    raise ValueError(f"短链解析失败（未找到房间号）：{final[:100]}")
