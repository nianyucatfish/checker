"""Package the external-agent skill and standalone CLI (no duplicated QC manual)."""
import argparse
from pathlib import Path
import re
import zipfile

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / 'skills' / 'audio-qc'
FILES = ['SKILL.md', 'scripts/qc.py', 'references/interface.md']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('version')
    parser.add_argument('--output-dir', type=Path, default=REPO / 'app' / 'release')
    args = parser.parse_args()
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?', args.version):
        parser.error('invalid release version')
    for name in FILES:
        if not (SOURCE / name).is_file():
            raise ValueError(f'Missing skill file: {name}')
    compile((SOURCE / 'scripts/qc.py').read_text(), 'qc.py', 'exec')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / f'Audio-QC-Agent-Skill-{args.version}.zip'
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in FILES:
            archive.write(SOURCE / name, 'audio-qc/' + name)
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError('Invalid ZIP')
        assert sorted(archive.namelist()) == sorted('audio-qc/' + name for name in FILES)
    print(f'External agent skill packaged: {output}')


if __name__ == '__main__':
    main()
