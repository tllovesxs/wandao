# Wandao vX.Y.Z

用一两句话说明这个版本解决了什么问题，以及是否建议用户升级。

## 重点更新

- 从用户视角描述最重要的修复或功能，最多保留 5 条。
- 优先说明数据丢失、任务中断、登录失效、导入导出完整性等变化。
- 插件自身的更新请注明插件名称和版本，不与主程序更新混写。

**升级建议**：建议升级 / 可按需升级，并简要说明原因。

## 下载与安装

- Windows：下载并运行本版本的 `.exe` 安装包。
- macOS Apple Silicon：下载本版本的 `.zip`，解压后将 `Wandao.app` 拖入“应用程序”。

正式 Release 的 Windows 安装包必须通过 Authenticode 签名和可信时间戳校验；macOS 应用必须通过 Developer ID Application 签名、Apple 公证和 stapling 校验。如果干净系统仍显示未知发布者、应用已损坏或 Gatekeeper 阻止，不得指导用户执行 `xattr -cr` 绕过，应保留 draft 并修复发布链。

## 插件更新

- 如果本版本包含插件更新，请在“插件中心”点击“一键更新全部”。
- 如果不需要更新插件，写明“本版本无需额外更新插件”。

<details>
<summary>构建与验证信息</summary>

- 源码提交：`填写 commit`
- 质量检查：`填写 Python / Node.js 测试结果`
- 安装包冒烟：`填写插件、Provider 和可执行后端数量`
- Windows 签名：`填写 Authenticode 签名者、证书指纹和时间戳验证结果`
- macOS 签名/公证：`填写 codesign、spctl 和 stapler 验证结果`
- SHA256：`填写最终 Release 文件校验值`

</details>
