"""下载引擎的统一接口。

每个引擎实现三件事：
    match(url)                         这个链接我能不能试
    inspect(url, ctx) -> MediaInfo     解析：标题、时长、封面、作者、所有可选格式
    download(url, ctx, sel, progress)  按选择下载，返回最终文件路径
ctx 是界面发来的请求（登录浏览器、cookies.txt、代理等），sel 是用户选的画质。
"""
from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class Format:
    format_id: str
    ext: str = ""
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None
    vcodec: Optional[str] = None   # "none" = 没有画面；None = 未知
    acodec: Optional[str] = None   # "none" = 没有声音；None = 未知
    bitrate: Optional[float] = None    # 总码率 kbps
    vbitrate: Optional[float] = None   # 画面码率 kbps（网站给了才有）
    abitrate: Optional[float] = None   # 声音码率 kbps（网站给了才有）
    est_size: Optional[int] = None     # 字节，精确或估算
    split: bool = False                # 只有画面或只有声音，需要和另一路合并

    @property
    def resolution(self):
        return f"{self.width}x{self.height}" if self.width and self.height else (
            f"{self.height}p" if self.height else "")

    @property
    def has_video(self):
        return self.vcodec != "none"


@dataclass
class MediaInfo:
    title: str
    duration: Optional[float] = None
    cover: Optional[str] = None
    author: Optional[str] = None
    formats: list = field(default_factory=list)
    id: str = ""
    webpage_url: str = ""
    extractor: str = ""          # 引擎内部的站点解析器名，如 "BiliBili"
    engine: str = ""             # 由哪个引擎解析的
    # 引擎自己给出的画质档位 [{"value": 短边, "kbps": 码率, "size": 字节}]（下载前拿不到格式列表时用）
    qualities: list = field(default_factory=list)
    # 栏目页的节目列表（最新的在前）：[{"index", "guid", "title", "time", "duration", "thumbnail"}]
    entries: list = field(default_factory=list)
    images: int = 0              # 图集的图片张数（0 = 视频，-1 = 图集但张数未知）
    batch: str = ""              # 批量页面：user（抖音主页）/ collection（合集），下载全部作品


@dataclass
class Selection:
    quality: str = "best"        # best / audio / 数字（短边像素）/ f:<格式编号>
    portrait: bool = False
    output_dir: str = ""
    name_tag: str = ""           # 文件名末尾的附加标记，如 " audio"


@dataclass
class DownloadResult:
    path: str
    duration: Optional[float] = None
    files: list = field(default_factory=list)   # 一次下了多个文件时（图集）的全部路径
    kind: str = "video"                         # video / images / batch（主页、合集）
    note: str = ""                              # 批量下载的结果说明，如「新下载 3 个作品」
    author: str = ""


# progress 回调收到的字典：status（downloading / finished）、downloaded_bytes、
# total_bytes、total_bytes_estimate、speed、eta，字段含义和 yt-dlp 的进度钩子一致；
# 只知道百分比的引擎给 percent（0–100）；处理阶段（status=processing）可带 note 说明在做什么
Progress = Callable[[dict], None]


class EngineError(RuntimeError):
    """引擎报的错，文字直接给用户看（会经过 app.friendly_error 加提示）。"""


class Extractor:
    name = "base"
    priority = 0          # 数字越大越先试

    def match(self, url) -> bool:
        """这个引擎能不能尝试这个链接（宽松，可兜底）。"""
        raise NotImplementedError

    def recognizes(self, url) -> bool:
        """是否明确认识这个网站（用来判断剪贴板里的链接值不值得自动填入）。"""
        return self.match(url)

    def inspect(self, url, ctx) -> MediaInfo:
        raise NotImplementedError

    def download(self, url, ctx, sel: Selection, progress: Progress) -> DownloadResult:
        raise NotImplementedError

    def version(self) -> str:
        return ""
