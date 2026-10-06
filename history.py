"""下载记录：每次下载完成记一条，存在 ~/Library/Application Support/Manna/history.json，重启后还在。

封面图下载完成时存到 Application Support/Manna/thumbs/ 里（网站给的封面地址常常几小时就失效）；
拿不到网站封面时，从视频里截一帧。
"""
import json
import os
import re
import subprocess
import threading
import time
import urllib.request

from config import SUPPORT_DIR, get_logger

HISTORY_FILE = SUPPORT_DIR / "history.json"
THUMB_DIR = SUPPORT_DIR / "thumbs"
MAX_ENTRIES = 2000
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Safari/605.1.15"

_lock = threading.Lock()
log = get_logger()


def _load():
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(entries):
    SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = HISTORY_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(HISTORY_FILE)


def add(entry):
    with _lock:
        entries = [e for e in _load() if e.get("id") != entry["id"]]
        entries.append(entry)
        dropped = entries[:-MAX_ENTRIES]
        _save(entries[-MAX_ENTRIES:])
    for e in dropped:
        _remove_thumb(e)


def update(entry_id, **kw):
    with _lock:
        entries = _load()
        for e in entries:
            if e.get("id") == entry_id:
                e.update(kw)
                _save(entries)
                return


def get(entry_id):
    with _lock:
        return next((e for e in _load() if e.get("id") == entry_id), None)


def remove(entry_id):
    with _lock:
        entries = _load()
        gone = [e for e in entries if e.get("id") == entry_id]
        if gone:
            _save([e for e in entries if e.get("id") != entry_id])
    for e in gone:
        _remove_thumb(e)


def all_entries():
    """最新的在前；每条补上 exists（文件还在不在）。"""
    with _lock:
        entries = _load()
    out = []
    for e in reversed(entries):
        files = e.get("files") or []
        out.append(dict(e, exists=bool(files) and all(os.path.exists(p) for p in files)))
    return out


def known_files():
    with _lock:
        return {p for e in _load() for p in (e.get("files") or [])}


def thumb_path(entry_id):
    """封面文件路径（只接受 Manna 自己生成的编号，防止读到别的文件）。"""
    if not re.fullmatch(r"[0-9a-f]{8,32}", entry_id or ""):
        return None
    p = THUMB_DIR / f"{entry_id}.jpg"
    return p if p.exists() else None


def _remove_thumb(e):
    p = thumb_path(e.get("id"))
    if p:
        p.unlink(missing_ok=True)


def save_thumb(entry_id, cover_url, video_path, ffmpeg):
    """先下载网站封面；不行就用 ffmpeg 从视频第 1 秒截一帧。成功返回 True。"""
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    dst = THUMB_DIR / f"{entry_id}.jpg"
    if cover_url and re.match(r"https?://", cover_url):
        try:
            req = urllib.request.Request(cover_url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = r.read(5 << 20)
            if data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n" or data[8:12] == b"WEBP":
                if ffmpeg:
                    # 统一缩小成 jpg，记录多了也不占地方
                    raw = dst.with_suffix(".src")
                    raw.write_bytes(data)
                    ok = _ffmpeg_thumb(ffmpeg, ["-i", str(raw)], dst)
                    raw.unlink(missing_ok=True)
                    if ok:
                        return True
                else:
                    dst.write_bytes(data)
                    return True
        except Exception as e:
            log.info("下载封面失败：%s", e)
    if ffmpeg and video_path and os.path.exists(video_path):
        return _ffmpeg_thumb(ffmpeg, ["-ss", "1", "-i", str(video_path)], dst) or \
            _ffmpeg_thumb(ffmpeg, ["-i", str(video_path)], dst)
    return False


def _ffmpeg_thumb(ffmpeg, inputs, dst):
    try:
        r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *inputs, "-frames:v", "1",
                            "-vf", "scale=320:-2", "-q:v", "4", str(dst)],
                           capture_output=True, timeout=30, stdin=subprocess.DEVNULL)
        return r.returncode == 0 and dst.exists() and dst.stat().st_size > 0
    except Exception:
        return False


def now():
    return int(time.time())
