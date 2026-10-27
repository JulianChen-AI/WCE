#!/bin/bash
# Local source build: no privilege escalation, key capture, or security bypass.
set -eu
ROOT="$(cd "$(dirname "$0")" && pwd -P)"
app_dir="$HOME/Applications"
check_only=0
no_open=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --check) check_only=1; shift ;;
    --no-open) no_open=1; shift ;;
    --app-dir)
      [ "$#" -ge 2 ] || { echo '--app-dir 需要绝对路径' >&2; exit 2; }
      app_dir="$2"; shift 2 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done
case "$app_dir" in /*) ;; *) echo '应用目录必须是绝对路径' >&2; exit 2 ;; esac
if [ "$(/usr/bin/uname -s)" != Darwin ] || [ "$(/usr/bin/uname -m)" != arm64 ]; then
  echo '当前只支持原生运行的 Apple Silicon Mac' >&2; exit 3
fi
os_major="$(/usr/bin/sw_vers -productVersion | /usr/bin/cut -d. -f1)"
if [ "$os_major" -lt 13 ]; then
  echo '当前候选的媒体运行库需要 macOS 13 或更新版本' >&2; exit 3
fi
py="${CHATKIT_PYTHON:-}"
if [ -z "$py" ]; then py="$(command -v python3.11 || true)"; fi
if [ -z "$py" ] || [ ! -x "$py" ]; then
  echo '需要原生 Python 3.11；请让 Agent 按 docs/INSTALL.md 准备环境' >&2; exit 4
fi
if ! "$py" -c 'import platform,sys; sys.exit(0 if sys.version_info[:2] == (3,11) and platform.machine() == "arm64" else 1)'; then
  echo '解释器必须是原生 arm64 Python 3.11' >&2; exit 4
fi
for rel in requirements-desktop.txt scripts/multimodal/launcher.py scripts/multimodal/build_desktop.py; do
  [ -f "$ROOT/$rel" ] || { echo "源码不完整：$rel" >&2; exit 5; }
done
app="$app_dir/WCE.app"
if [ -e "$app" ] || [ -L "$app" ]; then
  echo "已有应用，未覆盖：$app" >&2; exit 6
fi
echo 'OK: macOS 13+ arm64 / Python 3.11 / source ready'
[ "$check_only" -eq 0 ] || exit 0
cd "$ROOT"
if [ -L .venv ]; then echo '拒绝使用符号链接虚拟环境' >&2; exit 7; fi
if [ ! -x .venv/bin/python ]; then "$py" -m venv .venv; fi
.venv/bin/python -c 'import platform,sys; assert sys.version_info[:2] == (3,11) and platform.machine() == "arm64", "虚拟环境不兼容"'
.venv/bin/python -m pip install --disable-pip-version-check -r requirements-desktop.txt PyInstaller==6.22.3
mkdir -p .build
build="$(mktemp -d "$ROOT/.build/install.XXXXXX")/desktop"
.venv/bin/python scripts/multimodal/build_desktop.py --output "$build"
[ -d "$build/WCE.app" ] || { echo '未生成 App，未安装' >&2; exit 8; }
mkdir -p "$app_dir"
stage="$(mktemp -d "$app_dir/.wechat-chatkit.XXXXXX")"
/usr/bin/ditto "$build/WCE.app" "$stage/WCE.app"
/bin/mv -n "$stage/WCE.app" "$app_dir/"
if [ -d "$stage/WCE.app" ]; then echo '目标已存在，停止安装且未覆盖' >&2; exit 6; fi
rmdir "$stage"
echo "安装完成：$app"
if [ "$no_open" -eq 0 ]; then /usr/bin/open "$app"; fi
