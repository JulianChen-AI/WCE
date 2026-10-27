"""Create a new archive with locally available forwarded files; preserve the input."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil
from attachments import index_local_files, resolve_forward_files
from forwards import normalize_forward
from export_full import write_archive
from run import media_keys
from archive_export import build_export
from portable import load_settings


def repair(source, account, output_root):
    source = Path(source).resolve()
    data = json.loads((source / 'chat.json').read_text(encoding='utf-8'))
    out = Path(output_root) / ('files-fixed-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f')) / source.name
    out.mkdir(parents=True, exist_ok=False)
    if (source / 'media').is_dir():
        shutil.copytree(source / 'media', out / 'media')
    for item in data['messages']:
        normalize_forward(item)
    result = resolve_forward_files(data['messages'], index_local_files(account), out, media_keys(account))
    data['meta']['repaired_from'] = str(source)
    write_archive(out, data)
    build_export(out)
    return out, result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='补齐本机现存的转发附件；生成新档案，不覆盖原档案')
    parser.add_argument('--archive', type=Path, required=True)
    args = parser.parse_args()
    config = load_settings()
    out, result = repair(args.archive, Path(config['db_storage']).parent, config['output_dir'])
    print(json.dumps(result, ensure_ascii=False))
    print('修复档案：' + str(out / 'index.html'))
