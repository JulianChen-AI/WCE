"""Prepare a byte-preserving ZIP for the offline viewer's export button."""
from zipfile import ZipFile, ZIP_STORED
import base64
import json
from pathlib import Path
from forwards import walk_messages


def build_export(out):
    data = json.loads((out / 'chat.json').read_text(encoding='utf-8'))
    paths = {value for m in walk_messages(data['messages']) for key, value in m.get('media', {}).items()
             if key in ('src', 'thumb', 'original') and isinstance(value, str) and value.startswith('media/')}
    assets = {}
    for rel in sorted(paths):
        path = (out / rel).resolve()
        if path.is_relative_to(out.resolve()) and path.is_file():
            assets[rel] = base64.b64encode(path.read_bytes()).decode('ascii')
    (out / 'export-assets.js').write_text('window.exportAssets=' + json.dumps(assets) + ';', encoding='utf-8')
    (out / 'filtered-export.js').write_text((Path(__file__).parent / 'filtered-export.js').read_text(encoding='utf-8'), encoding='utf-8')
    files = sorted(p for p in out.rglob('*') if p.is_file() and p.name != '完整聊天档案.zip')
    with ZipFile(out / '完整聊天档案.zip', 'x', compression=ZIP_STORED) as archive:
        for path in files:
            archive.write(path, path.relative_to(out))
