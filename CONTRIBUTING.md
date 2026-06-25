# 参与贡献

感谢你愿意为万能导 Wandao 做贡献。

万能导的目标是减少用户手动复制、整理、迁移知识库文档的重复劳动。欢迎提交 Bug 修复、平台适配、导入导出效果优化、文档补充和界面体验改进。

## 贡献前请先了解

- 请只围绕用户有权限访问的内容做自动化整理，不要提交绕过登录、绕过权限、规避平台访问控制的实现。
- 请不要在 Issue、PR、截图或测试文件里提交 Cookie、账号密码、App Secret、Token 等敏感信息。
- 涉及平台导出或导入时，请尽量说明测试平台、入口类型、是否包含图片/附件/多层目录。
- 大功能建议先开 Issue 讨论，避免实现方向和项目定位偏离。

## 本地开发

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

## 推荐检查

提交 PR 前建议至少运行：

```powershell
python -m py_compile wandao.py export_zsxq.py export_yuque.py export_feishu.py export_aliyun_thoughts.py export_yinxiang.py import_feishu.py import_yuque.py
node --check wandao_electron\main.js
node --check wandao_electron\renderer\app.js
```

如果只改文档，可以说明没有运行代码检查。

## PR 建议

一个 PR 尽量只解决一个问题，例如：

- 修复某个平台的图片导出
- 增加一个平台的目录读取
- 优化桌面端某个表单
- 补充一段使用教程

请在 PR 描述里写清楚：

- 改了什么
- 为什么要改
- 怎么测试
- 是否涉及用户凭证、登录态或平台权限

## 代码风格

- Python 代码尽量保持标准库优先，避免为了小功能引入重量依赖。
- UI 改动要注意小窗口可滚动、按钮含义清楚、日志能帮助用户定位问题。
- 导出/导入逻辑要尽量保留目录结构、图片、附件和正文格式。
- 错误提示尽量说人话，告诉用户下一步该怎么做。

## 合规说明

万能导是本地自动化工具，用来帮助用户整理自己有权限访问的内容。请不要提交任何用于未授权访问、绕过权限控制、破解平台限制的代码或说明。

更多说明见 [docs/合规说明.md](docs/合规说明.md)。
