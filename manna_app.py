"""Manna 桌面应用入口：启动本地服务，用系统自带的网页引擎在独立窗口里显示界面。"""
import sys


def main():
    import webview

    import app
    url = app.start_server()
    webview.create_window("Manna", url, width=920, height=780, min_size=(640, 560))
    webview.start()


if __name__ == "__main__":
    # Manna 用子进程跑 yt-dlp 时会以这个参数调用自己（见 tools.py / ytdlp_runner.py）
    if len(sys.argv) > 1 and sys.argv[1] == "--manna-yt-dlp":
        import ytdlp_runner
        ytdlp_runner.main(sys.argv[2:])
    else:
        main()
