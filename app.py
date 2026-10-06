"""Manna：基于 yt-dlp 的视频下载器（本地服务 + 网页界面）。

Mac 应用由 manna_app.py 启动（自带窗口）；也可以直接运行 python app.py 用浏览器打开。
"""
import json
import os
import re
import secrets
import shutil
import sys
import threading
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from config import BASE_DIR, FROZEN, SUPPORT_DIR, YTDLP_ZIP, get_logger, load_config, save_config

# 应用内「更新 yt-dlp」下载的新版优先于打包进来的版本（解析、下载在子进程里跑，见 tools.py；
# 这里只影响本进程里读浏览器登录信息、识别剪贴板链接用到的 yt-dlp 代码）
if FROZEN and YTDLP_ZIP.exists():
    sys.path.insert(0, str(YTDLP_ZIP))

import history
import shortlinks
import tools
from engines import Selection, browser_cookie_header, registry
from engines import douyin
from tools import FFMPEG

log = get_logger()
HOST, PORT = "127.0.0.1", int(os.environ.get("PORT", 8765))
# 每次启动随机生成，写进 Manna 自己的页面；别的网页拿不到，就没法调用本地接口
API_TOKEN = secrets.token_urlsafe(24)


DOWNLOAD_DIR = Path(os.environ.get("DOWNLOAD_DIR") or load_config().get("download_dir") or (
    Path.home() / "Downloads" / "Manna" if FROZEN else BASE_DIR / "downloads"))
SITE_NAMES = {
    "youtube": "YouTube", "bilibili": "B站", "douyin": "抖音", "tiktok": "TikTok",
    "twitter": "X / Twitter", "instagram": "Instagram", "pinterest": "Pinterest",
    "xiaohongshu": "小红书", "cctv": "CCTV", "xinpianchang": "新片场",
}

tasks = {}
tasks_lock = threading.Lock()


def site_key(extractor):
    e = (extractor or "").lower()
    for k in SITE_NAMES:
        if k in e:
            return k
    return e.split(":")[0]


def human_size(n):
    if not n:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit in ("B", "KB") else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def cookie_header(req, url):
    return browser_cookie_header(req, url)


def resolve_url(req):
    """展开短链接（b23.tv / v.douyin.com / xhslink.com），并整理成解析引擎认识的形式。"""
    return shortlinks.resolve(req["url"], (req.get("proxy") or "").strip())


LOGIN_HINTS = ("login", "log in", "cookie", "sign in", "fresh cookies", "not a bot", "rate-limit")


def needs_login(err):
    low = str(err).lower()
    return any(k in low for k in LOGIN_HINTS)


def auto_browsers():
    """「自动」模式下可以尝试的浏览器：Safari（已授权时）+ 本机装了的 Chrome / Edge / Firefox。"""
    out = []
    if sys.platform == "darwin":
        if safari_access() == "ok":
            out.append("safari")
        for b, app_name in (("chrome", "Google Chrome"), ("edge", "Microsoft Edge"), ("firefox", "Firefox")):
            if Path(f"/Applications/{app_name}.app").exists():
                out.append(b)
    return out


# 这些网站不登录只给低画质，「自动」模式下一开始就带上浏览器登录状态。
# (域名后缀, 用来检查登录的网址, 登录后才有的 cookie 名)
LOGIN_FIRST_SITES = (
    (("bilibili.com", "b23.tv"), "https://www.bilibili.com/", "SESSDATA"),
    (("douyin.com",), "https://www.douyin.com/", "sessionid"),
    (("xiaohongshu.com", "xhslink.com"), "https://www.xiaohongshu.com/", "web_session"),
    (("instagram.com",), "https://www.instagram.com/", "sessionid"),
)


def login_first_browser(req):
    """链接属于 LOGIN_FIRST_SITES 时，按 Safari → Chrome → Edge → Firefox 找第一个已登录该网站的浏览器。"""
    from urllib.parse import urlparse
    host = (urlparse((req.get("url") or "").strip()).hostname or "").lower()
    for suffixes, check_url, cookie_name in LOGIN_FIRST_SITES:
        if any(host == d or host.endswith("." + d) for d in suffixes):
            for b in auto_browsers():
                if re.search(rf"(^|;\s*){cookie_name}=", cookie_header(dict(req, cookies_browser=b), check_url)):
                    return b
            return ""
    return ""


def with_auto_cookies(req, fn):
    """登录浏览器为「自动」时：B站 / 抖音 / 小红书 / Instagram 先用已登录的浏览器；
    其他网站先不带登录信息，遇到需要登录的错误，再依次试本机可用的浏览器。
    返回 (结果, 实际用到的浏览器)。"""
    chosen = (req.get("cookies_browser") or "auto").strip()
    if chosen != "auto" or (req.get("cookies_file") or "").strip():
        return fn(req), ("" if chosen == "auto" else chosen)
    b = login_first_browser(req)
    if b:
        try:
            return fn(dict(req, cookies_browser=b)), b
        except Exception:
            pass
    try:
        return fn(dict(req, cookies_browser="")), ""
    except Exception as e:
        if not needs_login(e):
            raise
        first = e
    for b in auto_browsers():
        try:
            return fn(dict(req, cookies_browser=b)), b
        except Exception:
            continue
    raise first


