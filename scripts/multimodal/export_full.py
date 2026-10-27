#!/usr/bin/env python3
"""Export explicitly named conversations, hash-verified files, and local ASR.

Original databases are opened read-only. Every invocation creates a new archive.
"""
import argparse
import collections
import hashlib
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

import run as legacy
from quotes import normalize_quote
from attachments import index_local_files, verified_path, resolve_forward_files
from forwards import walk_messages
from emojis import fill_emojis
from archive_export import build_export
from portable import load_settings, load_keys, account_id, safe_conversation_dir

ROOT = legacy.ROOT


def body_and_sender(raw, sender, group):
    prefix, sep, body = raw.partition(':\n')
    if group and sep and re.fullmatch(r'[A-Za-z0-9_@.-]+', prefix):
        return body, prefix
    return raw, sender


def verified_file(app, files):
    """Names alone never establish identity; require message MD5 and byte size."""
    return verified_path(app.findtext('md5') or app.findtext('appattach/md5'), app.findtext('appattach/totallen'), files)


def readable_content(item):
    raw, _ = body_and_sender(item['raw_content'], '', True)
    if item['type'] == 'system' and raw.startswith('<'):
        try:
            root = ET.fromstring(raw)
            template = root.findtext('.//content_template/template')
            if template:
                for link in root.findall('.//content_template/link_list/link'):
                    names = [n.text or '' for n in link.findall('.//nickname')]
                    value = (link.findtext('separator') or '、').join(names) if names else link.findtext('plain') or ''
                    template = template.replace('$' + link.get('name', '') + '$', value)
                item['text'] = template
            elif root.findtext('.//replacemsg'):
                item['text'] = root.findtext('.//replacemsg')
        except ET.ParseError:
            pass
    normalize_quote(item)


