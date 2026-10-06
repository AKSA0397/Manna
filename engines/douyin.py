"""抖音引擎：读抖音官方分享页里自带的作品数据（单个视频、图集），不调用需要签名的接口。

第一层：打开 https://www.iesdouyin.com/share/video/<作品ID>/ ，从页面里的 _ROUTER_DATA 读出标题、作者、
        封面、视频地址 / 图集图片地址。带上用户自己的 Cookie（导入的 cookies.txt 或浏览器登录状态）。
第二层：这里失败时注册表自动换 yt-dlp（同样带用户的 Cookie）。
只下载抖音服务器本身提供的文件，不对画面做任何去水印、裁剪或遮挡。
Cookie 只在本机使用，只发给 douyin.com / iesdouyin.com，不写进日志。
"""
import http.cookiejar
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import tools
from config import SUPPORT_DIR, get_logger

from .base import DownloadResult, EngineError, Extractor, Format, MediaInfo, Selection

log = get_logger()

DOUYIN_HOSTS = ("douyin.com", "iesdouyin.com")
MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
REFERER = "https://www.douyin.com/"
# 设置里「导入 Cookie」保存的位置（只保留抖音域名的行，权限 600）
COOKIE_FILE = SUPPORT_DIR / "cookies" / "douyin.txt"
COOKIE_EXPIRED = "请在浏览器登录抖音后重新导出 Cookie（设置 → 抖音 → 导入 Cookie）。"
ID_RE = re.compile(r"/(video|note|slides)/(\d{8,})")


def is_douyin(url):
    host = (urlparse(url or "").hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in DOUYIN_HOSTS)


def work_id(url):
    """(类型, 作品ID)；不是单个作品的链接返回 (None, None)。"""
    m = ID_RE.search(urlparse(url or "").path or "")
    return (m.group(1), m.group(2)) if m else (None, None)


# ---------- Cookie ----------

def import_cookies(src):
    """把用户导出的 cookies.txt 里抖音域名的行复制到 Manna 自己的目录。返回 {count, login}。"""
    src = Path(os.path.expanduser(str(src).strip().strip('"')))
    if not src.is_file():
        raise EngineError("找不到这个文件。")
    if src.stat().st_size > 5 << 20:
        raise EngineError("这个文件太大，不像是 cookies.txt。")
    lines = src.read_text(encoding="utf-8", errors="replace").splitlines()
    keep, names = [], set()
    for line in lines:
        # Netscape 格式：域名 \t 子域 \t 路径 \t 安全 \t 过期 \t 名称 \t 值；HttpOnly 的行以 #HttpOnly_ 开头
        parts = line.split("\t")
        if len(parts) != 7:
            continue
        domain = parts[0].removeprefix("#HttpOnly_").lstrip(".").lower()
        if any(domain == d or domain.endswith("." + d) for d in DOUYIN_HOSTS):
            keep.append(line)
            names.add(parts[5])
    if not keep:
        raise EngineError("文件里没有抖音的 Cookie：请在浏览器里登录 douyin.com 后，用 Get cookies.txt 一类的扩展导出 cookies.txt 再导入。")
    COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(COOKIE_FILE.parent, 0o700)
    tmp = COOKIE_FILE.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("# Netscape HTTP Cookie File\n" + "\n".join(keep) + "\n")
    tmp.replace(COOKIE_FILE)
    log.info("已导入抖音 Cookie：%d 条", len(keep))   # 只记条数，不记内容
    return {"count": len(keep), "login": "sessionid" in names or "sessionid_ss" in names}


