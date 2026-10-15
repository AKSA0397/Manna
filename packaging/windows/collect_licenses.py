"""把打包进 Manna 的开源组件的许可证文本收集到一个文件夹（Windows 打包用，Mac 上由 build_mac.command 完成）。

用法：python collect_licenses.py <输出目录> <Python 版本> <Deno 版本> <ffmpeg 路径>
"""
import shutil
import subprocess
import sys
import sysconfig
import urllib.request
from pathlib import Path

SKIP = {"pip", "setuptools", "pyinstaller-hooks-contrib", "altgraph", "pefile", "pywin32-ctypes",
        "packaging", "pillow", "wheel"}


def main(out, py_ver, deno_ver, ffmpeg):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    site = Path(sysconfig.get_paths()["purelib"])
    for d in site.glob("*.dist-info"):
        meta = (d / "METADATA").read_text(encoding="utf-8", errors="replace").splitlines()
        field = lambda k: next((x.split(":", 1)[1].strip() for x in meta if x.startswith(k + ":")), "")
        name, ver = field("Name"), field("Version")
        if not name or name.lower() in SKIP:
            continue
        dst = out / f"{name}-{ver}"
        dst.mkdir(exist_ok=True)
        for f in d.rglob("*"):
            if f.is_file() and f.name.lower().startswith(("licen", "copying", "notice")):
                shutil.copy(f, dst / f.name)
        if not any(dst.iterdir()):
            lic = field("License-Expression") or field("License")
            (dst / "LICENSE.txt").write_text(f"License: {lic}\n", encoding="utf-8")
    py_lic = Path(sys.base_prefix) / "LICENSE.txt"
    if py_lic.is_file():
        shutil.copy(py_lic, out / f"Python-{py_ver}-LICENSE.txt")
    deno_lic = out / f"Deno-{deno_ver}-LICENSE.md"
    try:
        url = f"https://raw.githubusercontent.com/denoland/deno/v{deno_ver}/LICENSE.md"
        with urllib.request.urlopen(url, timeout=60) as r:
            deno_lic.write_bytes(r.read())
    except Exception:
        deno_lic.write_text(f"Deno 以 MIT 许可证发布：https://github.com/denoland/deno/blob/v{deno_ver}/LICENSE.md\n",
                            encoding="utf-8")
    ver = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True, errors="replace").stdout
    (out / "FFmpeg-NOTICE.txt").write_text(
        f"FFmpeg（随 imageio-ffmpeg 提供的可执行文件：{Path(ffmpeg).name}）\n\n"
        "该 FFmpeg 以 --enable-gpl 方式构建，整体按 GNU 通用公共许可证（GPL）第 2 版或更高版本授权，\n"
        "许可证全文见 https://www.gnu.org/licenses/old-licenses/gpl-2.0.html 。\n"
        "Manna 没有修改 FFmpeg，只是作为独立程序调用它。\n\n"
        "源代码获取：\n"
        "  FFmpeg 7.1 源代码：https://ffmpeg.org/releases/ffmpeg-7.1.tar.xz\n"
        "  该二进制的构建脚本与说明：https://github.com/imageio/imageio-ffmpeg\n\n"
        "构建配置：\n" + "\n".join(ver.splitlines()[:3]) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main(*sys.argv[1:5])
