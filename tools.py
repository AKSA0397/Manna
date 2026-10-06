"""外部工具：yt-dlp、ffmpeg、Deno（以及可选的 node / bun）。

全部以子进程方式调用。路径可以在 config.json 的 "tools" 里指定，或用环境变量覆盖：
    {"tools": {"yt-dlp": "/opt/homebrew/bin/yt-dlp", "ffmpeg": "/opt/homebrew/bin/ffmpeg", "deno": "..."}}
    MANNA_YTDLP / MANNA_FFMPEG / MANNA_DENO
没指定时用 Manna 自带的版本。启动时检查每个工具是否存在、能否运行、版本号，缺失时给出中文说明。
"""
import os
import shutil
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

from config import BASE_DIR, FROZEN, SUPPORT_DIR, YTDLP_ZIP, get_logger, load_config

log = get_logger()

# 从访达双击打开的应用拿不到终端的 PATH，补上 Homebrew / Deno 的常见位置
for _p in ("/opt/homebrew/bin", "/usr/local/bin", str(Path.home() / ".deno" / "bin")):
    if _p not in os.environ.get("PATH", "").split(os.pathsep) and os.path.isdir(_p):
        os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + _p

ENV_KEYS = {"yt-dlp": "MANNA_YTDLP", "ffmpeg": "MANNA_FFMPEG", "deno": "MANNA_DENO"}

MISSING = {
    "yt-dlp": "找不到可以运行的 yt-dlp，解析和下载都用不了。请重新安装 Manna；"
              "如果在 config.json 里指定了 yt-dlp 的路径，请确认那个文件存在并且能运行。",
    "ffmpeg": "找不到可以运行的 ffmpeg：高清视频的音画合并、转 H.264、导出 MP3 都要用它。"
              "请重新安装 Manna，或在终端运行 brew install ffmpeg。",
    "deno": "找不到 Deno：YouTube 的 2K / 4K 等高画质需要它解出下载地址，其他网站不受影响。"
            "请重新安装 Manna，或在终端运行 brew install deno。",
}


def configured(name):
    """用户指定的路径（环境变量优先于 config.json）；没指定返回空字符串。"""
    p = os.environ.get(ENV_KEYS[name]) or (load_config().get("tools") or {}).get(name) or ""
    return os.path.expanduser(str(p).strip())


def _run(cmd, timeout=60, env=None):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout,
                           env=env, stdin=subprocess.DEVNULL)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return -1, str(e)


def first_line(text):
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


# ---------- yt-dlp ----------

def ytdlp_command():
    """返回 (命令前缀, 额外环境变量)。后面接 yt-dlp 的参数即可运行。"""
    env = {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "LC_ALL": "en_US.UTF-8",
           "LANG": "en_US.UTF-8"}
    p = configured("yt-dlp")
    if p:
        if not os.path.isfile(p):
            log.warning("config.json 指定的 yt-dlp 不存在：%s，改用自带版本", p)
        elif zipfile.is_zipfile(p):
            # 单文件版（zipapp）：用 Manna 自带的 Python 运行
            return _self_python(), dict(env, MANNA_YTDLP_ZIP=p)
        else:
            return [p], env
    if FROZEN and YTDLP_ZIP.exists():
        env["MANNA_YTDLP_ZIP"] = str(YTDLP_ZIP)
    return _self_python(), env


def _self_python():
    if FROZEN:
        return [sys.executable, "--manna-yt-dlp"]
    return [sys.executable, str(Path(__file__).resolve().parent / "ytdlp_runner.py")]


def ytdlp_env(extra):
    env = dict(os.environ)
    env.update(extra)
    return env


# ---------- ffmpeg ----------

def _ffmpeg_works(path):
    return _run([path, "-version"], timeout=20)[0] == 0


