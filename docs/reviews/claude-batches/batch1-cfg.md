# 配置修改实施报告

基线仓库：`/tmp/w/cfg`（干净检出，无 `.claude/`、`crawl4ai-cache/`）

## 一、已实施

| # | 编号 | 文件 | 做了什么 |
|---|---|---|---|
| 1 | P1e | `.gitignore` | 追加 `out/`、`crawl4ai-cache/`、`.claude/`，另补 `.worktrees/`、`/dist-*/`、`/release-*/`；每条前一行加中文注释 |
| 2 | （新增） | `.gitattributes` | 新建。`* text=auto eol=lf` 兜底；`.py/.js/.json/.md/.yml/.css/.html/.sh` 显式 `text eol=lf`；`.cmd/.ps1/.bat` `text eol=crlf`；`.png/.jpg/.ico/.zip/.exe/.wandao-plugin` 标 `binary`；顶部注释说明作用与需跑一次 `git add --renormalize .` |
| 3 | H10 | `wandao_electron/package.json` | `build.compression: "maximum"`（顶层）、`build.win.artifactName`、`build.nsis` 补 `perMachine: false` / `artifactName` / `uninstallDisplayName`，`shortcutName` → `"万能导 Wandao"` |
| 4 | P1d | `scripts/quality_check.py` | tests_js 的 20 条硬编码清单删除，改成 `tests_js/*.test.js` 目录级发现 |
| 5 | （新增） | `.github/FUNDING.yml` | 新建。爱发电 custom 链接占位 + 注释说明开通 GitHub Sponsors 后可加 `github: [tllovesxs]` |

实际改动文件共 5 个，全部在授权清单内（用 mtime 比对确认，无越界文件）。

## 二、已跳过

| 编号 | 文件 | 原因 |
|---|---|---|
| T2 | `plugins/web-crawl/plugin.json` | **该路径在当前基线不存在**。`plugins/` 下只有 aliyun_thoughts、dingtalk、feishu、ima、notion、obsidian、onenote、wiz、wps、xiliu、yinxiang、youdao、yuque、zsxq，无 web-crawl；全仓 grep `crawl4ai` 也零命中。web-crawl 插件尚未合进 main，license 改 `AGPL-3.0-only` 无处可改，已跳过。 |

## 三、node 测试数变化

| | 测试文件数 | 测试数 | 结果 |
|---|---|---|---|
| 基线 | 20（硬编码清单） | **105** | pass 105 / fail 0 |
| 改后 | 26 运行 + 1 跳过 = 27 全发现 | **127** | pass 127 / fail 0 |

`127 ≥ 105`，净增 22 个测试，正好等于此前漏跑的 6 个文件单独运行的测试数（`guide_assets`、`guide_ipc`、`guide_markdown`、`release_diagnostics_and_size`、`scan_stdout_relay`、`toc_browser` = 22）。目录级发现覆盖 27/27 个 `tests_js/*.test.js`。

门禁末行输出：

```
Skipping tests_js/yuque_converter.test.js: wandao_electron/node_modules/electron/dist is not installed.
Node syntax check passed (25 files, 26 test files).
Git diff whitespace check passed.
Quality check passed.
```

## 四、JSON 合法性

```
package.json OK
web-crawl 不存在,已跳过
```

`wandao_electron/package.json` 另做了字段级比对：`version`（1.3.9）、`dependencies`、`devDependencies`、`scripts` 及全部非 build 顶层字段逐一确认未变；`build` 下只新增 `compression`，只改动 `win` 与 `nsis`。

## 五、两处需要注意的偏离

### 1. `yuque_converter.test.js` 的 Electron 守卫改放在 `quality_check.py` 里

sol-security.md 的 P1d 第三步要求给 `tests_js/yuque_converter.test.js` 加 skip 守卫（该测试依赖真实 Electron 二进制，本机未安装 `wandao_electron/node_modules/electron`，纳入后必红）。但该文件不在授权清单内，未改动。

替代做法：在 `quality_check.py` 的 `iter_node_test_files()` 里做同样的守卫——按**内容**判断（源码里出现 `node_modules/electron/dist` 路径构造）而非写死文件名，且仅在 Electron 未安装时跳过。装了 Electron 就会照常运行。这样不必碰测试文件，也不会退化成新的硬编码清单。

### 2. 两个"自注册"测试断言迫使本文件保留 2 条字面量（方案文档未预见）

删掉硬编码清单后出现 2 个失败，方案文档没有提到：

- `tests_js/import_write_guidance.test.js:62` → `assert.match(qualityCheckSource, /"tests_js\/import_write_guidance\.test\.js"/)`
- `tests_js/toc_rendering.test.js:146` → `assert.match(qualityCheckSource, /"tests_js\/toc_rendering\.test\.js"/)`

这两个测试把 `quality_check.py` 当纯文本读，断言里写死了自己的文件名，用来证明"我已被门禁跑到"。改成目录级发现后字面量消失，断言直接失败。两个测试文件都不在授权清单内，无法修改。

处理：在 `quality_check.py` 里保留 `SELF_REGISTERING_TEST_FILES` 常量（仅这 2 条），并在 `run_node_checks()` 中加一致性检查——若目录级发现没覆盖到其中任何一条就 `SystemExit` 报错。因此它不是测试清单的来源（清单仍是 glob），只是让那两条历史断言继续成立，且不会悄悄失效。

**建议后续**（需授权改 `tests_js/`）：把这两条断言改成检查目录级发现机制本身，之后即可删除 `SELF_REGISTERING_TEST_FILES`。

## 六、其他记录

- `NODE_CHECK_FILES`（`node --check` 的 26 条清单）**未改动**——本次任务只要求改 tests_js 清单。sol-security.md 的 P1d 同时指出它也漂移了（漏 `plugin_format.js`、`scan_stdout_relay.js`、`guide_assets.js`、`renderer/form_drafts.js`、`renderer/toc_browser.js`，并混入一个恒被跳过的 `.py`），留待后续。
- H10 的 `nsis.include: "build/installer.nsh"` **未添加**：`wandao_electron/build/` 不存在（H6 未做），文档明确说此时应删掉该行，否则打包报文件不存在。H10 里 `allowElevation`、`menuCategory`、各 `*Icon`、`deleteAppDataOnUninstall`、`differentialPackage` 不在本次指定的字段范围内，未添加；`win.target` 结构也未改动。
- `.gitignore` 的注释一律独占一行：gitignore 语法中 `#` 只在行首才是注释，写成行尾注释会让整行变成匹配不到任何东西的字面量 pattern。
- `out/` 确认是真实产物目录——它由 Python 测试套件在跑基线时生成（`out/Doc 1.md` 等），正是应当忽略的对象。