def with_site_cookies(req):
    """抖音链接：设置里导入过抖音 Cookie、又没填别的 cookies.txt 时，用导入的那份。"""
    if douyin.is_douyin(req.get("url")) and not (req.get("cookies_file") or "").strip():
        path = douyin.cookies_file_for(req)
        if path:
            return dict(req, cookies_file=path)
    return req


def get_info(req):
    req = with_site_cookies(req)
    info, used = with_auto_cookies(req, _get_info)
    info["cookies_used"] = used
    return info


def _get_info(req):
    url = resolve_url(req)
    media, engine = registry.inspect(url, req)

    key = site_key(media.extractor)
    base = {
        "title": media.title,
        "uploader": media.author,
        "duration": media.duration,
        "thumbnail": media.cover,
        "site": SITE_NAMES.get(key, media.extractor),
        "webpage_url": media.webpage_url or url,
        "portrait": False,
        "engine": engine,
    }
    if media.images:
        # 抖音图集：全部图片一起下载，没有画质可选
        return dict(base, qualities=[], images=media.images)
    if media.qualities:
        return dict(base, qualities=engine_qualities(media.qualities))

    formats = media.formats
    best_audio = max((f.est_size or 0 for f in formats if not f.has_video), default=0)
    # 按「短边」分档：横屏 1920×1080 和竖屏 1080×1920 都算 1080p
    portrait = any((f.height or 0) > (f.width or 10 ** 6) for f in formats if f.has_video)
    by_side = {}
    for f in formats:
        side = short_side(f)
        if not side or not f.has_video:
            continue
        size = f.est_size or 0
        if f.acodec in (None, "none"):
            size = size + best_audio if size else 0
        cur = by_side.get(side)
        # 成片统一是 H.264：同一分辨率优先显示 H.264 的那条，没有才显示需要转码的
        score = (is_h264(f.vcodec), f.fps or 0, f.bitrate or 0)
        if not cur or score > cur["_score"]:
            vcodec_known = (f.vcodec or "unknown") not in ("unknown", "none")
            if vcodec_known and not score[0]:
                # 需要转码的：显示转成 H.264 之后的大小（估算），而不是网站上原文件的大小
                size = transcoded_size(f, media.duration, best_audio)
            by_side[side] = {"fps": f.fps, "size": size, "_score": score,
                             "h264": score[0], "vcodec_known": vcodec_known}
    qualities = []
    for side in sorted(by_side, reverse=True):
        q = by_side[side]
        label = f"{side}p"
        if q["fps"] and q["fps"] > 30:
            label += f"{int(q['fps'])}"
        # 编码未知（如 CCTV 的 HLS）时不下结论，下载后按实际编码决定是否转码
        codec = "H.264" if q["h264"] else ("需转码为 H.264" if q["vcodec_known"] else "")
        size = human_size(q["size"])
        if size and q["vcodec_known"] and not q["h264"]:
            size = "转码后约 " + size
        extra = " · ".join(x for x in (codec, size) if x)
        qualities.append({"value": str(side), "label": label + (f"（{extra}）" if extra else "")})
    if not qualities:
        # 一个格式都没有分辨率信息：按码率逐个列出，选哪个就下哪个
        rated = sorted((f for f in formats if f.has_video and f.format_id),
                       key=lambda f: f.bitrate or 0, reverse=True)
        if len(rated) > 1:
            for n, f in enumerate(rated, 1):
                size = human_size(f.est_size)
                extra = " · ".join(x for x in (f"{int(f.bitrate)}kbps" if f.bitrate else "", size) if x)
                qualities.append({"value": "f:" + f.format_id,
                                  "label": f"画质 {n}" + (f"（{extra}）" if extra else "")})

    return dict(base, qualities=qualities, portrait=portrait)


def engine_qualities(levels):
    """引擎给出的档位（下载前不知道实际有哪几档）：按短边列出，带码率和估算大小。"""
    out = []
    for q in levels:
        extra = " · ".join(x for x in (f"约 {q['kbps'] / 1000:g} Mbps" if q.get("kbps") else "",
                                       "约 " + human_size(q["size"]) if q.get("size") else "") if x)
        out.append({"value": str(q["value"]), "label": f"{q['value']}p" + (f"（{extra}）" if extra else "")})
    return out


def is_h264(vcodec):
    v = (vcodec or "").lower()
    return v.startswith(("avc", "h264"))


def short_side(f):
    w, h = f.width, f.height
    return min(w, h) if w and h else h


