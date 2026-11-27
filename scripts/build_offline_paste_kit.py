"""Compatibility CLI for the canonical committed-snapshot paste-kit builder.

Prefer scripts/build_paste_kit.py. Keeps prior --repo-root/--source-harness2-kit/
--out-dir arguments while using the same verified format and source snapshot.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import build_paste_kit as canonical


def build_kit(repo_root, source_harness2_kit, out_dir):
    original = canonical.ROOT
    try:
        canonical.ROOT = Path(repo_root).resolve()
        return canonical.build('HEAD', out_dir, harness2_from_kit=source_harness2_kit)
    finally:
        canonical.ROOT = original


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--source-harness2-kit', type=Path, default=Path('offline/paste-kit-7ff1213'))
    parser.add_argument('--out-dir', type=Path)
    args = parser.parse_args(argv)
    root = args.repo_root.resolve()
    source = args.source_harness2_kit
    if not source.is_absolute(): source = root / source
    import subprocess
    short = subprocess.check_output(['git', '-C', str(root), 'rev-parse', '--short=7', 'HEAD'], text=True).strip()
    output = args.out_dir or root / 'offline' / f'paste-kit-{short}'
    if not output.is_absolute(): output = root / output
    print(json.dumps(build_kit(root, source, output), indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
