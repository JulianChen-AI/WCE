# Mac 安装

面向 Apple Silicon（M 系列）Mac，要求 macOS 13 或更新版本。当前走源码构建，不要求用户自己操作命令；可把仓库链接和本说明交给编程 Agent。

## 给 Agent

1. 检查源码来源、`LICENSE` 和第三方声明。只在有权使用的设备上安装。
2. 确认 macOS、原生 arm64 和 Python 3.11。缺 Python 时，优先使用用户已安装的包管理器；如已有 uv，可运行 `uv python install 3.11`，再把 `uv python find 3.11` 返回的解释器路径设为 `CHATKIT_PYTHON`。不要擅自安装包管理器或索取账号密码。
3. 在仓库根目录先运行 `bash install.command --check`。检查不安装依赖、不创建环境、不改用户配置。
4. 运行 `bash install.command`。它创建仓库内独立虚拟环境，联网下载 Python 依赖，在本机构建 App，安装到 `~/Applications/WeChatChatkit.app` 并打开。全程不需要 sudo。
5. 若发现已有同名 App，停止并告知用户；脚本不会覆盖。需要并存时，用 `--app-dir` 指定新的绝对目录。
6. 首次配置打开后，说明“安装完成，读取尚未配置”。让本人确认系统权限，并通过本地界面导入已有密钥；不要让用户把密钥贴进聊天，也不要上传数据库或档案。

可选：`--no-open` 只安装不启动；`--app-dir /absolute/path` 指定应用目录。若解释器不在 PATH，设置 `CHATKIT_PYTHON=/absolute/path/to/python3.11`。

## 遇到阻碍

- 网络或构建失败：保留错误输出，解决后重跑。未成功构建不会安装半成品。
- 系统权限弹窗：由用户确认，不关闭 SIP、Gatekeeper，不移除隔离标记，不修改或重签名微信。
- 没有数据库密钥：停止在首次配置页。安装程序不能替代密钥获取。
- 只有 Intel Mac、Windows 或不兼容的微信版本：不要宣称支持。

## 数据与卸载

用户配置、密钥与默认档案在 `~/Library/Application Support/WeChatChatkit`；模型首次使用时需联网下载。安装不会导入开发者配置或读取其聊天。

卸载时由用户确认移除自己安装的 App。聊天档案和密钥目录默认保留，不自动删除。候选版尚未完成独立干净 Mac 的端到端验证；本机构建成功不等于签名、公证或所有版本兼容。
