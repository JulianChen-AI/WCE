"""Resolve local attachments by exact message MD5 and byte count, never by name."""
import hashlib
from pathlib import Path
import re
import shutil
from forwards import record_text, walk_messages


def file_md5(path):
    digest = hashlib.md5()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def index_local_files(account):
    index = {}
    account = Path(account).resolve()
    for folder in (account / 'msg/file', account / 'msg/attach'):
        for path in folder.rglob('*'):
            try:
                if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(account):
                    index.setdefault(path.stat().st_size, []).append(path)
            except OSError:
                continue
    return index


def verified_path(digest, size, files):
    digest = str(digest or '').lower()
    if not re.fullmatch('[a-f0-9]{32}', digest) or not str(size).isdigit():
        return None
    size = int(size)
    candidates = files.get(size, []) if isinstance(files, dict) else files
    for path in candidates:
        try:
            if path.stat().st_size == size and file_md5(path) == digest:
                return path
        except OSError:
            continue
    return None


def _resolve_forward_image(item, files, out, keys):
    from core.image_dat import decode_dat
    from images import decode_to_displayable
    media = item['media']
    digest, size = media.get('md5', ''), media.get('bytes', '')
    if not re.fullmatch('[a-f0-9]{32}', digest) or not str(size).isdigit():
        item['missing_reason'] = '本机记录缺少图片校验信息，无法确认原图'
        return
    size = int(size)
    candidates = []
    if isinstance(files, dict):
        for length in [size] + list(range(size + 16, size + 32)):
            candidates.extend(files.get(length, []))
    else:
        candidates = files
    for source in candidates:
        try:
            raw = source.read_bytes()
            decoded = decode_dat(raw, keys.get('img_key'), keys.get('xor_key'))
            originals = [raw] + ([decoded[0]] if decoded else [])
            if not any(len(blob) == size and hashlib.md5(blob).hexdigest() == digest for blob in originals):
                continue
            display = decode_to_displayable(raw, keys.get('img_key'), keys.get('xor_key'))
            if not display or display[1] not in ('jpg', 'png', 'gif', 'webp'):
                continue
            blob, extension = display
            relative = 'media/image/' + hashlib.sha256((digest + ':' + str(size)).encode()).hexdigest()[:32] + '.' + extension
            target = out / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob)
            media.update(src=relative, verified_by='decoded_original_md5_and_size', display_md5=hashlib.md5(blob).hexdigest(), display_bytes=len(blob))
            item['availability'] = 'ready'
            item.pop('missing_reason', None)
            return
        except OSError:
            continue
    item['availability'] = 'missing'
    media.pop('src', None)
    item['missing_reason'] = '本机未找到校验一致且可解码的转发图片，请先在微信打开原图再重新导出'


def resolve_forward_files(messages, files, out, image_keys=None):
    total = ready = 0
    for parent in messages:
        if not parent.get('forward'):
            continue
        for item in walk_messages(parent['forward']['messages']):
            if item.get('type') == 'image':
                _resolve_forward_image(item, files, out, image_keys or {})
                continue
            if item.get('type') != 'file':
                continue
            total += 1
            media = item['media']
            digest, size = media.get('md5', ''), media.get('bytes', '')
            source = verified_path(digest, size, files)
            if source is None:
                item.update(availability='missing', missing_reason='本机未找到 MD5 和大小均一致的附件，请先在微信打开该转发记录并下载文件，再重新导出')
                media.pop('src', None)
                continue
            extension = media.get('fileext') or Path(media.get('filename', '')).suffix.lstrip('.') or source.suffix.lstrip('.')
            extension = re.sub('[^a-zA-Z0-9]', '', extension)[:16] or 'bin'
            name = hashlib.sha256((digest + ':' + str(size)).encode()).hexdigest()[:32] + '.' + extension
            relative = 'media/file/' + name
            target = out / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            if target.stat().st_size != int(size) or file_md5(target) != digest:
                target.unlink()
                item.update(availability='missing', missing_reason='复制期间附件发生变化，请重新导出')
                media.pop('src', None)
                continue
            media.update(src=relative, verified_by='message_md5_and_size')
            item['availability'] = 'ready'
            item.pop('missing_reason', None)
            ready += 1
        parent['text'] = '[聊天记录] ' + parent['forward']['title'] + '\n' + record_text(parent['forward'])
    return dict(total=total, ready=ready, missing=total-ready)
