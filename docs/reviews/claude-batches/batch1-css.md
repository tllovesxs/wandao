# CSS 批次实施报告

范围：仅修改 `wandao_electron/renderer/styles.css`（共 125 行新增 / 82 行删除，`git diff --stat` 确认无第二个文件被改动）。
未执行任何 git 写操作（`add` / `commit` / `checkout`）。文件保持 LF + UTF-8。

## 已实施

| 编号 | 改了什么 | 定位方式 |
|---|---|---|
| A2 | `.home-hero` gap 30→24、整行删除 `min-height: 308px`、padding 34→20px；`.home-hero h3` margin-top 12→8、font-size `clamp(34px,4vw,49px)`→`clamp(26px,2.2vw,32px)`、line-height 1.04→1.12；`.home-hero-copy > p:not(.view-kicker)` margin-top 18→8、font-size 15→14、line-height 1.7→1.6；`.home-hero-actions` margin-top 28→14 | 搜索 `align-items: center;\n  gap: 30px;\n  min-height: 308px;` 等属性串（唯一匹配） |
| A3 | `@media (max-width: 1120px)` 内 `.home-hero` gap 28→0；`.knowledge-route` 由 `max-width: 680px` 改为 `display: none`，并加上方案给的中文注释 | 搜索 `grid-template-columns: 1fr;\n    gap: 28px;` + 相邻 `.knowledge-route` 块 |
| A4 | `.platform-card, .provider-action-card` min-height 220→160、padding 22px→16px 18px；`.card-action` margin-top 22→14；`.provider-action-card` min-height 204→160；`.provider-action-card > button` margin-top 20→12 | 搜索 4 个完整规则块 |
| A6 | `.nav-item` grid-template-columns 36px→32px、min-height 56→44、gap 12→10、margin `0 0 6px`→`0 0 4px`、padding `9px 12px`→`6px 10px`；`.sidebar-footnote` margin `20px 10px 0`→`12px 10px 0` | 搜索 `.nav-item {` 头部属性串、`.sidebar-footnote {` |
| E1 | `:root` `--focus` → `#1f6f2e`，新增 `--focus-inner: #ffffff`；`body[data-theme="dark"]` `--focus` → `#c2fa9e`，新增 `--focus-inner: #0f1210`；全局 `*:focus-visible` 组改为 `outline: 2px solid transparent` + `box-shadow: 0 0 0 2px var(--focus-inner), 0 0 0 5px var(--focus)`；`.form-group *:focus`、`.task-history-filter *:focus` 的 `0 0 0 4px var(--focus)` 换成同一双色环；`.plugin-search-row input:focus` 增加 `:focus-visible` 选择器并改用双色环 + `border-color: var(--focus)` | 搜索 `--focus: rgba(...)`、`[tabindex]:focus-visible {`、`box-shadow: 0 0 0 4px var(--focus);`、`.plugin-search-row input:focus {` |
| E2 | `.nav-copy small` `color: inherit`→`var(--shell-text-muted)`，`opacity: 0.7`→`1` | 搜索完整规则块 |
| E3 | `:root` / `body[data-theme="dark"]` 各新增 `--text-disabled` 与 `--surface-disabled`（放在 `--text-invert` 之后）；`button:disabled` 去 opacity 改实色（border-color/background/color/box-shadow:none）；`.nav-item:disabled:not(.active)` 去 opacity 改 `color: var(--text-disabled)` | 搜索 `--text-invert:` 行、`button:disabled {`、`.nav-item:disabled:not(.active) {` |
| E4 | `--text-3` `#666b65`→`#565b55`；`--warning` `#b86700`→`#8a4c00`（仅浅色主题）；placeholder `opacity: 0.85`→`1` | 搜索 token 行与 `::placeholder` 规则块 |
| E6 | `.custom-plugin-frame` `background: #fff`→`var(--surface-raise)`，新增 `border-radius: var(--r-md)` 与 `color-scheme: light dark` | 搜索完整规则块 |
| E7 | 7 处硬编码绿调 rgba 阴影换成 token：`.brand-logo` / `.nav-item.active` / `.app-main` / `.platform-card:hover` → `var(--shadow-soft)`；`.knowledge-route` / `.progress-section` → `var(--shadow-float)`；`.mode-button.active` → `var(--shadow-soft), inset 0 -2px 0 var(--brand-pressed)` | 逐条搜索完整 `box-shadow:` 声明串（各自唯一），并核对上方选择器与方案一致 |
| E8 | `.knowledge-route` / `.provider-mode-switcher` / `.action-section` 删除 `backdrop-filter: blur(...)`，`color-mix(... 88%/94%, transparent)` 背景改为实色 `var(--surface-raise)` | 搜索 `background: color-mix(...);` + 相邻 `backdrop-filter` 组合（全文已无 backdrop-filter 残留） |
| E9 | `.advanced-section` `overflow: hidden`→`isolation: isolate`；`.advanced-section summary` 加上半圆角；新增 `.advanced-section:not([open]) summary` 四角圆角；`.advanced-content` 加下半圆角 | 搜索 `.advanced-section {` 规则块、`.advanced-section summary {` 头部、`.advanced-section summary::after {`、`.advanced-content {` |
| E10 | `.task-result-summary` 与 `.task-result-document-note/.task-result-resource-note/.task-result-failures` 增加 `min-width: 0` + `overflow-wrap: anywhere` + `word-break: break-word`；`.task-result-failures ul` 增加 `list-style-position: outside`；新增 `.task-result-failures li { overflow-wrap: anywhere; }`；`.task-history-title` 增加 `overflow-wrap: anywhere` | 搜索各规则块 |
| E11 | font-weight 三档收敛：650→500（1 处）、720/740/750/760/780/800/900→700（共 28 处）。改后全文分布：400×1、500×1、700×38；`font: 700 21px/...` 与 `font: 700 22px/...` 两处简写按方案保持 700 不动 | 按 `font-weight: <值>;` 精确串全量替换，替换前逐值核对数量与方案清单一致（650×1、720×1、740×2、750×12、760×2、780×2、800×8、900×1） |
| E12 | 在 `@media (prefers-reduced-motion: reduce)` 块内部追加 `.progress-fill.indeterminate` 覆盖（width 100% / animation none / repeating-linear-gradient 条纹） | 搜索整个 media 块 |

