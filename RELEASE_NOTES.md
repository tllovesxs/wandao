# Wandao 1.0.7 Release Notes

## 新增

- 桌面端新增夜间模式切换，并会记住用户的主题选择。
- 侧边栏改为“导出 / 导入”两组折叠分类，平台入口更清晰。
- 新增版本更新检查：启动后静默检测 GitHub 最新 Release，也可以手动点击“检查更新”。

## 改进

- 暗色主题覆盖表单、目录树、日志区、进度条、提示框等主要界面区域。
- 更新 README 和桌面端使用说明，补充主题、分类侧边栏和版本检查说明。
- 开源协议切换为 Apache License 2.0，并同步 Python/Electron 项目元数据。

## 发行包

- Windows：`.exe` 安装包和便携版。
- macOS：源码压缩包；真正的 macOS App 建议在 macOS 或 GitHub Actions macOS runner 上构建。

## 注意

- 桌面端当前仍需要本机安装 Python 3.10+。
- 源码运行可通过 `python -m pip install -r requirements.txt` 安装 Python 依赖。
- 请只导出自己有权限访问的内容，并遵守目标平台服务条款。
