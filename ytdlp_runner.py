"""在子进程里运行 yt-dlp。

打包后的 Manna 没有单独的 python 程序，所以让 Manna 自己充当解释器：
    Manna --manna-yt-dlp <yt-dlp 参数>
环境变量 MANNA_YTDLP_ZIP 指向一个 yt-dlp 单文件版（zip 包）时优先用它（应用内自动更新下载的就是它）。
"""
import os
import sys


def main(argv):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    z = os.environ.get("MANNA_YTDLP_ZIP")
    if z and os.path.isfile(z):
        sys.path.insert(0, z)
    import yt_dlp
    yt_dlp.main(argv)


if __name__ == "__main__":
    main(sys.argv[1:])