def write_archive(out, data):
    for item in data['messages']:
        readable_content(item)
    nested_files = [m for parent in data['messages'] if parent.get('forward') for m in walk_messages(parent['forward']['messages']) if m.get('type') == 'file']
    data['meta']['forwarded_files'] = dict(total=len(nested_files), ready=sum(bool(m.get('media', {}).get('src')) for m in nested_files))
    nested = [m for parent in data['messages'] if parent.get('forward') for m in walk_messages(parent['forward']['messages'])]
    records = [m['forward'] for m in walk_messages(data['messages']) if m.get('forward')]
    images = [m for m in nested if m.get('type') == 'image']
    data['meta']['forward_completeness'] = dict(records=len(records), expected_entries=sum(r.get('expected_count', len(r['messages'])) for r in records), parsed_entries=sum(len(r['messages']) for r in records), native_references=sum(bool(m.get('has_native_reference')) for m in nested), readable_references=sum(bool(m.get('media', {}).get('quote_text')) for m in nested), images=len(images), ready_images=sum(bool(m.get('media', {}).get('src')) for m in images))
    payload = json.dumps(data, ensure_ascii=False)
    (out / 'chat.json').write_text(payload, encoding='utf-8')
    (out / 'report.json').write_text(json.dumps(data['meta'], ensure_ascii=False, indent=2), encoding='utf-8')
    template = (Path(__file__).parent / 'viewer-transcript.html').read_text(encoding='utf-8')
    template = template.replace('<option value="transcribed">已转写语音</option><option value="">全部消息</option>', '<option value="">全部消息</option><option value="transcribed">已转写语音</option>')
    template = template.replace('语音转文字 · 本地预览', '完整本机记录 · 含语音文字及附件')
    template = template.replace('本轮仅转写最近 ', '本轮转写全部可用语音，共 ')
    template = template.replace("if(d.quote_text)", "if(d.desc)bubble.append(node('div','quote',d.desc));if(d.url&&/^https?:\\/\\//.test(d.url)){const a=node('a','','打开链接');a.href=d.url;a.target='_blank';a.rel='noopener noreferrer';bubble.append(a);}if(d.quote_text)")
    (out / 'index.html').write_text(template.replace('__CHAT_DATA__', payload.replace('<', '\\u003c')), encoding='utf-8')
    lines = [data['meta']['peer'], '时间均为北京时间；机器转写未人工校对。', '范围：本机现存全部记录，不代表手机历史已全部同步。', '']
    missing = []
    for m in data['messages']:
        lines.append(f"[{m['time']}] {m['sender']} [{m['type']}] {m['text']}")
        if m.get('transcription'):
            lines.append('语音转写：' + (m['transcription']['text'] or '[转写失败或无文字]'))
        if m['media'].get('quote_text'):
            lines.append('引用：' + m['media'].get('quote_from', '') + '：' + m['media']['quote_text'])
        if m['media'].get('desc'):
            lines.append(m['media']['desc'])
        if m['media'].get('url'):
            lines.append('链接：' + m['media']['url'])
        if m['media'].get('src'):
            lines.append('媒体/附件：' + m['media']['src'])
        if m.get('availability') == 'missing':
            lines.append('缺失：' + m['missing_reason'])
            missing.append({k: m[k] for k in ('message_key', 'time', 'sender', 'type', 'text', 'missing_reason')})
        lines.append('')
    (out / '聊天记录.txt').write_text('\n'.join(lines), encoding='utf-8')
    (out / '缺失清单.json').write_text(json.dumps(missing, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--contact', action='append', required=True)
    args = ap.parse_args()
    os.umask(0o077)
    cfg = load_settings()
    dbroot = Path(cfg['db_storage'])
    account = dbroot.parent
    own = account_id(account, cfg)
    keys = load_keys(cfg)
    matched, missing = {}, []
    dbs = sorted(set([dbroot / 'contact/contact.db'] + list((dbroot / 'message').glob('*.db')) + list((dbroot / 'emoticon').glob('*.db'))))
    for db in dbs:
        for key in keys:
            try:
                c = legacy.connect(db, key)
                c.close()
                matched[db] = key
                break
            except legacy.sqlcipher3.DatabaseError:
                pass
        if db not in matched:
            missing.append(str(db.relative_to(dbroot)))

    def open_db(path):
        return legacy.connect(Path(path), matched[Path(path)])

    if dbroot / 'contact/contact.db' not in matched:
        raise RuntimeError('联系人数据库未能解密，请重新配置数据目录和密钥')
    contact = open_db(dbroot / 'contact/contact.db')
    required = {'username', 'nick_name', 'remark'}
    if not required <= {r[1] for r in contact.execute('PRAGMA table_info(contact)')}:
        raise RuntimeError('联系人数据库格式尚未适配，请记录微信完整版本号后反馈')
    targets = []
    for query in args.contact:
        rows = contact.execute('SELECT username,nick_name,remark FROM contact WHERE username=? OR nick_name=? OR remark=?', (query,) * 3).fetchall()
        if len(rows) != 1:
            raise RuntimeError('目标未唯一匹配：' + query)
        targets.append(rows[0])
    output = Path(cfg['output_dir']) / ('full-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    output.mkdir(parents=True, exist_ok=False)
    print('输出目录：' + str(output), flush=True)
    files = index_local_files(account)
    model = None
    reports = []
    for username, nick, remark in targets:
        display = remark or nick or username
        out = safe_conversation_dir(output, display, username)
        media = out / 'media'
        for folder in ['image', 'image_enc', 'video', 'file', 'emoji', 'emoji_enc', 'avatar', 'voice']:
            (media / folder).mkdir(parents=True, exist_ok=False)
        mod = legacy.load_exporter()
        mod.sqlite3 = type('DB', (), {'connect': staticmethod(open_db)})
        mod.load_emoji_cdn = lambda _: {}
        original_decode = mod.decode_content
        mod.decode_content = lambda content, ct: body_and_sender(original_decode(content, ct), '', username.endswith('@chatroom'))[0]
        table = 'Msg_' + hashlib.md5(username.encode()).hexdigest()
        options = dict(dec=dbroot, table=table, my_wxid=own, my_name='我', display=display, **legacy.media_keys(account), media=media, since=None,
                       attach=account / 'msg/attach' / hashlib.md5(username.encode()).hexdigest(), video_root=account / 'msg/video', file_root=account / 'msg/file', emo_root=account / 'business/emoticon')
        messages, seen, shards = [], set(), []
        source_count = 0
        for db in matched:
            if not re.fullmatch(r'message_\d+\.db', db.name):
                continue
            c = open_db(db)
            c.execute('BEGIN')
            if not c.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone():
                c.close()
                continue
            required = {'local_id', 'server_id', 'real_sender_id', 'message_content', 'WCDB_CT_message_content', 'local_type', 'create_time', 'packed_info_data'}
            if not required <= {r[1] for r in c.execute(f'PRAGMA table_info({table})')}:
                c.close()
                raise RuntimeError('消息数据库格式尚未适配：' + db.name + '，请记录微信完整版本号后反馈')
            senders = dict(c.execute('SELECT rowid,user_name FROM Name2Id'))
            rawrows = {r[0]: r[1:] for r in c.execute(f'SELECT local_id,real_sender_id,message_content,WCDB_CT_message_content,local_type FROM {table}')}
            source_count += len(rawrows)
            # Reuse the same read transaction for parsing and raw metadata.
            class Snapshot:
                def execute(self, *a): return c.execute(*a)
                def close(self): pass
            mod.sqlite3.connect = lambda _: Snapshot()
            options['message_db'] = db
            rows, _ = mod.build_messages(options)
            c.close()
            shards.append(db.name)
            for item in rows:
                ident = 'server:' + item['server_id'] if item['server_id'] not in ('', '0') else f'{db.name}:{item["id"]}'
                if ident in seen:
                    continue
                seen.add(ident)
                sender_id, raw, ct, local_type = rawrows[item['id']]
                raw = original_decode(raw, ct)
                body, sender = body_and_sender(raw, senders.get(sender_id, ''), username.endswith('@chatroom'))
                named = contact.execute('SELECT nick_name,remark FROM contact WHERE username=?', (sender,)).fetchone()
                item.update(message_key=ident, source_db=db.name, sender_id=sender, raw_content=raw, local_type=local_type)
                if item['role'] != 'system':
                    item['role'] = 'me' if sender == own else 'peer'
                    item['sender'] = '我' if sender == own else ((named[1] or named[0]) if named else sender or '未知')
                if item['type'] == 'system' and not body.startswith('<'):
                    item['text'] = body
                if (local_type & 65535) == 49:
                    app = ET.fromstring(body).find('appmsg')
                    if app is not None and app.findtext('type') == '87':
                        item.update(type='text', text='[群公告]\n' + (app.findtext('textannouncement') or app.findtext('announcement') or ''))
                        item['media'] = {}
                if item['type'] == 'file':
                    app = ET.fromstring(body).find('appmsg')
                    src = verified_file(app, files)
                    item['media']['filename'] = app.findtext('title') or '附件'
                    if src:
                        ext = re.sub('[^a-zA-Z0-9]', '', app.findtext('appattach/fileext') or src.suffix.lstrip('.'))
                        name = hashlib.sha256(ident.encode()).hexdigest()[:24] + '.' + ext
                        shutil.copy2(src, media / 'file' / name)
                        item['media'].update(src='media/file/' + name, md5=hashlib.md5(src.read_bytes()).hexdigest(), bytes=src.stat().st_size, verified_by='message_md5_and_size')
                        print(display + '：已校验附件 ' + item['media']['filename'], flush=True)
                messages.append(item)
        resolve_forward_files(messages, files, out, legacy.media_keys(account))
        messages.sort(key=lambda m: (m['timestamp'], m['source_db'], m['id']))
        emoji_cdn = {}
        emoji_db = dbroot / 'emoticon/emoticon.db'
        if emoji_db in matched:
            ec = open_db(emoji_db)
            emoji_cdn = {md5.lower(): url for md5, url in ec.execute(
                'SELECT md5,cdn_url FROM kNonStoreEmoticonTable WHERE cdn_url IS NOT NULL AND cdn_url!=\'\'') if md5}
            ec.close()
        fill_emojis(messages, account, out, emoji_cdn)
        voices = [open_db(p) for p in matched if re.fullmatch(r'media_\d+\.db', p.name)]
        for item in messages:
            if item['type'] == 'voice':
                for c in voices:
                    row = c.execute('SELECT voice_data FROM VoiceInfo WHERE svr_id=? AND chat_name_id IN (SELECT rowid FROM Name2Id WHERE user_name=?)', (item['server_id'], username)).fetchone()
                    if row and row[0]:
                        blob = bytes(row[0])
                        tag = hashlib.sha256(item['message_key'].encode()).hexdigest()[:24]
                        (media / 'voice' / (tag + '.silk')).write_bytes(blob)
                        item['media']['original'] = 'media/voice/' + tag + '.silk'
                        wav = legacy.silk_to_wav(blob)
                        if wav:
                            (media / 'voice' / (tag + '.wav')).write_bytes(wav)
                            item['media']['src'] = 'media/voice/' + tag + '.wav'
                        break
            if item['type'] in ('image', 'voice', 'video', 'emoji', 'file'):
                item['availability'] = 'ready' if item['media'].get('src') else 'missing'
                if item['availability'] == 'missing':
                    item.setdefault('missing_reason', '本机未找到校验一致的附件' if item['type'] == 'file' else '本机媒体未找到或尚未成功解码')
        for c in voices:
            c.close()
        selected = [m for m in messages if m['type'] == 'voice' and m['media'].get('src')]
        meta = dict(peer=display, conversation_id=username, count=len(messages), source_rows=source_count,
                    duplicates_removed=source_count-len(messages), start=messages[0]['time'] if messages else '', end=messages[-1]['time'] if messages else '',
                    counts=dict(collections.Counter(m['type'] for m in messages)), ready=dict(collections.Counter(m['type'] for m in messages if m.get('availability') == 'ready')),
                    unmatched_databases=missing, message_shards=shards, timezone='Asia/Shanghai', scope='本机现存全部历史',
                    limitations=['手机未同步的历史不包含在内', '缺失媒体已逐条标记', '机器转写未人工校对', '原始消息内容保存在 chat.json'],
                    transcription=dict(selected=len(selected), success=0, scope='全部可用语音', model='base', local_only=True))
        data = dict(meta=meta, messages=messages)
        write_archive(out, data)
        for i, item in enumerate(selected if cfg['transcribe'] else [], 1):
            if model is None:
                try:
                    from faster_whisper import WhisperModel
                    model = WhisperModel('base', device='cpu', compute_type='int8', cpu_threads=4, local_files_only=bool(cfg.get('legacy')),
                                         download_root=cfg['model_dir'])
                except Exception:
                    print('本地转写模型不可用，已保留原语音；可安装模型后重试。', flush=True)
                    break
            try:
                segments, _ = model.transcribe(str(out / item['media']['src']), language='zh', beam_size=5, condition_on_previous_text=False)
                text = ''.join(s.text for s in segments).strip()
                item['transcription'] = dict(text=text, status='done' if text else 'empty', note='机器转写，未人工校对', model='base')
                meta['transcription']['success'] += bool(text)
            except Exception as e:
                item['transcription'] = dict(text='', status='failed', error_type=type(e).__name__)
            print(f'{display}：语音转写 {i}/{len(selected)} {item["transcription"]["status"]}', flush=True)
            write_archive(out, data)
        build_export(out)
        reports.append(meta)
        print(json.dumps(meta, ensure_ascii=False), flush=True)
    contact.close()
    (output / '汇总.json').write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding='utf-8')
    print('完成：' + str(output), flush=True)


if __name__ == '__main__':
    main()
