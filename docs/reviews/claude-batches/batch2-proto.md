# 批次 B2 实施报告（W4 / T3）

## 一、已实施表

| 编号 | 问题 | 改动文件 | 提交 / 标签 |
| --- | --- | --- | --- |
| W4 | assertSafeRelativePath 未拦截段内冒号（Windows 上被当成 NTFS 备用数据流）与保留设备名、结尾点/空格 | wandao_electron/plugin_format.js、新增 tests_js/plugin_path_safety.test.js | ad7b403 / sol/W4 |
| T3 | plugin.schema.json 的 additionalProperties:false 加运行时 allowedKeys 硬拒未知字段，使 v1 内新增可选字段成为破坏性变更 | wandao_electron/plugin_format.js、plugins/plugin.schema.json、新增 tests_js/plugin_manifest_tolerant.test.js | 8dcb002 / sol/T3 |
### W4 实际改动

assertSafeRelativePath 在原有「空目录 / . / ..」校验之后追加一段路径段检查：

    const WINDOWS_RESERVED_SEGMENT = /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\.|$)/i;
    const WINDOWS_UNSAFE_CHARS = /[\u0000-\u001f:<>"|?*]/;

    if (parts.some((part) => WINDOWS_UNSAFE_CHARS.test(part)
      || WINDOWS_RESERVED_SEGMENT.test(part)
      || /[. ]$/.test(part))) {
      throw new Error(label + 包含 Windows 非法路径段 + value);
    }

与方案原文的偏差（有意）：方案给出的字符类是 /[ -<>"|?*]/，JS 会把「空格到 <」当成一个字符区间（涵盖 . - / 0-9 : 等），会把 backend/export.py 这类正常路径一并拒掉。这里按方案「说明」段的真实意图落地为「控制字符 + Windows 非法字符 + 冒号」，并用反向断言锁死不误伤。

原有的反斜杠 / 绝对路径 / 盘符 / .. 三条拦截一行未动，只在其后叠加。

### T3 实际改动

validatePluginManifest 中唯一改动的是未知顶层键分支：告警并忽略，不再 throw。默认实现是 console.warn，可通过 validatePluginManifest.onUnknownKey 注入更严格的处理器。

plugins/plugin.schema.json 顶层 additionalProperties 由 false 改为 true（与 providers/provider.schema.json 一致）。core 与 entrypoints 两个嵌套对象的 additionalProperties:false 保持不变，只放开顶层。

仓库内 lint 保持严格的挂钩点已经就位：scripts/validate_plugins.js 可在顶部设 validatePluginManifest.onUnknownKey 把告警重新变成硬失败（该文件不在本批次允许改动清单内，未改）。

## 二、测试先行：改实现之前的失败输出

node --test tests_js/plugin_path_safety.test.js tests_js/plugin_manifest_tolerant.test.js

    not ok 1 - 未知顶层字段被接受，只产生告警
    ok 2 - 没有未知字段时不产生告警
    not ok 3 - 默认告警走 console.warn 且不抛错
    ok 4 - 已登记的可选字段不算未知字段
    ok 5 - 必填字段缺失仍然被拒
    ok 6 - 已知字段类型错误仍然被拒
    not ok 7 - 红线：未知字段不能松动 permissions 校验
    not ok 8 - 红线：未知字段不能松动 entrypoints 路径安全校验
    not ok 9 - 红线：未知字段不能松动 core.minVersion / id / version 校验
    not ok 10 - 红线：未知字段仍进入签名内容，不产生绕过
    not ok 11 - plugin.schema.json 允许未来的可选字段
    not ok 12 - 段内冒号被拒绝，避免 NTFS 备用数据流
    not ok 13 - Windows 保留设备名被拒绝
    not ok 14 - 结尾点或空格的路径段被拒绝
    not ok 15 - Windows 其余非法字符与控制字符被拒绝
    ok 16 - 原有的反斜杠、绝对路径、.. 拦截保持不变
    ok 17 - 正常路径依然通过
    not ok 18 - 自定义 label 出现在错误信息里
    # tests 18
    # pass 6
    # fail 12

典型失败原因：

    error: Missing expected exception: 应拒绝 a(小于号)b.py      W4：根本没有拦截
    error: plugin.json 包含未知字段：futureField                 T3：未知字段被硬拒
    error: plugin.json 包含未知字段：futureField                 T3 红线：未知字段的错误盖住了权限/路径的真实错误

先绿的 6 条同样是基线：

- ok 17 正常路径依然通过 —— 改动前绿，改动后必须仍然绿，这是防止 W4 过度拦截的反向断言。
- ok 5 / ok 6 —— 改动前绿，改动后必须仍然绿，这是 T3 不能顺手放松必填与类型校验的基线。

## 三、安全红线验证

放松只覆盖「完全不认识的顶层键」。下列 4 条红线用例都在 manifest 里塞了未知字段 futureField，断言仍然抛出各自领域的严格错误，而不是被未知字段分支吞掉：

| 红线 | 用例断言 | 结果 |
| --- | --- | --- |
| permissions | [filesystem:write, root]、[*] 触发 /不支持的权限/ | 通过 |
| entrypoints | providers 为 ../escape/provider.json、/etc/provider.json 触发 /Provider 入口/；ui 为 ../evil.html 触发 /自定义 UI 入口/ | 通过 |
| core.minVersion | latest 触发 /core.minVersion/ | 通过 |
| id / version | Demo Plugin、../../etc 触发 /插件 ID 不合法/；v1 触发 /插件版本不合法/ | 通过 |

另有两条防绕过断言：

- 未知字段仍进签名：带 futureField 的插件包经 createPluginEnvelope 后，把 manifest.futureField 从 original 改成 tampered，verifyPluginEnvelope 抛 /完整性/；并直接断言 canonicalStringify(body) 含 futureField、integrity.value 等于重算的 sha256。未知字段不构成签名旁路。
- schema 只放顶层：core / entrypoints 的 additionalProperties:false 未动，未知子键仍被 schema 拒绝。

W4 侧同样只收紧不放松：原有两条错误路径（POSIX 相对路径、空目录/./..）的断言全部保留并通过（ok - 原有的反斜杠、绝对路径、.. 拦截保持不变）。

## 四、最终测试数

python3 scripts/quality_check.py（exit=0）

    # tests 145
    # pass 145
    # fail 0
    Ran 535 tests in 7.639s
    OK (skipped=2)
    Skipping tests_js/yuque_converter.test.js: wandao_electron/node_modules/electron/dist is not installed.
    Node syntax check passed (25 files, 28 test files).
    Git diff whitespace check passed.
    Quality check passed.

- Node：基线 127 提升到 145 通过（新增 18 条 = 路径安全 7 条 + tolerant reader 11 条），0 失败。
- Python：535 通过，与基线一致。
- Quality check passed. 全绿。

## 五、边界说明

- 只改了允许清单内的文件：wandao_electron/plugin_format.js、plugins/plugin.schema.json，外加 2 个新建测试；未改任何已有测试。
- git 只用了 add / commit / tag，未 checkout / reset / push / rebase。标签 sol/W4、sol/T3 已打在对应提交上。
- 仓库现有 14 个插件、20 个 Provider 的路径全部是 ASCII 安全字符，W4 收紧后 node scripts/validate_plugins.js 仍然通过（14 plugins, 20 providers）。
