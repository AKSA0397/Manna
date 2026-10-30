import io
import json
import unittest
from unittest.mock import patch
import app_updates as u

class UpdateTests(unittest.TestCase):
    def setUp(self):
        u._cached = None
        u._checked_at = 0

    def test_versions(self):
        self.assertGreater(u.version_tuple('v1.10.0'), u.version_tuple('1.9.0'))
        self.assertIsNone(u.version_tuple('v2.0.0-beta'))

    def test_assets(self):
        prefix = f'https://github.com/{u.REPOSITORY}/releases/download/v2.0.0/'
        names = ['Manna-2.0.0-Intel.dmg', 'Manna-2.0.0-AppleSilicon.dmg', 'Manna-Setup-2.0.0.exe']
        assets = [{'name': n, 'browser_download_url': prefix+n} for n in names]
        self.assertEqual(u.asset_for(assets, 'darwin', 'arm64'), prefix+names[1])
        self.assertEqual(u.asset_for(assets, 'darwin', 'x86_64'), prefix+names[0])
        self.assertEqual(u.asset_for(assets, 'win32', 'AMD64'), prefix+names[2])
        self.assertEqual(u.asset_for(assets, 'linux', 'x86_64'), u.RELEASES_URL)
        assets[1]['browser_download_url'] = 'https://example.com/untrusted'
        self.assertEqual(u.asset_for(assets, 'darwin', 'arm64'), u.RELEASES_URL)

    def test_new_release_and_cache(self):
        with patch.object(u, 'PRIVATE', False), patch.object(u.urllib.request, 'urlopen', return_value=io.BytesIO(json.dumps({'tag_name':'v99.0.0', 'body':'Notes', 'assets':[]}).encode())) as request:
            result = u.check_update()
            self.assertTrue(result['available'])
            self.assertEqual(result['notes'], 'Notes')
            self.assertEqual(u.check_update(), result)
            self.assertEqual(request.call_count, 1)

    def test_current_and_prerelease(self):
        for release, expected in [({'tag_name': 'v'+u.VERSION}, 'ok'), ({'tag_name':'v99.0.0', 'prerelease':True}, 'error')]:
            with patch.object(u, 'PRIVATE', False), patch.object(u.urllib.request, 'urlopen', return_value=io.BytesIO(json.dumps(release).encode())):
                result = u.check_update(force=True)
                self.assertFalse(result['available'])
                self.assertEqual(result['status'], expected)

    def test_private_browser(self):
        with patch.object(u, 'PRIVATE', True), patch.dict(u.os.environ, {'MANNA_UPDATE_TOKEN':''}), patch.object(u.urllib.request, 'urlopen') as request:
            self.assertEqual(u.check_update()['status'], 'login')
            request.assert_not_called()

    def test_failure(self):
        with patch.object(u, 'PRIVATE', False), patch.object(u.urllib.request, 'urlopen', side_effect=TimeoutError):
            self.assertEqual(u.check_update()['status'], 'error')

    def test_private_token_not_returned(self):
        with patch.object(u, 'PRIVATE', True), patch.dict(u.os.environ, {'MANNA_UPDATE_TOKEN':'secret'}), patch.object(u.urllib.request, 'urlopen', return_value=io.BytesIO(b'{"tag_name":"v99.0.0"}')) as request:
            result = u.check_update()
            self.assertNotIn('secret', json.dumps(result))
            self.assertEqual(request.call_args.args[0].get_header('Authorization'), 'Bearer secret')
            self.assertEqual(result['url'], u.RELEASES_URL)

    def test_open_uses_server_url(self):
        with patch.object(u.webbrowser, 'open', return_value=True) as browser:
            self.assertTrue(u.open_update()['ok'])
            browser.assert_called_once_with(u.RELEASES_URL)

if __name__ == '__main__':
    unittest.main()