## 已跳过

无。15 条全部实施，均只涉及 `styles.css`。

## 与方案文本的细微偏差（均为等价实现，非功能改动）

| 编号 | 偏差 |
|---|---|
| E9 | 方案把 `.advanced-section summary { border-radius: ... }` 与 `.advanced-content { border-radius: ... }` 写成独立规则块；文件里这两个选择器已存在，我把声明并入既有规则而非新建重复选择器。计算值完全相同（`.advanced-section:not([open]) summary` 特异性更高，仍能覆盖），只是避免了重复选择器。新增的 `:not([open])` 规则按方案独立成块。 |
| E12 | 方案「现在」写的 media 块只有 2 条属性，实际文件里已有 4 条（含 `scroll-behavior`、`transition-duration`），与方案「改成」的 4 条完全一致，因此只追加了 `.progress-fill.indeterminate` 覆盖，未动原有 4 条。 |
| A2/A4/E10 等 | 方案代码块是省略写法（例如 `.task-result-summary` 未列出 `color: var(--text-2)`、note 组未列出 `border-radius: var(--r-sm)`）。这些未提及的属性一律原样保留，只增改方案明确写出的项。 |

## 自检结果

- 花括号配对：改动前 `{` 442 / `}` 442；改动后 `{` 445 / `}` 445（新增 3 个规则块：`.advanced-section:not([open]) summary`、`.task-result-failures li`、reduced-motion 内的 `.progress-fill.indeterminate`）。
- 额外用忽略注释与字符串的扫描器校验嵌套：`final depth: 0, max nesting: 2, BALANCED`。
- `git status --porcelain`：仅 ` M wandao_electron/renderer/styles.css`。
- `python3 scripts/quality_check.py`：**passed**
  ```
  # tests 105
  # pass 105
  # fail 0
  Node syntax check passed (25 files).
  Git diff whitespace check passed.
  Quality check passed.
  ```

