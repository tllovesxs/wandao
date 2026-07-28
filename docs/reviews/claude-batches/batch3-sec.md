# B3-SEC：S1 / S3 修复报告

分支 `b3/sec`，只改动 `wandao_electron/main.js` 与新建 `tests_js/main_path_guards.test.js`。
S2、S4 本次未做。

| 项 | 值 |
| --- | --- |
| S1 commit | `00289a6e24259e1e936f528f0d845a9b43bd43c2`（tag `sol/S1`） |
| S3 commit | `23fdcda7bb26ea3bce19e4d15936f5b0a1bb05a6`（tag `sol/S3`） |
| `node --check wandao_electron/main.js` | 通过 |
| `python3 scripts/quality_check.py` | `Quality check passed.`（25 files, 31 test files，198 → 223 条断言全绿） |
| 新增测试 | `tests_js/main_path_guards.test.js`，25 个用例，已被 quality_check 的目录级发现自动纳入门禁 |

---

## 一、正常路径清单（白名单依据 + 测试用例来源）

### 1.1 `openPath()` —— 渲染进程实际会打开的每一种路径

| # | 调用点 | 传入的是什么 | 典型取值 | 改后行为 |
| --- | --- | --- | --- | --- |
| 1 | `renderer/app.js:1318` `openTaskArtifact(task,'output')` | 任务结果 JSON 的 `report.output` | 用户输出目录，任意盘符，如 `D:\导出\飞书` | **目录 → 直接打开**（无根目录限制） |
| 2 | `renderer/app.js:1318` `openTaskArtifact(task,'report')` | 任务结果 JSON 的 `report.reportFile` | `D:\导出\飞书\00-导出报告.json`、`00-导出报告.md` | **`.json`/`.md` → 直接打开**（无根目录限制） |
| 3 | `renderer/app.js:4331` `${prefix}-open-dir` | 导出表单 `${prefix}-output` 输入框的值 | 任意用户目录 | 目录 → 直接打开 |
| 4 | `renderer/app.js:4902` `ima-import-open-dir` | `ima-import-source` 输入框 | 任意本地 Markdown 目录 | 目录 → 直接打开 |
| 5 | `renderer/app.js:5162` `yuque-import-open-report` | `latestYuqueImportReportFile` 或 `${yuque-import-output}\00-语雀导入报告.json` | `D:\md\00-语雀导入报告.json` | `.json` → 直接打开 |
| 6 | `renderer/app.js:5332` `yinxiang-import-open-dir` | `yinxiang-import-source` 输入框 | 任意目录 | 目录 → 直接打开 |
| 7 | `renderer/app.js:6704` `feishu-import-open-dir` | `feishu-import-source` 输入框 | 任意目录 | 目录 → 直接打开 |

报告文件的实际扩展名已核对：`wandao_core/report.py`、`plugins/*/backend/*.py`、
`providers/_template_import/actions.py` 产出的 `reportFile` 全部是 `.json`（个别是 `.md`），
没有 `.html` / `.csv`。

### 1.2 `writeFile()` —— 渲染进程实际会写的全部路径

全仓库（`wandao_electron/**` 的 `.js` 与 `.html`）只有三处 `electronAPI.writeFile`：

| # | 调用点 | 目标路径 |
| --- | --- | --- |
| 1 | `renderer/app.js:1138` `saveTaskHistory()` ← `taskHistoryPath()`:853 | `${userData}/task_history.json` |
| 2 | `renderer/app.js:6359` `saveFeishuImportConfigFromForm()` ← `feishuImportConfigPath()`:6133 | `${userData}/plugin-data/feishu/feishu_import_config.json` |
| 3 | `renderer/app.js:6312` `readJsonConfigWithMigration()` 写 canonicalPath；两个调用方是 ima(:4522) 与飞书(:6325) | `${userData}/plugin-data/ima/ima_config.json`、同上飞书路径 |

其余相关但**不经过 write-file** 的：

