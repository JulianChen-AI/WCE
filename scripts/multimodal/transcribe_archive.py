"""Add voice transcripts to an archive that was exported without them (or redo them).

    python transcribe_archive.py <聊天档案目录> [--redo]

Packaged app: WCE --transcribe-archive <聊天档案目录> [--redo]
Runs only on this computer with the local Whisper model; chat.json, index.html,
聊天记录.txt and 完整聊天档案.zip are rewritten in place.
"""
import argparse
import json
import os
import shutil
from pathlib import Path

from archive_export import build_export
from asr import transcribe_voices
from export_full import write_archive
from portable import load_settings


def main(argv=None):
    ap = argparse.ArgumentParser(description='为已导出的 WCE 聊天档案补做语音转写（只在本机运行）')
    ap.add_argument('archive', help='聊天档案目录（里面有 chat.json）')
    ap.add_argument('--redo', action='store_true', help='连已经转写成功的语音也重新转写')
    args = ap.parse_args(argv)
    os.umask(0o077)
    out = Path(args.archive).expanduser().resolve()
    chat = out / 'chat.json'
    if not chat.is_file():
        raise SystemExit(f'找不到 chat.json：{chat}')
    data = json.loads(chat.read_text(encoding='utf-8'))
    if not isinstance(data.get('messages'), list):
        raise SystemExit('这不是 WCE 导出的 chat.json')
    cfg = load_settings()
    display = data['meta'].get('peer') or out.name
    info = transcribe_voices(out, data, cfg, display, lambda: write_archive(out, data), only_missing=not args.redo)
    # build_export never overwrites, so clear the previous export bundle first.
    shutil.rmtree(out / 'export-assets', ignore_errors=True)
    for name in ('export-assets.js', '完整聊天档案.zip'):
        (out / name).unlink(missing_ok=True)
    build_export(out)
    print(json.dumps(info, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