## 风险提示

1. **E7 / `.app-main` 阴影方向变了**：原来是 `-10px 0 32px`（向左投影，模拟侧栏压主区），换成 `var(--shadow-soft)` 后变成 `0 4px 18px`（向下）。方案「改成」块明确写的是 `var(--shadow-soft)`，我按「改成」执行；方案副作用里提到的「保留左投影」变体属于可选项，未采纳。如果侧栏/主区分界看起来变平，这里是第一嫌疑点。
2. **E1 有一处未被方案覆盖的焦点样式**：`.task-result-card:focus-visible` 仍是 `outline: 3px solid var(--focus); outline-offset: 4px;`。因为 `--focus` 已从半透明淡绿变成不透明深绿 `#1f6f2e`，这处焦点环会明显变重（浅色主题下尤其），且样式与其它控件的「双色环」不统一。方案未提及，故未改。
3. **E1 + E9 的联动只覆盖了 `.advanced-section`**：`box-shadow` 焦点环仍可能被其它祖先的 `overflow: hidden` 裁切。文件里还有多处 `overflow: hidden`（如 `.task-history-section`、`.home-hero`、各卡片容器），若这些容器边缘紧贴可聚焦元素，焦点环仍会被切。本批次只按方案处理了 `.advanced-section`。
4. **E4 `--text-3` 加深影响面大**：`--text-3` 全站 30+ 处引用（helper-note / kicker / meta / `.advanced-section summary::after` 的「+」号等），次要文字整体会明显加深，视觉层次被压缩。方案副作用里建议给 `.route-node small` 单独回退，属于可选项，未采纳。
5. **E11 让中英文重量层次同时变平**：`.platform-mark`（原 900）、各 hero/section 标题（原 800）在英文与数字场景下会比现在细一档，因为 Aptos Display 是可变字体、能真实渲染 800/900。方案副作用建议可只给 `.platform-mark` 保留 800，属于可选项，未采纳。
6. **A2 未采纳的配套项**：hero 变矮后 `.home-hero::before` 装饰圆（430px、border-width 74px）在容器里的占比变大，可能显得过满。方案给的「74px → 56px」是可选建议，未改。
7. **A6 未采纳的配套项**：`.nav-item::before` 高亮条 `height: 22px` 在 44px 行高下占比偏大。方案建议可降到 18px，属于可选项，未改。
8. **E8 观感变化**：`.knowledge-route` 原来 88% 半透明能透出 hero 渐变的绿意，改实色后会更「白」一块。方案给的 `color-mix(... 88%, var(--brand-soft))` 折中写法属于可选项，未采纳。
9. **E9 溢出兜底消失**：`.advanced-content` 里若出现宽表格或超长路径，原先被父级 `overflow: hidden` 挡住，现在会溢出圆角边界。E10 的 `overflow-wrap: anywhere` 只覆盖了任务结果相关选择器，未覆盖 `.advanced-content` 内部。
10. **E6 依赖插件页自带背景**：`color-scheme: light dark` 生效后，硬编码「白底深字」的插件页在深色主题下会变成深底深字。方案要求配套更新插件开发文档，属于本批次范围外（不改 styles.css 以外文件），未处理。
11. **仓库出现未跟踪目录 `out/`**：这是按要求运行 `scripts/quality_check.py` 时，`tests/test_xiliu_plugin.py` 用相对路径写出的测试残留（`Doc 1.md` / `Doc 2.md` / `Doc A.md`），不是我的改动产物，也未被 .gitignore 忽略。我没有删除它（避免在 styles.css 之外做任何文件操作），提交前建议手动清掉。
