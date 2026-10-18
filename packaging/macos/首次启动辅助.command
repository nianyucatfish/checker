#!/bin/bash
# Only remove the download quarantine attribute from this installed application.
set -u
APP='/Applications/Audio QC.app'
pause_exit() {
  printf '\n按回车键关闭此窗口。'
  read -r _ || true
  exit "$1"
}
printf 'Audio QC · 首次启动辅助\n\n'
printf '仅在你确认安装包来自可信来源，并遇到 macOS 无法打开提示时使用。\n'
printf '本脚本只移除 /Applications/Audio QC.app 的下载隔离标记，\n不会关闭系统安全检查，也不会提供 Apple 签名或公证。\n\n'
if [[ ! -d "$APP" || -L "$APP" ]]; then
  printf '请先将 Audio QC 拖入「应用程序」文件夹，再运行本脚本。\n'
  pause_exit 1
fi
BUNDLE_ID=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$APP/Contents/Info.plist" 2>/dev/null) || BUNDLE_ID=''
if [[ "$BUNDLE_ID" != 'com.audioqc.desktop' ]]; then
  printf '应用标识不匹配，已停止。请重新下载安装包。\n'
  pause_exit 1
fi
printf '是否继续？输入 y 并按回车确认，其他输入取消：'
read -r answer || answer=''
if [[ "$answer" != 'y' && "$answer" != 'Y' ]]; then
  printf '已取消，没有进行更改。\n'
  pause_exit 0
fi
if ! /usr/bin/xattr -dr com.apple.quarantine "$APP"; then
  printf '\n处理失败，可能没有应用文件的修改权限。请联系管理员协助。\n'
  pause_exit 1
fi
printf '\n处理完成，正在打开 Audio QC…\n'
if ! /usr/bin/open "$APP"; then
  printf '应用未能打开，请记录具体提示并反馈。\n'
  pause_exit 1
fi
pause_exit 0
