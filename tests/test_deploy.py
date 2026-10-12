"""Isolated deployment-script check: command ordering and invalid config rollback."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("research_bridge", [False, True])
def test_deploy_success_and_config_rollback(tmp_path: Path, research_bridge: bool) -> None:
    root = tmp_path / "app"
    (root / "docker").mkdir(parents=True)
    shutil.copyfile(Path(__file__).resolve().parents[1] / "deploy.sh", root / "deploy.sh")
    (root / ".web.env").write_text("# isolated test\n")
    (root / "docker-compose.yml").write_text("services: {}\n")
    if research_bridge:
        (root / ".research-web.env").write_text("# synthetic bridge configuration\n")
        (root / "docker-compose.research.yml").write_text("services: {}\n")
    candidate = root / "docker" / "nginx-stock.conf"
    candidate.write_text("new config\n")

    nginx_dir = tmp_path / "nginx"
    site = nginx_dir / "sites-enabled" / "stock.conf"
    site.parent.mkdir(parents=True)
    site.write_text("old config\n")
    log = tmp_path / "commands.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        'echo "docker $*" >> "$COMMAND_LOG"\n'
        # A stopped predecessor cannot resolve until the new upstream is installed.
        'if [ "$1 $2 $3 ${4:-}" = "exec nginx nginx -t" ] && '
        'grep -q "^old config" "$NGINX_DIR/sites-enabled/stock.conf"; then exit 1; fi\n'
        'if [ "$1 $2 $3 ${4:-}" = "exec nginx nginx -t" ] && '
        'grep -q INVALID "$NGINX_DIR/sites-enabled/stock.conf"; then exit 1; fi\n'
        'if [ "$1" = inspect ]; then echo "true 0"; fi\n'
    )
    docker.chmod(0o755)
    curl = bin_dir / "curl"
    curl.write_text('#!/bin/sh\necho "curl $*" >> "$COMMAND_LOG"\n')
    curl.chmod(0o755)
    make = bin_dir / "make"
    make.write_text('#!/bin/sh\necho "make $*" >> "$COMMAND_LOG"\n')
    make.chmod(0o755)
    env = {
        **os.environ,
        "NGINX_DIR": str(nginx_dir),
        "COMMAND_LOG": str(log),
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "RESEARCH_WEB_ENABLED": "1" if research_bridge else "0",
    }

    def run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(root / "deploy.sh")],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    result = run()
    assert result.returncode == 0, result.stderr
    assert site.read_text() == "new config\n"
    assert len(list(site.parent.glob("stock.conf.bak.*"))) == 1
    calls = log.read_text().splitlines()
    assert any("--env-file .research-web.env" in line for line in calls) is research_bridge
    build = next(i for i, line in enumerate(calls) if " build web" in line)
    up = next(i for i, line in enumerate(calls) if " up -d " in line and line.endswith(" web"))
    reload = next(i for i, line in enumerate(calls) if "nginx -s reload" in line)
    check = calls.index("make check")
    assert check < build < up < reload
    assert "--remove-orphans" in calls[up]
    assert not any(line.endswith(" worker") for line in calls)
    assert not any("down" in line or "-v" in line for line in calls)
    assert any(line.startswith("curl ") for line in calls[up:reload])
    assert any(line.startswith("curl ") for line in calls[reload:])

    log.write_text("")
    candidate.write_text("INVALID config\n")
    result = run()
    assert result.returncode != 0
    assert site.read_text() == "new config\n"
    assert " web" in log.read_text()
    assert " worker" not in log.read_text()
    assert "nginx -s reload" not in log.read_text()


@pytest.fixture
def deployment(tmp_path: Path):
    """Every external command is a stub; never execute deploy against real Docker."""
    root = tmp_path / "app"
    (root / "docker").mkdir(parents=True)
    shutil.copyfile(Path(__file__).resolve().parents[1] / "deploy.sh", root / "deploy.sh")
    for name in (
        ".web.env",
        "docker-compose.yml",
        ".research-web.env",
        "docker-compose.research.yml",
    ):
        (root / name).write_text("# synthetic configuration\n")
    (root / "docker/nginx-stock.conf").write_text("new config\n")
    nginx = tmp_path / "nginx"
    site = nginx / "sites-enabled/stock.conf"
    site.parent.mkdir(parents=True)
    site.write_text("old config\n")
    log = tmp_path / "commands.log"
    log.touch()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    commands = {
        "docker": """#!/bin/sh
