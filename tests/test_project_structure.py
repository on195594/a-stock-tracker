"""The workbench runs from one package without retired collectors or private artifacts."""

import ast
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_package_has_no_legacy_entrypoints_or_path_injection():
    package = ROOT / "a_stock_tracker"
    assert list(ROOT.glob("*.py")) == []
    for retired in ("data", "scoring.py", "signals", "qualitative", "reporting"):
        assert not (package / retired).exists()
    for path in package.rglob("*.py"):
        source = path.read_text()
        assert "sys.path.insert" not in source and "sys.path.append" not in source
        for node in ast.walk(ast.parse(source)):
            names = (
                [node.module]
                if isinstance(node, ast.ImportFrom) and node.module
                else [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else []
            )
            assert not any(
                name.split(".")[0] in {"screen", "app", "workspace", "services", "scripts"}
                for name in names
            )


def test_private_runtime_files_and_keys_are_not_tracked():
    paths = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT
    ).split(b"\0")
    for raw in paths:
        if not raw:
            continue
        path = ROOT / raw.decode()
        if not path.is_file():
            continue
        assert path.relative_to(ROOT).parts[0] not in {"data", "credentials", "backups", ".local"}
        assert path.name not in {
            ".env",
            ".web.env",
            ".worker.env",
            "workspace.sqlite3",
            "tracker.db",
        }
        for marker in (
            b"-----BEGIN " + b"PRIVATE KEY-----",
            b"-----BEGIN RSA " + b"PRIVATE KEY-----",
            b"-----BEGIN EC " + b"PRIVATE KEY-----",
        ):
            assert marker not in path.read_bytes()
