"""Turn quoted WeChat payloads into readable summaries; retain raw content."""
from xml.etree import ElementTree as ET


def normalize_quote(item):
    media = item.get('media', {})
    quote = media.get('quote_raw', media.get('quote_text', ''))
    if not quote:
        return
    kind = ''
    try:
        raw = item.get('raw_content', '')
        if ':\n' in raw and not raw.lstrip().startswith('<'):
            raw = raw.split(':\n', 1)[1]
        root = ET.fromstring(raw) if raw else None
        refer = root.find('.//refermsg') if root is not None else None
        if refer is None and root is not None:
            refer = root.find('.//refermsgitem')
        if refer is not None:
            kind = refer.findtext('type') or ''
            media['quote_server_id'] = refer.findtext('svrid') or ''
    except ET.ParseError:
        pass
    labels = {'3': '图片', '34': '语音', '43': '视频', '47': '表情', '62': '视频'}
    summary = quote
    if kind in labels:
        summary = '[' + labels[kind] + ']'
    elif kind != '1' and quote.lstrip().startswith('<'):
        try:
            root = ET.fromstring(quote)
            tag_labels = {'img': '图片', 'voicemsg': '语音', 'videomsg': '视频', 'emoji': '表情', 'location': '位置'}
            label = next((name for tag, name in tag_labels.items()
                          if root.tag == tag or root.find('.//' + tag) is not None), None)
            app = root if root.tag == 'appmsg' else root.find('.//appmsg')
            if label:
                summary = '[' + label + ']'
            elif app is not None:
                label = {'6': '文件', '5': '链接', '33': '小程序', '36': '小程序', '57': '引用消息'}.get(app.findtext('type'), '消息')
                summary = '[' + label + ']' + (app.findtext('title') or '')
            else:
                summary = '[引用消息]'
        except ET.ParseError:
            summary = '[引用消息无法解析]'
    media['quote_raw'] = quote
    media['quote_text'] = summary
