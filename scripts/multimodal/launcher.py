"""Lightweight local conversation picker; reuses the existing exporter."""
import json
import os
import subprocess
import sys
import threading
import webbrowser
import time
import tempfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

if '--export' in sys.argv and os.environ.get('CHATKIT_EXPORT_LOG'):
    sys.stdout = open(os.environ['CHATKIT_EXPORT_LOG'], 'a', encoding='utf-8', buffering=1)
    sys.stderr = sys.stdout

import run
from portable import (ROOT, user_dir, load_settings, load_keys, discover_accounts,
                      parse_keys, save_settings, account_id, TARGET_VERSION)

state = dict(status='idle', logs=[], results=[])
lock = threading.Lock()


def conversations():
    cfg = load_settings()
    db = Path(cfg['db_storage']) / 'contact/contact.db'
    try:
        with db.open('rb') as source:
            source.read(16)
    except PermissionError:
        raise RuntimeError('系统拒绝读取微信数据。Mac 请为实际启动本程序的 App 或终端开启完全磁盘访问权限；Windows 请检查目录访问权限。') from None
    keys = load_keys(cfg)
    last_error = ''
    for key in keys:
        try:
            conn = run.connect(db, key)
            try:
                if not {'username', 'nick_name', 'remark'} <= {r[1] for r in conn.execute('PRAGMA table_info(contact)')}:
                    raise RuntimeError('联系人表结构尚未适配，请反馈微信完整版本号；不会猜测读取')
                rows = conn.execute("SELECT username,nick_name,remark FROM contact ORDER BY nick_name,username").fetchall()
                own = account_id(Path(cfg['db_storage']).parent, cfg)
                return [dict(id=id, name=remark or nick or id, nickname=nick or '',
                             kind='group' if id.endswith('@chatroom') else 'contact')
                        for id, nick, remark in rows if id and id != own]
            finally:
                conn.close()
        except run.sqlcipher3.DatabaseError as exc:
            last_error = str(exc)
            continue
    if 'unable to open' in last_error.lower() or 'authorization' in last_error.lower():
        raise RuntimeError('无法打开微信数据库。桌面入口由终端启动，请检查终端的完全磁盘访问权限。错误：' + last_error)
    raise RuntimeError('微信数据库读取失败：' + last_error + '。可能是密钥失效或数据库暂不可用。')


def groups():
    return [item for item in conversations() if item['kind'] == 'group']


def contacts():
    return [item for item in conversations() if item['kind'] == 'contact']


