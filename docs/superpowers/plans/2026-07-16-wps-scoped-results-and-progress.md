# WPS 专属结果卡与逐文件进度实施计划

> **执行范围：** 仅修改 WPS Provider 及其受 WPS 条件保护的 Electron 渲染代码；不改变主页、任务中心、其他平台或通用任务历史行为。

**目标：** 撤销全平台“最新任务结果”布局，把失败结果卡固定到 WPS Provider 页面内；同时让 WPS 导出按照现有结构化日志协议输出逐文件进度。

**设计依据：** `docs/superpowers/specs/2026-07-16-wps-scoped-results-and-progress-design.md`

**技术栈：** Electron renderer（HTML/CSS/JavaScript）、Node.js built-in test runner、Python `unittest`、Wandao Provider manifest、`wandao_core.browser.emit`。

## 文件边界

### 新增

- `tests_js/wps_result_scope.test.js`：只验证 WPS 专属结果卡的归属、按钮和登录操作顺序。

### 修改

- `wandao_electron/renderer/app.js`：在 `wps-export` Provider 表单内创建和更新结果卡；切换离开 WPS 后不再渲染。
- `wandao_electron/renderer/styles.css`：仅新增或调整以 WPS 专属容器/类名为范围的样式。
- `plugins/wps/backend/export_wps.py`：输出 WPS 文件开始、完成、失败及总进度结构化事件。
- `tests/test_wps_smartdoc.py`：覆盖 WPS 进度事件、间隔、跳过/失败推进与脱敏。

### 回退

- 完整回退提交 `812603d fix: improve WPS task result layout`，先移除全平台结果卡，再按上述边界重做其中仍需要的 WPS 登录顺序和 WPS 专属结果卡。

## Task 1：建立前端回归测试并验证 RED

1. 新建 `tests_js/wps_result_scope.test.js`。
2. 测试 `index.html` 不存在全局 `task-result-card`。
3. 测试 `renderManifestProviderForm` 只为 `provider.id === 'wps-export'` 生成 WPS 结果容器。
4. 测试 WPS 结果卡没有 `task-center` 和 `open-report` 操作。
5. 测试失败/部分完成的失败项可展开且默认展开。
6. 测试 WPS 登录、扫描、清理认证、导出操作保持要求的显示顺序。
7. 运行：
   `node --test tests_js/progress_visibility.test.js tests_js/wps_result_scope.test.js`
8. 确认新测试在当前全局实现上失败。

## Task 2：回退全平台结果卡并重做 WPS 专属 UI

1. 执行 `git revert 812603d`，留下清晰的回退提交。
2. 在 `renderManifestProviderForm(provider)` 内，仅对 `wps-export` 插入专属结果容器。
3. 将结果渲染入口限制为：当前 Provider 是 WPS、任务 Provider 是 WPS、任务为导出任务。
4. 保留 WPS 卡操作：重试失败项、打开输出、复制失败项、复制报告。
5. 删除 WPS 卡操作：查看任务中心、打开报告。
6. 恢复 WPS 登录确认操作顺序，但不修改其他 Provider 的操作排序。
7. 对 WPS 失败列表使用专属类名；失败或部分完成默认展开。
8. 运行前端定向测试，直到 GREEN。
9. 检查 `git diff`，确保任务中心通用历史代码没有被加入 WPS 特例。

## Task 3：建立后端进度测试并验证 RED

1. 在 `tests/test_wps_smartdoc.py` 构造少量成功、已存在跳过、checkpoint 跳过和失败节点。
2. patch `plugins.wps.backend.export_wps.emit` 收集结构化事件。
3. 断言存在 `task.started`、每文件 `document.export.started`、对应的 completed/failed，以及 `task.progress`。
4. 断言成功、跳过和失败都推进 `progress.current`。
5. 断言 `--progress-every` 控制中间进度，最后一项无条件输出。
6. 断言单文件失败后继续处理下一项。
7. 断言事件字段不包含 Cookie、认证内容或下载 URL。
8. 运行：
   `python -m unittest discover -s tests -p 'test_wps_*.py' -v`
9. 确认新增测试因当前后端没有 emit 事件而失败。

## Task 4：实现 WPS 逐文件结构化进度

1. 在 WPS 后端导入 `wandao_core.browser.emit`。
2. 让 `WPSExportTask` 保存当前 argparse `args`，仅用于 WPS emit 和停止检查。
3. 得到导出列表后发送 `task.started`，只带总数和输出目录。
4. 每个文件处理前发送 `document.export.started`。
5. 统一成功、目标已存在、checkpoint 已完成和失败路径，避免 `continue` 绕过进度统计。
6. 成功/跳过发送 `document.export.completed`；失败发送 `document.export.failed`，错误使用现有安全错误文本。
7. 按 `progress_every` 发送 `task.progress`，并保证最后一项发送。
8. 事件中不写 Cookie、认证文件、下载 URL 或原始响应。
9. 运行 WPS Python 测试直到 GREEN。

## Task 5：验证范围和回归

1. 运行：
   `node --test tests_js/progress_visibility.test.js tests_js/wps_result_scope.test.js`
2. 运行：
   `python -m unittest discover -s tests -p 'test_wps_*.py' -v`
3. 运行 Electron 检查：
   `Push-Location wandao_electron; npm run check; Pop-Location`
4. 运行相关 JS 测试集合（若耗时可接受则运行全部 `node --test tests_js/*.test.js`）。
5. 执行 `git fetch origin main`。
6. 对比：
   `git diff --name-only origin/main...HEAD`
   `git diff --name-only dbc3a0c..HEAD`
7. 逐段确认通用 Electron 改动都有 `wps-export` 条件保护；确认没有 WPS 以外插件目录变化。

## Task 6：本地提交与源码启动

1. 将最终实现和测试本地提交，不 push、不创建 Issue/PR。
2. 查询并停止该工作区旧 Electron 源码进程，避免重复实例。
3. 在 `wandao_electron` 目录执行 `npm start` 启动源码版本。
4. 向用户报告：改动范围、测试证据、GitHub 对比结果、本地提交和启动状态，以及仍需人工验证的 WPS 操作。
