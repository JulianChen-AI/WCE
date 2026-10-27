"""Recover missing emoji from account-local files and verified CDN downloads."""
import hashlib
from io import BytesIO
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from xml.etree import ElementTree as ET

from PIL import Image

LIMIT = 20 * 1024 * 1024


def allowed_url(url):
    p = urlsplit(url)
    return (p.scheme in ('http', 'https') and not p.username and not p.password
            and p.port in (None, 80, 443)
            and any((p.hostname or '').endswith('.' + domain) or p.hostname == domain
                    for domain in ('qq.com', 'qpic.cn')))


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed_url(newurl):
            raise ValueError('unsupported redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def verified_image(blob, digest):
    if hashlib.md5(blob).hexdigest() != digest:
        raise ValueError('MD5 mismatch')
    with Image.open(BytesIO(blob)) as image:
        ext = {'PNG': 'png', 'GIF': 'gif', 'JPEG': 'jpg', 'WEBP': 'webp'}.get(image.format)
        image.verify()
    if not ext:
        raise ValueError('unsupported image')
    return ext


def fill_emojis(messages, account, out, cdn=None):
    index = {}
    # Account-scoped cache only; never match another account's same-name file.
    for root in (account / 'business', account / 'msg', account / 'cache'):
        if root.exists():
            for path in root.rglob('*'):
                if path.is_file():
                    index.setdefault(path.name.split('.')[0].split('_')[0].lower(), []).append(path)
    cache = {}
    opener = build_opener(SafeRedirect())
    for item in messages:
        if item['type'] != 'emoji' or item.get('media', {}).get('src'):
            continue
        media = item['media']
        try:
            raw = item.get('raw_content', '')
            root = ET.fromstring(raw[raw.index('<'):])
            emoji = root if root.tag == 'emoji' else root.find('.//emoji')
            if emoji is None:
                raise ValueError('missing emoji metadata')
            digest = emoji.get('md5', '').lower()
            if len(digest) != 32 or any(c not in '0123456789abcdef' for c in digest):
                raise ValueError('invalid emoji MD5')
            if digest not in cache:
                blob = None
                for path in index.get(digest, []):
                    try:
                        if path.stat().st_size > LIMIT:
                            continue
                        candidate = path.read_bytes()
                        ext = verified_image(candidate, digest)
                        blob = candidate
                        break
                    except (ValueError, OSError):
                        pass
                source = 'local'
                if blob is None:
                    url = emoji.get('cdnurl') or (cdn or {}).get(digest)
                    if not url:
                        if index.get(digest):
                            raise ValueError('已找到本地表情缓存，但当前无法解码，且没有可用下载地址')
                        raise ValueError('no download URL or matching local file')
                    if not allowed_url(url):
                        raise ValueError('unsupported CDN URL')
                    with opener.open(Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=15) as response:
                        blob = response.read(LIMIT + 1)
                    if len(blob) > LIMIT:
                        raise ValueError('emoji exceeds size limit')
                    ext = verified_image(blob, digest)
                    source = 'cdn'
                dest = out / 'media/emoji' / (digest + '.' + ext)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(blob)
                cache[digest] = (str(dest.relative_to(out)), source)
            media['src'], media['recovered_from'] = cache[digest]
            item['availability'] = 'ready'
            item.pop('missing_reason', None)
        except Exception as exc:
            item['availability'] = 'missing'
            # Exception strings may contain signed URLs; keep them out of reports.
            reason = str(exc) if type(exc) is ValueError else type(exc).__name__
            item['missing_reason'] = '表情补取失败：' + reason
