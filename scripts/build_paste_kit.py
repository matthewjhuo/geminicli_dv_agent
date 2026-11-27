#!/usr/bin/env python3
"""Build a reproducible copy/paste kit from a committed framework snapshot.

Runtime needs only Python stdlib; git is used only on the packaging computer.
Historical offline kits are excluded to prevent recursively embedding old payloads.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import textwrap
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_kit(directory):
    """Verify a prior text kit before reusing its pinned Harness² source snapshot."""
    directory = Path(directory)
    info = json.loads((directory / "package-info.json").read_text(encoding="utf-8"))
    chunks = []
    for index in range(1, info["parts"] + 1):
        text = (directory / f"{index:02d}_PART_{index:03d}.txt").read_text(encoding="utf-8")
        marker = f"DV_PAYLOAD_PART_{index:03d}"
        body = text.split(f"<<'{marker}'\n", 1)[1].split(f"\n{marker}", 1)[0]
        chunks.append("".join(body.split()))
    data = base64.b64decode("".join(chunks), validate=True)
    if sha(data) != info["sha256"]:
        raise ValueError("Source paste kit checksum mismatch")
    with zipfile.ZipFile(io.BytesIO(gzip.decompress(data))) as archive:
        manifest = json.loads(archive.read("TRANSFER_MANIFEST.json"))
        if set(archive.namelist()) != set(manifest["files"]) | {"TRANSFER_MANIFEST.json"}:
            raise ValueError("Source paste kit manifest mismatch")
        files = {}
        for name, expected in manifest["files"].items():
            raw = archive.read(name)
            if sha(raw) != expected:
                raise ValueError(f"Source file checksum mismatch: {name}")
            files[name] = raw
    return info, files


RESTORE = r'''import base64, gzip, hashlib, io, json, os, tempfile, zipfile
from pathlib import Path, PurePosixPath
PARTS = @PARTS@
EXPECTED = @DIGEST@
base = Path.cwd().resolve()
if os.name == "nt" and not str(base).startswith("\\\\?\\"):
    raw = str(base)
    base = Path("\\\\?\\UNC\\" + raw[2:] if raw.startswith("\\\\") else "\\\\?\\" + raw)
destination = base / @DESTINATION@
pieces = []
for index in range(1, PARTS + 1):
    path = Path(f"part_{index:03d}.b64")
    if not path.is_file():
        raise SystemExit(f"Missing part {index}/{PARTS}; paste it before restoring")
    pieces.append(b"".join(path.read_bytes().split()))
try:
    data = base64.b64decode(b"".join(pieces), validate=True)
except ValueError as exc:
    raise SystemExit(f"Invalid Base64: {exc}")
if hashlib.sha256(data).hexdigest() != EXPECTED:
    raise SystemExit("Archive checksum mismatch: re-paste failed parts")
if destination.exists():
    raise SystemExit(f"Destination already exists; nothing overwritten: {destination}")
with zipfile.ZipFile(io.BytesIO(gzip.decompress(data))) as archive:
    names = archive.namelist()
    if len(names) != len(set(names)):
        raise SystemExit("Duplicate archive entries")
    for entry in archive.infolist():
        path = PurePosixPath(entry.filename)
        if path.is_absolute() or ".." in path.parts or "\\" in entry.filename or ":" in entry.filename:
            raise SystemExit("Unsafe archive path")
        if (entry.external_attr >> 16) & 0o170000 == 0o120000:
            raise SystemExit("Symlink archive entry")
    manifest = json.loads(archive.read("TRANSFER_MANIFEST.json"))
    if set(names) != set(manifest["files"]) | {"TRANSFER_MANIFEST.json"}:
        raise SystemExit("Manifest file list mismatch")
    for name, expected in manifest["files"].items():
        if hashlib.sha256(archive.read(name)).hexdigest() != expected:
            raise SystemExit(f"File checksum mismatch: {name}")
    with tempfile.TemporaryDirectory(prefix=".dv-restore-", dir=base) as temp:
        staging = Path(temp) / "content"
        staging.mkdir()
        for name in names:
            path = staging / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(archive.read(name))
        if destination.exists():
            raise SystemExit("Destination appeared during restore; nothing overwritten")
        staging.rename(destination)
print(f"RESTORE OK: {len(manifest['files'])} files verified")
print(f"Framework snapshot: {manifest['framework_commit']}")
print(f"Location: {destination}")
'''


def build(ref, output, harness2_dir=None, harness2_from_kit=None, chunk_chars=48000):
    if chunk_chars < 100 or chunk_chars % 4:
        raise ValueError("chunk_chars must be >= 100 and divisible by 4")
    commit = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "--verify", ref + "^{commit}"],
                                     text=True, encoding="utf-8").strip()
    archive_data = subprocess.check_output(["git", "-c", "core.autocrlf=false", "-C", str(ROOT),
                                            "archive", "--format=zip", commit])
    files = {}
    with zipfile.ZipFile(io.BytesIO(archive_data)) as archive:
        for name in archive.namelist():
            if not name.endswith("/") and not name.startswith("offline/"):
                files["dv-agent-framework/" + name] = archive.read(name)
    if harness2_from_kit:
        _, prior_files = read_kit(harness2_from_kit)
        files.update({name: data for name, data in prior_files.items() if name.startswith("harness2/")})
    elif harness2_dir:
        source = Path(harness2_dir)
        for path in sorted((source / "harness2").rglob("*.py")):
            if "__pycache__" not in path.parts:
                files["harness2/" + path.relative_to(source).as_posix()] = path.read_bytes()
        for name in ("LICENSE", "README.md", "pyproject.toml"):
            files["harness2/" + name] = (source / name).read_bytes()
    else:
        raise ValueError("Supply a Harness² source directory or verified prior kit")
    if "harness2/harness2/core/schedule.py" not in files or "harness2/LICENSE" not in files:
        raise ValueError("Harness² source and license are required")
    manifest = {"framework_commit": commit, "harness2_origin": "Pinned source snapshot; Apache-2.0; exact hashes below",
                "files": {name: sha(data) for name, data in sorted(files.items())}}
    files["TRANSFER_MANIFEST.json"] = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    # Normalize gzip OS byte (Python 3.11 may emit a platform-specific value).
    compressed = bytearray(gzip.compress(raw.getvalue(), compresslevel=9, mtime=0))
    compressed[9] = 255
    compressed = bytes(compressed)
    encoded = base64.b64encode(compressed).decode("ascii")
    parts = [encoded[i:i+chunk_chars] for i in range(0, len(encoded), chunk_chars)]
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"Choose a new output directory: {output}")
    output.mkdir(parents=True)
    short = commit[:7]
    stage = f"agentic-dv-paste-{short}"
    destination = f"agentic-dv-offline-{short}"
    def write(name, text):
        (output / name).write_text(text, encoding="utf-8", newline="\n")
    start = (f'# Bash terminal; keep the same directory for every part.\n'
             f'mkdir -p "$HOME/{stage}"\ncd "$HOME/{stage}" || exit 1\npython3 --version\n')
    write("00_START.txt", start)
    blocks = [start]
    for index, part in enumerate(parts, 1):
        marker = f"DV_PAYLOAD_PART_{index:03d}"
        block = f"# Part {index}/{len(parts)}; re-paste this same part if its checksum fails.\n"
        block += f"cat > part_{index:03d}.b64 <<'{marker}'\n" + "\n".join(textwrap.wrap(part, 100)) + f"\n{marker}\n"
        block += ("python3 - <<'DV_CHECK_PART'\nimport hashlib\nfrom pathlib import Path\n"
                  f"data = b''.join(Path('part_{index:03d}.b64').read_bytes().split())\n"
                  f"if hashlib.sha256(data).hexdigest() != '{sha(part.encode())}':\n"
                  f"    raise SystemExit('Part {index} CORRUPTED; paste this part again')\n"
                  f"print('Part {index}/{len(parts)} OK')\nDV_CHECK_PART\n")
        write(f"{index:02d}_PART_{index:03d}.txt", block)
        blocks.append(block)
    restore = RESTORE.replace("@PARTS@", str(len(parts))).replace("@DIGEST@", repr(sha(compressed))).replace("@DESTINATION@", repr(destination))
    write("restore.py", restore)
    restore_block = "python3 - <<'DV_RESTORE_PYTHON'\n" + restore + "\nDV_RESTORE_PYTHON\n"
    write("99_RESTORE.txt", restore_block)
    blocks.append(restore_block)
    write("ALL_IN_ONE.sh", "#!/usr/bin/env bash\nset -e\n" + "\n".join(blocks))
    info = dict(commit=commit, files=len(manifest["files"]), parts=len(parts),
                compressed_bytes=len(compressed), base64_characters=len(encoded), sha256=sha(compressed))
    write("package-info.json", json.dumps(info, indent=2) + "\n")
    (output / "LICENSE-HARNESS2").write_bytes(files["harness2/LICENSE"])
    setup = f'''# Bash; full bundle: Python 3.12 or 3.13. No model API or EDA license for offline checks.
export DV_AGENT_OFFLINE_HOME="$HOME/{stage}/{destination}"
export DV_AGENT_FRAMEWORK_HOME="$DV_AGENT_OFFLINE_HOME/dv-agent-framework"
export HARNESS2_REPO_DIR="$DV_AGENT_OFFLINE_HOME/harness2"
export DV_AGENT_PLATFORM_PYTHONPATH="$DV_AGENT_FRAMEWORK_HOME/src"
export PYTHONPATH="$DV_AGENT_PLATFORM_PYTHONPATH"
export DV_ADAPTER_MODE=mock
export DV_ALLOW_EXTERNAL_UPLOAD=false
python3 -c 'import sys; assert (3,12) <= sys.version_info[:2] < (3,14), "Use Python 3.12 or 3.13"'
cd "$DV_AGENT_FRAMEWORK_HOME" || exit 1
python3 -m unittest discover -s tests -p 'test_*.py' -q
python3 -m unittest discover -s tests_e2e -p 'test_*.py' -q
python3 scripts/run_harness2_dv.py --mode dry_sim --k 1 --recursion both --output .dv_agent/harness2-selftest
if [ ! -e "$DV_AGENT_OFFLINE_HOME/company_profile.json" ]; then
  cp project-template/company_profile.example.json "$DV_AGENT_OFFLINE_HOME/company_profile.json"
fi
# STOP: edit company_profile.json in VS Code (DUT, paths, filelist, clocks, compile/run).
# Replace the following path with the existing company TB root.
# export DV_PROJECT_ROOT="/actual/company/testbench"
# python3 "$DV_AGENT_FRAMEWORK_HOME/scripts/install.py" --project-dir "$DV_PROJECT_ROOT" --profile "$DV_AGENT_OFFLINE_HOME/company_profile.json" --strict-render --emit-vscode --emit-env-example
# Review the preview, then repeat with --apply.
# cd "$DV_PROJECT_ROOT"
# gemini
'''
    write("100_VERIFY_AND_INSTALL.txt", setup)
    index = [
        "# 完整 Gemini CLI DV 框架貼上部署包", "",
        f"固定來源版本：`{commit}`，共 **{info['files']} 個來源檔案**。", "",
        "包含完整 L1–L5、測試、文件和 Harness² 原始碼。排除歷史 offline/ 傳輸包以避免遞迴打包。",
        "不含 Gemini CLI/EDA 可執行檔、license、憑證或 Harness² 額外 bridge/render 依賴。",
        "Harness² 授權見 [LICENSE-HARNESS2](LICENSE-HARNESS2)，逐檔雜湊見還原後 TRANSFER_MANIFEST.json。", "",
        "## 單檔貼上", "",
        "1. 開啟 [ALL_IN_ONE.sh](ALL_IN_ONE.sh)，按 Raw，全選複製。",
        "2. 在公司 server VS Code 新增 dv_restore.sh，貼入全部文字，以 UTF-8 / LF 儲存。",
        "3. 在 server terminal 執行 `bash dv_restore.sh`。",
        f"4. 出現 `RESTORE OK: {info['files']} files verified` 後，依 [驗證與安裝](100_VERIFY_AND_INSTALL.txt) 設定。",
        "5. 編輯公司 profile，預覽安裝後套用；從 TB 專案根目錄啟動 `gemini`。", "",
        "## 分段貼上", "",
        "若剪貼簿限制長度，先進入 Bash，依下表把每個檔案的全部內容貼到同一個 terminal。",
        "每段應顯示 Part n/N OK；失敗只需重貼同編號。終端機停在 > 時按 Ctrl+C，再完整重貼該段。", "",
        "| 順序 | 檔案 |", "| --- | --- |", "| 開始 | [00_START.txt](00_START.txt) |",
    ]
    index.extend(f"| {i} | [{i:02d}_PART_{i:03d}.txt]({i:02d}_PART_{i:03d}.txt) |" for i in range(1, len(parts)+1))
    index.extend(["| 還原 | [99_RESTORE.txt](99_RESTORE.txt) |",
                  "| 驗證與安裝 | [100_VERIFY_AND_INSTALL.txt](100_VERIFY_AND_INSTALL.txt) |", "",
                  "完整功能含 Harness² 請用 Python 3.12/3.13；核心框架與測試需 Python 3.11+。",
                  "解碼不需連網或 pip。Gemini CLI 仍須能存取公司核准的模型服務。",
                  "還原器先核對整包與逐檔 SHA-256，才寫入新目錄；同名目錄存在時會停止。",
                  "dry_sim 僅為合成流程檢查。真實 EDA、排程器與模型效果仍需現場驗證。",
                  "Windows 請用短暫存/輸出路徑以避開 WinError 206；本部署包主要供 Linux server 使用。", "",
                  f"壓縮內容 SHA-256：`{info['sha256']}`。"])
    write("README.md", "\n".join(index) + "\n")
    # Independently re-read generated text and per-file hashes.
    _, restored = read_kit(output)
    assert all(restored[name] == data for name, data in files.items() if name != "TRANSFER_MANIFEST.json")
    return info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--framework-ref", default="HEAD")
    parser.add_argument("--output", required=True, type=Path)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--harness2-dir", type=Path)
    source.add_argument("--harness2-from-kit", type=Path)
    parser.add_argument("--chunk-chars", type=int, default=48000)
    args = parser.parse_args()
    print(json.dumps(build(args.framework_ref, args.output, args.harness2_dir,
                           args.harness2_from_kit, args.chunk_chars), indent=2))


if __name__ == "__main__":
    main()