def export(ids):
    output = None
    try:
        archives = Path(load_settings()['output_dir']).resolve()
        cmd = ([sys.executable, '--export'] if getattr(sys, 'frozen', False) else
               [sys.executable, '-u', str(Path(__file__)), '--export'])
        for id in ids:
            cmd += ['--contact', id]
        jobs = user_dir() / 'jobs'
        jobs.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.NamedTemporaryFile(dir=jobs, suffix='.log', delete=False) as log:
            log_path = log.name
        env = {**os.environ, 'CHATKIT_EXPORT_LOG': log_path, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'}
        with subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, env=env) as proc, open(log_path, encoding='utf-8') as stream:
            while True:
                line = stream.readline()
                if not line:
                    if proc.poll() is not None:
                        break
                    time.sleep(0.1)
                    continue
                line = line.rstrip()
                with lock:
                    state['logs'] = (state['logs'] + [line])[-100:]
                if line.startswith('输出目录：'):
                    output = Path(line.split('：', 1)[1])
            code = proc.wait()
        results = []
        if output and output.resolve().is_relative_to(archives):
            for page in output.glob('*/index.html'):
                results.append(dict(name=page.parent.name, url='/archive/' + quote(page.relative_to(archives).as_posix())))
        with lock:
            state.update(status='done' if code == 0 else 'failed', results=results)
        if code == 0:
            for result in results:
                webbrowser.open(BASE + result['url'])
    except Exception as exc:
        with lock:
            state.update(status='failed', logs=state['logs'] + [str(exc)])


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def valid_host(self):
        return self.headers.get('Host') == BASE.removeprefix('http://')

    def reply(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.valid_host():
            self.send_error(403)
            return
        path = urlsplit(self.path).path
        if path == '/api/groups':
            try:
                self.reply(dict(groups=groups()))
            except Exception as exc:
                self.reply(dict(error=str(exc)), 500)
        elif path == '/api/contacts':
            try:
                self.reply(dict(contacts=contacts()))
            except Exception as exc:
                self.reply(dict(error=str(exc)), 500)
        elif path == '/api/setup':
            try:
                settings = load_settings()
                config = {k: settings.get(k, '') for k in ('db_storage', 'own_id', 'output_dir', 'model_dir', 'transcribe', 'platform')}
                configured = True
            except RuntimeError:
                config, configured = {}, False
            self.reply(dict(configured=configured, config=config, accounts=discover_accounts(),
                            platform='windows' if sys.platform == 'win32' else 'macos', target_version=TARGET_VERSION))
        elif path == '/api/status':
            with lock:
                self.reply(state)
        else:
            if path == '/':
                file = Path(__file__).with_name('launcher.html')
            elif path.startswith('/archive/'):
                archives = Path(load_settings()['output_dir']).resolve()
                file = (archives / unquote(path[len('/archive/'):])).resolve()
                if not file.is_relative_to(archives):
                    self.send_error(403)
                    return
            else:
                self.send_error(404)
                return
            if not file.is_file():
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', self.guess_type(str(file)))
            self.send_header('Content-Length', str(file.stat().st_size))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            with file.open('rb') as source:
                import shutil
                shutil.copyfileobj(source, self.wfile)

    def do_POST(self):
        if not self.valid_host() or self.headers.get('Origin') != BASE or self.path not in ('/api/export', '/api/setup'):
            self.send_error(403)
            return
        try:
            length = int(self.headers.get('Content-Length', 0))
            if not 0 < length <= 65536:
                raise ValueError('请求过大或为空')
            body = json.loads(self.rfile.read(length))
            if self.path == '/api/setup':
                with lock:
                    if state['status'] == 'running':
                        raise ValueError('导出期间不能修改配置')
                config = body.get('config', {})
                dbroot = Path(config.get('db_storage', '')).expanduser().resolve()
                if not (dbroot / 'contact/contact.db').is_file():
                    raise ValueError('请选择包含 contact/contact.db 的 db_storage 目录')
                config = {k: config[k] for k in ('own_id', 'image_key', 'xor_key', 'transcribe', 'output_dir') if k in config}
                if type(config.get('transcribe', False)) is not bool:
                    raise ValueError('语音转写选项应为开或关')
                if config.get('own_id') and not isinstance(config['own_id'], str):
                    raise ValueError('微信账号 ID 格式不正确')
                try:
                    previous = load_settings()
                    if Path(previous['db_storage']).resolve() == dbroot and not config.get('image_key'):
                        for name in ('image_key', 'xor_key'):
                            if name in previous:
                                config[name] = previous[name]
                except RuntimeError:
                    pass
                if config.get('image_key'):
                    import re
                    if not re.fullmatch('[a-fA-F0-9]{32}', config['image_key']):
                        raise ValueError('图片密钥必须为 32 位十六进制字符串')
                if type(config.get('xor_key', 0)) is not int or not 0 <= config.get('xor_key', 0) <= 255:
                    raise ValueError('XOR 参数必须在 0 到 255 之间')
                config.update(db_storage=str(dbroot), platform='windows' if sys.platform == 'win32' else 'macos',
                              model_dir=str(user_dir() / 'models'), target_version=TARGET_VERSION)
                if not config.get('output_dir'):
                    config['output_dir'] = str(user_dir() / 'archives')
                else:
                    config['output_dir'] = str(Path(config['output_dir']).expanduser().resolve())
                keys = parse_keys(body['keys']) if body.get('keys') else load_keys(load_settings())
                validated = False
                for key in keys:
                    try:
                        c = run.connect(dbroot / 'contact/contact.db', key)
                        columns = {r[1] for r in c.execute('PRAGMA table_info(contact)')}
                        c.close()
                        if not {'username', 'nick_name', 'remark'} <= columns:
                            raise ValueError('密钥可用，但联系人表结构尚未适配')
                        validated = True
                        break
                    except run.sqlcipher3.DatabaseError:
                        continue
                if not validated:
                    raise ValueError('密钥无法解密所选账号的联系人数据库，未保存配置')
                save_settings(config, keys)
                self.reply(dict(ok=True))
                return
            ids = body.get('ids')
            if not isinstance(ids, list) or not ids or any(not isinstance(id, str) for id in ids):
                raise ValueError('请选择联系人或群聊')
            available = {g['id'] for g in conversations()}
            if any(id not in available for id in ids):
                raise ValueError('联系人或群聊列表已变化，请刷新列表')
            with lock:
                if state['status'] == 'running':
                    self.reply(dict(error='已有导出正在进行'), 409)
                    return
                state.update(status='running', logs=['开始导出本机现存记录…'], results=[])
            threading.Thread(target=export, args=(list(dict.fromkeys(ids)),), daemon=True).start()
            self.reply(dict(ok=True))
        except Exception as exc:
            self.reply(dict(error=str(exc)), 400)


if __name__ == '__main__':
    if '--export' in sys.argv:
        sys.argv.remove('--export')
        from export_full import main
        main()
        raise SystemExit(0)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    BASE = 'http://127.0.0.1:' + str(server.server_port)
    print('本地工作台：' + BASE, flush=True)
    print('保持此窗口运行；按 Control+C 停止。', flush=True)
    webbrowser.open(BASE)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
