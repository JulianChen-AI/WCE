"""Regression: generic agent prompt, on-page notes, and the quit/restart controls."""
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import threading

SOURCE = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / 'scripts/multimodal'))
import launcher


def check_prompt_and_page():
    html = (SOURCE / 'scripts/multimodal/launcher.html').read_text(encoding='utf-8')
    start = html.index('async function copyConnectionPrompt')
    body = html[start:html.index('navigator.clipboard.writeText', start)]
    for leftover in ('Desktop/Today', 'WorkBuddy', '已知本机案例', '菜单栏退出', '关掉启动窗口'):
        assert leftover not in body, 'stale or author-specific detail in prompt: ' + leftover
    for needed in ('rt.app', 'rt.pid', 'rt.runtime_file', 'location.origin', "d.platform==='windows'",
                   '完全磁盘访问权限', 'Command+Shift+G', '「−」', '/api/restart', 'xwechat_files',
                   '不能自动获取', '不要重新安装、重新打包或替换'):
        assert needed in body, 'prompt is missing: ' + needed
    for needed in ('id="quit-app"', 'id="restart-app"', 'id="notes"', '升级或重装 WCE 后要重新授权', 'id="note-app-path"'):
        assert needed in html, 'page is missing: ' + needed
    assert '保持启动窗口运行' not in html, 'App has no launch window; footer must not say so'


def check_controls(home):
    os.environ['CHATKIT_USER_DIR'] = str(home / 'user')
    calls = []
    launcher.shutdown = lambda restart=False: calls.append(restart)
    server = launcher.ThreadingHTTPServer(('127.0.0.1', 0), launcher.Handler)
    launcher.BASE = 'http://127.0.0.1:' + str(server.server_port)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def post(path, origin=launcher.BASE):
        c = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
        headers = {'Content-Type': 'application/json'}
        if origin:
            headers['Origin'] = origin
        c.request('POST', path, body='{}', headers=headers)
        r = c.getresponse()
        status, data = r.status, r.read()
        c.close()
        return status, data

    try:
        assert post('/api/quit', origin=None)[0] == 403, 'quit must require same-origin'
        assert post('/api/restart', origin='http://evil.invalid')[0] == 403
        launcher.state['status'] = 'running'
        assert post('/api/quit')[0] == 409 and calls == [], 'must not quit during export'
        launcher.state['status'] = 'idle'
        status, data = post('/api/restart')
        assert status == 200 and json.loads(data)['restarting'] is True and calls == [True]
        launcher.state['status'] = 'idle'
        assert post('/api/quit')[0] == 200 and calls == [True, False]
        launcher.write_runtime_file()
        saved = json.loads((home / 'user/runtime.json').read_text(encoding='utf-8'))
        assert saved['base'] == launcher.BASE and saved['pid'] == os.getpid()
        info = launcher.runtime_info()
        assert {'mode', 'app', 'pid', 'runtime_file'} <= set(info)
        assert launcher.relaunch_command()[-1] == str(Path(launcher.__file__).resolve())
    finally:
        server.shutdown()


def test_connection_prompt():
    check_prompt_and_page()
    with tempfile.TemporaryDirectory() as temp:
        check_controls(Path(temp))
    print('PASS: generic prompt, on-page notes, guarded quit/restart, runtime.json')


if __name__ == '__main__':
    test_connection_prompt()
