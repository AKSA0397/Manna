"""短链接展开与链接整理：b23.tv（B站）、v.douyin.com（抖音）、xhslink.com（小红书）。

只跟随跳转拿到真实地址，不下载页面内容；展开失败就原样返回，交给解析引擎处理。
"""
import re
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urljoin, urlparse

from config import get_logger

log = get_logger()

SHORT_HOSTS = ("b23.tv", "bili2233.cn", "v.douyin.com", "xhslink.com")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")


def is_short(url):
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in SHORT_HOSTS)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _next_hop(url, proxy, timeout):
    handlers = [_NoRedirect()]
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with opener.open(req, timeout=timeout) as r:
            # 有的短链用页面里的 meta refresh / JS 跳转
            body = r.read(64 * 1024).decode("utf-8", "replace")
            m = re.search(r"""(?:http-equiv=["']?refresh["']?[^>]*url=|location\.(?:href|replace)\(?\s*=?\s*["'])"""
                          r"""(https?://[^"'\s>]+)""", body, re.I)
            return m.group(1) if m else None
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308):
            loc = e.headers.get("Location")
            return urljoin(url, loc) if loc else None
        raise


def expand(url, proxy="", timeout=10, max_hops=6):
    """跟随短链接跳转，直到离开短链域名。失败时返回原链接。"""
    if not is_short(url):
        return url
    cur = url
    try:
        for _ in range(max_hops):
            nxt = _next_hop(cur, proxy, timeout)
            if not nxt:
                break
            cur = nxt
            if not is_short(cur):
                break
    except Exception as e:
        log.warning("短链接展开失败 %s：%s", url, e)
        return url
    if cur != url:
        log.info("短链接 %s → %s", url, cur)
    return cur if not is_short(cur) else url


def normalize(url):
    """把抖音的各种分享 / 弹窗链接换成视频详情页链接 https://www.douyin.com/video/<id>。"""
    u = urlparse(url)
    host = (u.hostname or "").lower()
    if host.endswith("douyin.com"):
        q = parse_qs(u.query)
        vid = (q.get("modal_id") or q.get("vid") or [None])[0]
        if vid and vid.isdigit() and "/video/" not in u.path and "/note/" not in u.path:
            return f"https://www.douyin.com/video/{vid}"
    if host.endswith("iesdouyin.com"):
        m = re.search(r"/share/(video|note|slides)/(\d+)", u.path)
        if m:
            return f"https://www.douyin.com/{'note' if m.group(1) == 'slides' else m.group(1)}/{m.group(2)}"
        # 主页、合集的分享链接
        m = re.search(r"/share/user/([A-Za-z0-9_-]+)", u.path)
        if m:
            return f"https://www.douyin.com/user/{m.group(1)}"
        m = re.search(r"/share/mix/(?:detail/)?(\d+)", u.path)
        if m:
            return f"https://www.douyin.com/collection/{m.group(1)}"
    return url


def resolve(url, proxy=""):
    """入口：先展开短链接，再整理成解析引擎认识的形式。"""
    return normalize(expand(url.strip(), proxy))