def find_ffmpeg():
    """返回一个确认能运行的 ffmpeg 完整路径；找不到返回 None。"""
    p = configured("ffmpeg")
    if p:
        if _ffmpeg_works(p):
            return p
        log.warning("config.json 指定的 ffmpeg 不能运行：%s，改用自带版本", p)
    candidates = [shutil.which("ffmpeg")]
    try:
        import imageio_ffmpeg
        candidates.append(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        pass
    for c in filter(None, candidates):
        if _ffmpeg_works(c):
            return c
    # 打包后自带的 ffmpeg 可能丢了可执行权限：复制一份到应用数据目录再试
    for c in filter(None, candidates[1:]):
        try:
            SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
            dst = SUPPORT_DIR / "ffmpeg"
            if not dst.exists() or dst.stat().st_size != os.path.getsize(c):
                shutil.copyfile(c, dst)
            os.chmod(dst, 0o755)
            if sys.platform == "darwin":
                subprocess.run(["xattr", "-c", str(dst)], capture_output=True)
                subprocess.run(["codesign", "--force", "--sign", "-", str(dst)], capture_output=True)
            if _ffmpeg_works(str(dst)):
                return str(dst)
        except Exception:
            pass
    return None


# ---------- Deno / node / bun ----------

def find_js_runtimes():
    """YouTube 要靠 JS 运行环境解出 4K / 8K 等高画质地址。打包后的 App 拿不到终端的 PATH，所以按常见位置找。"""
    dirs = ["/opt/homebrew/bin", "/usr/local/bin", str(Path.home() / ".deno/bin"),
            str(Path.home() / ".bun/bin"), "/usr/bin"]
    found = {}
    p = configured("deno")
    if p:
        if os.access(p, os.X_OK):
            found["deno"] = {"path": p}
        else:
            log.warning("config.json 指定的 deno 不能运行：%s，改用自带版本", p)
    # 其次用 App 里自带的 Deno，这样别人的 Mac 没装 Node / Deno 也能下 4K
    bundled = BASE_DIR / "deno" / "deno"
    if "deno" not in found and bundled.is_file():
        if not os.access(bundled, os.X_OK):
            try:
                bundled.chmod(0o755)
            except OSError:
                pass
        if os.access(bundled, os.X_OK):
            found["deno"] = {"path": str(bundled)}
    for name in ("deno", "node", "bun"):
        if name in found:
            continue
        p = shutil.which(name) or next((f"{d}/{name}" for d in dirs if os.access(f"{d}/{name}", os.X_OK)), None)
        if p:
            found[name] = {"path": p}
    return found


FFMPEG = find_ffmpeg()
# 完整性检查用；Manna 自带的 ffmpeg 没有 ffprobe，找不到时改读 ffmpeg 的输出
# （要先试运行一次：Homebrew 的 ffprobe 可能因为依赖库升级而打不开）
FFPROBE = next((p for p in (os.path.join(os.path.dirname(FFMPEG), "ffprobe") if FFMPEG else "",
                            shutil.which("ffprobe"))
                if p and os.access(p, os.X_OK) and _run([p, "-version"], timeout=20)[0] == 0), None)
JS_RUNTIMES = find_js_runtimes()


# ---------- 启动检查 ----------

_status = {}
_checked = threading.Event()


def check_all():
    """检查三个工具是否存在、能否运行、版本号；结果写日志，并供 /api/status 读取。"""
    out = {}
    cmd, extra = ytdlp_command()
    code, text = _run(cmd + ["--version"], timeout=90, env=ytdlp_env(extra))
    out["yt-dlp"] = {"ok": code == 0, "path": " ".join(cmd),
                     "version": first_line(text) if code == 0 else "",
                     "message": "" if code == 0 else MISSING["yt-dlp"] + (
                         f"\n（{first_line(text)[:200]}）" if first_line(text) else "")}
    if FFMPEG:
        code, text = _run([FFMPEG, "-version"], timeout=20)
        ver = first_line(text).replace("ffmpeg version ", "").split(" ")[0]
        out["ffmpeg"] = {"ok": True, "path": FFMPEG, "version": ver, "message": ""}
    else:
        out["ffmpeg"] = {"ok": False, "path": "", "version": "", "message": MISSING["ffmpeg"]}
    deno = (JS_RUNTIMES.get("deno") or {}).get("path")
    code, text = _run([deno, "--version"], timeout=20) if deno else (-1, "")
    out["deno"] = {"ok": code == 0, "path": deno or "",
                   "version": first_line(text).replace("deno ", "").split(" ")[0] if code == 0 else "",
                   "message": "" if code == 0 else MISSING["deno"]}
    for name, s in out.items():
        if s["ok"]:
            log.info("工具 %s %s：%s", name, s["version"], s["path"])
        elif s.get("optional"):
            log.info("可选工具 %s 未启用：%s", name, s["message"])
        else:
            log.warning("工具 %s 不可用：%s", name, s["message"])
    _status.clear()
    _status.update(out)
    _checked.set()
    return out


def status(wait=30):
    """启动检查的结果（还没检查完时最多等 wait 秒）。"""
    _checked.wait(wait)
    return dict(_status)


def require(name):
    """要用某个工具前调用：不可用时抛出中文说明。"""
    s = status().get(name)
    if s and not s["ok"]:
        raise RuntimeError(s["message"])
