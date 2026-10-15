"""Windows 上的功能检查（GitHub Actions 打包前运行，也可以在 Windows 电脑上手动运行）：
外部工具能找到、转 H.264 能走通、回收站 / 剪贴板 / 浏览器检测不报错。

用法：python packaging/windows/check.py      （需要先把 Deno 路径放进环境变量 MANNA_DENO）
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import app  # noqa: E402
import tools  # noqa: E402
import winsupport  # noqa: E402


def main():
    assert sys.platform == "win32", "只在 Windows 上运行"
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("应用数据目录：", app.SUPPORT_DIR)
    st = tools.check_all()
    for name in ("yt-dlp", "ffmpeg", "deno"):
        s = st[name]
        print(f"{name}: ok={s['ok']} {s['version']} {s['path']}")
        assert s["ok"], f"{name} 不可用：{s['message']}"

    tmp = Path(tempfile.mkdtemp(prefix="manna-check-"))
    src = tmp / "测试 视频.mkv"
    subprocess.run([app.FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25:duration=4",
                    "-f", "lavfi", "-i", "sine=duration=4", "-c:v", "mpeg4", "-c:a", "libmp3lame", str(src)], check=True)
    app.tasks["check"] = {"status": "downloading"}
    out = app.ensure_h264("check", str(src), 4)
    codecs = app.probe_codecs(out)
    print("转 H.264：", out, codecs, "跳过的硬件编码器：", sorted(app._hw_failed))
    assert codecs == ("h264", "aac") and out.endswith(".mp4"), codecs

    winsupport.move_to_trash(out)
    assert not os.path.exists(out)
    print("移到回收站：ok")
    print("剪贴板：", repr(winsupport.clipboard_text()[:40]))
    print("浏览器：", winsupport.installed_browsers(), app.auto_browsers())
    print("检查全部通过")


if __name__ == "__main__":
    main()
