"""引擎注册表：按优先级排列，第一个失败就换下一个，每次失败的原因写进日志。"""
import threading

from config import get_logger

from .base import EngineError

log = get_logger()


class AllEnginesFailed(EngineError):
    def __init__(self, first, attempts):
        super().__init__(str(first))
        self.first = first
        self.attempts = attempts      # [(引擎名, 错误文字), ...]


class Registry:
    def __init__(self):
        self._engines = []
        self._lock = threading.Lock()

    def register(self, engine):
        with self._lock:
            self._engines = sorted(self._engines + [engine], key=lambda e: -e.priority)
        log.info("注册引擎 %s（优先级 %s）", engine.name, engine.priority)

    @property
    def engines(self):
        return list(self._engines)

    def get(self, name):
        return next((e for e in self._engines if e.name == name), None)

    def candidates(self, url, only=None):
        """能尝试这个链接的引擎，按优先级排好；only 指定时把那个引擎排第一。"""
        c = [e for e in self._engines if e.match(url)]
        if only:
            c.sort(key=lambda e: e.name != only)
        return c

    def recognizes(self, url):
        return any(e.recognizes(url) for e in self._engines)

    def _try(self, action, url, call, only=None):
        engines = self.candidates(url, only)
        if not engines:
            raise EngineError(f"Unsupported URL: {url}")
        attempts = []
        for e in engines:
            try:
                result = call(e)
                if attempts:
                    log.info("%s %s：%s 成功（之前失败 %d 次）", action, url, e.name, len(attempts))
                return result, e.name
            except Exception as ex:
                reason = str(ex).strip().splitlines()[-1:] or [type(ex).__name__]
                log.warning("%s %s：引擎 %s 失败：%s", action, url, e.name, reason[0][:500])
                attempts.append((e.name, ex))
        first = attempts[0][1]
        if len(attempts) == 1:
            raise first
        raise AllEnginesFailed(first, [(n, str(x)) for n, x in attempts]) from first

    def inspect(self, url, ctx):
        """返回 (MediaInfo, 引擎名)。"""
        return self._try("解析", url, lambda e: e.inspect(url, ctx))

    def download(self, url, ctx, sel, progress, prefer=None):
        """返回 (DownloadResult, 引擎名)。prefer 是解析时成功的引擎，下载时先用它。"""
        return self._try("下载", url, lambda e: e.download(url, ctx, sel, progress), only=prefer)
