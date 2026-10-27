"""Same synthetic input for baseline, modified and rollback; no private data."""
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


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def exercise(home):
    os.environ['CHATKIT_USER_DIR'] = str(home / 'user')
    own, alice, bob, group = 'wxid_owner', 'wxid_alice', 'wxid_bob', '123@chatroom'
    key = 'a' * 64  # Public, synthetic fixture only.
    dbroot = home / 'wxid_owner_abcd/db_storage'
    def db(relative):
        path = dbroot / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlcipher3.connect(str(path))
        conn.execute('PRAGMA key = "x\'' + key + '\'"')
        conn.execute('PRAGMA cipher_compatibility = 4')
        return conn
    c = db('contact/contact.db')
    c.execute('CREATE TABLE contact(username TEXT,nick_name TEXT,remark TEXT)')
    c.executemany('INSERT INTO contact VALUES(?,?,?)', [
        (own, '自己', ''), (group, '合成测试群', ''),
        (alice, '同名联系人', '测试备注'), (bob, '同名联系人', ''),
        ('wxid_collision', '重名歧义测试', alice), ('', '空账号', '')])
    c.commit(); c.close()
    c = db('message/message_0.db')
    c.execute('CREATE TABLE Name2Id(user_name TEXT)')
    c.executemany('INSERT INTO Name2Id VALUES(?)', [(own,), (alice,), (bob,)])
    for index, peer in enumerate((group, alice, bob)):
        table = 'Msg_' + hashlib.md5(peer.encode()).hexdigest()
        c.execute(f'CREATE TABLE {table}(local_id INTEGER,server_id INTEGER,local_type INTEGER,create_time INTEGER,real_sender_id INTEGER,message_content TEXT,WCDB_CT_message_content INTEGER,packed_info_data BLOB)')
        sender = 3 if peer == bob else 2
        for local_id, who, text in ((1, 1, '我的合成消息'), (2, sender, 'wxid_bob:\n保留私聊前缀')):
            c.execute(f'INSERT INTO {table} VALUES(?,?,1,?,?,?,0,NULL)', (local_id, index * 100 + local_id, 1792000000 + local_id, who, text))
    c.commit(); c.close()
    paths = list(dbroot.rglob('*.db'))
    original = {str(p): digest(p) for p in paths}
    portable.save_settings(dict(db_storage=str(dbroot), platform='windows', own_id=own,
                               output_dir=str(home / 'archives'), transcribe=False), [key])
    launcher.webbrowser.open = lambda *args: None
    server = launcher.ThreadingHTTPServer(('127.0.0.1', 0), launcher.Handler)
    launcher.BASE = 'http://127.0.0.1:' + str(server.server_port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    def request(path, data=None, origin=None):
        conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=15)
        headers = {}
        if data is not None:
            headers = {'Content-Type': 'application/json', 'Origin': origin or launcher.BASE}
        conn.request('GET' if data is None else 'POST', path, json.dumps(data) if data is not None else None, headers)
        response = conn.getresponse(); code, raw = response.status, response.read(); conn.close()
        try: body = json.loads(raw)
        except ValueError: body = {}
        return code, body
    def export(ids):
        code, body = request('/api/export', {'ids': ids})
        deadline = time.monotonic() + 60
        while code == 200 and launcher.state['status'] == 'running' and time.monotonic() < deadline:
            time.sleep(.05)
        return code, launcher.state['status'] if code == 200 else body.get('error')
    result = {}
    try:
        code, body = request('/api/groups')
        result['groups'] = {'http': code, 'ids': [g['id'] for g in body.get('groups', [])]}
        code, body = request('/api/contacts')
        result['contacts'] = {'http': code, 'ids': [g['id'] for g in body.get('contacts', [])]}
        result['unknown_id'] = request('/api/export', {'ids': ['not-in-contacts']})[0]
        result['foreign_origin'] = request('/api/export', {'ids': [group]}, 'https://invalid.example')[0]
        result['group_export'] = export([group])
        result['contact_export'] = export([alice, bob, alice])
        pages = sorted((home / 'archives').glob('*/*/chat.json'))
        result['archives'] = []
        for page in pages:
            data = json.loads(page.read_text())
            with ZipFile(page.parent / '完整聊天档案.zip') as archive:
                assert archive.testzip() is None
                assert json.loads(archive.read('chat.json')) == data
            texts = [m['text'] for m in data['messages']]
            roles = [m['role'] for m in data['messages']]
            assert len(texts) == 2 and roles == ['me', 'peer'], (texts, roles)
            if data['meta']['conversation_id'] != group:
                assert texts[1] == 'wxid_bob:\n保留私聊前缀'
            result['archives'].append({'id': data['meta']['conversation_id'], 'count': len(texts), 'roles': roles, 'zip': 'ok'})
        result['databases_unchanged'] = all(digest(p) == original[str(p)] for p in paths)
        assert result['databases_unchanged']
    finally:
        server.shutdown(); server.server_close()
    return result


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='synthetic-', dir=Path(__file__).parent) as home:
        observed = exercise(Path(home))
        assert observed['contact_export'] == (200, 'done'), observed
        assert observed['group_export'] == (200, 'done'), observed
        assert observed['contacts']['http'] == 200
        assert len(observed['contacts']['ids']) == 3
        assert len(observed['archives']) == 3
        assert observed['unknown_id'] == 400 and observed['foreign_origin'] == 403
        print(json.dumps(observed, ensure_ascii=False, sort_keys=True))
