"""yt-dlp 引擎：以子进程方式运行 yt-dlp（Manna 自带 / 每日自动更新的单文件版 / 用户指定的路径）。"""
import json
import os
import re
import subprocess
import tempfile
import threading

import tools

from .base import DownloadResult, EngineError, Extractor, Format, MediaInfo, Selection

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
# 同一分辨率里优先选 H.264 视频 + AAC 音频，省掉转码；分辨率仍然优先
FORMAT_SORT = "res,vcodec:h264,acodec:aac"
# 合并音视频时优先 mp4；mp4 装不下的编码组合（如 VP9 + Opus）先放进 mkv，之后由 Manna 统一转成 H.264 mp4
MERGE_FORMAT = "mp4/mkv"
PROGRESS = ("download:MANNAPROG %(progress.status)s|%(progress.downloaded_bytes)s|%(progress.total_bytes)s|"
            "%(progress.total_bytes_estimate)s|%(progress.speed)s|%(progress.eta)s")


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def common_args(ctx):
    """把界面上的设置（登录浏览器、cookies.txt、代理）翻译成 yt-dlp 命令行参数。"""
    args = ["--ignore-config", "--no-warnings", "--no-playlist", "--playlist-items", "1",
            "--socket-timeout", "30", "--retries", "5",
            "--add-header", f"User-Agent:{UA}",
            # 打包后的 App 当前目录是只读的「/」，yt-dlp 检测格式时写的临时文件要放到系统临时目录
            "-P", f"temp:{tempfile.gettempdir()}"]
    if tools.FFMPEG:
        args += ["--ffmpeg-location", tools.FFMPEG]
    for name, rt in tools.JS_RUNTIMES.items():
        args += ["--js-runtimes", f"{name}:{rt['path']}" if rt.get("path") else name]
    browser = (ctx.get("cookies_browser") or "").strip()
    if browser == "auto":
        browser = ""
    cookie_file = (ctx.get("cookies_file") or "").strip().strip('"')
    if re.match(r"https?://", cookie_file, re.I):
        cookie_file = ""  # 视频链接误贴进了 cookies.txt 栏，忽略它
    if cookie_file:
        if not os.path.isfile(os.path.expanduser(cookie_file)):
            raise RuntimeError(f"cookies.txt 路径不存在：{cookie_file}（不用 cookies.txt 就把这一栏清空）")
        args += ["--cookies", os.path.expanduser(cookie_file)]
    elif browser:
        args += ["--cookies-from-browser", browser]
    proxy = (ctx.get("proxy") or "").strip()
    if proxy:
        args += ["--proxy", proxy]
    return args


def error_text(stderr):
    """从 yt-dlp 的错误输出里取出 ERROR 那几行（和以前库方式报错的文字一致）。"""
    lines = [line for line in stderr.splitlines() if line.startswith("ERROR:")]
    if lines:
        return "\n".join(lines)
    tail = [line for line in stderr.strip().splitlines() if line.strip()][-5:]
    return "\n".join(tail) or "yt-dlp 运行失败（没有错误信息）"


def format_for(quality, portrait=False):
    if quality == "audio":
        return "bestaudio/best"
    if quality.startswith("f:"):
        fid = quality[2:]
        return f"{fid}+ba/{fid}"
    if quality.isdigit():
        # 竖屏视频的短边是宽度
        dim, h = ("width" if portrait else "height"), int(quality)
        return (f"bv*[{dim}<={h}]+ba/b[{dim}<={h}]/"
                f"bv*[{dim}<={h}]/bv*+ba/b")
    return "bv*+ba/b"


def to_format(f):
    v, a = f.get("vcodec"), f.get("acodec")
    return Format(
        format_id=str(f.get("format_id") or ""), ext=f.get("ext") or "",
        width=f.get("width"), height=f.get("height"), fps=f.get("fps"),
        vcodec=v, acodec=a, bitrate=f.get("tbr"), vbitrate=f.get("vbr"), abitrate=f.get("abr"),
        est_size=f.get("filesize") or f.get("filesize_approx") or None,
        split=v == "none" or a in (None, "none"))


