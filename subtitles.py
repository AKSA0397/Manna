"""Download subtitle sidecars and turn timed text into a faithful Markdown transcript."""
import html
import re
from pathlib import Path


def transcript(text):
    paragraphs = []
    previous = []
    previous_end = -1
    stamp = re.compile(r'(\d+):(\d{2}):(\d{2})[.,](\d{3})')
    def seconds(value):
        h, m, s, ms = map(int, stamp.search(value).groups())
        return h * 3600 + m * 60 + s + ms / 1000
    for block in re.split(r'\n\s*\n', text.replace('\r\n', '\n').lstrip('\ufeff')):
        lines = block.splitlines()
        if not lines or lines[0].startswith(('NOTE', 'STYLE', 'REGION', 'WEBVTT')):
            continue
        timing = next((i for i, line in enumerate(lines) if '-->' in line), None)
        if timing is None:
            continue
        start, end = lines[timing].split('-->', 1)
        start, end = seconds(start), seconds(end)
        clean = []
        for line in lines[timing + 1:]:
            line = html.unescape(re.sub(r'<[^>]*>', '', line)).strip()
            if line:
                clean.append(line)
        # Only remove repeated rolling-caption lines when cues touch or overlap.
        overlap = 0
        if start <= previous_end + .1:
            for n in range(1, min(len(previous), len(clean)) + 1):
                if previous[-n:] == clean[:n]:
                    overlap = n
        paragraphs.extend(clean[overlap:])
        previous, previous_end = clean, end
    return '\n\n'.join(re.sub(r'([\\`*_{}\[\]<>#|])', r'\\\1', p) for p in paragraphs)


def download_subtitles(url, req, media_path, progress):
    """Return sidecar paths and a non-fatal warning, never fail the media job."""
    if not req.get('download_subtitles'):
        return [], ''
    files = []
    try:
        from engines.ytdlp import YtDlpEngine, common_args
        progress({'status': 'processing', 'note': '下载字幕并整理逐字稿'})
        base = str(Path(media_path).with_suffix('')).replace('%', '%%')
        args = common_args(req) + [
            '--skip-download', '--write-subs', '--write-auto-subs',
            '--sub-langs', 'zh.*,en.*', '--sub-format', 'vtt/srt',
            '-o', base + '.%(ext)s', '--dump-single-json', '--no-simulate', '--', url]
        info = YtDlpEngine()._run_json(args)
        subtitles = info.get('requested_subtitles') or {}
        for lang, sub in subtitles.items():
            path = Path(sub.get('filepath') or '')
            if not path.is_file():
                continue
            files.append(str(path))
            if req.get('subtitle_markdown'):
                body = transcript(path.read_text(encoding='utf-8-sig'))
                if not body:
                    raise ValueError('字幕没有可转换的正文')
                md = path.with_suffix('.md')
                title = str(info.get('title') or Path(media_path).stem).replace('\n', ' ')
                md.write_text('# ' + html.escape(title) + '\n\n字幕语言：' + lang +
                              '\n\n来源：' + url + '\n\n' + body + '\n', encoding='utf-8')
                files.append(str(md))
        if not files:
            return [], '未找到可下载的中文或英文字幕（含自动字幕）。'
        return files, ''
    except Exception as exc:
        return files, '字幕处理未完成（视频已保留）：' + str(exc)
