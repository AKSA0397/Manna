# Manna

基于 [yt-dlp](https://github.com/yt-dlp/yt-dlp) 的 Mac 桌面视频下载器。粘贴链接 → 解析 → 选清晰度 → 下载，成片统一为 H.264 + AAC 的 mp4。

常用平台：YouTube、B站、TikTok、X(Twitter)、Instagram、Pinterest、小红书，以及 yt-dlp 支持的其他网站。
- 抖音：只读官方公开分享页（单个视频 / 图集），拿到的是公开播放的文件，画质较低、带抖音水印。
- 央视网：通过 yt-dlp 下载公开的 270p 流。

Manna 只使用网站公开提供的文件，以及你本人在浏览器里的登录状态（Cookie，只在本机使用）。不去水印、不破解加密或付费内容。
**仅用于下载你有权保存的内容，请遵守各平台的使用条款和版权规定。**

## 功能

- 解析后列出所有可用分辨率，带编码和估算大小；也可选「最高画质」或「仅音频（MP3）」
- 统一输出 H.264 + AAC mp4：只有 VP9 / AV1 等编码时，下载后用 FFmpeg 转码（优先 VideoToolbox 硬件编码）
- 下载完成后做完整性检查（ffprobe 读时长 + 解码前 5 秒），有问题标「可能花屏/不完整」
- 下载记录（封面、文件位置）
- 自动使用浏览器里的登录状态，也可以导入 cookies.txt
- yt-dlp 每天自动检查更新一次，也可以在设置里手动更新
- 展开 b23.tv / v.douyin.com / xhslink.com 短链接；整段分享文字直接粘贴即可

## 打包（Mac）

需要 Python 3.10+（只在打包时用到）。在终端运行：

```bash
./build_mac.command
```

几分钟后得到 `dist/<架构>/Manna.app` 和 .dmg。把 Manna.app 拖进「应用程序」即可使用。视频默认保存到「下载/Manna」。

## 代码结构

| 文件 | 作用 |
|---|---|
| `app.py` | 本地服务（127.0.0.1:8765）和网页接口、画质列表、登录信息自动选择、转 H.264、任务列表 |
| `index.html` | 界面 |
| `manna_app.py` | Mac 应用入口（pywebview 窗口） |
| `engines/base.py` | 引擎接口 `Extractor`：`match` / `inspect` / `download` |
| `engines/registry.py` | 引擎注册表：按优先级逐个尝试，失败自动换下一个 |
| `engines/ytdlp.py` | yt-dlp 引擎（全部网站），以子进程运行 yt-dlp |
| `engines/douyin.py` | 抖音公开分享页（单个视频、图集），失败时换 yt-dlp |
| `history.py` | 下载记录 |
| `shortlinks.py` | 短链接展开 |
| `tools.py` | 外部工具 yt-dlp / ffmpeg / Deno：查找、版本检查、缺失提示 |
| `ytdlp_runner.py` | 打包后让 Manna 自己运行 yt-dlp（`Manna --manna-yt-dlp …`） |
| `smoke_test.py` | 冒烟测试：各平台解析 + 下载最低画质 |

外部工具默认用 Manna 自带的，也可以在 `~/Library/Application Support/Manna/config.json` 里指定完整路径：

```json
{"tools": {"yt-dlp": "/opt/homebrew/bin/yt-dlp", "ffmpeg": "/opt/homebrew/bin/ffmpeg", "deno": "/opt/homebrew/bin/deno"}}
```

日志在同一目录的 `manna.log`。

## 需要登录的网站

| 网站 | 说明 |
|---|---|
| 小红书 | 建议登录 |
| Instagram | 多数内容需要登录 |
| B站 | 不登录最高 480p；1080P 需要登录，4K / 1080P60 需要大会员 |
| YouTube | 一般不需要；提示 "Sign in to confirm you're not a bot" 时再登录 |

Manna 会自动读取你在浏览器里的登录状态（Safari 需要在「完全磁盘访问权限」里打开 Manna）。也可以用浏览器扩展导出 cookies.txt，在设置 →「高级」里填入。

## 代理

YouTube、X、Instagram、TikTok、Pinterest 在国内需要代理，在设置里填写，例如 `http://127.0.0.1:7890`。

## 第三方软件与许可

打包时会把以下工具放进 Manna.app（不在本仓库里，由 `build_mac.command` 下载）：

| 软件 | 许可 | 源码 |
|---|---|---|
| yt-dlp（含 yt-dlp-ejs） | Unlicense（yt-dlp-ejs 另含 MIT / ISC 部分） | https://github.com/yt-dlp/yt-dlp |
| FFmpeg（经 imageio-ffmpeg 提供） | GPL v2 或更高版本 | https://ffmpeg.org/download.html ；未修改，原样调用其可执行文件 |
| Deno | MIT | https://github.com/denoland/deno |
| imageio-ffmpeg | BSD-2-Clause | https://github.com/imageio/imageio-ffmpeg |
| pywebview | BSD-3-Clause | https://github.com/r0x0r/pywebview |
| PyInstaller | GPL v2+ 及打包例外条款 | https://github.com/pyinstaller/pyinstaller |
| Python | PSF License | https://www.python.org |
| mutagen | GPL v2 或更高版本 | https://github.com/quodlibet/mutagen |

完整许可证文本随应用一起提供（设置 →「查看完整许可证文本」）。

## 许可

Manna 自身的代码以 [MIT 许可](LICENSE) 发布。
