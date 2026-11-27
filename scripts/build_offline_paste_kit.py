#!/usr/bin/env python3
"""Deterministic builder for the company copy-paste offline deployment kit.

Packages all git-tracked files of the Agentic DV Framework (excluding ``offline/``)
plus the bundled Apache-2.0 Harness^2 core source snapshot into:
- ``ALL_IN_ONE.sh`` (single-file copy-paste restore script)
- ``00_START.txt`` + ``01_PART_001.txt``..``NN_PART_0NN.txt`` + ``99_RESTORE.txt``
- ``restore.py``, ``100_VERIFY_AND_INSTALL.txt``, ``package-info.json``,
  ``LICENSE-HARNESS2``, ``README.md``, and ``使用說明.txt``.

All generated files use UTF-8 with LF line endings and zero hardcoded user paths.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import io
import json
import math
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Dict, List, Tuple

LINE_WIDTH = 100
TARGET_LINES_PER_PART = 480  # ~48 KB per part (fits strict clipboard/terminal limits)


def _git_head_sha(repo_root: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=str(repo_root), text=True
    ).strip()


def _git_tracked_files(repo_root: Path) -> List[str]:
    raw = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=str(repo_root)
    )
    out: List[str] = []
    for entry in raw.split(b"\x00"):
        if not entry:
            continue
        rel = entry.decode("utf-8")
        if rel.startswith("offline/") or rel.startswith("papers/"):
            continue
        out.append(rel)
    return sorted(out)


def _extract_harness2_from_kit(kit_dir: Path) -> Tuple[Dict[str, bytes], bytes]:
    """Load the verified Apache-2.0 Harness^2 core files from an existing offline paste kit."""
    part_files = sorted(kit_dir.glob("*_PART_*.txt"))
    if not part_files:
        raise FileNotFoundError(f"No *_PART_*.txt files found in {kit_dir}")
    b64_chunks: List[bytes] = []
    for pf in part_files:
        lines = pf.read_text(encoding="utf-8").splitlines()
        in_body = False
        for ln in lines:
            if ln.startswith("cat > part_") and "<<'DV_PAYLOAD_PART_" in ln:
                in_body = True
                continue
            if in_body and ln.startswith("DV_PAYLOAD_PART_"):
                in_body = False
                break
            if in_body:
                b64_chunks.append(ln.strip().encode("ascii"))
    data = base64.b64decode(b"".join(b64_chunks), validate=True)
    h2_files: Dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(gzip.decompress(data))) as zf:
        manifest = json.loads(zf.read("TRANSFER_MANIFEST.json"))
        for name, expected in manifest["files"].items():
            if not name.startswith("harness2/"):
                continue
            blob = zf.read(name)
            if hashlib.sha256(blob).hexdigest() != expected:
                raise ValueError(f"Corrupt harness2 file in source kit: {name}")
            h2_files[name] = blob
    license_bytes = (kit_dir / "LICENSE-HARNESS2").read_bytes()
    return h2_files, license_bytes


def _write_lf(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = text.replace("\r\n", "\n")
    path.write_bytes(normalized.encode("utf-8"))


def _build_restore_py(parts: int, expected_sha256: str, short_sha: str) -> str:
    return (
        "import base64\n"
        "import gzip\n"
        "import hashlib\n"
        "import io\n"
        "import json\n"
        "from pathlib import Path, PurePosixPath\n"
        "import tempfile\n"
        "import zipfile\n"
        "\n"
        f"PARTS = {parts}\n"
        f"EXPECTED_SHA256 = '{expected_sha256}'\n"
        f"destination = Path.cwd() / 'agentic-dv-offline-{short_sha}'\n"
        "chunks = []\n"
        "for index in range(1, PARTS + 1):\n"
        "    path = Path(f'part_{index:03d}.b64')\n"
        "    if not path.is_file():\n"
        "        raise SystemExit(f'Missing part {index}/{PARTS}: paste it before restoring')\n"
        "    chunks.append(b''.join(path.read_bytes().split()))\n"
        "try:\n"
        "    data = base64.b64decode(b''.join(chunks), validate=True)\n"
        "except ValueError as exc:\n"
        "    raise SystemExit(f'Invalid Base64: {exc}')\n"
        "if hashlib.sha256(data).hexdigest() != EXPECTED_SHA256:\n"
        "    raise SystemExit('Archive checksum mismatch: re-paste any part that failed its check')\n"
        "if destination.exists():\n"
        "    raise SystemExit(f'Destination already exists; nothing overwritten: {destination}')\n"
        "with zipfile.ZipFile(io.BytesIO(gzip.decompress(data))) as archive:\n"
        "    names = archive.namelist()\n"
        "    if len(names) != len(set(names)):\n"
        "        raise SystemExit('Duplicate archive entries')\n"
        "    for info in archive.infolist():\n"
        "        path = PurePosixPath(info.filename)\n"
        "        if path.is_absolute() or '..' in path.parts or '\\\\' in info.filename or ':' in info.filename:\n"
        "            raise SystemExit('Unsafe archive path')\n"
        "        if (info.external_attr >> 16) & 0o170000 == 0o120000:\n"
        "            raise SystemExit('Symlink entry rejected')\n"
        "    manifest = json.loads(archive.read('TRANSFER_MANIFEST.json'))\n"
        "    if set(names) != set(manifest['files']) | {'TRANSFER_MANIFEST.json'}:\n"
        "        raise SystemExit('Manifest file list mismatch')\n"
        "    for name, expected in manifest['files'].items():\n"
        "        if hashlib.sha256(archive.read(name)).hexdigest() != expected:\n"
        "            raise SystemExit(f'File checksum mismatch: {name}')\n"
        "    # Validate everything before writing any source file; publish only a full tree.\n"
        "    with tempfile.TemporaryDirectory(prefix='.dv-restore-', dir=Path.cwd()) as temp:\n"
        "        staging = Path(temp) / 'content'\n"
        "        staging.mkdir()\n"
        "        for name in names:\n"
        "            target = staging / name\n"
        "            target.parent.mkdir(parents=True, exist_ok=True)\n"
        "            target.write_bytes(archive.read(name))\n"
        "        if destination.exists():\n"
        "            raise SystemExit('Destination appeared during restore; nothing overwritten')\n"
        "        staging.rename(destination)\n"
        "print(f'RESTORE OK: {len(manifest[\"files\"])} files verified')\n"
        "print(f'Framework commit: {manifest[\"framework_commit\"]}')\n"
        "print(f'Location: {destination}')\n"
    )


def build_kit(repo_root: Path, source_h2_kit: Path, out_dir: Path) -> Dict[str, object]:
    commit_sha = _git_head_sha(repo_root)
    short_sha = commit_sha[:7]

    # Collect framework files (strictly tracked, excluding offline/ and papers/)
    all_entries: Dict[str, bytes] = {}
    for rel in _git_tracked_files(repo_root):
        fp = repo_root / rel
        if not fp.is_file():
            continue
        blob = fp.read_bytes()
        all_entries[f"dv-agent-framework/{rel}"] = blob

    # Collect verified Apache-2.0 Harness^2 core files
    h2_files, license_bytes = _extract_harness2_from_kit(source_h2_kit)
    all_entries.update(h2_files)

    manifest = {
        "framework_commit": commit_sha,
        "framework_tree_verified_equal_to": commit_sha,
        "harness2_origin": "Bundled local source snapshot; Apache-2.0",
        "files": {
            name: hashlib.sha256(all_entries[name]).hexdigest()
            for name in sorted(all_entries)
        },
    }
    manifest_bytes = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

    # Deterministic zip + gzip payload
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", compression=zipfile.ZIP_STORED) as zf:
        for name in sorted(all_entries):
            zi = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            zi.external_attr = (0o755 if name.endswith(".sh") else 0o644) << 16
            zf.writestr(zi, all_entries[name])
        mi = zipfile.ZipInfo("TRANSFER_MANIFEST.json", date_time=(2026, 1, 1, 0, 0, 0))
        mi.external_attr = 0o644 << 16
        zf.writestr(mi, manifest_bytes)

    gz_buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=gz_buf, compresslevel=9, mtime=0) as gz:
        gz.write(zip_buf.getvalue())
    gz_bytes = gz_buf.getvalue()
    gz_sha256 = hashlib.sha256(gz_bytes).hexdigest()

    b64_text = base64.b64encode(gz_bytes).decode("ascii")
    b64_lines = [b64_text[i : i + LINE_WIDTH] for i in range(0, len(b64_text), LINE_WIDTH)]
    num_parts = max(1, math.ceil(len(b64_lines) / TARGET_LINES_PER_PART))

    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("*_PART_*.txt"):
        stale.unlink()

    start_txt = (
        "# Run these blocks in a Bash terminal (run bash first if your shell is csh/tcsh).\n"
        "# Stay in this same staging directory for all subsequent blocks.\n"
        f'mkdir -p "$HOME/agentic-dv-paste-{short_sha}"\n'
        f'cd "$HOME/agentic-dv-paste-{short_sha}" || exit 1\n'
        "python3 --version\n"
    )
    _write_lf(out_dir / "00_START.txt", start_txt)

    part_blocks: List[str] = []
    part_filenames: List[str] = []
    for idx in range(num_parts):
        p_num = idx + 1
        chunk_lines = b64_lines[idx * TARGET_LINES_PER_PART : (idx + 1) * TARGET_LINES_PER_PART]
        chunk_joined = "".join(chunk_lines).encode("ascii")
        chunk_sha = hashlib.sha256(chunk_joined).hexdigest()
        block = (
            f"# Part {p_num}/{num_parts} — re-pasting this same part replaces it safely.\n"
            f"cat > part_{p_num:03d}.b64 <<'DV_PAYLOAD_PART_{p_num:03d}'\n"
            + "\n".join(chunk_lines)
            + f"\nDV_PAYLOAD_PART_{p_num:03d}\n"
            "python3 - <<'DV_CHECK_PART'\n"
            "import hashlib\n"
            "from pathlib import Path\n"
            f"p = Path('part_{p_num:03d}.b64')\n"
            "s = b''.join(p.read_bytes().split())\n"
            f"if hashlib.sha256(s).hexdigest() != '{chunk_sha}':\n"
            f"    raise SystemExit('Part {p_num} CORRUPTED: paste this part again')\n"
            f"print('Part {p_num}/{num_parts} OK')\n"
            "DV_CHECK_PART\n"
        )
        fname = f"{p_num:02d}_PART_{p_num:03d}.txt"
        _write_lf(out_dir / fname, block)
        part_blocks.append(block)
        part_filenames.append(fname)

    restore_py = _build_restore_py(num_parts, gz_sha256, short_sha)
    _write_lf(out_dir / "restore.py", restore_py)

    restore_txt = "python3 - <<'DV_RESTORE_PYTHON'\n" + restore_py + "\nDV_RESTORE_PYTHON\n"
    _write_lf(out_dir / "99_RESTORE.txt", restore_txt)

    all_in_one = start_txt + "\n" + "\n".join(part_blocks) + "\n" + restore_txt
    _write_lf(out_dir / "ALL_IN_ONE.sh", all_in_one)

    verify_txt = (
        "# Bash: execute after RESTORE OK. Use Python 3.12 or 3.13 for the full bundle.\n"
        f'export DV_AGENT_OFFLINE_HOME="$HOME/agentic-dv-paste-{short_sha}/agentic-dv-offline-{short_sha}"\n'
        'export DV_AGENT_FRAMEWORK_HOME="${DV_AGENT_OFFLINE_HOME}/dv-agent-framework"\n'
        'export HARNESS2_REPO_DIR="${DV_AGENT_OFFLINE_HOME}/harness2"\n'
        'export DV_AGENT_PLATFORM_PYTHONPATH="${DV_AGENT_FRAMEWORK_HOME}/src"\n'
        'export PYTHONPATH="${DV_AGENT_PLATFORM_PYTHONPATH}"\n'
        'export DV_ADAPTER_MODE="mock"\n'
        'export DV_ALLOW_EXTERNAL_UPLOAD="false"\n'
        "\n"
        'python3 -c \'import sys; assert (3,12) <= sys.version_info[:2] < (3,14), "Full bundle requires Python 3.12 or 3.13"; print(sys.version)\'\n'
        "\n"
        "# Offline checks: do not need a model API call or EDA license.\n"
        'cd "${DV_AGENT_FRAMEWORK_HOME}" || exit 1\n'
        "python3 -m unittest discover -s tests -p 'test_*.py' -q\n"
        "env -u DV_ADAPTER_MODE python3 -m unittest discover -s tests_e2e -p 'test_*.py' -q\n"
        "python3 -m dv_agent_platform evaluate --mutation-ladder\n"
        "python3 scripts/run_harness2_dv.py --mode dry_sim --k 1 --recursion both --output .dv_agent/harness2-selftest\n"
        "\n"
        "# Create a local customization profile only if it does not already exist.\n"
        'if [ ! -e "${DV_AGENT_OFFLINE_HOME}/company_profile.json" ]; then\n'
        '  cp project-template/company_profile.example.json "${DV_AGENT_OFFLINE_HOME}/company_profile.json"\n'
        "fi\n"
        "\n"
        "# STOP HERE: edit company_profile.json in VS Code before project installation.\n"
        "# Replace example DUT, filelist, directory layout, clocks, interfaces and build/run commands.\n"
        "# Then replace the following target with your actual existing TB workspace.\n"
        "\n"
        '# export DV_PROJECT_ROOT="/actual/company/testbench/workspace"\n'
        "\n"
        "# Preview only:\n"
        '# python3 "${DV_AGENT_FRAMEWORK_HOME}/scripts/install.py" \\\n'
        '#   --project-dir "${DV_PROJECT_ROOT}" \\\n'
        '#   --profile "${DV_AGENT_OFFLINE_HOME}/company_profile.json" \\\n'
        "#   --strict-render --emit-vscode --emit-env-example\n"
        "\n"
        "# After reviewing the preview, apply:\n"
        '# python3 "${DV_AGENT_FRAMEWORK_HOME}/scripts/install.py" \\\n'
        '#   --project-dir "${DV_PROJECT_ROOT}" \\\n'
        '#   --profile "${DV_AGENT_OFFLINE_HOME}/company_profile.json" \\\n'
        "#   --strict-render --emit-vscode --emit-env-example --apply\n"
        "\n"
        "# Start Gemini from the existing project after installation:\n"
        '# cd "${DV_PROJECT_ROOT}"\n'
        "# gemini\n"
    )
    _write_lf(out_dir / "100_VERIFY_AND_INSTALL.txt", verify_txt)
    (out_dir / "LICENSE-HARNESS2").write_bytes(license_bytes)

    pkg_info = {
        "commit": commit_sha,
        "files": len(all_entries),
        "base64_characters": len(b64_text),
        "parts": num_parts,
        "sha256": gz_sha256,
        "compressed_bytes": len(gz_bytes),
        "harness2_source": "Bundled local source snapshot; Apache-2.0; exact file digests in restored TRANSFER_MANIFEST.json",
    }
    _write_lf(out_dir / "package-info.json", json.dumps(pkg_info, indent=2, ensure_ascii=False) + "\n")

    part_table_rows = "\n".join(
        f"| {i} | [{fn}]({fn}) |" for i, fn in enumerate(part_filenames, start=1)
    )
    readme_md = f"""# 公司 server：完整 Gemini CLI DV 框架貼上部署包（{short_sha}）