def notify(title, text):
    if sys.platform != "darwin":
        return
    import subprocess
    script = "on run argv\ndisplay notification (item 2 of argv) with title (item 1 of argv)\nend run"
    try:
        subprocess.Popen(["osascript", "-e", script, title, text or ""],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def run_download(task_id, req):
    def hook(d):
        with tasks_lock:
            t = tasks[task_id]
            if d["status"] == "downloading" and "percent" in d:
                pct = d.get("percent")
                t.update(status="downloading", percent=round(pct, 1) if pct is not None else None)
            elif d["status"] == "processing":
                t.update(status="processing", percent=None, note=d.get("note") or "")
            elif d["status"] == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                done = d.get("downloaded_bytes") or 0
                t.update(status="downloading",
                         percent=round(done * 100 / total, 1) if total else None,
                         speed=human_size(d.get("speed")) + "/s" if d.get("speed") else "",
                         eta=d.get("eta"), size=human_size(total))
            elif d["status"] == "finished":
                t.update(status="processing", percent=100)

    quality = str(req.get("quality") or "best")
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    # 视频的分辨率标签等下载完按实际画面补上（见 tag_with_resolution）
    sel = Selection(quality=quality, portrait=bool(req.get("portrait")), output_dir=str(DOWNLOAD_DIR),
                    name_tag=" audio" if quality == "audio" else "")
    req = with_site_cookies(req)
    url = resolve_url(req)
    try:
        if quality == "audio":
            tools.require("ffmpeg")
        result, used = registry.download(url, req, sel, hook, prefer=req.get("engine"))
        path = result.path
        if result.kind == "images":
            with tasks_lock:
                notify("下载完成", tasks[task_id].get("title"))
                tasks[task_id].update(status="done", percent=100, file=result.files[0],
                                      files=result.files, engine=used)
            record_history(task_id, req, result.files, quality)
            return
        if quality != "audio":
            path = ensure_h264(task_id, path, result.duration)
            path = tag_with_resolution(path, best=quality == "best")
        expected = req.get("duration") or result.duration
        problem = verify_media(task_id, path, float(expected) if expected else None, audio=quality == "audio")
        warning = ""
        if douyin.is_douyin(url):
            # 分享页 / yt-dlp 拿到的是抖音公开播放的文件：画质较低，带抖音水印
            warning = "抖音视频来自公开分享页，画质较低且带抖音水印。"
        if problem:
            warning = f"可能花屏/不完整：{problem}。" + warning
            warning += "可以点「重试」重新下载。"
        with tasks_lock:
            notify("下载完成" + ("（可能不完整）" if warning else ""), tasks[task_id].get("title"))
            tasks[task_id].update(status="done", percent=100, file=str(path), warning=warning, engine=used)
        record_history(task_id, req, [str(path)], quality)
    except Exception as e:
        with tasks_lock:
            tasks[task_id].update(status="error", error=friendly_error(str(e)))


def record_history(task_id, req, paths, quality):
    """下载完成后写进「下载记录」，再保存一张封面。出错只记日志，不影响下载结果。"""
    try:
        first = paths[0]
        size = video_size(first) if quality != "audio" and os.path.exists(first) else None
        history.add({
            "id": task_id, "title": req.get("title") or Path(first).stem,
            "url": req.get("url") or "", "files": paths,
            "width": size[0] if size else None, "height": size[1] if size else None,
            "audio": quality == "audio",
            "size": sum(os.path.getsize(p) for p in paths if os.path.exists(p)),
            "date": history.now(), "thumb": False,
        })
        if history.save_thumb(task_id, req.get("thumbnail"), None if quality == "audio" else first, FFMPEG):
            history.update(task_id, thumb=True)
    except Exception as e:
        log.warning("写下载记录失败：%s", e)


def history_list():
    out = []
    for e in history.all_entries():
        w, h = e.get("width"), e.get("height")
        res = "音频" if e.get("audio") else (f"{min(w, h)}p" if w and h else "")
        files = e.get("files") or []
        out.append({"id": e["id"], "title": e.get("title") or "", "url": e.get("url") or "",
                    "file": files[0] if files else "", "count": len(files),
                    "resolution": res, "dimensions": f"{w}×{h}" if w and h else "",
                    "size": human_size(e.get("size")), "date": e.get("date"),
                    "thumb": f"/thumbs/{e['id']}.jpg" if e.get("thumb") else "",
                    "exists": e["exists"]})
    return out


def delete_history(entry_id):
    """从下载记录里删除一条：文件移到废纸篓（已经不在的就跳过），记录一并删掉。"""
    e = history.get(entry_id)
    if not e:
        return
    with tasks_lock:
        t = tasks.get(entry_id)
        if t and t["status"] not in ("done", "error"):
            raise RuntimeError("这个文件还在处理中，请等它完成后再删。")
    for path in e.get("files") or []:
        if os.path.exists(path):
            move_to_trash(path)
    history.remove(entry_id)
    with tasks_lock:
        tasks.pop(entry_id, None)
        task_reqs.pop(entry_id, None)


def verify_media(task_id, path, expected=None, audio=False, need_audio=False):
    """完整性检查：用 ffprobe 读时长和音视频流，再用 ffmpeg 解码前 5 秒。
    没问题返回空字符串，有问题返回原因（文件保留，任务标记为「可能花屏/不完整」）。"""
    import subprocess
    if not FFMPEG or not os.path.exists(path):
        return ""
    with tasks_lock:
        tasks[task_id].update(status="processing", percent=None, note="检查文件")
    try:
        actual, has_v, has_a = probe_streams(path)
        dec = subprocess.run([FFMPEG, "-hide_banner", "-nostats", "-v", "error", "-t", "5", "-i", str(path),
                              "-map", "0:v:0?", "-map", "0:a:0?", "-f", "null", "-"],
                             capture_output=True, text=True, errors="replace", timeout=120)
    finally:
        with tasks_lock:
            tasks[task_id].pop("note", None)
    errors = [line for line in dec.stderr.splitlines() if line.strip()]
    problem = ""
    if not audio and not has_v:
        problem = "文件里没有画面"
    elif need_audio and not has_a:
        # 有些网站的视频本来就没有声音（如部分 Instagram），只在调用方要求时检查声音
        problem = "文件里没有声音"
    elif not actual:
        problem = "读不出时长"
    elif expected and abs(actual - expected) > max(expected * 0.03, 5):
        problem = f"时长 {fmt_minutes(actual)}，网站标的是 {fmt_minutes(expected)}"
    elif dec.returncode != 0 or errors:
        problem = "前 5 秒解码出错（" + (errors[0][:100] if errors else f"ffmpeg 返回 {dec.returncode}") + "）"
    log.info("完整性检查 %s：%s（时长 %.1fs，应为 %s）", Path(path).name, problem or "通过", actual, expected)
    return problem


def fmt_minutes(sec):
    return f"{int(sec // 60)} 分 {int(sec % 60)} 秒"


def probe_streams(path):
    """(时长秒, 有没有画面, 有没有声音)。有 ffprobe 用 ffprobe；Manna 自带的 ffmpeg 没有 ffprobe，就读 ffmpeg -i 的输出。"""
    import subprocess
    if tools.FFPROBE:
        r = subprocess.run([tools.FFPROBE, "-v", "error", "-show_entries", "format=duration:stream=codec_type",
                            "-of", "json", str(path)], capture_output=True, text=True, errors="replace", timeout=60)
        try:
            j = json.loads(r.stdout)
            kinds = {st.get("codec_type") for st in j.get("streams") or []}
            return float((j.get("format") or {}).get("duration") or 0), "video" in kinds, "audio" in kinds
        except (ValueError, TypeError):
            pass
    info = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                          errors="replace", timeout=60).stderr
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.?\d*)", info)
    actual = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0
    return actual, bool(re.search(r"Stream #.*?Video:", info)), bool(re.search(r"Stream #.*?Audio:", info))


