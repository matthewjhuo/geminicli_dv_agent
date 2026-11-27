import base64, gzip, hashlib, io, json, tempfile, zipfile
from pathlib import Path, PurePosixPath
PARTS = 11
EXPECTED = '8012f1ca4ac5c28ad64b76a1a818de77c31b7f0c7230d40835a0e6f613e49b5d'
destination = Path.cwd() / 'agentic-dv-offline-0696ff0'
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
    with tempfile.TemporaryDirectory(prefix=".dv-restore-", dir=Path.cwd()) as temp:
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
