# 应用图标

`icon.png` 为 Audio QC 应用图标的 1024px 主资源，2026-10-18 使用 ImageGen 生成，采用黑白灰配色，意象为音频波形与校验标记。`icon.icns` 和 `icon.ico` 分别供 macOS 和 Windows 打包使用；开发版使用同一 PNG。

`app-icon.svg` 为保留的旧版设计，不再覆盖当前图标。`scripts/render-dmg-background.mjs` 仅生成 DMG 背景。修改图标时应一并更新三种格式。

DMG 背景包含 540×380 标准 PNG 和 1080×760 的 `dmg-background@2x.png`；dmg-builder 会自动合并为多分辨率 TIFF，使 Finder 在 Retina 屏幕上使用高清版本。两份由同一个 SVG 直接渲染，避免放大已有位图。
