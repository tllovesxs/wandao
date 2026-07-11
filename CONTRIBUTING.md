# 参与贡献

感谢你愿意为万能导 Wandao 做贡献。

万能导的目标是减少用户手动复制、整理、备份和迁移知识库文档的重复劳动。欢迎提交 Bug 修复、平台适配、导入导出效果优化、文档教程、UI 体验优化和 Provider 共创插件。

为了避免多人重复开发同一个平台或同一个问题，万能导采用：

```text
先搜索 -> 提 Issue -> 认领 -> 开发 -> 提 PR -> Review -> 合并
```

文档错字、轻量说明补充、小范围 UI 文案可以直接 PR。新增平台、大功能、复杂 Bug 修复请先提 Issue 并完成认领。

## 共创流程

### 1. 先搜索

开始开发前，请先搜索已有 Issue 和 PR：

- 搜平台名：例如 `语雀`、`飞书`、`OneNote`、`为知笔记`。
- 搜功能名：例如 `导入`、`导出`、`图片`、`目录结构`、`登录失败`。
- 搜报错关键词：例如 `remote debugging port`、`Access denied`、`HTTP 429`。

如果已经有人在做，请在现有 Issue 下补充信息或协助测试，不要重复开工。

### 2. 提 Issue

以下情况请先提 Issue：

- 新增一个平台的导入或导出。
- 给已有平台增加图片、附件、目录、断点续跑、失败重试等能力。
- 修复复杂 Bug，尤其是登录、权限、接口、目录结构和图片问题。
- 修改 Provider 架构、任务中心、打包发布、主界面等影响范围较大的内容。

Issue 请尽量写清楚：

- 平台名称。
- 要做导出、导入、教程，还是 Bug 修复。
- 是否已搜索过重复 Issue/PR。
- 是否愿意认领开发。
- 已知参考资料、样例结构或测试方式。

### 3. 认领 Issue

如果你准备开发某个 Issue，请在 Issue 下评论：

```text
我来认领这个问题，预计 X 天内提交 Draft PR。
```

维护者确认后会添加 `status:claimed` 或在评论中确认认领。认领后，其他贡献者请先沟通再做同类实现。

认领规则：

- 认领后建议 7 天内提交 Draft PR 或说明进展。
- 7 天没有进展，维护者可以释放认领，让其他人接手。
- 如果你暂时做不下去，可以主动评论“释放认领”。
- 同一个平台可以拆多个 Issue，例如“语雀导出”“语雀导入”“语雀图片修复”，不要把所有事情塞进一个 Issue。

### 4. 开发和 Draft PR

建议尽早开 Draft PR，让大家看到你正在做，避免重复劳动。

分支命名建议：

```text
feat/yuque-import-images
feat/new-provider-wiz
fix/zsxq-login-browser
docs/notion-guide
```

一个 PR 尽量只解决一个问题。不要把多个平台、UI 大改、文档重写混在同一个 PR 里。

### 5. PR Review 和合并

PR 请关联 Issue，例如：

```text
Closes #12
```

PR 描述必须说明：

- 改了什么。
- 为什么要改。
- 怎么测试。
- 是否影响已有平台。
- 是否涉及用户凭证、登录态、平台权限或 API。
- 是否支持目录结构、图片、附件、批量、断点续跑和失败重试。

维护者会根据影响范围做 Review。复杂平台可能需要补充测试样例、截图、导入导出结果或失败报告。

## 新平台共创规范

新增平台统一优先使用在线 Plugin v1：

- 平台插件放在 `plugins/<plugin-id>/`，一个插件可以包含导出、导入、教程和多个 Provider。
- 标准平台优先使用插件内 Provider v1 的标准 UI：`fields`、`actions`、`toc`、`capabilities`。
- 复杂平台可以在同一个插件里拆成多个 Provider；标准 UI 不够时，再声明沙箱自定义 UI。
- 教程型平台也建议做成插件，插件内可以只有教程 Provider 和 README。
- 旧 `providers/` 文件型 Provider 只作为兼容维护、迁移参考或已有功能修复路径，不再作为新增平台首选入口。

详细规范见：

- [docs/在线插件开发与发布.md](docs/在线插件开发与发布.md)
- [docs/共创流程.md](docs/共创流程.md)
- [docs/Provider接入说明.md](docs/Provider接入说明.md)

新增平台建议按这个顺序推进：

1. 先写清楚平台限制、登录方式、权限边界和使用教程。
2. 在 `plugins/<plugin-id>/` 跑通最小导入或导出能力。
3. 补目录结构。
4. 补图片和附件。
5. 补失败报告、重试和断点续跑。
6. 提升 `plugin.json.version`，补充真实测试结果和已知限制。

