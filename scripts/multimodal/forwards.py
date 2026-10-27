"""Read inline merged-forward records. Never fetch URLs or guess attachment paths."""
from datetime import datetime, timezone, timedelta
from html import unescape
import re
from xml.etree import ElementTree as ET

MAX_BYTES = 4 * 1024 * 1024
MAX_ITEMS = 2000
MAX_DEPTH = 5
LABELS = {'1': 'text', '2': 'image', '3': 'voice', '4': 'video', '5': 'link',
          '6': 'location', '7': 'music', '8': 'file', '17': 'forward', '19': 'forward'}
NAMES = {'image': '图片', 'voice': '语音', 'video': '视频', 'location': '位置',
         'music': '音乐', 'file': '文件', 'link': '链接', 'forward': '聊天记录', 'unknown': '消息'}
CST = timezone(timedelta(hours=8))


def _xml(value):
    if len(value.encode('utf-8')) > MAX_BYTES:
        raise ValueError('聊天记录超过读取大小限制')
    # No DTD/entities; plain chat XML must not expand declarations or read files.
    if re.search(r'<!\s*(?:DOCTYPE|ENTITY)\b', value, re.I):
        raise ValueError('聊天记录含不支持的 XML 声明')
    return ET.fromstring(value)


def _record(node):
    if node is None:
        raise ValueError('本机消息未包含转发正文（可能未同步）')
    if node.find('datalist') is not None:
        return node
    for child in node:
        if child.find('datalist') is not None:
            return child
    value = (node.text or '').strip()
    # CDATA and escaped XML both occur; only unwrap the container, never datadesc.
    for _ in range(3):
        if value.startswith('<'):
            return _xml(value)
        decoded = unescape(value)
        if decoded == value:
            break
        value = decoded
    raise ValueError('本机消息未包含可读的转发正文')


def _read_record(node, title, depth, remaining):
    result = dict(title=title or '聊天记录', messages=[], status='ready')
    try:
        if depth >= MAX_DEPTH:
            raise ValueError('嵌套层级超过读取限制')
        root = _record(node)
        listing = root if root.tag == 'datalist' else root.find('datalist')
        if listing is None:
            raise ValueError('转发正文格式无法识别')
        declared = listing.get('count', '')
        result['expected_count'] = int(declared) if declared.isdigit() else len(listing.findall('dataitem'))
        for child in listing.findall('dataitem'):
            if remaining[0] <= 0:
                raise ValueError('转发条数超过读取限制')
            remaining[0] -= 1
            kind = LABELS.get(child.get('datatype') or child.findtext('datatype'), 'unknown')
            name = child.findtext('sourcename') or child.findtext('datasource/sourcename') or child.findtext('datasource/realchatname') or '未知发送人'
            when = child.findtext('sourcetime') or child.findtext('datasource/sourcetime') or ''
            if re.fullmatch(r'\d{10}|\d{13}', when):
                when = datetime.fromtimestamp(int(when) / (1000 if len(when) == 13 else 1), CST).strftime('%Y-%m-%d %H:%M:%S')
            text = child.findtext('datadesc') or ''
            title_text = child.findtext('datatitle') or ''
            media = {}
            entry = dict(type=kind, sender=name, time=when, text=text, media=media, server_id=child.findtext('fromnewmsgid') or '')
            if kind != 'text':
                entry['text'] = '[' + NAMES[kind] + ']' + title_text + (('\n' + text) if text and text != title_text else '')
            if kind in ('image', 'voice', 'video', 'file'):
                entry.update(availability='missing', missing_reason='仅有转发记录信息，未导出媒体或附件实体')
            if kind in ('image', 'voice', 'video', 'file'):
                if kind == 'file':
                    media['filename'] = title_text
                digest = (child.findtext('fullmd5') or child.findtext('filemd5') or child.findtext('datamd5') or child.findtext('md5') or '').lower()
                if re.fullmatch('[a-f0-9]{32}', digest):
                    media['md5'] = digest
                media['fileext'] = child.findtext('datafmt') or ''
                size = child.findtext('filesize') or child.findtext('datasize') or ''
                if size.isdigit():
                    media['bytes'] = int(size)
            if kind == 'forward':
                nested = child.find('recordxml')
                if nested is None:
                    nested = child.find('recorditem')
                entry['forward'] = _read_record(nested, title_text, depth + 1, remaining)
            if kind == 'text' and text.lstrip().startswith('<'):
                # Some clients store a quoted appmsg in datadesc instead of plain text.
                try:
                    raw = _xml(text)
                    app = raw if raw.tag == 'appmsg' else raw.find('appmsg')
                    if app is not None:
                        subtype = app.findtext('type') or ''
                        entry['text'] = app.findtext('title') or '[消息]'
                        if subtype == '57':
                            entry['type'] = 'quote'
                            media['quote_from'] = app.findtext('refermsg/displayname') or ''
                            media['quote_text'] = app.findtext('refermsg/content') or ''
                            from quotes import normalize_quote
                            normalize_quote(dict(raw_content=text, media=media))
                        elif subtype == '19':
                            entry['type'] = 'forward'
                            entry['forward'] = _read_record(app.find('recorditem'), entry['text'], depth + 1, remaining)
                except (ET.ParseError, ValueError):
                    pass  # It can also be literal user text beginning with '<'.
            reference = child.find('refermsgitem')
            if reference is not None:
                entry['has_native_reference'] = True
                entry['type'] = 'quote' if kind == 'text' else entry['type']
                media['quote_from'] = reference.findtext('displayname') or ''
                media['quote_text'] = reference.findtext('content') or reference.findtext('referdesc') or ''
                media['quote_desc'] = reference.findtext('referdesc') or ''
                from quotes import normalize_quote
                normalize_quote(dict(raw_content=ET.tostring(child, encoding='unicode'), media=media))
            result['messages'].append(entry)
        declared = listing.get('count', '')
        if declared.isdigit() and int(declared) > len(result['messages']):
            raise ValueError('转发正文不完整，部分消息未同步')
        if not result['messages']:
            raise ValueError('本机转发正文为空')
    except (ET.ParseError, ValueError, OverflowError, OSError) as exc:
        result.update(status='partial' if result['messages'] else 'missing', missing_reason=str(exc) if not isinstance(exc, ET.ParseError) else '转发正文 XML 无法解析')
    return result


