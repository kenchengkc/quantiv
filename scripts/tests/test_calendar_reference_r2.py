from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "r2_push_calendar_reference.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def _fake_tools(tmp_path: Path) -> tuple[Path, Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    remote = tmp_path / "remote"
    log = tmp_path / "rclone.jsonl"

    fake_python = bin_dir / "fake-python"
    _write_executable(
        fake_python,
        """#!/usr/bin/env python3
import json
from pathlib import Path
import sys

args = sys.argv[1:]
command = args[1]
out = Path(args[args.index('--output-dir') + 1])
if command == 'build':
    (out / 'releases').mkdir(parents=True, exist_ok=True)
    (out / 'receipts').mkdir(parents=True, exist_ok=True)
    (out / 'releases/release-1.json').write_text('{\"release_id\":\"release-1\"}\\n')
    (out / 'receipts/receipt-1.json').write_text('{\"receipt_id\":\"receipt-1\"}\\n')
    (out / 'current.json').write_text(json.dumps({
        'release_id': 'release-1',
        'release': 'releases/release-1.json',
        'receipt_id': 'receipt-1',
        'receipt': 'receipts/receipt-1.json',
    }, sort_keys=True) + '\\n')
elif command == 'verify':
    assert (out / 'current.json').is_file()
elif command == 'project':
    pass
else:
    raise SystemExit(f'unsupported command: {command}')
""",
    )

    rclone = bin_dir / "rclone"
    _write_executable(
        rclone,
        """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import shutil
import sys

args = sys.argv[1:]
if not args or args[0] != 'copyto':
    raise SystemExit('fake rclone only supports copyto')
src, dst = args[1], args[2]
root = Path(os.environ['FAKE_R2_ROOT'])
prefix = 'r2:test/'

def resolve(value: str) -> Path:
    return root / value[len(prefix):] if value.startswith(prefix) else Path(value)

src_path, dst_path = resolve(src), resolve(dst)
dst_path.parent.mkdir(parents=True, exist_ok=True)
if '--immutable' in args and dst_path.exists() and dst_path.read_bytes() != src_path.read_bytes():
    raise SystemExit('immutable collision')
if (
    os.environ.get('FAKE_R2_CORRUPT_RELEASE_READBACK') == '1'
    and src.startswith(prefix)
    and '/releases/' in src
    and '.release-readback' in dst
):
    dst_path.write_text('corrupt\\n')
else:
    shutil.copyfile(src_path, dst_path)
with open(os.environ['RCLONE_LOG'], 'a') as handle:
    handle.write(json.dumps({'src': src, 'dst': dst, 'args': args}) + '\\n')
""",
    )
    return bin_dir, remote, log


def _run(tmp_path: Path, *, corrupt: bool = False) -> subprocess.CompletedProcess[str]:
    bin_dir, remote, log = _fake_tools(tmp_path)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{env['PATH']}",
            "PYTHON_BIN": str(bin_dir / "fake-python"),
            "CALENDAR_REFERENCE_DIR": str(tmp_path / "stage"),
            "CALENDAR_REFERENCE_NOT_BEFORE": "2026-09-09T11:00:00Z",
            "CALENDAR_REFERENCE_SOURCE_REVISION": "test-sha",
            "R2_REMOTE": "r2:test",
            "CALENDAR_REFERENCE_PREFIX": "calendar-reference",
            "FAKE_R2_ROOT": str(remote),
            "RCLONE_LOG": str(log),
        }
    )
    if corrupt:
        env["FAKE_R2_CORRUPT_RELEASE_READBACK"] = "1"
    return subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def test_r2_calendar_release_is_immutable_readback_verified_and_pointer_last(
    tmp_path: Path,
) -> None:
    result = _run(tmp_path)
    assert result.returncode == 0, result.stdout
    operations = [
        json.loads(line) for line in (tmp_path / "rclone.jsonl").read_text().splitlines()
    ]

    assert len(operations) == 6
    assert operations[0]["src"].endswith("releases/release-1.json")
    assert "--immutable" in operations[0]["args"]
    assert operations[1]["src"].endswith("receipts/receipt-1.json")
    assert "--immutable" in operations[1]["args"]
    assert operations[2]["src"].endswith("releases/release-1.json")
    assert operations[3]["src"].endswith("receipts/receipt-1.json")
    assert operations[4]["src"].endswith("current.json")
    assert operations[4]["dst"].endswith("calendar-reference/current.json")
    assert operations[5]["src"].endswith("calendar-reference/current.json")


def test_r2_calendar_pointer_is_not_promoted_when_readback_is_corrupt(
    tmp_path: Path,
) -> None:
    result = _run(tmp_path, corrupt=True)
    assert result.returncode != 0
    assert not (tmp_path / "remote" / "calendar-reference" / "current.json").exists()