不要把尚未实现的能力写成已支持。

### 发布等级：合并不等于稳定发布

新插件即使通过 CI 并被合并，也会默认进入 `experimental` 实验库；这不会阻止共创、PR 预览或源码测试。实验插件会在插件中心正常显示和搜索，但会带有明确实验性标记，并应说明已知限制。

只有维护者批准后，插件才会进入 `plugins/release-policy.json` 的 `stable` 列表并进入默认官方插件库。申请稳定发布时，请额外提交：脱敏样例输入/输出、目录/图片/附件与失败场景证据、真实人工验证记录、已知限制、恢复与报告验证结果。不要为了通过稳定审核而提交真实凭证或私有内容。

`CODEOWNERS` 已把发布策略和插件目录指定给维护者审核；维护者还需要在 GitHub 的 `main` 分支保护规则中启用“Require review from Code Owners”，否则 GitHub 不会实际阻止绕过审核的合并。

## 本地开发

Windows：

```powershell
git clone https://github.com/tllovesxs/wandao.git
cd wandao
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install -r requirements.txt
cd wandao_electron
npm install
npm start
```

macOS/Linux：

```bash
git clone https://github.com/tllovesxs/wandao.git
cd wandao
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
cd wandao_electron
npm install
npm start
```

也可以直接用项目根目录的一键启动脚本：

```powershell
.\start-wandao.cmd
```

```bash
./start-wandao.sh
```

## 推荐检查

提交 PR 前建议优先运行统一质量检查：

```powershell
python scripts\quality_check.py
```

这会检查在线插件、Provider 配置、Python 语法、单元测试和 Electron JS 语法。只改在线插件时，也可以单独运行：

```powershell
node scripts\validate_plugins.js
node --test tests_js/plugin_manager.test.js
```

只维护旧 `providers/` 文件型 Provider 或公告索引时，可以单独运行：

```powershell
python scripts\validate_providers.py
```

如果只改文档，可以在 PR 中说明没有运行代码检查。

## Plugin PR 检查

新平台优先使用可独立发布的 Plugin v1。完整目录、权限、签名、版本和发布流程见 [在线插件开发与发布](docs/在线插件开发与发布.md)。一个插件可以包含多个 Provider，复杂平台不需要把导入和导出拆成两个安装包。

插件 PR 还必须满足：

- 一个 PR 只负责一个 `plugins/<id>`；批量迁移必须由维护者确认并添加 `plugin-batch` 标签。
- 修改插件业务代码时同步提升 `plugin.json.version`。
- 插件不能导入其他平台的业务脚本；公共逻辑应进入稳定 SDK。
- PR 预览包使用临时密钥，只用于流水线验证；只有合并后流水线生成的正式签名包能被普通用户安装。
- 新插件默认发布为 `experimental`；修改 `plugins/release-policy.json` 或申请 `stable` 必须由维护者审核，且不会作为普通插件 PR 的前置条件。
- 已运行 `node scripts\validate_plugins.js` 和 `node --test tests_js/plugin_manager.test.js`，或说明无法运行原因。

## Provider v1 兼容 PR 检查

如果 PR 涉及旧 `providers/` 兼容目录，请确认：

- 已提供 `provider.json`。
- 已提供 `README.md` 使用说明。
- 已声明真实支持的 `capabilities`。
- 已声明 `trustLevel` 和 `status`。
- 已说明登录方式、权限要求和凭证保存方式。
- 已说明是否支持目录结构、图片、附件、批量、断点续跑和失败重试。
- 已说明真实导入/导出测试结果，或说明暂时无法测试的原因。
- 如果参考第三方项目，已说明来源和许可证。
- 未把尚未实现的能力写成已支持。
- 已通过 `python scripts\validate_providers.py`。

## 敏感信息和合规要求

请不要在 Issue、PR、截图、日志或测试文件里提交：

- Cookie。
- 账号密码。
- App Secret。
- Token。
- API Key。
- 私人知识库内容。
- 未授权平台数据。

万能导只用于整理用户自己有权限访问的内容。请不要提交任何绕过登录、绕过权限校验、破解平台限制或批量抓取未授权内容的代码和说明。

更多说明见 [docs/合规说明.md](docs/合规说明.md)。

## 开源协议

万能导采用 AGPL-3.0-only 协议开源。提交 PR 即表示你同意将贡献内容按本项目协议授权。

如果贡献内容参考了第三方项目，请在 PR 中说明来源和许可证。不要直接复制许可证不兼容的代码、图片或文档；如果只是借鉴思路，也请写清楚实现方式是自己完成的。
