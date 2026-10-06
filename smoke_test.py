"""冒烟测试：每个目标平台解析一条样例链接，能下载的再下载最低一档画质。

用法（开发环境）：
    MANNA_DENO=.build/arm64/deno/deno .build/arm64/python/bin/python3 smoke_test.py [--no-download] [--cookies=auto] [平台名 ...]
下载的文件放在临时目录，测试完删除。
"""
import sys
import tempfile
import time
from pathlib import Path

import app

SAMPLES = [
    ("YouTube", "https://www.youtube.com/watch?v=jNQXAC9IVRw"),
    ("B站", "https://www.bilibili.com/video/BV1GJ411x7h7"),
    ("B站短链", "https://b23.tv/BV1GJ411x7h7"),
    ("小红书", "https://www.xiaohongshu.com/discovery/item/674051740000000007027a15"
              "?xsec_token=CBgeL8Dxd1ZWBhwqRd568gAZ_iwG-9JIf9tnApNmteU2E="),
    ("TikTok", "https://www.tiktok.com/@patroxofficial/video/6742501081818877190"),
    ("X", "https://twitter.com/LisPower1/status/1001551623938805763"),
    ("Instagram", "https://www.instagram.com/reel/Chunk8-jurw/"),
    ("Pinterest", "https://www.pinterest.com/pin/664281013778109217/"),
    ("央视网", "http://tv.cctv.com/2016/02/05/VIDEUS7apq3lKrHG9Dncm03B160205.shtml"),
    ("抖音", "https://www.douyin.com/video/6961737553342991651"),
    ("新片场", "https://www.xinpianchang.com/a11766551"),
]


def run(name, url, download=True, cookies=""):
    req = {"url": url, "cookies_browser": cookies, "cookies_file": "", "proxy": ""}
    t = time.time()
    try:
        info = app.get_info(req)
    except Exception as e:
        return {"平台": name, "解析": "失败", "原因": app.friendly_error(str(e)).splitlines()[0][:160],
                "秒": round(time.time() - t)}
    row = {"平台": name, "解析": "通过", "标题": (info["title"] or "")[:30], "引擎": info.get("engine"),
           "画质": " / ".join(q["label"].split("（")[0] for q in info["qualities"]) or "（无列表，下最佳）",
           "登录": info.get("cookies_used") or "-"}
    if download:
        qs = [q["value"] for q in info["qualities"]]
        quality = qs[-1] if qs else "best"
        tid = "smoke"
        app.tasks[tid] = {"id": tid, "title": info["title"], "status": "queued", "percent": 0}
        dreq = dict(req, url=info["webpage_url"], quality=quality, portrait=info["portrait"],
                    cookies_browser=info.get("cookies_used") or "", title=info["title"],
                    duration=info.get("duration"))
        app.run_download(tid, dreq)
        task = app.tasks.pop(tid)
        if task["status"] == "done" and task.get("summary"):
            row["下载"] = f"通过（{task['summary']}，{len(task.get('files') or [])} 个文件）"
            for f in task.get("files") or []:
                Path(f).unlink(missing_ok=True)
        elif task["status"] == "done":
            p = Path(task["file"])
            row["下载"] = f"通过（{quality}，{p.suffix}，{app.human_size(p.stat().st_size)}，" \
                        f"{'/'.join(app.probe_codecs(p))}，{'×'.join(map(str, app.video_size(p) or ()))}）"
            row["完整性"] = task.get("warning") or "通过"
            p.unlink()
            h = app.history.all_entries()
            row["记录"] = "已写入" if h and h[0]["id"] == tid else "没写入"
        else:
            row["下载"] = "失败：" + task.get("error", "").splitlines()[0][:160]
    row["秒"] = round(time.time() - t)
    return row


def main():
    download = "--no-download" not in sys.argv
    # 默认不带登录信息；--cookies=auto 和应用里「自动」一样（可能弹出钥匙串授权）
    cookies = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--cookies=")), "")
    only = [a for a in sys.argv[1:] if not a.startswith("--")]
    app.DOWNLOAD_DIR = Path(tempfile.mkdtemp(prefix="manna-smoke-"))
    # 测试下载的文件会删掉，不写进真正的下载记录
    app.history.HISTORY_FILE = app.DOWNLOAD_DIR / "history.json"
    app.history.THUMB_DIR = app.DOWNLOAD_DIR / "thumbs"
    app.tools.check_all()
    for name, url in SAMPLES:
        if only and name not in only:
            continue
        row = run(name, url, download, cookies)
        print(" | ".join(f"{k}: {v}" for k, v in row.items()), flush=True)


if __name__ == "__main__":
    main()
