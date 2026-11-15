"""Incremental update of an existing archive, on synthetic data only."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from zipfile import ZipFile

SOURCE = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / 'scripts/multimodal'))
import sqlcipher3
import launcher
import portable

KEY = 'b' * 64  # Public, synthetic fixture only.
OWN, ALICE, GROUP = 'wxid_owner', 'wxid_alice', '456@chatroom'
TABLE = 'Msg_' + hashlib.md5(GROUP.encode()).hexdigest()
BASE_TIME = 1792000000


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def open_db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlcipher3.connect(str(path))
    conn.execute('PRAGMA key = "x\'' + KEY + '\'"')
    conn.execute('PRAGMA cipher_compatibility = 4')
    return conn


def add_messages(dbroot, rows):
    c = open_db(dbroot / 'message/message_0.db')
    c.executemany(f'INSERT INTO {TABLE} VALUES(?,?,?,?,?,?,0,NULL)', rows)
    c.commit(); c.close()


def exercise(home):
    os.environ['CHATKIT_USER_DIR'] = str(home / 'user')
    dbroot = home / 'wxid_owner_abcd/db_storage'
    c = open_db(dbroot / 'contact/contact.db')
    c.execute('CREATE TABLE contact(username TEXT,nick_name TEXT,remark TEXT)')
    c.executemany('INSERT INTO contact VALUES(?,?,?)', [(OWN, '自己', ''), (GROUP, '合成增量群', ''), (ALICE, '爱丽丝', '')])
    c.commit(); c.close()
    c = open_db(dbroot / 'message/message_0.db')
    c.execute('CREATE TABLE Name2Id(user_name TEXT)')
    c.executemany('INSERT INTO Name2Id VALUES(?)', [(OWN,), (ALICE,)])
    c.execute(f'CREATE TABLE {TABLE}(local_id INTEGER,server_id INTEGER,local_type INTEGER,create_time INTEGER,real_sender_id INTEGER,message_content TEXT,WCDB_CT_message_content INTEGER,packed_info_data BLOB)')
    c.commit(); c.close()
    add_messages(dbroot, [(1, 101, 1, BASE_TIME + 1, 1, '第一条'), (2, 102, 1, BASE_TIME + 3600, 2, 'wxid_alice:\n第二条')])
    portable.save_settings(dict(db_storage=str(dbroot), platform='macos', own_id=OWN,
                               output_dir=str(home / 'archives'), transcribe=False), [KEY])
    launcher.webbrowser.open = lambda *args: None
    server = launcher.ThreadingHTTPServer(('127.0.0.1', 0), launcher.Handler)
    launcher.BASE = 'http://127.0.0.1:' + str(server.server_port)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def request(path, data=None):
        conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=15)
        headers = {'Content-Type': 'application/json', 'Origin': launcher.BASE} if data is not None else {}
        conn.request('GET' if data is None else 'POST', path, json.dumps(data) if data is not None else None, headers)
        response = conn.getresponse(); code, raw = response.status, response.read(); conn.close()
        try: body = json.loads(raw)
        except ValueError: body = {}
        return code, body

    def run(payload):
        code, body = request('/api/export', payload)
        deadline = time.monotonic() + 60
        while code == 200 and launcher.state['status'] == 'running' and time.monotonic() < deadline:
            time.sleep(.05)
        assert code == 200 and launcher.state['status'] == 'done', (code, body, launcher.state['logs'][-8:])

    def snapshot(out):
        data = json.loads((out / 'chat.json').read_text(encoding='utf-8'))
        with ZipFile(out / '完整聊天档案.zip') as archive:
            assert archive.testzip() is None
            assert json.loads(archive.read('chat.json')) == data
        keys = [m['message_key'] for m in data['messages']]
        assert len(keys) == len(set(keys)), keys
        assert data['meta']['count'] == len(keys)
        text = (out / '聊天记录.txt').read_text(encoding='utf-8')
        return data, text

    originals = {}
    try:
        run({'ids': [GROUP]})
        code, body = request('/api/archives')
        assert code == 200 and len(body['archives']) == 1, body
        rel = body['archives'][0]['path']
        out = home / 'archives' / rel
        data, _ = snapshot(out)
        assert [m['text'] for m in data['messages']] == ['第一条', '第二条']

        # 1. Nothing new: same messages, one update record with added=0.
        run({'update_archive': rel})
        data, _ = snapshot(out)
        assert [m['text'] for m in data['messages']] == ['第一条', '第二条']
        assert [u['added'] for u in data['meta']['updates']] == [0]

        # 2. New messages, including one synced late with an earlier timestamp and a
        #    type-49 message whose body is not XML (used to crash the exporter).
        add_messages(dbroot, [(3, 103, 1, BASE_TIME + 7200, 1, '第三条'),
                              (4, 104, 1, BASE_TIME + 1800, 2, 'wxid_alice:\n晚到的一条'),
                              (5, 105, 49, BASE_TIME + 7300, 2, 'wxid_alice:\n不是 XML 的 49 类消息')])
        run({'update_archive': rel, 'transcribe': False})
        data, text = snapshot(out)
        texts = [m['text'] for m in data['messages']]
        assert texts[:4] == ['第一条', '晚到的一条', '第二条', '第三条'], texts
        assert len(texts) == 5, texts
        assert '第三条' in text and '晚到的一条' in text
        assert data['meta']['updates'][-1]['added'] == 3
        assert data['meta']['end'] == data['messages'][-1]['time']

        # 3. Running again adds nothing and duplicates nothing.
        for p in (dbroot).rglob('*.db'):
            originals[str(p)] = digest(p)
        run({'update_archive': rel})
        again, _ = snapshot(out)
        assert [m['message_key'] for m in again['messages']] == [m['message_key'] for m in data['messages']]
        assert again['meta']['updates'][-1]['added'] == 0
        assert all(digest(Path(p)) == h for p, h in originals.items()), '原始数据库被改动'

        # 4. Only archives inside the save location can be updated.
        assert request('/api/export', {'update_archive': '../../etc'})[0] == 400
        assert request('/api/export', {'update_archive': rel + '/media'})[0] == 400
        assert len(list((home / 'archives').iterdir())) == 1, '更新不应新建档案目录'
    finally:
        server.shutdown(); server.server_close()
    return dict(messages=len(again['messages']), updates=[u['added'] for u in again['meta']['updates']])


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='synthetic-', dir=Path(__file__).parent) as home:
        print(json.dumps(exercise(Path(home)), ensure_ascii=False))
