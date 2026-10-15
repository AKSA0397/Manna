"""Windows 专用的系统功能：隐藏子进程的黑色命令行窗口、剪贴板、回收站、资源管理器、系统通知。

Mac 上不会调用这里的函数（app.py 里按 sys.platform 分开）。
"""
import os
import subprocess
import sys
from pathlib import Path

IS_WIN = sys.platform == "win32"


def hide_console_windows():
    """Manna 是窗口程序，没有命令行窗口；它启动 ffmpeg、deno 等命令行程序时 Windows 会各弹出一个黑框。
    给所有子进程默认加上 CREATE_NO_WINDOW（调用方自己指定了 creationflags 时不改）。"""
    if not IS_WIN or getattr(subprocess.Popen, "_manna_hidden", False):
        return
    orig = subprocess.Popen.__init__

    def __init__(self, *args, **kw):
        if not kw.get("creationflags"):
            kw["creationflags"] = subprocess.CREATE_NO_WINDOW
        orig(self, *args, **kw)

    subprocess.Popen.__init__ = __init__
    subprocess.Popen._manna_hidden = True


def fix_std_streams():
    """窗口程序（没有命令行窗口）里 sys.stdout / sys.stderr 可能是 None。
    Manna 以 --manna-yt-dlp 充当 yt-dlp 子进程时，父进程给了管道，这里把管道接回来，输出才能传回去。"""
    if not IS_WIN or (sys.stdout is not None and sys.stderr is not None):
        return
    import ctypes
    import msvcrt
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetStdHandle.restype = ctypes.c_void_p
    for name, std in (("stdout", -11), ("stderr", -12), ("stdin", -10)):
        if getattr(sys, name) is not None:
            continue
        h = k32.GetStdHandle(std)
        if not h or h == ctypes.c_void_p(-1).value:
            stream = open(os.devnull, "r" if name == "stdin" else "w", encoding="utf-8")
        else:
            fd = msvcrt.open_osfhandle(h, os.O_RDONLY if name == "stdin" else 0)
            stream = open(fd, "r" if name == "stdin" else "w", encoding="utf-8", errors="replace",
                          buffering=1 if name != "stdin" else -1, closefd=False)
        setattr(sys, name, stream)
        setattr(sys, "__%s__" % name, stream)


def clipboard_text():
    """读剪贴板里的文字（Windows API，不启动别的程序）。读不到返回空字符串。"""
    import ctypes
    from ctypes import wintypes
    CF_UNICODETEXT = 13
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    u32.OpenClipboard.argtypes = [wintypes.HWND]
    u32.GetClipboardData.restype = wintypes.HANDLE
    u32.GetClipboardData.argtypes = [wintypes.UINT]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    if not u32.IsClipboardFormatAvailable(CF_UNICODETEXT) or not u32.OpenClipboard(None):
        return ""
    try:
        h = u32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return ""
        p = k32.GlobalLock(h)
        if not p:
            return ""
        try:
            return ctypes.wstring_at(p)
        finally:
            k32.GlobalUnlock(h)
    finally:
        u32.CloseClipboard()


def move_to_trash(path):
    """移到回收站（可以从回收站恢复），不直接删除。"""
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                    ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                    ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 3, 0x4, 0x10, 0x40, 0x400
    op = SHFILEOPSTRUCTW(wFunc=FO_DELETE, pFrom=os.path.abspath(path) + "\0",
                         fFlags=FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI)
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if rc or op.fAnyOperationsAborted or os.path.exists(path):
        raise RuntimeError(f"没能把文件移到回收站（错误码 {rc}），请手动删除：{path}")


def reveal(path):
    """在资源管理器里打开文件所在的文件夹并选中它。"""
    subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])


def notify(title, text):
    """右下角的系统通知（用 PowerShell 调用 Windows 自带的托盘气泡，Windows 10 / 11 显示为通知）。"""
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$n=New-Object System.Windows.Forms.NotifyIcon;"
        "$n.Icon=[System.Drawing.Icon]::ExtractAssociatedIcon($env:MANNA_EXE);"
        "$n.Visible=$true;$n.ShowBalloonTip(5000,$env:MANNA_TITLE,$env:MANNA_TEXT,'None');"
        "Start-Sleep -Seconds 6;$n.Dispose()")
    env = dict(os.environ, MANNA_TITLE=title, MANNA_TEXT=text or " ", MANNA_EXE=sys.executable)
    subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
                      "-Command", script], env=env, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def installed_browsers():
    """本机装了（用过）的浏览器，按「自动」模式尝试的顺序。
    Firefox 排第一：Chrome / Edge 从 127 版起用「应用绑定加密」保存 Cookie，别的程序通常读不出来。"""
    appdata = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    places = (("firefox", appdata / "Mozilla" / "Firefox" / "Profiles"),
              ("edge", local / "Microsoft" / "Edge" / "User Data"),
              ("chrome", local / "Google" / "Chrome" / "User Data"))
    return [b for b, p in places if p.is_dir()]