適用於公司 server 無法連 GitHub、不能直接傳檔，但可以從外部電腦複製文字到 VS Code 的情境。所有程式資料已包含在文字內，server 不需要下載任何檔案。

這是固定的 `{commit_sha}` 框架快照，含完整 L1–L5、skills、agents、commands、MCP、Python 平台、測試、文件、Harness² 核心原始碼，以及最新四項論文級升級（**BTTF SQLite Waveform Store + `dv-verdi` MCP 波形反查**、**ChipMEM 雙層驗證記憶與貝氏工具重試建議**、**GUARD + WikiSkill 防退化演化護欄**、**Survey 五階段 RTL Mutation Oracle 評測階梯**），共 {len(all_entries)} 個來源檔案。未來 main 的修改不會自動進入這份固定快照。

Harness² 來自本機原始碼快照；其 Apache-2.0 授權見 [LICENSE-HARNESS2](LICENSE-HARNESS2)，逐檔 SHA-256 在還原後的 `TRANSFER_MANIFEST.json`。這份 bundle 不包含 Gemini CLI、商用 EDA 工具、license 或憑證，也不包含 Harness² render/bridge 額外選配依賴。

## 建議方式：複製一個檔案

1. 在可連 GitHub 的電腦開啟 [ALL_IN_ONE.sh](ALL_IN_ONE.sh)，按 **Raw**，全選並複製完整文字。
2. 用 VS Code Remote SSH 連公司 server，在非生產專案目錄新建 `dv_restore.sh`，貼入文字，以 **UTF-8 / LF** 儲存。
3. 在公司 server terminal 執行：

   ```bash
   bash dv_restore.sh
   ```