def probe_codecs(path):
    """用 ffmpeg 读出文件里第一条视频流和音频流的编码名，例如 ("h264", "aac")。"""
    import subprocess
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                       errors="replace", timeout=60)
    v = re.search(r"Stream #.*?Video: (\w+)", r.stderr)
    a = re.search(r"Stream #.*?Audio: (\w+)", r.stderr)
    return (v.group(1) if v else ""), (a.group(1) if a else "")


def video_size(path):
    """用 ffmpeg 读出画面宽高；读不到返回 None。"""
    import subprocess
    if not FFMPEG:
        return None
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                       errors="replace", timeout=60)
    m = re.search(r"Video: .*?(\d{2,5})x(\d{2,5})", r.stderr)
    return (int(m.group(1)), int(m.group(2))) if m else None


def tag_with_resolution(path, best=False):
    """文件名补上实际下载到的分辨率（按短边），如「标题 [id] 1080p.mp4」「标题 [id] 最高2160p.mp4」。"""
    path = Path(path)
    size = video_size(path) if path.exists() else None
    if not size:
        return str(path)
    tag = ("最高" if best else "") + f"{min(size)}p"
    dst = path.with_name(f"{path.stem} {tag}{path.suffix}")
    n = 2
    while dst.exists():
        dst = path.with_name(f"{path.stem} {tag} ({n}){path.suffix}")
        n += 1
    path.rename(dst)
    return str(dst)


# 转 H.264 的目标码率：原视频画面码率 × 1.4（H.264 压缩效率比 VP9 / AV1 / HEVC 低，多给一些才不掉画质），
# 再按分辨率限制在上下限之间（下限防止原片码率很低时糊成马赛克，上限防止文件无谓地变大）
H264_FACTOR = 1.4
# (短边至少, 下限 kbps, 上限 kbps)
H264_LIMITS = ((2160, 6000, 40000), (1440, 3000, 20000), (1080, 1500, 10000), (720, 900, 5000), (0, 500, 2500))
AAC_KBPS = 192  # 声音不是 AAC 时重新编码用的码率


def h264_kbps(short, src_kbps=None):
    """按短边分辨率和原画面码率（kbps，未知时为 None）算 VideoToolbox 的目标码率（kbps）。"""
    short = short or 1080
    lo, hi = next((lo, hi) for limit, lo, hi in H264_LIMITS if short >= limit)
    if not src_kbps:
        return hi // 2
    return int(min(max(src_kbps * H264_FACTOR, lo), hi))