printf 'docker %s\\n' "$*" >> "$COMMAND_LOG"
case "$1" in
  ps)
    if [ "$2" = -a ]; then
      [ "${PS_FAIL:-0}" = 1 ] && exit 1
      [ "${EXISTING:-none}" != none ] && echo synthetic-container
    fi
    exit 0 ;;
  inspect)
    case "$3" in
      *RestartCount*) echo 'true 0' ;;
      *)
        [ "${INSPECT_FAIL:-0}" = 1 ] && exit 1
        [ "${EXISTING:-none}" = bridge ] && echo bridge
        ;;
    esac
    exit 0 ;;
  exec)
    if [ "$2" = a-stock-tracker-web ]; then
      exit "${PROBE_FAIL:-0}"
    fi ;;
esac
exit 0
""",
        "make": '#!/bin/sh\necho "make $*" >> "$COMMAND_LOG"\n',
        "curl": '#!/bin/sh\necho "curl $*" >> "$COMMAND_LOG"\n',
    }
    for name, script in commands.items():
        target = bin_dir / name
        target.write_text(script)
        target.chmod(0o755)
    env = {
        **{k: v for k, v in os.environ.items() if k != "RESEARCH_WEB_ENABLED"},
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "NGINX_DIR": str(nginx),
        "COMMAND_LOG": str(log),
    }

    def run(**settings):
        return subprocess.run(
            ["bash", str(root / "deploy.sh")],
            env={**env, **settings},
            capture_output=True,
            text=True,
            check=False,
        )

    return root, site, log, run


@pytest.mark.parametrize("existing", ["none", "basic"])
def test_omitted_flag_never_enables_from_config_files(deployment, existing):
    _, _, log, run = deployment
    result = run(EXISTING=existing)
    assert result.returncode == 0, result.stderr
    assert "--env-file" not in log.read_text()
    assert "a_stock_tracker.research_client" not in log.read_text()


def test_omitted_flag_refuses_to_remove_existing_bridge(deployment):
    _, site, log, run = deployment
    result = run(EXISTING="bridge")
    assert result.returncode != 0
    assert "RESEARCH_WEB_ENABLED=1 ./deploy.sh" in result.stderr
    assert "RESEARCH_WEB_ENABLED=0 ./deploy.sh" in result.stderr
    assert site.read_text() == "old config\n"
    calls = log.read_text()
    assert "ps -a --filter name=^/a-stock-tracker-web$" in calls  # Includes stopped web.
    assert ".Config.Env" in calls and ".Mounts" in calls
    assert "compose" not in calls and "make" not in calls and "exec" not in calls


@pytest.mark.parametrize("flag", ["", "true", "false", "2", "01", " 1"])
def test_invalid_flag_fails_before_docker_commands(deployment, flag):
    _, site, log, run = deployment
    result = run(RESEARCH_WEB_ENABLED=flag)
    assert result.returncode != 0
    assert "must be exactly 0" in result.stderr
    assert log.read_text() == ""
    assert site.read_text() == "old config\n"


@pytest.mark.parametrize("flag", ["0", "1"])
def test_explicit_choice_overrides_existing_bridge(deployment, flag):
    _, _, log, run = deployment
    result = run(RESEARCH_WEB_ENABLED=flag, EXISTING="bridge")
    assert result.returncode == 0, result.stderr
    calls = log.read_text()
    assert ("--env-file .research-web.env" in calls) is (flag == "1")
    assert ("a_stock_tracker.research_client" in calls) is (flag == "1")
    if flag == "1":
        assert calls.index(" up -d ") < calls.index("a_stock_tracker.research_client")
        assert calls.index("a_stock_tracker.research_client") < calls.index("nginx -s reload")


@pytest.mark.parametrize("missing", [".research-web.env", "docker-compose.research.yml"])
def test_enabled_bridge_missing_configuration_stops_before_changes(deployment, missing):
    root, site, log, run = deployment
    (root / missing).unlink()
    result = run(RESEARCH_WEB_ENABLED="1")
    assert result.returncode != 0
    assert "Missing .research-web.env" in result.stderr
    assert log.read_text() == ""
    assert site.read_text() == "old config\n"


@pytest.mark.parametrize("failure", ["PS_FAIL", "INSPECT_FAIL"])
def test_unknown_existing_state_fails_closed(deployment, failure):
    _, site, log, run = deployment
    result = run(EXISTING="bridge", **{failure: "1"})
    assert result.returncode != 0
    assert "compose" not in log.read_text()
    assert site.read_text() == "old config\n"


def test_probe_failure_does_not_reload_or_report_success(deployment):
    _, site, log, run = deployment
    result = run(RESEARCH_WEB_ENABLED="1", PROBE_FAIL="1")
    assert result.returncode != 0
    assert "Web was already recreated" in result.stderr
    assert "Deployed:" not in result.stdout
    assert "nginx -s reload" not in log.read_text()
    assert site.read_text() == "old config\n"
