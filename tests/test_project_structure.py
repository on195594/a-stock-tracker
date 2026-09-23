"""Regression tests for the repository layout and import boundaries."""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "a_stock_tracker"

ALLOWED_TOP_LEVEL_FILES = {
    ".gitattributes",
    ".gitignore",
    "AGENTS.md",
    "CHANGELOG.md",
    "CLAUDE.md",
    "README.md",
    "TODOS.md",
    "cron-alert-wrap.sh",
    "cron-setup.sh",
    "pipeline.py",
    "pyproject.toml",
    "requirements.txt",
}
ALLOWED_TOP_LEVEL_DIRECTORIES = {
    ".github",
    "a_stock_tracker",
    "config",
    "docs",
    "scripts",
    "tests",
}
ALLOWED_PACKAGE_FILES = {"__init__.py", "cli.py", "config.py", "paths.py", "scoring.py"}
ALLOWED_PACKAGE_DIRECTORIES = {"data", "qualitative", "reporting", "signals"}
LEGACY_IMPORT_ROOTS = {
    "config",
    "gemini_scorer",
    "lib",
    "scorer",
    "sheets_sync",
    "telegram_push",
}
PRIVATE_KEY_MARKERS = (
    b"-----BEGIN " + b"PRIVATE KEY-----",
    b"-----BEGIN RSA " + b"PRIVATE KEY-----",
    b"-----BEGIN EC " + b"PRIVATE KEY-----",
    b'"private_' + b'key"',
)
EXPECTED_A_STOCK_LIB_REQUIREMENT = (
    "a-stock-lib @ https://github.com/on195594/a-stock-lib/releases/download/"
    "v0.8.0/a_stock_lib-0.8.0-py3-none-any.whl#sha256="
    "a811945b23d97eb121ff82d54bc0ba0810000a5379a9e9786fdcdc9220b30310"
)


def _repository_files() -> set[Path]:
    """Return tracked and non-ignored untracked files that exist in this checkout."""
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
    )
    paths = {
        PROJECT_ROOT / raw.decode("utf-8")
        for raw in result.stdout.split(b"\0")
        if raw and (PROJECT_ROOT / raw.decode("utf-8")).is_file()
    }
    return paths


def _production_modules() -> list[Path]:
    return sorted([*PACKAGE_ROOT.rglob("*.py"), *(PROJECT_ROOT / "scripts").glob("*.py")])


def test_top_level_entries_are_explicitly_allowed() -> None:
    files = _repository_files()
    top_level_files = {path.name for path in files if path.parent == PROJECT_ROOT}
    top_level_directories = {path.relative_to(PROJECT_ROOT).parts[0] for path in files if path.parent != PROJECT_ROOT}

    assert top_level_files <= ALLOWED_TOP_LEVEL_FILES
    assert top_level_directories <= ALLOWED_TOP_LEVEL_DIRECTORIES


def test_package_root_contains_only_composition_modules_and_domains() -> None:
    package_files = {path.name for path in PACKAGE_ROOT.iterdir() if path.is_file()}
    package_directories = {path.name for path in PACKAGE_ROOT.iterdir() if path.is_dir() and path.name != "__pycache__"}

    assert package_files == ALLOWED_PACKAGE_FILES
    assert package_directories == ALLOWED_PACKAGE_DIRECTORIES


def test_legacy_layout_is_not_reintroduced() -> None:
    assert not (PROJECT_ROOT / "lib").exists()
    assert not (PACKAGE_ROOT / "data" / "fetcher.py").exists()
    assert not (PACKAGE_ROOT / "data" / "akshare_provider.py").exists()
    assert list(PROJECT_ROOT.glob("qualitative_v2_*.py")) == []
    assert list(PROJECT_ROOT.glob("*.json")) == []
    assert {path.name for path in PROJECT_ROOT.glob("*.py")} == {"pipeline.py"}


def test_legacy_fetcher_is_not_referenced_by_production_code() -> None:
    violations = [
        str(path.relative_to(PROJECT_ROOT))
        for path in _production_modules()
        if any(
            marker in path.read_text(encoding="utf-8")
            for marker in ("a_stock_tracker.data.fetcher", "akshare_provider", "import akshare")
        )
    ]

    assert violations == []


def test_credentials_and_runtime_reports_are_not_tracked() -> None:
    relative_files = {path.relative_to(PROJECT_ROOT) for path in _repository_files()}

    assert not any(path.parts[0] == "credentials" for path in relative_files)
    assert Path("accuracy_report.txt") not in relative_files


def test_repository_files_do_not_contain_private_key_material() -> None:
    violations = [
        str(path.relative_to(PROJECT_ROOT))
        for path in _repository_files()
        if any(marker in path.read_bytes() for marker in PRIVATE_KEY_MARKERS)
    ]

    assert violations == []


def test_tracked_configuration_stays_in_config_directory() -> None:
    assert (PROJECT_ROOT / "config" / "weights.json").is_file()
    assert (PROJECT_ROOT / "config" / "experiment_manifest.json").is_file()
    assert (PROJECT_ROOT / "config" / "trading_calendar.json").is_file()


def test_shared_library_uses_the_immutable_release_artifact() -> None:
    requirements = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()

    assert EXPECTED_A_STOCK_LIB_REQUIREMENT in requirements
    assert not any(line.startswith("--find-links") for line in requirements)


def test_production_modules_use_package_imports_without_path_injection() -> None:
    violations: list[str] = []
    for path in _production_modules():
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        if "sys.path.insert" in source or "sys.path.append" in source:
            violations.append(f"{path.relative_to(PROJECT_ROOT)}: modifies sys.path")

        for node in ast.walk(tree):
            imported_names: list[str] = []
            if isinstance(node, ast.Import):
                imported_names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_names = [node.module]
            for name in imported_names:
                root = name.split(".", 1)[0]
                if root in LEGACY_IMPORT_ROOTS or root.startswith("qualitative_v2_"):
                    violations.append(f"{path.relative_to(PROJECT_ROOT)}: legacy import {name}")
                if name == "a_stock_tracker.cli":
                    violations.append(f"{path.relative_to(PROJECT_ROOT)}: reverse dependency on cli")

    assert violations == []


def test_root_pipeline_remains_a_thin_compatibility_launcher() -> None:
    launcher = PROJECT_ROOT / "pipeline.py"
    source = launcher.read_text(encoding="utf-8")

    assert len(source.splitlines()) <= 20
    assert "from a_stock_tracker import cli as _cli" in source
    assert "_cli.run()" in source