def video_kbps(f, duration):
    """一个格式的画面码率（kbps）：优先用网站给的，没有就用总码率或文件大小推算。"""
    if f.vbitrate:
        return f.vbitrate
    if f.bitrate:
        if f.acodec in (None, "none"):
            return f.bitrate
        if f.abitrate:
            return max(f.bitrate - f.abitrate, 0) or None
    if f.est_size and duration:
        kbps = f.est_size * 8 / duration / 1000
        return kbps if f.acodec in (None, "none") else max(kbps - (f.abitrate or 128), 0) or None
    return None


def transcoded_size(f, duration, audio_size):
    """估算转成 H.264 后的文件大小（字节）；算不出来返回 0。"""
    src = video_kbps(f, duration)
    if not src or not duration:
        return 0
    v = h264_kbps(short_side(f), src) * 1000 / 8 * duration
    if f.acodec in (None, "none"):
        a = audio_size or 0
    elif (f.acodec or "").startswith("mp4a") or f.acodec == "aac":
        a = (f.abitrate or 128) * 1000 / 8 * duration
    else:
        a = AAC_KBPS * 1000 / 8 * duration
    return int((v + a) * 1.02)  # mp4 封装另占约 2%


def probe_video_kbps(path):
    """用 ffmpeg 读出文件里画面的码率（kbps）；读不到时用总码率减去声音码率；都没有返回 None。"""
    import subprocess
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                       errors="replace", timeout=60)
    m = re.search(r"Stream #.*?Video: .*?(\d+) kb/s", r.stderr)
    if m:
        return int(m.group(1))
    total = re.search(r"bitrate: (\d+) kb/s", r.stderr)
    audio = re.search(r"Stream #.*?Audio: .*?(\d+) kb/s", r.stderr)
    if total:
        return max(int(total.group(1)) - (int(audio.group(1)) if audio else 0), 0) or None
    return None


def ensure_h264(task_id, path, duration):
    """成片统一为 H.264 + AAC 的 mp4（剪映、QuickTime 都能直接打开）。
    已经是 H.264 + AAC 的 mp4 原样返回；否则用 FFmpeg 转换，优先 VideoToolbox 硬件编码。"""
    import subprocess
    path = Path(path)
    if not FFMPEG or not path.exists():
        return str(path)
    vcodec, acodec = probe_codecs(path)
    if not vcodec:
        return str(path)
    v_ok, a_ok = vcodec == "h264", acodec in ("aac", "")
    if v_ok and a_ok and path.suffix.lower() == ".mp4":
        return str(path)
    out = path.with_suffix(".h264.tmp.mp4")
    audio = ["-c:a", "copy"] if a_ok else ["-c:a", "aac", "-b:a", f"{AAC_KBPS}k"]
    if v_ok:
        encoders = [["-c:v", "copy"]]
    else:
        size, src = video_size(path), probe_video_kbps(path)
        kbps = h264_kbps(min(size) if size else None, src)
        log.info("转 H.264：%s %s，原画面 %s kbps → 目标 %s kbps", vcodec, size, src, kbps)
        # VideoToolbox 不可用（或失败）时退回 libx264 软件编码
        encoders = [["-c:v", "h264_videotoolbox", "-b:v", f"{kbps}k", "-maxrate", f"{kbps * 3 // 2}k",
                     "-bufsize", f"{kbps * 2}k", "-profile:v", "high", "-pix_fmt", "yuv420p"],
                    ["-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p"]]
    with tasks_lock:
        tasks[task_id].update(status="processing", percent=0 if not v_ok else None,
                              note="转为 H.264" if not v_ok else "转换封装")
    err = ""
    for enc in encoders:
        cmd = [FFMPEG, "-hide_banner", "-y", "-i", str(path), "-map", "0:v:0", "-map", "0:a:0?",
               *enc, *audio, "-tag:v", "avc1", "-movflags", "+faststart",
               "-progress", "pipe:1", "-nostats", str(out)]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
        stderr_lines = []
        threading.Thread(target=lambda: stderr_lines.extend(p.stderr), daemon=True).start()
        for line in p.stdout:
            if line.startswith("out_time_us=") and duration:
                try:
                    done = int(line.split("=")[1]) / 1e6
                except ValueError:
                    continue
                with tasks_lock:
                    tasks[task_id]["percent"] = round(min(done / duration, 1) * 100, 1)
        p.wait()
        if p.returncode == 0 and out.exists() and out.stat().st_size > 0:
            final = path.with_suffix(".mp4")
            path.unlink(missing_ok=True)
            out.replace(final)
            with tasks_lock:
                tasks[task_id].pop("note", None)
            return str(final)
        err = "".join(stderr_lines[-5:])
        out.unlink(missing_ok=True)
    raise RuntimeError(f"转为 H.264 失败（原始文件保留在 {path}）：\n{err}")


# 国外网站：国内访问需要代理，网络出错时才提示填代理
FOREIGN_SITES = ("youtube", "twitter", "instagram", "tiktok", "facebook", "vimeo",
                 "pinterest", "reddit", "twitch", "dailymotion", "soundcloud", "threads")


