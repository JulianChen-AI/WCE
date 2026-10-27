#!/usr/bin/env python3
"""Local-only MVP. Reuse captured keys; never launch or modify WeChat."""
from __future__ import annotations
import argparse
import collections
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import types
from datetime import datetime
from urllib.parse import quote
from quotes import normalize_quote
from forwards import normalize_forward
from attachments import index_local_files, resolve_forward_files
from archive_export import build_export
from portable import ROOT, load_settings, load_keys

sys.path.insert(0, str(ROOT / 'vendor/wechat-exporter-mac'))
import sqlcipher3
from core.models import Layout
from core.image_dat import find_uin
from images import decode_to_displayable
from core.media import silk_to_wav
os.umask(0o077)

def connect(path, key):
    if not re.fullmatch(r'(?:[a-fA-F0-9]{64}|[a-fA-F0-9]{96})', key):
        raise ValueError('数据库密钥格式不正确')
    conn = sqlcipher3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        conn.execute(f'''PRAGMA key = "x'{key}'"''')
        conn.execute('PRAGMA cipher_compatibility = 4')
        conn.execute('PRAGMA query_only = ON')
        conn.execute('SELECT count(*) FROM sqlite_master').fetchone()
        return conn
    except Exception:
        conn.close()
        raise

def media_keys(account):
    config = load_settings()
    if config['platform'] == 'windows':
        image_key = config.get('image_key', '')
        # Windows image keys are supplied independently; never use Mac's UIN formula.
        return dict(img_key=bytes.fromhex(image_key) if image_key else None,
                    xor_key=config.get('xor_key', 0), emo_key=None)
    layout = Layout('v4', str(account), account.name, media_root=str(account / 'msg'))
    uin = find_uin(layout)
    if uin is None or hashlib.md5(str(uin).encode()).hexdigest()[:4] != account.name.rsplit('_', 1)[-1]:
        return dict(img_key=None, xor_key=0, emo_key=None)
    own = account.name.rsplit('_', 1)[0]
    return dict(img_key=hashlib.md5(f'{uin}{own}'.encode()).hexdigest()[:16].encode(),
                xor_key=uin & 255, emo_key=hashlib.md5(f'{uin}{own}EMOTICON'.encode()).digest())

def load_exporter():
    path = ROOT / 'vendor/wechat-export-macos/skills/wechat-export-macos/scripts/export_html_chat.py'
    source = path.read_text(encoding='utf-8')
    # A narrow version-pinned adapter: preserve upstream, support each shard and IDs.
    for old, new in [
        ('sqlite3.connect(dec / "message/message_0.db")', 'sqlite3.connect(cfg["message_db"])'),
        ('"id": local_id,', '"id": local_id, "server_id": str(_server_id or ""), "raw_content": raw, "local_type": local_type,'),
        ('item["media"]["quote_from"] =', 'item["media"]["quote_server_id"] = xml_text(refer, "svrid")\n                item["media"]["quote_from"] ='),
    ]:
        if old not in source:
            raise RuntimeError('上游接口已变化，请检查适配器')
        source = source.replace(old, new)
    mod = types.ModuleType('multimodal_upstream')
    exec(compile(source, str(path), 'exec'), mod.__dict__)
    # Use the existing V1/V2/legacy decoder and convert HEVC for browser display.
    def decode_image(data, key, xor):
        result = decode_to_displayable(data, key, xor)
        return result[0] if result else None
    mod.decrypt_v2 = decode_image
    # No CDN requests; preserve missing markers for non-local emoji.
    mod.download_emoji = lambda *args: False
    # Filename heuristics must not silently choose someone else's same-name file.
    original_index = mod.index_by_prefix
    def index(root):
        result = original_index(root)
        if root.name == 'file':
            return {}  # MVP deliberately leaves unverifiable attachments unresolved.
        return result
    mod.index_by_prefix = index
    original_build = mod.build_messages
    def build_messages(cfg):
        messages, stats = original_build(cfg)
        for item in messages:
            normalize_forward(item)
        return messages, stats
    mod.build_messages = build_messages
    return mod

