"""Regression: selected media only, bounded offline JS chunks, byte-exact ZIPs."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

def check(root):
    sys.path.insert(0, str(root / 'scripts/multimodal'))
    from archive_export import build_export
    
    with TemporaryDirectory() as temp:
        out = Path(temp)
        (out / 'media/file').mkdir(parents=True)
        (out / 'media/file/small.bin').write_bytes(bytes(range(256)))
        (out / 'media/file/large.bin').write_bytes(bytes(range(256)) * 12289)
        messages = [dict(type='file', sender='fixture', time='2026-11-01 12:00:00', text='fixture', media=dict(src='media/file/' + name + '.bin', filename=name + '.bin')) for name in ('small', 'large')]
        (out / 'chat.json').write_text(json.dumps(dict(messages=messages)))
        (out / 'index.html').write_text('fixture')
        build_export(out)
        manifest_size = (out / 'export-assets.js').stat().st_size
        scripts = list(out.glob('export-assets/*.js'))
        print('manifest_bytes=' + str(manifest_size), flush=True)
        assert manifest_size < 4096, 'export loads unselected media into one monolithic script'
        assert scripts and max(p.stat().st_size for p in scripts) <= 1048700, 'media scripts must stay under 1.01 MiB'
        with ZipFile(out / '完整聊天档案.zip') as archive:
            assert archive.testzip() is None
            for name in ('small', 'large'):
                rel = 'media/file/' + name + '.bin'
                assert archive.read(rel) == (out / rel).read_bytes()
        result = subprocess.run(['node', str(Path(__file__).with_suffix('.js')), str(root), str(out)], capture_output=True, text=True)
        print(result.stdout, end='')
        print(result.stderr, end='', file=sys.stderr)
        assert result.returncode == 0, 'browser export regression failed'
        print('PASS: bounded chunks, selected-only loads, exact full and filtered ZIP bytes')


def test_export_memory():
    check(Path(__file__).resolve().parents[1])


if __name__ == "__main__":
    check(Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[1])
