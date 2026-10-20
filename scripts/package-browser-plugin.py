"""Build a standalone, unpack-and-load GA Browser Bridge release archive."""
import argparse
import json
from pathlib import Path
import re
import zipfile

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / 'plugin' / 'browser-bridge'
GUIDE = '''GA 浏览器连接 — 安装说明

1. 解压整个 ZIP，保留 browser-bridge 文件夹。
2. Chrome 打开 chrome://extensions（Edge：edge://extensions），开启“开发者模式”。
3. 点击“加载已解压的扩展程序”，选择 browser-bridge 文件夹。
4. 打开 Audio QC，点击浏览器工具栏的插件图标查看连接状态；可将图标固定到工具栏。

已有旧版时，请先停用旧版，避免同时运行两个桥接插件。更新时替换插件文件后，在扩展管理页点击“重新加载”，并刷新需要操作的网页。
插件可供 GA 及兼容客户端使用。来源、许可和改动说明见 browser-bridge/README.md 与 LICENSE。
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('version', help='Application release version, e.g. 2.0.0-beta.1')
    parser.add_argument('--output-dir', type=Path, default=REPO / 'app' / 'release')
    args = parser.parse_args()
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?', args.version):
        parser.error('invalid release version')
    manifest = json.loads((SOURCE / 'manifest.json').read_text())
    required = ['LICENSE', 'README.md', 'UPSTREAM_SHA256.json', 'manifest.json',
                manifest['background']['service_worker'], manifest['action']['default_popup']]
    for entry in manifest.get('content_scripts', []):
        required.extend(entry.get('js', []))
    for name in required:
        if not (SOURCE / name).is_file():
            raise ValueError(f'Missing extension file: {name}')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    destination = args.output_dir / f'GA-Browser-Bridge-{args.version}.zip'
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        for p in sorted(SOURCE.rglob('*')):
            if not p.is_file() or any(part.startswith('.') for part in p.relative_to(SOURCE).parts):
                continue
            if p.is_symlink():
                raise ValueError(f'Unexpected symlink: {p}')
            archive.write(p, 'browser-bridge/' + p.relative_to(SOURCE).as_posix())
        archive.writestr('安装说明.txt', GUIDE)
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise ValueError('Archive integrity check failed')
        assert json.loads(archive.read('browser-bridge/manifest.json')) == manifest
    print(f'Browser plugin {manifest["version"]} packaged: {destination}')


if __name__ == '__main__':
    main()