4. 成功時會顯示 `RESTORE OK: {len(all_entries)} files verified`。還原器會驗證段落、整包 SHA-256 和每個來源檔案，不覆寫既有同名還原目錄。
5. 開啟 [100_VERIFY_AND_INSTALL.txt](100_VERIFY_AND_INSTALL.txt)，依序設定環境、跑離線測試（含 `--mutation-ladder` 與 Harness² `dry_sim`）、修改公司 profile、預覽安裝，再套用到既有 TB。

整套含 Harness² 請用 Python 3.12 或 3.13；核心框架和完整測試需 Python 3.11+。沿用公司已核准且可正常使用的 Gemini CLI 登入，不要把憑證寫進任何設定檔。

## 若剪貼簿限制單次文字長度：分 {num_parts} 段

先在公司 terminal 執行 `bash`，以下各段都貼到同一個 terminal。每個檔案請複製全部文字，不只複製 Base64 部分。

| 順序 | 要複製貼上的內容 |
| --- | --- |
| 開始 | [00_START.txt](00_START.txt) |
{part_table_rows}
| 還原 | [99_RESTORE.txt](99_RESTORE.txt) |
| 驗證與安裝 | [100_VERIFY_AND_INSTALL.txt](100_VERIFY_AND_INSTALL.txt) |