def friendly_error(msg):
    msg = msg.replace("ERROR: ", "").split("; please report this issue")[0]
    low = msg.lower()
    hints = []
    is_douyin = "douyin" in low
    if is_douyin and "fresh cookies" in low:
        hints.append(douyin.COOKIE_EXPIRED)
    elif any(k in low for k in ("login", "cookie", "sign in", "fresh cookies", "not a bot", "rate-limit")):
        hints.append("这个网站需要登录：先在浏览器里登录它，再在设置里把「登录浏览器」选成那个浏览器后重试。")
    if "operation not permitted" in low and "safari" in low:
        hints = ["读取 Safari 的登录信息需要「完全磁盘访问权限」：系统设置 → 隐私与安全性 → 完全磁盘访问权限，点 + 添加「应用程序」里的 Manna 并打开开关，然后退出重开 Manna。也可以改用 Chrome。"]
    if "ffmpeg" in low:
        hints.append("缺少 ffmpeg：运行 pip install imageio-ffmpeg，或自行安装 ffmpeg。")
    if "unsupported url" in low:
        hints.append("链接不受支持：请复制视频详情页的完整链接（不要用分享口令文字）。")
    m = re.match(r"\s*\[([^\]]+)\]", msg)
    extractor = (m.group(1) if m else "").lower()
    foreign = any(k in extractor for k in FOREIGN_SITES)
    if "xinpianchang" in extractor and "403" in low:
        hints.append("新片场对这个链接开启了「浏览器环境验证」（反爬虫），只放行真正的浏览器，Manna 暂时下载不了，换代理或登录也没用。")
    elif foreign and any(k in low for k in ("timed out", "connection", "proxy", "unable to download webpage")):
        hints.append("网络连不上：YouTube / X / Instagram 等在国内需要在设置里填写代理。")
    if not is_douyin and any(k in low for k in ("unable to extract", "unable to parse", "keyerror", "nsig", "signature")):
        hints.append("可能是网站改版、下载组件过旧：Manna 每天会自动更新下载组件，退出并重新打开 Manna 后再试。")
    return msg + ("\n\n" + "\n".join("• " + h for h in hints) if hints else "")


def update_ytdlp():
    if not FROZEN:
        import subprocess
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-U", "yt-dlp[default]"],
                           capture_output=True, text=True)
        return {"ok": r.returncode == 0,
                "output": (r.stdout + r.stderr)[-800:],
                "note": "更新完成，请关闭窗口重新运行程序以生效。" if r.returncode == 0 else "更新失败"}
    # 打包后的应用没有 pip：下载 yt-dlp 官方发布的单文件版（本身是 zip 包），
    # 并用同一版本发布页里的 SHA2-256SUMS 校验，不一致就不替换
    import hashlib
    import urllib.request
    import zipfile
    SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = YTDLP_ZIP.with_suffix(".tmp")
    try:
        def get(url):
            req = urllib.request.Request(url, headers={"User-Agent": "Manna"})
            return urllib.request.urlopen(req, timeout=120)
        with get("https://github.com/yt-dlp/yt-dlp/releases/latest") as r:
            tag = r.geturl().rstrip("/").rsplit("/", 1)[-1]
        if not re.fullmatch(r"[\w.]+", tag):
            raise RuntimeError(f"读不到最新版本号（{tag}）")
        base = f"https://github.com/yt-dlp/yt-dlp/releases/download/{tag}/"
        with get(base + "SHA2-256SUMS") as r:
            sums = r.read().decode()
        m = re.search(r"^([0-9a-f]{64})\s+\*?yt-dlp$", sums, re.M)
        if not m:
            raise RuntimeError("校验文件里没有 yt-dlp 的哈希")
        h = hashlib.sha256()
        with get(base + "yt-dlp") as r, open(tmp, "wb") as f:
            while chunk := r.read(1 << 20):
                h.update(chunk)
                f.write(chunk)
        if h.hexdigest() != m.group(1):
            raise RuntimeError("下载的文件校验不通过，已丢弃")
        with zipfile.ZipFile(tmp) as z:
            ver = z.read("yt_dlp/version.py").decode()
        new = re.search(r"__version__\s*=\s*'([^']+)'", ver).group(1)
        tmp.replace(YTDLP_ZIP)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        return {"ok": False, "note": f"更新失败：{e}\n（需要能访问 GitHub，必要时开代理）"}
    return {"ok": True, "note": f"已下载 yt-dlp {new}，退出并重新打开 Manna 后生效。"}


UPDATE_STAMP = SUPPORT_DIR / "last_update_check"


def auto_update_ytdlp():
    """每 24 小时在后台静默检查一次 yt-dlp 新版，下次打开 Manna 时生效。"""
    import time
    if not FROZEN:
        return
    try:
        if UPDATE_STAMP.exists() and time.time() - UPDATE_STAMP.stat().st_mtime < 24 * 3600:
            return
        # 只有更新成功才记时间，失败下次打开再试
        if update_ytdlp().get("ok"):
            UPDATE_STAMP.touch()
    except Exception:
        pass


