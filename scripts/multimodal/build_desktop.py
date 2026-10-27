"""Build on the target OS; never collect personal configs, archives or keys."""
import argparse
from datetime import datetime
from pathlib import Path
import subprocess
import sys
import platform
import shutil

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    output = args.output or ROOT / 'release' / ('desktop-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    if output.exists():
        raise SystemExit('构建目录已存在，请指定新目录')
    output.mkdir(parents=True)
    cmd = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir',
           '--name', 'WCE', '--distpath', str(output),
           '--workpath', str(output / 'work'), '--specpath', str(output / 'spec'),
           '--paths', str(ROOT / 'scripts/multimodal'),
           '--paths', str(ROOT / 'vendor/wechat-exporter-mac'),
           '--hidden-import', 'export_full', '--hidden-import', 'Crypto.Cipher.AES',
           '--collect-all', 'sqlcipher3', '--collect-all', 'pysilk', '--collect-all', 'av',
           '--collect-all', 'faster_whisper']
    if sys.platform == 'darwin':
        cmd += ['--windowed', '--osx-bundle-identifier', 'local.wechat-chatkit.desktop']
    for name in ('launcher.html', 'viewer.html', 'viewer-transcript.html', 'filtered-export.js'):
        cmd += ['--add-data', str(ROOT / 'scripts/multimodal' / name) + ':.']
    for source in ('LICENSE', 'THIRD_PARTY_NOTICES.md',
                   'vendor/wechat-export-macos/skills/wechat-export-macos/scripts/export_html_chat.py',
                   'vendor/wechat-export-macos/LICENSE', 'vendor/wechat-exporter-mac/LICENSE'):
        cmd += ['--add-data', str(ROOT / source) + ':' + str(Path(source).parent)]
    cmd.append(str(ROOT / 'scripts/multimodal/launcher.py'))
    subprocess.run(cmd, cwd=ROOT, check=True)
    stem = 'WCE-' + sys.platform + '-' + platform.machine()
    if sys.platform == 'darwin':
        subprocess.run(['ditto', '-c', '-k', '--sequesterRsrc', '--keepParent',
                        str(output / 'WCE.app'), str(output / (stem + '.zip'))], check=True)
    else:
        shutil.make_archive(str(output / stem), 'zip', root_dir=output, base_dir='WCE')
    print('构建完成：' + str(output))


if __name__ == '__main__':
    main()
