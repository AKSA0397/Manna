"""下载引擎。

新增一个引擎：在 engines/ 下写一个 Extractor 子类（match / inspect / download），
在下面 register() 一下，并设好 priority（越大越先试）。前一个引擎失败会自动换下一个。

目前有 yt-dlp（全部网站）和 douyin（抖音单个作品，读官方公开分享页，带水印）。
"""
from .base import DownloadResult, EngineError, Extractor, Format, MediaInfo, Selection
from .registry import AllEnginesFailed, Registry
from .douyin import DouyinEngine
from .ytdlp import YtDlpEngine, browser_cookie_header

registry = Registry()
_ytdlp = YtDlpEngine()
registry.register(_ytdlp)
registry.register(DouyinEngine())

__all__ = ["registry", "Extractor", "MediaInfo", "Format", "Selection", "DownloadResult",
           "EngineError", "AllEnginesFailed", "browser_cookie_header"]
