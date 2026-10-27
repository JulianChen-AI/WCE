# 第三方声明

本项目原创部分的个人非商业许可见根目录 `LICENSE`。下列第三方代码保留原版权及 MIT 许可证，根目录限制不覆盖这些独立授权。

| 来源 | 用途 | 原许可证 |
|---|---|---|
| wuyuxiangX/wechat-export-macos | 消息解析与导出 | `vendor/wechat-export-macos/LICENSE` |
| qwe11223/wechat-exporter-mac | 媒体解码与数据模型 | `vendor/wechat-exporter-mac/LICENSE` |

固定源码版本见 `scripts/multimodal/upstream-lock.json`。

运行依赖包括 SQLCipher/sqlcipher3、zstandard、PyCryptodome、silk-python、PyAV、Pillow、faster-whisper 及其依赖。它们适用各自许可，不因本项目许可而改变。PyInstaller 使用其自身许可及打包例外。

发布二进制安装包前，仍需核对完整传递依赖、保留版权与许可，并审查其中的第三方义务；本声明不是这项审查已经完成的证明。

不包含密钥获取工具、真实聊天、个人配置或转写模型。