def main():
    ap = argparse.ArgumentParser(description='单联系人本机历史 · 多模态档案第一版')
    ap.add_argument('--contact', help='精确备注、昵称或 wxid；默认当前配置联系人')
    ap.add_argument('--since', help='可选起始日期 YYYY-MM-DD；默认本机全部历史')
    args = ap.parse_args()
    import fcntl  # Legacy private-chat command; the shared exporter does not use it.
    base = load_settings()
    dbroot = Path(base['db_storage'])
    account = dbroot.parent
    state = ROOT / 'data/wechat-live/data/state'
    lock = (state / 'export.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    keys = load_keys(base)
    keys = [k for k in keys if re.fullmatch('[0-9a-fA-F]{64}', k)]
    matched, missing = {}, []
    dbs = sorted(set([dbroot / 'contact/contact.db'] + list((dbroot / 'message').glob('*.db')) + list((dbroot / 'emoticon').glob('*.db'))))
    print('复用缓存密钥，匹配本机数据库…', flush=True)
    for db in dbs:
        for key in keys:
            try:
                c = connect(db, key)
                c.close()
                matched[db] = key
                break
            except sqlcipher3.DatabaseError:
                continue
        if db not in matched:
            missing.append(str(db.relative_to(dbroot)))
    def open_db(path):
        path = Path(path)
        if path not in matched:
            raise RuntimeError('数据库密钥未匹配：' + path.name)
        return connect(path, matched[path])
    c = open_db(dbroot / 'contact/contact.db')
    query = args.contact or base['group']
    contacts = c.execute('SELECT username,nick_name,remark FROM contact WHERE username=? OR nick_name=? OR remark=?', (query, query, query)).fetchall()
    c.close()
    if len(contacts) != 1:
        raise RuntimeError('联系人没有唯一精确匹配，请使用精确备注或 wxid')
    username, nick, remark = contacts[0]
    if username.endswith('@chatroom'):
        raise RuntimeError('第一版只支持单联系人私聊')
    display = remark or nick or username
    own = account.name.rsplit('_', 1)[0]
    table = 'Msg_' + hashlib.md5(username.encode()).hexdigest()
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    out = ROOT / 'data/multimodal' / stamp
    media = out / 'media'
    for folder in ['image', 'image_enc', 'video', 'file', 'emoji', 'emoji_enc', 'avatar', 'voice']:
        (media / folder).mkdir(parents=True, exist_ok=False)
    mod = load_exporter()
    mod.sqlite3 = types.SimpleNamespace(connect=open_db)
    cfg = dict(dec=dbroot, table=table, my_wxid=own, my_name='我', display=display,
               **media_keys(account),
               media=media, since=datetime.strptime(args.since, '%Y-%m-%d').replace(tzinfo=mod.CST) if args.since else None,
               attach=account / 'msg/attach' / hashlib.md5(username.encode()).hexdigest(),
               video_root=account / 'msg/video', file_root=account / 'msg/file', emo_root=account / 'business/emoticon')
    messages, seen, shards = [], set(), []
    print('读取目标联系人历史并导出本地媒体…', flush=True)
    for db in matched:
        if not re.fullmatch(r'message_\d+\.db', db.name):
            continue
        c = open_db(db)
        exists = c.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone()
        c.close()
        if not exists:
            continue
        cfg['message_db'] = db
        rows, _ = mod.build_messages(cfg)
        shards.append(db.name)
        for item in rows:
            identity = 'server:' + item['server_id'] if item['server_id'] not in ('', '0') else f'{db.name}:{item["id"]}'
            if identity in seen:
                continue
            seen.add(identity)
            item['source_db'] = db.name
            item['message_key'] = identity
            messages.append(item)
    messages.sort(key=lambda m: (m['timestamp'], m['source_db'], m['id']))
    voices = [open_db(p) for p in matched if re.fullmatch(r'media_\d+\.db', p.name)]
    for item in messages:
        tag = hashlib.sha256(item['message_key'].encode()).hexdigest()[:24]
        if item['type'] == 'voice':
            blob = None
            for c in voices:
                try:
                    # Both conversation and server ID are required; no ambiguous local-ID fallback.
                    row = c.execute('SELECT voice_data FROM VoiceInfo WHERE svr_id=? AND chat_name_id IN (SELECT rowid FROM Name2Id WHERE user_name=?)', (item['server_id'], username)).fetchone() if item['server_id'] not in ('', '0') else None
                    if row and row[0]:
                        blob = bytes(row[0])
                        break
                except sqlcipher3.DatabaseError:
                    continue
            if blob:
                (media / 'voice' / f'{tag}.silk').write_bytes(blob)
                wav = silk_to_wav(blob)
                if wav:
                    (media / 'voice' / f'{tag}.wav').write_bytes(wav)
                    item['media']['src'] = f'media/voice/{tag}.wav'
                else:
                    item['media']['original'] = f'media/voice/{tag}.silk'
        if item['type'] == 'image' and not item['media'].get('src') and item['media'].get('encrypted'):
            raw = (out / item['media']['encrypted']).read_bytes()
            decoded = decode_to_displayable(raw, cfg['img_key'], cfg['xor_key']) if cfg['img_key'] else None
            if decoded:
                blob, ext = decoded
                (media / 'image' / f'{tag}.{ext}').write_bytes(blob)
                item['media']['src'] = f'media/image/{tag}.{ext}'
        if item['type'] in ('image', 'voice', 'video', 'emoji', 'file'):
            item['availability'] = 'ready' if item['media'].get('src') else 'missing'
            if item['type'] == 'file':
                item['missing_reason'] = '第一版尚未接入可靠的附件关联'
            elif item['availability'] == 'missing':
                item['missing_reason'] = '本机媒体未找到或尚未成功解码'
    for c in voices:
        c.close()
    resolve_forward_files(messages, index_local_files(account), out, media_keys(account))
    counts = collections.Counter(m['type'] for m in messages)
    ready = collections.Counter(m['type'] for m in messages if m.get('availability') == 'ready')
    meta = dict(peer=display, count=len(messages), start=messages[0]['time'] if messages else '',
                end=messages[-1]['time'] if messages else '', counts=dict(counts), ready=dict(ready),
                unmatched_databases=missing, message_shards=shards, version='轻量第一版', timezone='Asia/Shanghai',
                scope='本机现存历史' if not args.since else '自 ' + args.since,
                limitations=['文件附件暂保留记录，不自动匹配同名文件', '未找到的媒体保留缺失提示', '尚未进行全面兼容性验证'])
    for item in messages:
        normalize_quote(item)
    payload = json.dumps(dict(meta=meta, messages=messages), ensure_ascii=False)
    (out / 'chat.json').write_text(payload, encoding='utf-8')
    (out / 'report.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
    template = (Path(__file__).parent / 'viewer.html').read_text(encoding='utf-8')
    (out / 'index.html').write_text(template.replace('__CHAT_DATA__', payload.replace('<', '\\u003c')), encoding='utf-8')
    build_export(out)
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    print('聊天档案：' + str(out / 'index.html'), flush=True)

if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('未完成：' + str(exc), file=sys.stderr)
        sys.exit(1)