def record_text(record, indent=''):
    lines = []
    for entry in record['messages']:
        header = ' · '.join(value for value in (entry['sender'], entry['time']) if value)
        readable = '[聊天记录] ' + entry['forward']['title'] if entry.get('forward') else entry['text']
        lines.append(indent + header + '：' + readable)
        media = entry['media']
        if 'bytes' in media:
            lines.append(indent + '文件大小：' + str(media['bytes']) + ' 字节')
        if media.get('src'):
            lines.append(indent + '附件：' + media['src'])
        if media.get('quote_text'):
            lines.append(indent + '引用：' + media.get('quote_from', '') + '：' + media['quote_text'])
        if entry.get('forward'):
            lines.append(record_text(entry['forward'], indent + '  '))
        if entry.get('missing_reason'):
            lines.append(indent + entry['missing_reason'])
    if record.get('missing_reason'):
        lines.append(indent + record['missing_reason'])
    return '\n'.join(lines)


def _forward_hint(raw):
    """Read only direct appmsg/type metadata when a large record cannot be parsed."""
    prefix = raw[:65536]
    if re.search(r'<!\s*(?:DOCTYPE|ENTITY)\b', prefix.partition('<appmsg')[0], re.I):
        return False
    parser = ET.XMLPullParser(events=('start', 'end'))
    stack = []
    try:
        for offset in range(0, len(prefix), 256):
            parser.feed(prefix[offset:offset + 256])
            for event, element in parser.read_events():
                if event == 'start':
                    stack.append(element.tag)
                else:
                    if stack in (['msg', 'appmsg', 'type'], ['appmsg', 'type']):
                        return element.text == '19'
                    stack.pop()
    except ET.ParseError:
        pass
    return False


def normalize_forward(item):
    raw = item.get('raw_content', '')
    # Only inspect app-card payloads, not text that happens to contain XML.
    local_type = item.get('local_type')
    if local_type is not None and int(local_type) & 65535 != 49:
        return
    if item.get('type') not in ('link', 'forward', 'app'):
        return
    if ':\n' in raw and not raw.lstrip().startswith('<'):
        prefix, body = raw.split(':\n', 1)
        if re.fullmatch(r'[A-Za-z0-9_@.-]+', prefix):
            raw = body
    try:
        root = _xml(raw)
        app = root if root.tag == 'appmsg' else root.find('appmsg')
        if app is None or app.findtext('type') != '19':
            return
        title = app.findtext('title') or '聊天记录'
        record = _read_record(app.find('recorditem'), title, 0, [MAX_ITEMS])
    except (ET.ParseError, ValueError):
        # An explicit packed subtype still identifies a damaged forward card.
        if item.get('type') != 'forward' and not (local_type and int(local_type) >> 32 == 19) and not _forward_hint(raw):
            return
        title = item.get('text') or '聊天记录'
        record = dict(title=title, messages=[], status='missing', missing_reason='转发正文 XML 无法解析或超过读取限制')
    item.update(type='forward', forward=record, text='[聊天记录] ' + title + '\n' + record_text(record))
    item.setdefault('media', {}).pop('url', None)
    item['media'].pop('desc', None)
    if record['status'] != 'ready':
        item.update(availability='missing', missing_reason=record['missing_reason'])


def walk_messages(messages):
    for message in messages:
        yield message
        if message.get('forward'):
            yield from walk_messages(message['forward']['messages'])