def import_from_browser(browser):
    """从本机浏览器读出抖音的登录 Cookie 存到 Manna 自己的目录（读 Chrome 时系统会弹钥匙串，由用户点允许）。"""
    import yt_dlp
    if browser not in ("chrome", "safari", "edge", "firefox", "brave", "arc"):
        browser = "chrome"
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "cookiesfrombrowser": (browser,)}) as ydl:
            jar = ydl.cookiejar
            fd, tmp = tempfile.mkstemp(prefix=".douyin-", suffix=".txt", dir=COOKIE_FILE.parent
                                       if COOKIE_FILE.parent.exists() else SUPPORT_DIR)
            os.close(fd)
            os.chmod(tmp, 0o600)
            try:
                jar.save(tmp, ignore_discard=True, ignore_expires=True)
                return dict(import_cookies(tmp), browser=browser)
            finally:
                Path(tmp).unlink(missing_ok=True)
    except EngineError:
        raise EngineError(f"{browser} 里没有抖音的登录信息：请先在 {browser} 里打开 douyin.com 并登录，再点一次。")
    except Exception as e:
        raise EngineError(f"读不到 {browser} 的登录信息（{str(e).splitlines()[0][:120]}）。"
                          "用 Chrome 时请在弹出的「钥匙串」里输入电脑密码并点「始终允许」；用 Safari 需要给 Manna「完全磁盘访问权限」。")


def clear_cookies():
    COOKIE_FILE.unlink(missing_ok=True)


def cookie_status():
    if not COOKIE_FILE.is_file():
        return {"imported": False}
    return {"imported": True, "date": int(COOKIE_FILE.stat().st_mtime)}


def cookies_file_for(ctx):
    """yt-dlp 等要用的 cookies.txt：设置里填的路径优先，其次是导入的抖音 Cookie。"""
    own = (ctx.get("cookies_file") or "").strip().strip('"')
    if own:
        return os.path.expanduser(own)
    return str(COOKIE_FILE) if COOKIE_FILE.is_file() else ""


def cookie_header(ctx, url="https://www.douyin.com/"):
    """拼出发给抖音的 Cookie 头：cookies.txt（设置里的或导入的）→ 浏览器登录状态。拿不到返回空字符串。"""
    path = cookies_file_for(ctx)
    if path and os.path.isfile(path):
        jar = http.cookiejar.MozillaCookieJar()
        try:
            jar.load(path, ignore_discard=True, ignore_expires=True)
        except (OSError, http.cookiejar.LoadError):
            return ""
        req = urllib.request.Request(url)
        jar.add_cookie_header(req)
        return req.get_header("Cookie") or ""
    browser = (ctx.get("cookies_browser") or "").strip()
    if browser and browser != "auto":
        from .ytdlp import browser_cookie_header
        return browser_cookie_header({"cookies_browser": browser}, url)
    return ""


# ---------- 分享页 ----------

def _get(url, headers, timeout=20):
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(4 << 20).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise EngineError(f"抖音分享页打不开（HTTP {e.code}）")
    except (urllib.error.URLError, OSError) as e:
        raise EngineError(f"连不上抖音（{getattr(e, 'reason', e)}），请检查网络。")


