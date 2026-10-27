"""Mac product source only; explicit allowlist, no data, logs or test records."""
from datetime import datetime
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[2]
TOP = ('README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md', '.gitignore',
       'requirements-desktop.txt', 'install.command', 'docs/INSTALL.md', 'docs/USAGE.md')
RUNTIME = (
    'archive_export.py', 'attachments.py', 'build_desktop.py', 'emojis.py',
    'export_full.py', 'filtered-export.js', 'forwards.py', 'images.py',
    'launcher.html', 'launcher.py', 'package_source.py', 'portable.py',
    'quotes.py', 'repair_files.py', 'requirements-asr.txt', 'requirements.txt',
    'run.py', 'upstream-lock.json', 'viewer-transcript.html', 'viewer.html',
)
CORE = ('__init__.py', 'models.py', 'constants.py', 'db_decryptor.py', 'image_dat.py', 'media.py')


def source_files(root=ROOT):
    root = Path(root).resolve()
    names = list(TOP) + ['scripts/multimodal/' + name for name in RUNTIME]
    names += ['vendor/wechat-exporter-mac/core/' + name for name in CORE]
    names += ['vendor/wechat-exporter-mac/LICENSE', 'vendor/wechat-export-macos/LICENSE',
              'vendor/wechat-export-macos/skills/wechat-export-macos/scripts/export_html_chat.py']
    files = []
    for name in names:
        path = root / name
        if any(part.is_symlink() for part in [path, *path.parents] if part != root and root in part.parents):
            raise ValueError('符号链接不能进入发布包：' + name)
        if not path.is_file() or not path.resolve().is_relative_to(root):
            raise ValueError('缺少普通产品文件：' + name)
        files.append(path)
    return files


if __name__ == '__main__':
    files = source_files()
    folder = ROOT / 'release'
    folder.mkdir(exist_ok=True)
    output = folder / ('wechat-chatkit-mac-source-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.zip')
    with ZipFile(output, 'x', compression=ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(ROOT).as_posix())
    print(output)
