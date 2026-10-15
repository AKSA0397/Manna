# 在 Windows 上打包 Manna：dist\Manna\Manna.exe（绿色版文件夹）+ dist\Manna-Setup-<版本>.exe（安装包）
# 用户使用时不需要装 Python / Deno / ffmpeg。只打 64 位（ARM 版 Windows 也能通过系统自带的转译运行）。
#
# 用法（PowerShell）：  powershell -ExecutionPolicy Bypass -File build_windows.ps1
# 需要：能访问 GitHub / PyPI；安装包需要 Inno Setup 6（没有时只出绿色版文件夹）。
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
Set-Location $PSScriptRoot
$ROOT = (Get-Location).Path

$PY_TAG = "20261013"
$PY_VER = "3.12.15"
$PY_SHA = "bbc902d4d187ac4914915bf538196a064b49109a33a104965b5164ced5b80a1e"
$DENO_VER = "2.9.7"
$DENO_SHA = "a0c3101b4158d1dfb7d6a78a7bf0f3de80c96bb423c152beec8beb22786f2238"
$VERSION = (Select-String -Path config.py -Pattern '^VERSION = "(.+)"').Matches[0].Groups[1].Value

$VENDOR = "$ROOT\vendor"
$B = "$ROOT\.build\win"
New-Item -ItemType Directory -Force $VENDOR | Out-Null
Remove-Item -Recurse -Force "$ROOT\dist", $B -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $B, "$ROOT\dist" | Out-Null

function Fetch($url, $out, $sha) {
  if (!(Test-Path $out) -or (Get-FileHash $out -Algorithm SHA256).Hash.ToLower() -ne $sha) {
    Write-Host "下载 $(Split-Path $out -Leaf) …"
    Invoke-WebRequest -Uri $url -OutFile "$out.part" -UseBasicParsing
    Move-Item -Force "$out.part" $out
  }
  if ((Get-FileHash $out -Algorithm SHA256).Hash.ToLower() -ne $sha) { throw "校验失败：$out" }
}

function Run { & $args[0] $args[1..($args.Length - 1)]; if ($LASTEXITCODE) { throw "命令失败（$LASTEXITCODE）：$args" } }

# 1. 独立版 Python（和 Mac 版同一个来源、同一个版本）
$PY_TGZ = "$VENDOR\cpython-$PY_VER+$PY_TAG-x86_64-pc-windows-msvc-install_only.tar.gz"
Fetch "https://github.com/astral-sh/python-build-standalone/releases/download/$PY_TAG/cpython-$PY_VER%2B$PY_TAG-x86_64-pc-windows-msvc-install_only.tar.gz" $PY_TGZ $PY_SHA
# 用系统自带的 tar（Git 带的 GNU tar 会把 C: 当成远程主机）
Run "$env:SystemRoot\System32\tar.exe" -xzf $PY_TGZ -C $B
$PY = "$B\python\python.exe"
Run $PY -m pip install -q -U pip
Run $PY -m pip install -q -U "yt-dlp[default]" imageio-ffmpeg pywebview pyinstaller pillow

# 2. 自带 Deno：YouTube 4K / 8K 需要 JavaScript 运行环境
$DENO_ZIP = "$VENDOR\deno-x86_64-pc-windows-msvc-$DENO_VER.zip"
Fetch "https://github.com/denoland/deno/releases/download/v$DENO_VER/deno-x86_64-pc-windows-msvc.zip" $DENO_ZIP $DENO_SHA
Expand-Archive -Force $DENO_ZIP "$B\deno"

# 3. 开源许可证文本、图标
$FFBIN = (& $PY -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())").Trim()
Run $PY "$ROOT\packaging\windows\collect_licenses.py" "$B\licenses" $PY_VER $DENO_VER $FFBIN
Run $PY -c "from PIL import Image; Image.open(r'$ROOT\icon.png').save(r'$B\icon.ico', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])"

# 4. 打包
Run "$B\python\Scripts\pyinstaller.exe" --noconfirm --clean --windowed `
  --distpath "$ROOT\dist" --workpath "$B\work" --specpath $B `
  --name Manna `
  --icon "$B\icon.ico" `
  --add-data "$ROOT\index.html;." `
  --add-data "$B\licenses;licenses" `
  --add-binary "$B\deno\deno.exe;deno" `
  --collect-all yt_dlp `
  --collect-all yt_dlp_ejs `
  --collect-all imageio_ffmpeg `
  --collect-all webview `
  --hidden-import clr `
  --hidden-import config `
  --hidden-import tools `
  --hidden-import shortlinks `
  --hidden-import ytdlp_runner `
  --hidden-import winsupport `
  --collect-submodules engines `
  --paths $ROOT `
  "$ROOT\manna_app.py"
$EXE = "$ROOT\dist\Manna\Manna.exe"

# 5. 检查：Manna.exe 充当 yt-dlp 子进程时能把输出传回来（窗口程序没有命令行窗口，这一步最容易出问题）
$v = (& $EXE --manna-yt-dlp --version | Out-String).Trim()
if ($LASTEXITCODE -or $v -notmatch '^\d{4}\.\d{2}\.\d{2}') { throw "Manna.exe --manna-yt-dlp --version 没有输出版本号：'$v'" }
Write-Host "yt-dlp（打包后）：$v"

# 6. 安装包（Inno Setup 6）
$ISCC = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($ISCC) {
  Run $ISCC /Q "/DAppVersion=$VERSION" "/DSourceDir=$ROOT\dist\Manna" "/DIconFile=$B\icon.ico" "/DOutDir=$ROOT\dist" `
    "$ROOT\packaging\windows\Manna.iss"
  Write-Host "完成：$ROOT\dist\Manna-Setup-$VERSION.exe"
} else {
  Write-Host "没有找到 Inno Setup 6，跳过安装包（绿色版在 dist\Manna）"
}
Compress-Archive -Force "$ROOT\dist\Manna" "$ROOT\dist\Manna-$VERSION-Windows-portable.zip"
Write-Host "完成：$ROOT\dist\Manna-$VERSION-Windows-portable.zip"