def router_data(html):
    m = re.search(r"window\._ROUTER_DATA\s*=\s*(\{.*?\})\s*</script>", html, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except ValueError:
        return None


def fetch_item(kind, aweme_id, ctx):
    """从分享页读出作品数据（dict）。读不到时抛出中文说明。"""
    page = "note" if kind in ("note", "slides") else "video"
    cookie = cookie_header(ctx)
    headers = {"User-Agent": MOBILE_UA, "Referer": REFERER, "Accept-Language": "zh-CN,zh;q=0.9"}
    if cookie:
        headers["Cookie"] = cookie
    html = _get(f"https://www.iesdouyin.com/share/{page}/{aweme_id}/", headers)
    data = router_data(html)
    if not data:
        raise EngineError("抖音分享页里没有作品数据（可能触发了验证）。" + (COOKIE_EXPIRED if cookie else ""))
    for v in (data.get("loaderData") or {}).values():
        res = (v or {}).get("videoInfoRes") if isinstance(v, dict) else None
        if not res:
            continue
        items = res.get("item_list") or []
        if items:
            return items[0]
        reason = next((f.get("detail_msg") or f.get("notice") for f in res.get("filter_list") or []
                       if f.get("detail_msg") or f.get("notice")), "")
        if reason:
            raise EngineError(f"抖音：{reason}")
    if cookie:
        raise EngineError("抖音没有返回这条作品的数据。" + COOKIE_EXPIRED)
    raise EngineError("抖音没有返回这条作品的数据：不登录时有些作品读不到，请在浏览器登录抖音，"
                      "或在设置 → 抖音里导入 Cookie 后重试。")


def _first(lst):
    return next((u for u in (lst or []) if u), "")


def _cover(item):
    v = item.get("video") or {}
    for k in ("origin_cover", "cover", "dynamic_cover"):
        u = _first((v.get(k) or {}).get("url_list"))
        if u:
            return u
    imgs = item.get("images") or []
    return _first((imgs[0] or {}).get("url_list")) if imgs else ""


def image_urls(item):
    """图集里每张图的地址（抖音分享页给的展示图，优先 jpeg）。"""
    out = []
    for img in item.get("images") or []:
        urls = [u for u in (img or {}).get("url_list") or [] if u]
        if urls:
            out.append(next((u for u in urls if ".jpeg" in u or ".jpg" in u), urls[0]))
    return out


def video_streams(item):
    """可下载的视频地址：[(短边, 是否 H.264, 码率 kbps, 地址)]，只用分享页里抖音给出的地址。"""
    v = item.get("video") or {}
    out = []
    for br in v.get("bit_rate") or []:
        pa = br.get("play_addr") or {}
        url = _first(pa.get("url_list"))
        if url:
            w, h = pa.get("width") or 0, pa.get("height") or 0
            out.append((min(w, h) if w and h else (h or 0), not br.get("is_h265"),
                        (br.get("bit_rate") or 0) / 1000, url))
    if not out:
        pa = v.get("play_addr") or {}
        url = _first(pa.get("url_list"))
        if url:
            # 这个地址的实际画质看地址里的 ratio（如 720p），作品本身的宽高不一定是这个文件的
            m = re.search(r"[?&]ratio=(\d+)p", url)
            out.append((int(m.group(1)) if m else 0, True, 0, url))
    return out


def _safe(name):
    return re.sub(r'[\\/:*?"<>|\r\n\t#@]+', " ", name or "").strip()[:80] or "douyin"


def _unique(path):
    n, dst = 2, path
    while dst.exists():
        dst = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    return dst


def fetch_file(url, dst, progress=None, part=(0, 1)):
    """下载一个文件到 dst（先写 .part，完成再改名）。只带 UA 和 Referer，不带 Cookie。"""
    req = urllib.request.Request(url, headers={"User-Agent": MOBILE_UA, "Referer": REFERER})
    tmp = dst.with_name(dst.name + ".part")
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done, start = 0, time.time()
            while chunk := r.read(256 * 1024):
                f.write(chunk)
                done += len(chunk)
                if progress:
                    speed = done / max(time.time() - start, 0.001)
                    if part[1] > 1:
                        pct = (part[0] + (done / total if total else 0)) * 100 / part[1]
                        progress({"status": "downloading", "percent": pct})
                    else:
                        progress({"status": "downloading", "downloaded_bytes": done, "total_bytes": total,
                                  "speed": speed, "eta": (total - done) / speed if total and speed else None})
        if os.path.getsize(tmp) == 0:
            raise EngineError("抖音返回了空文件")
        tmp.replace(dst)
    except urllib.error.HTTPError as e:
        tmp.unlink(missing_ok=True)
        raise EngineError(f"抖音拒绝了下载请求（HTTP {e.code}），可能是地址过期，请重新解析。")
    except (urllib.error.URLError, OSError) as e:
        tmp.unlink(missing_ok=True)
        raise EngineError(f"下载中断（{getattr(e, 'reason', e)}），请重试。")
    return dst


class DouyinEngine(Extractor):
    name = "douyin"
    priority = 150      # 抖音单个作品先读分享页，失败再交给 yt-dlp

    def match(self, url):
        return is_douyin(url) and work_id(url)[1] is not None

    def inspect(self, url, ctx):
        kind, aweme_id = work_id(url)
        item = fetch_item(kind, aweme_id, ctx)
        author = (item.get("author") or {}).get("nickname")
        title = (item.get("desc") or "").strip() or f"抖音作品 {aweme_id}"
        images = image_urls(item)
        info = MediaInfo(title=title, author=author, cover=_cover(item), id=aweme_id,
                         webpage_url=f"https://www.douyin.com/{'note' if images else 'video'}/{aweme_id}",
                         extractor="Douyin", engine=self.name)
        if images:
            info.images = len(images)
            return info
        dur = (item.get("video") or {}).get("duration") or item.get("duration")
        info.duration = dur / 1000 if dur and dur > 1000 else dur
        for side, h264, kbps, _ in video_streams(item):
            info.formats.append(Format(format_id=f"{side}", ext="mp4", height=side or None, width=None,
                                       vcodec="avc1" if h264 else "hevc", acodec="aac",
                                       bitrate=kbps or None))
        if not info.formats:
            raise EngineError("抖音分享页里没有这条视频的下载地址。")
        return info

    def download(self, url, ctx, sel: Selection, progress):
        kind, aweme_id = work_id(url)
        item = fetch_item(kind, aweme_id, ctx)
        title = _safe((item.get("desc") or "").strip() or aweme_id)
        out_dir = Path(sel.output_dir)
        images = image_urls(item)
        if images:
            files = []
            for n, u in enumerate(images, 1):
                ext = ".jpeg" if (".jpeg" in u or ".jpg" in u) else ".webp" if ".webp" in u else ".jpg"
                files.append(str(fetch_file(u, _unique(out_dir / f"{title} [{aweme_id}] {n}{ext}"),
                                            progress, (n - 1, len(images)))))
            return DownloadResult(path=files[0], files=files, kind="images")
        streams = video_streams(item)
        if not streams:
            raise EngineError("抖音分享页里没有这条视频的下载地址。")
        if sel.quality.isdigit():
            ok = [s for s in streams if s[0] <= int(sel.quality)] or streams
        else:
            ok = streams
        # 分辨率高的优先；同分辨率先选 H.264（省去转码），再选码率高的
        side, h264, kbps, src = max(ok, key=lambda s: (s[0], s[1], s[2]))
        fd, tmp = tempfile.mkstemp(prefix=".manna-douyin-", suffix=".mp4", dir=out_dir)
        os.close(fd)
        try:
            fetch_file(src, Path(tmp), progress)
            dst = _unique(out_dir / f"{title} [{aweme_id}]{sel.name_tag}.mp4")
            shutil.move(tmp, dst)
        finally:
            Path(tmp).unlink(missing_ok=True)
        if sel.quality == "audio":
            dst = to_mp3(dst)
        dur = (item.get("video") or {}).get("duration")
        return DownloadResult(path=str(dst), duration=dur / 1000 if dur and dur > 1000 else dur)


def to_mp3(path):
    """仅音频：用 ffmpeg 把下好的视频转成 MP3，删掉视频。"""
    path = Path(path)
    out = _unique(path.with_suffix(".mp3"))
    r = subprocess.run([tools.FFMPEG, "-hide_banner", "-y", "-i", str(path), "-vn", "-c:a", "libmp3lame",
                        "-q:a", "2", str(out)], capture_output=True, text=True, errors="replace")
    if r.returncode != 0 or not out.exists():
        out.unlink(missing_ok=True)
        raise EngineError("导出 MP3 失败：" + " ".join(r.stderr.strip().splitlines()[-2:]))
    path.unlink(missing_ok=True)
    return out