- `settings.json` 走 `save-app-settings`（主进程自己写），不受影响。
- `plugin-data/yuque/.yuque_import_config.json` 由 Python 侧写；仍在白名单内（`plugin-data/` 前缀），以后挪到 renderer 也不会当场失效。
- `projectRoot` 兜底路径 `.ima_config.json` / `.feishu_import_config.json`：`write-file` 不传 `allowProjectRoot`，改前改后都写不进去。
- `${userData}/ima_config.json`、`${userData}/feishu_import_config.json`、`${userData}/.feishu_import_config.json`、`${userData}/.yuque_import_config.json`：只出现在 **legacy 读取兜底清单** 里，只读不写；`read-file`/`file-exists` 未改动，旧配置迁移照常。
- `recent inputs`、`form drafts` 都在 localStorage，不落盘。

---

## 二、实现要点与相对方案文档的偏差（**重要**）

### S1 —— 与 `sol-security.md` 的差异

方案文档的写法是「不在 `userData` / `exports` / 本次会话选过的目录里 → 直接报错拒绝」。
**这一条会当场打断两个高频功能**，所以没有照抄：

1. 用户的输出目录绝大多数在 `D:\` `E:\` 等非 `userData` 位置；
2. `userOpenableRoots` 只在本次会话内有效，**重启后打开任务历史里的旧任务**，用户没有重新走过「选择目录」对话框，`打开报告` / `打开输出目录` 就会全部报错。

改用三层判定（`resolveOpenablePath()`）：

| 输入 | 结果 |
| --- | --- |
| UNC / 设备命名空间（`\\server\share`、`//server/share`、`\\?\`、`\\.\`），原串、`path.resolve` 后、`realpath` 后各查一次 | **拒绝**，`不允许打开网络共享路径。` |
| 不存在 | 拒绝，`文件或目录不存在。` |
| 目录 | **直接 `shell.openPath`**，不做根目录限制（打开目录只会拉起资源管理器，不可能执行代码） |
| 文件，扩展名在 `INERT_OPENABLE_EXTENSIONS`（`.md .markdown .txt .log .json .jsonl .ndjson .csv .tsv .yaml .yml .xml .ini .toml` + 常见图片） | **直接 `shell.openPath`**，不做根目录限制 |
| 文件，扩展名在 `ROOTED_OPENABLE_EXTENSIONS`（`.html .htm .pdf .svg .doc(x) .xls(x) .ppt(x)`）且在 `openableRoots()` 内 | 直接打开 |
| 其余一切（`.exe .bat .cmd .lnk .ps1 .vbs .js .msi .scr .py .dll …`，以及不在根目录内的 `.html`） | **`shell.showItemInFolder` 只做定位，永不执行**，返回 `{success:true, revealed:true}` |

`openableRoots()` = `userData` + `pythonLibraryDir()` + `pythonLibraryDir()/exports` +
`downloads` / `documents` / `desktop` + 本次会话经 `select-directory` / `select-file` / `save-file`
登记的目录（`rememberUserOpenableRoot()`，选中文件时登记其所在目录，UNC 不登记）。

**攻击面结论**：方案文档要挡的 `\\attacker\share\x.exe` 被第一条挡死；`userData\evil.exe`
按文档预期只做资源管理器定位。相比文档多放行的是「非白名单目录下的目录与惰性文本文件」——
这两类都不构成执行原语。**少拦的那一点是：攻击者可以让用户在任意位置的资源管理器里看到某个
文件（不执行），或打开一个任意位置的 `.json`/`.md`/`.csv`。** `.csv` 存在 Excel 公式注入的
理论路径（需用户再点两次警告框），这是本次刻意接受的残余风险。

### S3 —— 与方案文档一致，仅两处加固

- 白名单：`task_history.json` + `plugin-data/**`，与上面 1.2 的三条真实写入完全对齐。
- 受保护目录报错清单从 `plugins/ providers/ runtime/` 扩到含 `python-runtime/`（只影响报错文案，兜底 throw 本来就会拦）。
- **Windows 上比较前统一 `toLowerCase()`**：文档原写法里 `Plugins\state.json` 不会命中「受保护目录」分支（虽然仍会被最后的兜底 throw 拦下，但报错文案会误导）。
- `read-file` / `file-exists` 一字未动，仍带 `allowProjectRoot: true`。

---

## 三、待人工确认的白名单条目

1. **`ROOTED_OPENABLE_EXTENSIONS` 里的 `.html`**：现在只在 `userData` / 程序目录 / 下载·文档·桌面 /
   本次会话选过的目录里才直接打开，其他位置降级为定位。如果确认没有任何插件会产出 HTML 报告，
   可以把它整个删掉（更严）；如果有插件确实产出 HTML 报告且落在用户输出目录，**重启后第一次点
   「打开报告」会变成资源管理器定位**——届时需要改成 `INERT` 或者把 openable roots 持久化。
   当前仓库内没有找到产出 `.html` 报告的代码路径。
