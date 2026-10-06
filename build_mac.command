#!/bin/bash
# 在 Mac 上打包 Manna.app 和安装用的 .dmg（用户使用时不需要装 Python / Node / Deno）
#
# 默认同时打两个版本：
#   dist/arm64/Manna.app   + dist/Manna-AppleSilicon.dmg  （M1/M2/M3/M4 等 Apple 芯片的 Mac）
#   dist/x86_64/Manna.app  + dist/Manna-Intel.dmg         （Intel 芯片的 Mac，需要本机装了 Rosetta 才能在 M 芯片上打包）
# 只打一个：ARCHES=arm64 ./build_mac.command
#
# 用独立版 Python（python-build-standalone）打包，而不是 Homebrew 的 Python：
# Homebrew 的 Python 只能在和本机同样新的 macOS 上运行，独立版能在较老的 macOS 上运行。
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"

ARCHES="${ARCHES:-arm64 x86_64}"

PY_TAG="20261003"
PY_VER="3.12.15"
PY_SHA_arm64="316a463172740e71d8dca1f2730784e325f3f720941137b5d674d5801a632213"
PY_SHA_x86_64="a8fd7a91852f19b6d959793ef41fad048631ccb2a334a9ecdf573255298f7978"
DENO_VER="2.9.7"
DENO_SHA_arm64="5cd46d6268f6f78f5d88bdc7159d20bd44cdaa4b3303474839f87ec6fe7ae25c"
DENO_SHA_x86_64="95daaff11c116a52ad54785e7914c8e9c9cdcaba793c5ed929c74ca2d8e6259a"

VENDOR="$ROOT/vendor"
mkdir -p "$VENDOR"

fetch() {  # fetch <网址> <保存路径> <sha256>
  local url="$1" out="$2" sha="$3"
  if [ ! -f "$out" ] || [ "$(shasum -a 256 "$out" | cut -d' ' -f1)" != "$sha" ]; then
    echo "下载 $(basename "$out") …"
    curl -fL --retry 3 -o "$out.part" "$url"
    mv "$out.part" "$out"
  fi
  if [ "$(shasum -a 256 "$out" | cut -d' ' -f1)" != "$sha" ]; then
    echo "校验失败：$out" >&2; exit 1
  fi
}

SIGN_ID="Manna Local Signing"

rm -rf build dist .build
mkdir -p dist