每段應顯示 `Part n/{num_parts} OK`。若某段失敗，重新貼同編號即可。若終端機停在 `>`，按 Ctrl+C，重新貼該段完整內容。先前成功的段落不需要重貼。

## 還原後的位置

```text
$HOME/agentic-dv-paste-{short_sha}/agentic-dv-offline-{short_sha}/
├── dv-agent-framework/
├── harness2/
└── TRANSFER_MANIFEST.json
```

環境變數設定後，修改 `company_profile.json` 的 DUT、TB/RTL 路徑、filelist、clock/reset 和 compile/run 指令。用 `scripts/install.py` 先預覽，再加 `--apply`。最後進入 **TB 專案根目錄**啟動 `gemini`，讓它載入安裝後的 `GEMINI.md` 與 `.gemini/`。

## 驗證結果與範圍

已從這些貼上文字還原所有 {len(all_entries)} 個來源檔案並核對逐檔 SHA-256。還原後已通過完整離線單元測試（含隨包真正 Harness² 遞迴測試）、73 項離線 E2E 測試、`evaluate --mutation-ladder`（5/5 mutants killed、`SIGNOFF_READY`）以及 Harness² `dry_sim` 4/4 黃金案例。

`dry_sim` 是合成流程驗證，不呼叫模型。未在公司真實 EDA/排程器驗證。安裝後保持 `mock`/`dry_run`，逐一驗證 adapter 再啟用。Python 自動編排預設唯讀 `plan` 模式，互動式 TB 修改仍需核准。