def is_supported(url):
    if shortlinks.is_short(url):
        return True
    return registry.recognizes(url)


def clipboard_url():
    if sys.platform != "darwin":
        return ""
    import subprocess
    try:
        text = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=3).stdout
    except Exception:
        return ""
    m = re.search(r"https?://[^\s\"'<>，。！）]+", text or "")
    url = m.group(0) if m else ""
    return url if url and is_supported(url) else ""


def choose_folder():
    global DOWNLOAD_DIR
    import subprocess
    r = subprocess.run(["osascript", "-e", 'POSIX path of (choose folder with prompt "选择 Manna 的保存位置")'],
                       capture_output=True, text=True)
    path = r.stdout.strip()
    if r.returncode != 0 or not path:
        return {"ok": False, "download_dir": str(DOWNLOAD_DIR)}
    DOWNLOAD_DIR = Path(path.rstrip("/") or "/")
    save_config(download_dir=str(DOWNLOAD_DIR))
    return {"ok": True, "download_dir": str(DOWNLOAD_DIR)}


def import_douyin_cookies():
    """设置 → 抖音 → 导入 Cookie：选一个 cookies.txt，只保留抖音的行，存到 Manna 自己的目录。"""
    import subprocess
    r = subprocess.run(["osascript", "-e", 'POSIX path of (choose file with prompt '
                        '"选择从浏览器导出的 cookies.txt（只会保存其中抖音的部分）")'],
                       capture_output=True, text=True)
    path = r.stdout.strip()
    if r.returncode != 0 or not path:
        return dict(douyin.cookie_status(), cancelled=True)
    res = douyin.import_cookies(path)
    return dict(douyin.cookie_status(), **res)


def reveal(path):
    """只允许在访达里显示 Manna 自己下载的文件。"""
    import subprocess
    with tasks_lock:
        known = {t.get("file") for t in tasks.values()}
    known |= history.known_files()
    if path not in known or not os.path.exists(path):
        raise RuntimeError("找不到这个文件，可能已被移动或删除。")
    subprocess.Popen(["open", "-R", path])


def move_to_trash(path):
    """把文件移到废纸篓（可以从废纸篓恢复），不直接删除。"""
    if sys.platform == "darwin":
        try:
            from Foundation import NSFileManager, NSURL
            ok, _, err = NSFileManager.defaultManager().trashItemAtURL_resultingItemURL_error_(
                NSURL.fileURLWithPath_(path), None, None)
            if ok:
                return
        except Exception:
            pass
        trash = Path.home() / ".Trash"
        dest = trash / Path(path).name
        n = 1
        while dest.exists():
            dest = trash / f"{Path(path).stem} {n}{Path(path).suffix}"
            n += 1
        shutil.move(path, dest)
    else:
        raise RuntimeError("当前系统不支持移到废纸篓，请手动删除文件。")


def delete_task(task_id):
    """从下载列表里删除一条；已下载的文件移到废纸篓。下载中的任务不能删。"""
    with tasks_lock:
        t = tasks.get(task_id)
        if not t:
            return
        if t["status"] not in ("done", "error"):
            raise RuntimeError("正在下载的任务不能删除，请等它完成或失败后再删。")
        paths = t.get("files") or ([t["file"]] if t.get("file") else [])
    for path in paths:
        if os.path.exists(path):
            move_to_trash(path)
    with tasks_lock:
        tasks.pop(task_id, None)
        task_reqs.pop(task_id, None)
    if paths:
        history.remove(task_id)


task_reqs = {}


