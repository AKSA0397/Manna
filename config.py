"""Manna 的路径、配置文件和日志（各模块共用，不依赖 yt-dlp）。"""
import json
import logging
import logging.handlers
import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
if sys.platform == "darwin":
    SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "Manna"
else:
    SUPPORT_DIR = Path.home() / ".manna"
BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
CONFIG_FILE = SUPPORT_DIR / "config.json"
# 应用内「更新 yt-dlp」会把新版下载到这里，优先于打包进来的版本
YTDLP_ZIP = SUPPORT_DIR / "yt-dlp.zip"
LOG_FILE = SUPPORT_DIR / "manna.log"


def load_config():
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(**kw):
    cfg = load_config()
    cfg.update(kw)
    SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def get_logger():
    """写到 ~/Library/Application Support/Manna/manna.log（最多 1MB × 3 份）。"""
    log = logging.getLogger("manna")
    if not log.handlers:
        log.setLevel(logging.INFO)
        try:
            SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
            h = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=1 << 20, backupCount=3,
                                                     encoding="utf-8")
        except OSError:
            h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(h)
    return log