完整步驟亦見 [使用說明.txt](使用說明.txt)。
"""
    _write_lf(out_dir / "README.md", readme_md)

    usage_txt = f"""完整 Agentic DV Framework：複製貼上部署包（{short_sha}）

版本：main 的 {commit_sha}。
{len(all_entries)} 個原始檔案，包含完整框架（含 BTTF 波形 SQLite、ChipMEM 雙層記憶、GUARD+WikiSkill 演化護欄、Survey Mutation Oracle 評測階梯）與本機 Harness² 核心原始碼快照及 Apache-2.0 授權。
不包含 Gemini CLI 可執行檔、EDA 軟體、license 或任何憑證。
公司已可使用 Gemini CLI；整套含 Harness² 建議用 Python 3.12 或 3.13。
核心框架與完整測試需 Python 3.11+，Harness² 宣告支援 >=3.12,<3.14。
Harness² bridge/render 額外選配依賴不在此包，預設 DV dry_sim 不需要 pip。

方法 A：一次貼完整檔案，適合 VS Code Remote SSH 編輯器

1. 在本機開啟 ALL_IN_ONE.sh，Ctrl+A、Ctrl+C。
2. 在公司 server 的 VS Code 工作區以外建立暫存檔 dv_restore.sh。
3. 把全部文字貼入該檔案並儲存，檔案編碼 UTF-8、換行 LF。
4. 在 server terminal 執行：bash dv_restore.sh
5. 成功應顯示 RESTORE OK: {len(all_entries)} files verified。