2. **`INERT_OPENABLE_EXTENSIONS` 里的 `.csv` / `.tsv`**：Excel 公式注入的理论入口。若能确认没有
   CSV 报告需要一键打开，建议挪进 `ROOTED_*`。
3. **`openableRoots()` 里的 `downloads` / `documents` / `desktop`**：为了让「HTML/PDF/Office 类
   报告放在常见用户目录时也能直接打开」而加的宽松项，不是必需。若要更严可以删掉这三行，
   代价是这三个目录下的 `.pdf`/`.html` 会降级为定位。
4. **`userOpenableRoots` 不持久化**（跟随进程生命周期）。目前只影响 `ROOTED_*` 那批扩展名，
   影响面很小，因此没有引入新的落盘文件。如果第 1 条最终需要放宽，再考虑持久化。
5. **S3 的 `plugin-data/` 允许任意深度、任意插件目录**：新插件不用改代码就能存配置。若希望
   收窄到「已安装插件 id」需要主进程感知插件清单，本次未做。

---

## 四、作者需要在 Windows 上手动验证的场景

优先级从高到低。前四条是「正常功能不能失效」的回归，必须全过。

1. **打开输出目录（高频）**：跑一次导出，输出目录选在非系统盘（如 `D:\导出\飞书`），
   点「打开输出目录」→ 资源管理器应正常打开该目录。**关掉应用重开，再从任务历史里点同一条
   任务的「打开输出目录」，仍应正常打开**（这是会话级白名单最容易踩雷的地方，已按目录不限根
   目录处理，预期通过）。
2. **打开报告（高频）**：同一次导出，点「打开报告」→ `00-导出报告.json` 应被默认程序打开。
   **重启后从任务历史里再点一次，仍应打开而不是只弹资源管理器。**
3. **语雀导入报告**：跑一次语雀导入，点「打开报告」→ `00-语雀导入报告.json` 正常打开。
4. **S3 正常写入**：设置里保存飞书导入 API 配置（App ID / Secret）→ 提示保存成功，
   `%APPDATA%\wandao\plugin-data\feishu\feishu_import_config.json` 内容更新；跑一次任务，
   `%APPDATA%\wandao\task_history.json` 落盘更新。再验证 **ima 旧配置迁移**：把旧的
   `%APPDATA%\wandao\ima_config.json` 放回去，启动后应自动迁移到
   `plugin-data\ima\ima_config.json` 且日志里出现「已将旧版 ima API 配置迁移到统一配置目录」。
5. **S1 攻击回归**：编辑 `%APPDATA%\wandao\task_history.json`，把某条的 `report.reportFile`
   改成 `\\127.0.0.1\share\x.exe`，重启后点「打开报告」→ 应返回
   `不允许打开网络共享路径。`；改成 `%APPDATA%\wandao\evil.exe`（真放一个可执行文件）→
   应只弹出资源管理器定位、**不执行**。
6. **S1 junction 回归**（可选）：`mklink /J %APPDATA%\wandao\link \\127.0.0.1\share`，
   把 `report.output` 指向 `link` → 应报「不允许打开网络共享路径。」。
7. **S3 攻击回归**：DevTools 里逐条应返回 `{success:false}`：
   ```js
   const { userData } = await window.electronAPI.getAppPath();
   await window.electronAPI.writeFile(`${userData}/providers/evil/x.py`, 'x');
   await window.electronAPI.writeFile(`${userData}/plugins/state.json`, '{}');
   await window.electronAPI.writeFile(`${userData}/settings.json`, '{}');
   await window.electronAPI.writeFile(`${userData}/plugin-data/../plugins/state.json`, '{}');
   await window.electronAPI.writeFile(`${userData}/Plugins/state.json`, '{}');
   ```
8. **对话框登记链路**：「选择目录」选一个 `D:\导出`，把一个 `.pdf` 放进去，然后让
   `report.reportFile` 指向它 → 应直接打开（验证 `rememberUserOpenableRoot` 生效）；
   重启后同样操作应变成资源管理器定位（会话级白名单的已知行为，见第三节第 4 条）。
