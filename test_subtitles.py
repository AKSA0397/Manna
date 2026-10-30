import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from subtitles import transcript, download_subtitles

class SubtitleTests(unittest.TestCase):
    def test_srt_and_markup(self):
        self.assertEqual(transcript('1\n00:00:00,000 --> 00:00:02,000\n<b>你好</b> &amp; 世界\n'), '你好 & 世界')

    def test_rolling_and_later_repetition(self):
        text = 'WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nHello\n\n00:00:02.000 --> 00:00:04.000\nHello\nworld\n\n00:00:06.000 --> 00:00:07.000\nHello\n'
        self.assertEqual(transcript(text), 'Hello\n\nworld\n\nHello')

    def test_disabled(self):
        self.assertEqual(download_subtitles('', {}, '', lambda _: None), ([], ''))

    def test_sidecars(self):
        with tempfile.TemporaryDirectory() as d:
            sub = Path(d) / 'video.en.vtt'
            sub.write_text('WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nHello\n')
            with patch('engines.ytdlp.YtDlpEngine._run_json', return_value={'title': 'Title', 'requested_subtitles': {'en': {'filepath': str(sub)}}}) as run:
                files, warning = download_subtitles('https://example.com', {'download_subtitles': True, 'subtitle_markdown': True}, str(Path(d)/'video.mp4'), lambda _: None)
                self.assertEqual(warning, '')
                self.assertEqual(len(files), 2)
                self.assertIn('Hello', Path(files[1]).read_text(encoding='utf-8'))
                self.assertIn('--skip-download', run.call_args[0][0])

    def test_missing_and_failure(self):
        req = {'download_subtitles': True}
        with patch('engines.ytdlp.YtDlpEngine._run_json', return_value={}):
            self.assertIn('未找到', download_subtitles('', req, 'video.mp4', lambda _: None)[1])
        with patch('engines.ytdlp.YtDlpEngine._run_json', side_effect=RuntimeError('network')):
            self.assertIn('视频已保留', download_subtitles('', req, 'video.mp4', lambda _: None)[1])

if __name__ == '__main__':
    unittest.main()