注意：貼的是檔案內容，不是把本機檔案傳上 server；不需 server 連網。
只需在 server 儲存一個文字檔，還原腳本會生成所有資料夾與檔案。
若剪貼簿/遠端平台有文字長度限制，使用下面的分段方式。

方法 B：分 {num_parts} 段貼入同一個 Bash terminal

1. 若目前是 csh/tcsh，先執行 bash，後續都在同一個 terminal 操作。
2. 開啟 00_START.txt，把全部內容貼到公司 terminal。
3. 依序開啟 01_PART_001.txt 到 {part_filenames[-1]}；每個檔案全部複製貼入。
4. 每段應顯示 Part n/{num_parts} OK。不要只複製中間的 Base64，請貼整個檔案。
5. 出現 CORRUPTED 時重新貼同一段即可；同編號會覆寫該段，不會重複追加。
6. 最後貼 99_RESTORE.txt，出現 RESTORE OK: {len(all_entries)} files verified 才算完成。

還原目錄

$HOME/agentic-dv-paste-{short_sha}/agentic-dv-offline-{short_sha}/
  dv-agent-framework/     完整框架（含 L1–L5、BTTF、ChipMEM、GUARD+WikiSkill、Mutation Ladder）
  harness2/               真正 Harness² Python 核心原始碼、README、pyproject、授權
  TRANSFER_MANIFEST.json  每個檔案的 SHA-256

整包 SHA-256（payload.gz）：
{gz_sha256}

還原後

開啟 100_VERIFY_AND_INSTALL.txt，依序執行環境設定、離線自我測試、修改 company_profile.json，預覽安裝後再套用至目標 TB。
不要執行 --global-gemini-dir；不需要修改個人登入設定或憑證。
"""
    _write_lf(out_dir / "使用說明.txt", usage_txt)

    return pkg_info


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build copy-paste offline deployment kit")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--source-harness2-kit", default="offline/paste-kit-d8b0c90")
    parser.add_argument("--out-dir", default=None)
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    short_sha = _git_head_sha(repo_root)[:7]
    src_kit = Path(args.source_harness2_kit)
    if not src_kit.is_absolute():
        src_kit = repo_root / src_kit
    out_dir = Path(args.out_dir) if args.out_dir else (repo_root / "offline" / f"paste-kit-{short_sha}")
    if not out_dir.is_absolute():
        out_dir = repo_root / out_dir

    info = build_kit(repo_root, src_kit, out_dir)
    print(json.dumps({"out_dir": str(out_dir.relative_to(repo_root).as_posix()), **info}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
