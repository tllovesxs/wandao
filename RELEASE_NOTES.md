# Wandao 1.0.10 Release Notes

## 新增

- 知识星球导出新增 API 优先目录读取：读取目录时直接解析专栏和文章 `topicId`，减少页面点击依赖。
- 知识星球递归导出新增页面链接白名单：只会继续导出 `t.zsxq.com`、`wx.zsxq.com`、`articles.zsxq.com` 下的知识星球页面。

## 修复

- 修复知识星球目录导出时，部分目录项能读取目录但点击后提示“无法打开目录条目”的问题。
- 修复打包版默认把登录凭证和导出目录放到临时 `resources/python` 目录的问题。
- 修复知识星球正文里的图片 CDN、代码仓库、视频站点、云服务器等外部链接被误当作文档继续导出的情况。

## 改进

- 知识星球文章正文优先通过 `topicId` 获取，页面点击仅作为兜底，速度和稳定性更好。
- 知识星球目录接口增加顺序请求、间隔和补重试，降低读取过快导致目录缺失的概率。
- 桌面端默认输出目录改为用户数据目录下的 `exports/`，便携版和安装版都更稳定。
- 桌面端调用 Python 时会设置稳定的 `WANDAO_DATA_DIR`，避免凭证随临时目录丢失。

## 验证

- 已验证知识星球专栏目录可读取 16 个分组、256 篇文章，目录项全部带 `topicId`。
- 已验证单篇入口文档深度导出：入口文档和 10 篇知识星球子文档可正常导出，外部链接仅保留在正文中，不再继续打开。
- 已执行 `python -m py_compile export_zsxq.py`、`node --check wandao_electron/main.js`、`node --check wandao_electron/renderer/app.js`。

## 下载

- Windows：下载 `Wandao Setup 1.0.10.exe` 安装版。
- Windows 免安装：下载 `Wandao 1.0.10.exe` 便携版。
- macOS Apple Silicon：下载 `Wandao-1.0.10-arm64-mac.zip`，适合 M1 / M2 / M3 / M4 芯片 Mac。
- macOS Intel：下载 `Wandao-1.0.10-x64-mac.zip`，适合 Intel 芯片 Mac。

## 注意

- 普通用户请优先下载发行版，不需要安装 Python。
- 源码运行或参与开发时，仍需要自行安装 Python 3.10+ 和 Node.js。
- 请只处理自己有权限访问的内容，并遵守目标平台服务条款。