def queue_download(req):
    task_id = uuid.uuid4().hex[:8]
    with tasks_lock:
        tasks[task_id] = {"id": task_id, "title": req.get("title") or req["url"],
                          "thumbnail": req.get("thumbnail"),
                          "quality": req.get("quality") or "best",
                          "status": "queued", "percent": 0}
        task_reqs[task_id] = req
    threading.Thread(target=run_download, args=(task_id, req), daemon=True).start()
    return task_id


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def allowed(self):
        """只接受 Manna 自己页面发来的请求：Host 必须是本机地址（防 DNS 重绑定），
        /api/ 接口还要带上启动时生成的令牌（别的网页读不到）。"""
        port = self.server.server_address[1]
        if self.headers.get("Host") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            return False
        if self.path.startswith("/api/"):
            return secrets.compare_digest(self.headers.get("X-Manna-Token") or "", API_TOKEN)
        return True

    def do_GET(self):
        if not self.allowed():
            return self.send_json({"error": "forbidden"}, 403)
        if self.path in ("/", "/index.html"):
            body = (BASE_DIR / "index.html").read_bytes().replace(b"__MANNA_TOKEN__", API_TOKEN.encode())
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/tasks":
            with tasks_lock:
                self.send_json(list(tasks.values()))
        elif self.path == "/api/history":
            self.send_json(history_list())
        elif self.path.startswith("/thumbs/"):
            p = history.thumb_path(self.path[len("/thumbs/"):].split("?")[0].removesuffix(".jpg"))
            if not p:
                return self.send_json({"error": "not found"}, 404)
            body = p.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/clipboard":
            self.send_json({"url": clipboard_url()})
        elif self.path == "/api/status":
            st = tools.status()
            self.send_json({"version": st.get("yt-dlp", {}).get("version") or "未找到",
                            "tools": st,
                            "ffmpeg": bool(FFMPEG), "ffmpeg_path": FFMPEG,
                            "download_dir": str(DOWNLOAD_DIR),
                            "safari": safari_access(),
                            "douyin_cookies": douyin.cookie_status()})
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        if not self.allowed():
            return self.send_json({"error": "forbidden"}, 403)
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self.send_json({"error": "bad request"}, 400)
        if length > 1 << 20:
            return self.send_json({"error": "too large"}, 413)
        try:
            req = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self.send_json({"error": "bad json"}, 400)
        try:
            if self.path == "/api/info":
                self.send_json(get_info(req))
            elif self.path == "/api/download":
                self.send_json({"id": queue_download(req)})
            elif self.path == "/api/retry":
                with tasks_lock:
                    old = task_reqs.get(req.get("id"))
                    if old:
                        tasks.pop(req["id"], None)
                if not old:
                    raise RuntimeError("这个任务已经无法重试，请重新粘贴链接。")
                self.send_json({"id": queue_download(old)})
            elif self.path == "/api/reveal":
                reveal(req.get("path") or "")
                self.send_json({"ok": True})
            elif self.path == "/api/choose-folder":
                self.send_json(choose_folder())
            elif self.path == "/api/open-folder":
                DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
                open_folder(DOWNLOAD_DIR)
                self.send_json({"ok": True})
            elif self.path == "/api/delete":
                delete_task(req.get("id") or "")
                self.send_json({"ok": True})
            elif self.path == "/api/history/delete":
                delete_history(req.get("id") or "")
                self.send_json({"ok": True})
            elif self.path == "/api/open-fda":
                open_fda_settings()
                self.send_json({"ok": True})
            elif self.path == "/api/open-licenses":
                lic = BASE_DIR / "licenses"
                if not lic.is_dir():
                    raise RuntimeError("没有找到许可证文件夹。")
                open_folder(lic)
                self.send_json({"ok": True})
            elif self.path == "/api/douyin/import":
                self.send_json(import_douyin_cookies())
            elif self.path == "/api/douyin/import-browser":
                browser = (req.get("browser") or "").strip()
                res = douyin.import_from_browser("chrome" if browser in ("", "auto") else browser)
                self.send_json(dict(douyin.cookie_status(), **res))
            elif self.path == "/api/douyin/clear":
                douyin.clear_cookies()
                self.send_json(douyin.cookie_status())
            elif self.path == "/api/update":
                self.send_json(update_ytdlp())
            elif self.path == "/api/clear":
                with tasks_lock:
                    for k in [k for k, t in tasks.items() if t["status"] in ("done", "error")]:
                        del tasks[k]
                self.send_json({"ok": True})
            else:
                self.send_json({"error": "not found"}, 404)
        except Exception as e:
            self.send_json({"error": friendly_error(str(e))}, 400)


SAFARI_COOKIES = Path.home() / "Library/Containers/com.apple.Safari/Data/Library/Cookies/Cookies.binarycookies"


def safari_access():
    """Safari 登录信息能否读取：ok / denied（缺完全磁盘访问权限）/ missing / na（非 Mac）。"""
    if sys.platform != "darwin":
        return "na"
    try:
        with open(SAFARI_COOKIES, "rb") as f:
            f.read(4)
        return "ok"
    except PermissionError:
        return "denied"
    except OSError as e:
        return "denied" if getattr(e, "errno", None) == 1 else "missing"


def open_fda_settings():
    """打开「完全磁盘访问权限」设置页，并在访达里选中 Manna.app 方便拖进去。"""
    import subprocess
    subprocess.Popen(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"])
    if FROZEN:
        app_path = Path(sys.executable).resolve().parents[2]
        if app_path.suffix == ".app":
            subprocess.Popen(["open", "-R", str(app_path)])


def open_folder(path):
    import subprocess
    if sys.platform.startswith("win"):
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def startup_checks():
    """启动时检查外部工具（yt-dlp / ffmpeg / Deno），再在后台检查 yt-dlp 更新。"""
    tools.check_all()
    auto_update_ytdlp()


def start_server():
    """在后台线程启动本地服务，返回网址。端口被占用时自动换一个。"""
    try:
        os.chdir(Path.home())  # 从访达打开时当前目录是只读的「/」
    except OSError:
        pass
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError:
        server = ThreadingHTTPServer((HOST, 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Thread(target=startup_checks, daemon=True).start()
    return f"http://{HOST}:{server.server_address[1]}"


def main():
    url = start_server()
    print(f"Manna 已启动：{url}")
    st = tools.status()
    for name, s in st.items():
        print(f"{name}：{s['version'] + '  ' + s['path'] if s['ok'] else s['message']}")
    print(f"下载目录：{DOWNLOAD_DIR}")
    print("关闭此窗口即退出。")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
