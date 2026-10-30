"""Read GitHub Releases; installation is explicitly handled by the user."""
import json
import os
import platform
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

from config import VERSION

REPOSITORY = 'AKSA0397/Manna'
PRIVATE = False
RELEASES_URL = f'https://github.com/{REPOSITORY}/releases/latest'
_lock = threading.Lock()
_cached = None
_checked_at = 0


def version_tuple(value):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', value or '')
    return tuple(map(int, match.groups())) if match else None


def asset_for(assets, system=None, machine=None):
    system = system or sys.platform
    machine = (machine or platform.machine()).lower()
    for asset in assets:
        name = asset.get('name', '')
        if system == 'win32' and machine in ('amd64', 'x86_64'):
            match = name.startswith('Manna-Setup-') and name.endswith('.exe')
        elif system == 'darwin':
            label = 'AppleSilicon' if machine in ('arm64', 'aarch64') else 'Intel' if machine == 'x86_64' else None
            match = bool(label and name.endswith(f'-{label}.dmg'))
        else:
            match = False
        url = asset.get('browser_download_url', '')
        if match and url.startswith(f'https://github.com/{REPOSITORY}/releases/download/'):
            return url
    return RELEASES_URL


def check_update(force=False):
    global _cached, _checked_at
    with _lock:
        if not force and _cached and time.monotonic() - _checked_at < 3600:
            return dict(_cached)
        result = {'current': VERSION, 'available': False, 'url': RELEASES_URL}
        token = os.environ.get('MANNA_UPDATE_TOKEN', '') if PRIVATE else ''
        if PRIVATE and not token:
            result.update(status='login', note='私人版：请在浏览器登录 GitHub，查看最新版本并下载安装包。')
        else:
            try:
                headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'Manna-update-check'}
                if token:
                    headers['Authorization'] = 'Bearer ' + token
                request = urllib.request.Request(f'https://api.github.com/repos/{REPOSITORY}/releases/latest', headers=headers)
                with urllib.request.urlopen(request, timeout=15) as response:
                    release = json.load(response)
                latest = version_tuple(release.get('tag_name'))
                if not latest or release.get('draft') or release.get('prerelease'):
                    raise ValueError('正式版本号需要使用 v主版本.次版本.修订号')
                available = latest > version_tuple(VERSION)
                result.update(status='ok', available=available, latest=release['tag_name'],
                              note='发现新版本 ' + release['tag_name'] if available else '当前已是最新版本',
                              notes=str(release.get('body') or '')[:12000],
                              url=RELEASES_URL if PRIVATE else asset_for(release.get('assets') or []))
            except urllib.error.HTTPError as exc:
                result.update(status='error', note=('尚无正式发布版，或当前账号没有仓库访问权限。' if exc.code == 404 else
                                                    '检查更新失败，请稍后重试或在浏览器查看发布页。'))
            except Exception:
                result.update(status='error', note='无法检查更新，请检查网络后重试；也可在浏览器查看发布页。')
        _cached, _checked_at = result, time.monotonic()
        return dict(result)


def open_update():
    # The client cannot supply a URL; only a validated GitHub release URL is opened.
    with _lock:
        url = (_cached or {}).get('url', RELEASES_URL)
    return {'ok': bool(webbrowser.open(url))}