for ARCH in $ARCHES; do
  case "$ARCH" in
    arm64)  TRIPLE="aarch64-apple-darwin"; LABEL="AppleSilicon" ;;
    x86_64) TRIPLE="x86_64-apple-darwin";  LABEL="Intel" ;;
    *) echo "不认识的架构：$ARCH" >&2; exit 1 ;;
  esac
  echo "==== 打包 $ARCH ===="
  RUN="arch -$ARCH"
  B="$ROOT/.build/$ARCH"
  mkdir -p "$B"

  # 1. 独立版 Python
  PY_TGZ="$VENDOR/cpython-$PY_VER+$PY_TAG-$TRIPLE-install_only.tar.gz"
  eval "fetch https://github.com/astral-sh/python-build-standalone/releases/download/$PY_TAG/cpython-$PY_VER%2B$PY_TAG-$TRIPLE-install_only.tar.gz \"$PY_TGZ\" \$PY_SHA_$ARCH"
  tar -xzf "$PY_TGZ" -C "$B"
  PY="$B/python/bin/python3"
  PIP="$RUN $PY -m pip"
  $PIP install -q -U pip
  $PIP install -q -U "yt-dlp[default]" imageio-ffmpeg pywebview pyinstaller pillow \
    || $PIP install -q -U "yt-dlp[default]" imageio-ffmpeg pywebview pyinstaller pillow -i https://pypi.tuna.tsinghua.edu.cn/simple

  # 2. 自带 Deno：YouTube 4K / 8K 需要 JavaScript 运行环境
  DENO_ZIP="$VENDOR/deno-$TRIPLE-$DENO_VER.zip"
  eval "fetch https://github.com/denoland/deno/releases/download/v$DENO_VER/deno-$TRIPLE.zip \"$DENO_ZIP\" \$DENO_SHA_$ARCH"
  unzip -o -q "$DENO_ZIP" -d "$B/deno"

  # 3. 开源许可证文本
  LIC="$B/licenses"
  mkdir -p "$LIC"
  SP="$($RUN $PY -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
  for d in "$SP"/*.dist-info; do
    name="$(grep -m1 '^Name:' "$d/METADATA" | cut -d' ' -f2)"
    case "$name" in pip|setuptools|pyinstaller-hooks-contrib|altgraph|macholib|packaging|pillow) continue ;; esac
    ver="$(grep -m1 '^Version:' "$d/METADATA" | cut -d' ' -f2)"
    mkdir -p "$LIC/$name-$ver"
    find "$d" -maxdepth 2 -type f \( -iname 'licen[cs]e*' -o -iname 'copying*' -o -iname 'notice*' \) -exec cp {} "$LIC/$name-$ver/" \;
    [ -z "$(ls -A "$LIC/$name-$ver")" ] && grep -m1 -E '^License(-Expression)?:' "$d/METADATA" > "$LIC/$name-$ver/LICENSE.txt"
  done
  # PyInstaller 只用来打包，许可证仍附上（打包例外条款允许随应用分发）
  cp "$B/python/lib/python3.12/LICENSE.txt" "$LIC/Python-$PY_VER-LICENSE.txt"
  curl -fsSL -o "$LIC/Deno-$DENO_VER-LICENSE.md" "https://raw.githubusercontent.com/denoland/deno/v$DENO_VER/LICENSE.md" \
    || echo "Deno 以 MIT 许可证发布：https://github.com/denoland/deno/blob/v$DENO_VER/LICENSE.md" > "$LIC/Deno-$DENO_VER-LICENSE.md"
  FFBIN="$($RUN $PY -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')"
  {
    echo "FFmpeg（随 imageio-ffmpeg 提供的可执行文件：$(basename "$FFBIN")）"
    echo
    echo "该 FFmpeg 以 --enable-gpl 方式构建，整体按 GNU 通用公共许可证（GPL）第 2 版或更高版本授权，"
    echo "许可证全文见 mutagen 目录中的 COPYING（GPL v2 文本）或 https://www.gnu.org/licenses/old-licenses/gpl-2.0.html 。"
    echo "Manna 没有修改 FFmpeg，只是作为独立程序调用它。"
    echo
    echo "源代码获取："
    echo "  FFmpeg 7.1 源代码：https://ffmpeg.org/releases/ffmpeg-7.1.tar.xz"
    echo "  该二进制的构建脚本与说明：https://github.com/imageio/imageio-ffmpeg"
    echo
    echo "构建配置："
    "$FFBIN" -version 2>&1 | head -3
  } > "$LIC/FFmpeg-NOTICE.txt"

  # 4. 打包
  $RUN "$B/python/bin/pyinstaller" --noconfirm --clean --windowed \
    --distpath "$ROOT/dist/$ARCH" --workpath "$B/work" --specpath "$B" \
    --target-arch "$ARCH" \
    --name Manna \
    --icon "$ROOT/icon.png" \
    --osx-bundle-identifier com.manna.downloader \
    --add-data "$ROOT/index.html:." \
    --add-data "$LIC:licenses" \
    --add-binary "$B/deno/deno:deno" \
    --collect-all yt_dlp \
    --collect-all yt_dlp_ejs \
    --collect-all imageio_ffmpeg \
    --collect-all webview \
    --hidden-import config \
    --hidden-import tools \
    --hidden-import shortlinks \
    --hidden-import ytdlp_runner \
    --collect-submodules engines \
    --paths "$ROOT" \
    "$ROOT/manna_app.py"
  APP="dist/$ARCH/Manna.app"

  # 5. 用钥匙串里的固定证书「Manna Local Signing」签名，重新打包后「完全磁盘访问权限」不会失效；
  #    没有这个证书就用临时签名（避免「已损坏」提示，但每次重新打包都要重新授权）
  if security find-certificate -c "$SIGN_ID" >/dev/null 2>&1; then
    codesign --force --deep --sign "$SIGN_ID" "$APP"
  else
    echo "没有找到证书「$SIGN_ID」，改用临时签名"
    codesign --force --deep --sign - "$APP"
  fi
  codesign --verify --deep --strict "$APP"

  # 6. 拖拽安装的 .dmg：左边 Manna，右边「应用程序」
  DMG="dist/Manna-$LABEL.dmg"
  STAGE="$B/dmg"
  rm -rf "$STAGE"; mkdir -p "$STAGE"
  ditto "$APP" "$STAGE/Manna.app"
  ln -s /Applications "$STAGE/Applications"
  RW="$B/rw.dmg"
  hdiutil create -quiet -volname "Manna" -srcfolder "$STAGE" -fs HFS+ -format UDRW -ov "$RW"
  MNT="$(hdiutil attach -readwrite -noverify -noautoopen "$RW" | awk -F'\t' '/\/Volumes\//{print $NF}')"
  # 整理窗口布局（需要允许「终端」控制「访达」；不允许也不影响安装，只是图标位置是默认的）
  osascript >/dev/null 2>&1 <<EOF || echo "（跳过窗口布局）"
tell application "Finder"
  tell disk (do shell script "basename " & quoted form of "$MNT")
    open
    set current view of container window to icon view
    set toolbar visible of container window to false
    set statusbar visible of container window to false
    set the bounds of container window to {200, 120, 760, 460}
    set opts to the icon view options of container window
    set arrangement of opts to not arranged
    set icon size of opts to 112
    set text size of opts to 13
    set position of item "Manna.app" of container window to {140, 150}
    set position of item "Applications" of container window to {420, 150}
    update without registering applications
    delay 1
    close
  end tell
end tell
EOF
  sync
  hdiutil detach -quiet "$MNT" || hdiutil detach -force -quiet "$MNT"
  hdiutil convert -quiet "$RW" -format UDZO -imagekey zlib-level=9 -ov -o "$DMG"
  rm -f "$RW"
  codesign --force --sign "$SIGN_ID" "$DMG" 2>/dev/null || true
  echo "完成：$ROOT/$APP"
  echo "完成：$ROOT/$DMG"
done

[ -n "${NO_OPEN:-}" ] || open dist
