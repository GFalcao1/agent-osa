"""Implantação na VM Core (Linux, TASK-113): systemd, fstab, logrotate e túnel do modelo.

Só lê os arquivos; a prova de que sobem, reiniciam e sobrevivem a reboot é a validação manual
registrada na TASK-113.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SYSTEMD = ROOT / "deploy" / "systemd"
SECRET_NAMES = re.compile(r"(TOKEN|PASSWORD|SENHA|SECRET|KEY|DATABASE_URL)", re.IGNORECASE)
SHARE = "/mnt/osa-share"


def unit(name: str) -> dict[str, dict[str, list[str]]]:
    """Seções -> chave -> valores (systemd repete chaves, como Environment=)."""
    sections: dict[str, dict[str, list[str]]] = {}
    current: dict[str, list[str]] | None = None
    for raw in (SYSTEMD / name).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = sections.setdefault(line[1:-1], {})
            continue
        assert current is not None, f"{name}: chave fora de seção: {line}"
        key, _, value = line.partition("=")
        current.setdefault(key.strip(), []).append(value.strip())
    return sections


def one(section: dict[str, list[str]], key: str) -> str:
    values = section.get(key, [])
    assert len(values) == 1, f"{key}: {values}"
    return values[0]


def environment(service: dict[str, list[str]]) -> dict[str, str]:
    return dict(value.split("=", 1) for value in service.get("Environment", []))


@pytest.mark.parametrize("service", ["bot", "worker"])
def test_bot_and_worker_start_with_the_machine_and_restart_after_a_kill(service: str) -> None:
    sections = unit(f"osa-{service}.service")
    body = sections["Service"]

    assert one(body, "ExecStart") == f"/opt/osa/.venv/bin/osa-{service}"
    assert one(body, "Restart") == "always"
    assert int(one(body, "RestartSec")) >= 10
    assert one(body, "User") == "osa"  # the service account, never a person's
    assert one(body, "WorkingDirectory") == "/opt/osa"  # where .env is read
    assert one(body, "UMask") == "0077"
    assert environment(body)["OSA_LOG_FILE"] == f"/var/log/osa/{service}.log"
    assert one(body, "StandardError") == f"append:/var/log/osa/{service}.stderr.log"
    assert one(sections["Install"], "WantedBy") == "multi-user.target"


@pytest.mark.parametrize("service", ["bot", "worker", "backup"])
def test_nothing_that_touches_the_share_starts_without_it(service: str) -> None:
    sections = unit(f"osa-{service}.service")
    assert one(sections["Unit"], "RequiresMountsFor") == SHARE


@pytest.mark.parametrize("service", ["bot", "worker"])
def test_bot_and_worker_wait_for_the_database_and_the_model_tunnel(service: str) -> None:
    after = " ".join(unit(f"osa-{service}.service")["Unit"]["After"]).split()
    assert {"postgresql.service", "osa-model-tunnel.service", "network-online.target"} <= set(after)


def test_backup_runs_nightly_from_a_timer_and_does_not_stay_alive() -> None:
    service = unit("osa-backup.service")["Service"]
    timer = unit("osa-backup.timer")

    assert one(service, "Type") == "oneshot"
    assert "Restart" not in service
    assert one(service, "ExecStart") == "/opt/osa/scripts/backup.sh"
    env = environment(service)
    assert (
        env["OSA_ENV_FILE"] == "/opt/osa/.env" and env["OSA_PYTHON"] == "/opt/osa/.venv/bin/python"
    )
    assert env["PATH"].split(":")[0] == "/usr/lib/postgresql/17/bin"  # pg_dump 17 (PGDG)
    assert one(timer["Timer"], "OnCalendar") == "*-*-* 02:30:00"
    assert one(timer["Timer"], "Persistent") == "true"
    assert one(timer["Timer"], "Unit") == "osa-backup.service"


@pytest.mark.parametrize(
    "name",
    ["osa-bot.service", "osa-worker.service", "osa-backup.service", "osa-model-tunnel.service"],
)
def test_every_service_runs_as_osa_without_new_privileges_and_without_secrets(name: str) -> None:
    body = unit(name)["Service"]
    assert one(body, "User") == "osa" and one(body, "Group") == "osa"
    assert one(body, "NoNewPrivileges") == "yes"
    assert one(body, "ProtectSystem") in {"full", "strict"}
    assert not [key for key in environment(body) if SECRET_NAMES.search(key)]
    text = (SYSTEMD / name).read_text(encoding="utf-8")
    assert "postgresql://" not in text and "EnvironmentFile" not in text  # .env is read by the app


def test_the_model_tunnel_keeps_ollama_on_loopback_at_both_ends() -> None:
    body = unit("osa-model-tunnel.service")["Service"]
    assert one(body, "ExecStart") == "/usr/bin/ssh -N -F /etc/osa/ssh_config osa-model"
    assert one(body, "Restart") == "always"
    config = (ROOT / "deploy" / "ssh" / "osa-model.conf").read_text(encoding="utf-8")
    options = {
        line.split(None, 1)[0]: line.split(None, 1)[1]
        for line in (raw.strip() for raw in config.splitlines())
        if line and not line.startswith("#") and " " in line
    }
    assert options["LocalForward"] == "127.0.0.1:11434 127.0.0.1:11434"
    assert options["ExitOnForwardFailure"] == "yes"
    assert options["StrictHostKeyChecking"] == "yes" and options["BatchMode"] == "yes"
    assert options["ForwardAgent"] == "no"
    # the key authorized on the model server opens only this forward
    assert 'permitopen="127.0.0.1:11434"' in config and "restrict," in config


def test_the_share_mounts_on_demand_with_fresh_directory_listings() -> None:
    lines = [
        line
        for line in (ROOT / "deploy" / "fstab" / "osa-share.fstab").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert len(lines) == 1
    _source, point, fstype, options, *_ = lines[0].split()
    opts = set(options.split(","))
    assert point == SHARE and fstype == "cifs"
    assert {"nohandlecache", "x-systemd.automount", "_netdev", "vers=3.1.1"} <= opts
    assert "credentials=/etc/osa/smb.cred" in opts
    assert not [opt for opt in opts if opt.startswith(("password=", "pass="))]
    assert {"uid=osa", "gid=osa", "file_mode=0600", "dir_mode=0700"} <= opts


def test_every_log_the_units_write_is_rotated_the_right_way() -> None:
    text = (ROOT / "deploy" / "logrotate" / "osa").read_text(encoding="utf-8")
    blocks = {
        frozenset(head.split()): body
        for head, body in re.findall(r"^([^#\s{][^{]*)\{(.*?)\}", text, re.MULTILINE | re.DOTALL)
    }
    reopened = {path for head, body in blocks.items() if "create" in body for path in head}
    truncated = {path for head, body in blocks.items() if "copytruncate" in body for path in head}
    # the app reopens OSA_LOG_FILE by path after a rename; systemd's append: files are never
    # reopened, so they are truncated in place
    json_logs = {
        environment(unit(f"osa-{s}.service")["Service"])["OSA_LOG_FILE"] for s in ("bot", "worker")
    }
    systemd_files = {
        one(unit(name)["Service"], key).removeprefix("append:")
        for name in ("osa-bot.service", "osa-worker.service", "osa-backup.service")
        for key in ("StandardOutput", "StandardError")
    }
    assert json_logs <= reopened
    assert systemd_files <= truncated
    assert not reopened & truncated
    assert all("su osa osa" in body for body in blocks.values())


def test_the_log_folder_exists_before_systemd_opens_the_append_files() -> None:
    # systemd opens StandardOutput=append: before it would create LogsDirectory=, so the folder
    # comes from tmpfiles.d at boot
    lines = [
        line.split()
        for line in (ROOT / "deploy" / "tmpfiles" / "osa.conf").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert ["d", "/var/log/osa", "0700", "osa", "osa", "-"] in lines
    # the append: files are created by osa, so logrotate (su osa osa) can truncate them
    created = {line[1] for line in lines if line[0] == "f" and line[2:5] == ["0600", "osa", "osa"]}
    assert created == {
        one(unit(name)["Service"], key).removeprefix("append:")
        for name in ("osa-bot.service", "osa-worker.service", "osa-backup.service")
        for key in ("StandardOutput", "StandardError")
    }
    for name in ("osa-bot.service", "osa-worker.service", "osa-backup.service"):
        assert "LogsDirectory" not in unit(name)["Service"]
