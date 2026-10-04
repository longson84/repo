import importlib.util
import pathlib
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('repo_companion', pathlib.Path(__file__).parents[1] / 'companion' / 'server.py')
module = importlib.util.module_from_spec(spec) if spec else None
if module and pathlib.Path(spec.origin).exists():
    spec.loader.exec_module(module)
else:
    module = None

class CompanionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
    def api(self):
        self.assertIsNotNone(module, 'Local companion is not implemented')
        return module
    def git(self, path, *args):
        return subprocess.check_output(['git', '-C', str(path), *args], stderr=subprocess.DEVNULL).decode().strip()
    def repo(self, name):
        path = self.root / name
        path.mkdir()
        self.git(path, 'init', '-b', 'main')
        self.git(path, 'config', 'user.name', 'Test')
        self.git(path, 'config', 'user.email', 'test@example.com')
        (path/'file.txt').write_text('initial')
        self.git(path, 'add', '.')
        self.git(path, 'commit', '-m', 'initial')
        return path
    def test_discovery_and_selection_are_persistent(self):
        api = self.api()
        repo = self.repo('chosen')
        self.repo('excluded')
        state = api.State([self.root], self.root/'settings.json')
        self.assertEqual(len(state.discover(str(self.root))), 2)
        state.select([str(repo)])
        loaded = api.State([self.root], self.root/'settings.json')
        self.assertEqual(loaded.paths, [str(repo.resolve())])
    def test_paths_cannot_escape_approved_roots(self):
        api = self.api()
        with tempfile.TemporaryDirectory() as outside:
            state = api.State([self.root], self.root/'settings.json')
            with self.assertRaises(ValueError):
                state.select([outside])
            (self.root/'escape').symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                state.discover(str(self.root/'escape'))
    def test_real_git_counts_and_changed_files(self):
        api = self.api()
        repo = self.repo('working')
        remote = self.root/'remote.git'
        subprocess.run(['git', 'init', '--bare', str(remote)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.git(repo, 'remote', 'add', 'origin', str(remote))
        self.git(repo, 'push', '-u', 'origin', 'main')
        (repo/'file.txt').write_text('second')
        self.git(repo, 'commit', '-am', 'second')
        (repo/'file.txt').write_text('dirty')
        (repo/'new.txt').write_text('untracked')
        result = api.inspect_repo(str(repo), True)
        self.assertEqual((result['ahead'], result['behind']), (1, 0))
        self.assertEqual((result['modified'], result['untracked']), (1, 1))
        self.assertEqual(result['upstream'], 'origin/main')
        self.assertTrue(result['fresh'])
    def test_fetch_failure_does_not_claim_current_counts(self):
        api = self.api()
        repo = self.repo('offline')
        self.git(repo, 'remote', 'add', 'origin', str(self.root/'missing.git'))
        self.git(repo, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
        self.git(repo, 'branch', '--set-upstream-to=origin/main')
        result = api.inspect_repo(str(repo), True)
        self.assertFalse(result['fresh'])
        self.assertIsNone(result['ahead'])
        self.assertTrue(result['error'])
    def test_request_auth_rejects_other_origins_hosts_and_tokens(self):
        api = self.api()
        self.assertTrue(api.authorized('127.0.0.1:48731', 'https://repo.theana.online', 'Bearer secret', 'secret', 48731))
        for host, origin, token in [('evil.example:48731','https://repo.theana.online','Bearer secret'),('127.0.0.1:48731','https://evil.example','Bearer secret'),('127.0.0.1:48731','https://repo.theana.online','Bearer wrong')]:
            self.assertFalse(api.authorized(host, origin, token, 'secret', 48731))

if __name__ == '__main__': unittest.main()

class HttpBoundaryTests(unittest.TestCase):
    def test_http_requires_token_and_allowed_origin(self):
        import http.client
        import threading
        with tempfile.TemporaryDirectory() as folder:
            server = module.ThreadingHTTPServer(('127.0.0.1', 0), module.Handler)
            server.state = module.State([folder], pathlib.Path(folder)/'settings.json')
            server.token = 'test-token'
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                for origin, authorization, expected in [('https://evil.example','Bearer test-token',403),('https://repo.theana.online','Bearer wrong',403),('https://repo.theana.online','Bearer test-token',200)]:
                    connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
                    connection.request('GET', '/api/status', headers={'Origin':origin,'Authorization':authorization})
                    response = connection.getresponse()
                    self.assertEqual(response.status, expected)
                    response.read();connection.close()
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
                connection.request('OPTIONS','/api/scan',headers={'Origin':'https://repo.theana.online'})
                response = connection.getresponse()
                self.assertEqual(response.status,204)
                self.assertEqual(response.getheader('Access-Control-Allow-Origin'),'https://repo.theana.online')
                self.assertEqual(response.getheader('Access-Control-Allow-Private-Network'),'true')
                response.read();connection.close()
            finally:
                server.shutdown();server.server_close();thread.join()
