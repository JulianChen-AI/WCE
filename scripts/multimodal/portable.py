"""User-owned configuration and platform paths. No WeChat writes or key capture."""
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[2]
TARGET_VERSION = '4.1.13'


def user_dir():
    override = os.environ.get('CHATKIT_USER_DIR')
    if override:
        return Path(override).expanduser()
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'WeChatChatkit'
    return Path.home() / 'Library/Application Support/WeChatChatkit'


def discover_accounts(platform=None, home=None):
    platform, home = platform or sys.platform, Path(home or Path.home())
    if platform == 'win32':
        documents = home / 'Documents'
        # Windows may redirect Documents to OneDrive or another drive.
        if home == Path.home() and sys.platform == 'win32':
            import winreg
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders') as key:
                    documents = Path(os.path.expandvars(winreg.QueryValueEx(key, 'Personal')[0]))
            except OSError:
                pass
        roots = [documents / 'xwechat_files', home / 'xwechat_files', home / 'OneDrive/Documents/xwechat_files']
    else:
        roots = [home / 'Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files']
    found = set()
    for root in roots:
        try:
            for db in root.glob('*/db_storage'):
                if (db / 'contact/contact.db').is_file():
                    found.add(str(db))
        except PermissionError:
            continue
    return sorted(found)


def parse_keys(payload):
    """Accept candidate lists or per-database maps; never infer arbitrary strings."""
    if isinstance(payload, str):
        payload = json.loads(payload)
    if isinstance(payload, dict) and 'candidates' in payload:
        payload = payload['candidates']
    elif isinstance(payload, dict) and 'keys' in payload:
        payload = payload['keys']
    if isinstance(payload, dict):
        payload = [entry.get('key') if isinstance(entry, dict) else entry for entry in payload.values()]
    if not isinstance(payload, list) or not payload:
        raise ValueError('密钥文件应包含 candidates 数组或 keys 映射')
    if any(not isinstance(key, str) or not re.fullmatch(r'(?:[a-fA-F0-9]{64}|[a-fA-F0-9]{96})', key) for key in payload):
        raise ValueError('数据库密钥必须是 64 或 96 位十六进制字符串')
    return list(dict.fromkeys(key.lower() for key in payload))


def load_settings():
    path = user_dir() / 'settings.json'
    legacy_root = Path(os.environ.get('CHATKIT_LEGACY_ROOT', ROOT)).expanduser()
    legacy = legacy_root / 'config/wechat-auto-export.json'
    if path.is_file():
        config = json.loads(path.read_text(encoding='utf-8'))
        config['keys_file'] = str(user_dir() / 'keys.json')
    elif sys.platform == 'darwin' and legacy.is_file() and not os.environ.get('CHATKIT_USER_DIR'):
        # Keep the existing Mac usable; the legacy files never enter a release.
        config = json.loads(legacy.read_text(encoding='utf-8'))
        config['keys_file'] = str(legacy_root / 'data/wechat-live/data/state/candidates.json')
        config['legacy'] = True
    else:
        raise RuntimeError('请先在工作台的“首次配置”中选择数据目录并导入密钥')
    config.setdefault('platform', 'windows' if sys.platform == 'win32' else 'macos')
    config.setdefault('output_dir', str(legacy_root / 'data/multimodal') if config.get('legacy') else str(user_dir() / 'archives'))
    config.setdefault('model_dir', str(user_dir() / 'models'))
    legacy_models = Path.home() / '30_Developer/wechat-chatkit-tools/asr-models'
    if config.get('legacy') and legacy_models.exists():
        config['model_dir'] = str(legacy_models)
    config.setdefault('transcribe', bool(config.get('legacy')))
    return config


def load_keys(config):
    path = Path(config['keys_file'])
    return parse_keys(json.loads(path.read_text(encoding='utf-8')))


def save_settings(config, keys):
    folder = user_dir()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Preserve each previous configuration before replacing it.
    from datetime import datetime
    import shutil
    for name in ('settings.json', 'keys.json'):
        path = folder / name
        if path.exists():
            shutil.copy2(path, folder / (name + '.' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.bak'))
    for name, data in [('settings.json', config), ('keys.json', {'candidates': parse_keys(keys)})]:
        path = folder / name
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as file:
            json.dump(data, file, ensure_ascii=False, indent=2)


def account_id(account, config):
    return config.get('own_id') or re.sub(r'_[0-9a-fA-F]{4,}$', '', account.name)


def safe_conversation_dir(parent, display, username):
    import hashlib
    # Windows filenames and duplicate group names must never escape/collide.
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', display).strip(' .')[:80] or '群聊'
    return parent / (name + '-' + hashlib.sha256(username.encode()).hexdigest()[:8])