class YtDlpEngine(Extractor):
    name = "yt-dlp"
    priority = 100

    def __init__(self):
        self._extractors = None

    def _run_json(self, args, timeout=600):
        cmd, extra = tools.ytdlp_command()
        try:
            r = subprocess.run(cmd + args, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=timeout, env=tools.ytdlp_env(extra),
                               stdin=subprocess.DEVNULL)
        except FileNotFoundError:
            raise EngineError(tools.MISSING["yt-dlp"])
        except subprocess.TimeoutExpired:
            raise EngineError("解析超时（超过 10 分钟没有结果），请检查网络或代理后重试。")
        if r.returncode != 0:
            raise EngineError(error_text(r.stderr))
        try:
            return json.loads(r.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise EngineError(error_text(r.stderr) if r.stderr.strip() else "yt-dlp 没有返回解析结果")

    # ---- 接口 ----

    def match(self, url):
        # yt-dlp 有通用解析器，任何网页链接都可以试
        return bool(re.match(r"https?://", url or "", re.I))

    def recognizes(self, url):
        """用自带的 yt-dlp 解析器列表判断（不算通用解析器）。只读规则，不联网。"""
        if self._extractors is None:
            try:
                import yt_dlp
                self._extractors = [ie for ie in yt_dlp.extractor.gen_extractor_classes()
                                    if ie.IE_NAME != "generic"]
            except Exception:
                self._extractors = []
        return any(ie.suitable(url) for ie in self._extractors)

    def inspect(self, url, ctx):
        tools.require("yt-dlp")
        info = self._run_json(common_args(ctx) + ["-J", url])
        if info.get("_type") == "playlist" and info.get("entries"):
            info = next(e for e in info["entries"] if e)
        return MediaInfo(
            title=info.get("title"), duration=info.get("duration"),
            cover=info.get("thumbnail"), author=info.get("uploader"),
            formats=[to_format(f) for f in (info.get("formats") or [info])],
            id=str(info.get("id") or ""), webpage_url=info.get("webpage_url") or url,
            extractor=info.get("extractor_key") or info.get("extractor") or "", engine=self.name)

    def download(self, url, ctx, sel: Selection, progress):
        tools.require("yt-dlp")
        args = common_args(ctx) + [
            "-f", format_for(sel.quality, sel.portrait),
            "-S", FORMAT_SORT,
            "-o", os.path.join(sel.output_dir, "%(title).80B [%(id)s]" + sel.name_tag + ".%(ext)s"),
            "--windows-filenames", "--concurrent-fragments", "4",
            "--newline", "--progress", "--progress-template", PROGRESS,
            "--print", "after_move:MANNAFILE %(filepath)s",
            "--print", "after_move:MANNADUR %(duration)s",
        ]
        if sel.quality == "audio":
            args += ["--extract-audio", "--audio-format", "mp3"]
        else:
            args += ["--merge-output-format", MERGE_FORMAT]
        cmd, extra = tools.ytdlp_command()
        try:
            p = subprocess.Popen(cmd + args + ["--", url], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                                 env=tools.ytdlp_env(extra))
        except FileNotFoundError:
            raise EngineError(tools.MISSING["yt-dlp"])
        stderr_lines = []
        t = threading.Thread(target=lambda: stderr_lines.extend(p.stderr), daemon=True)
        t.start()
        path, duration = None, None
        for line in p.stdout:
            line = line.rstrip("\n")
            if line.startswith("MANNAPROG "):
                st, done, total, est, speed, eta = (line[10:].split("|") + [""] * 6)[:6]
                progress({"status": st, "downloaded_bytes": _num(done), "total_bytes": _num(total),
                          "total_bytes_estimate": _num(est), "speed": _num(speed), "eta": _num(eta)})
            elif line.startswith("MANNAFILE "):
                path = line[10:]
            elif line.startswith("MANNADUR "):
                duration = _num(line[9:])
        p.wait()
        t.join(5)
        if p.returncode != 0:
            raise EngineError(error_text("".join(stderr_lines)))
        if not path:
            raise EngineError("yt-dlp 没有报告下载好的文件位置。\n" + error_text("".join(stderr_lines)))
        return DownloadResult(path=path, duration=duration)


def browser_cookie_header(ctx, url):
    """读出浏览器 / cookies.txt 里某个网址的 Cookie（用来判断是否已登录）。只读本机文件，不联网。"""
    try:
        import yt_dlp
        opts = {"quiet": True, "no_warnings": True}
        cookie_file = (ctx.get("cookies_file") or "").strip().strip('"')
        browser = (ctx.get("cookies_browser") or "").strip()
        if cookie_file and os.path.isfile(os.path.expanduser(cookie_file)):
            opts["cookiefile"] = os.path.expanduser(cookie_file)
        elif browser and browser != "auto":
            opts["cookiesfrombrowser"] = (browser,)
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.cookiejar.get_cookie_header(url) or ""
    except Exception:
        return ""
