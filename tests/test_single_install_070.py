"""Single-entry installation contracts for the 0.7 development release."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_ubuntu_bootstrap_reuses_proven_installer_without_overwriting_data():
    code = (ROOT / 'install-pharos.sh').read_text()
    assert "git clone --" in code
    assert "scripts/install-ubuntu.sh" in code
    assert "scripts/update-ubuntu.sh" in code
    assert "--language en" in code
    assert "git reset --hard" not in code
    assert "REPO=\"${PHAROS_REPO_URL:-$TRUSTED_HTTPS}\"" in code
    assert "TRUSTED_SSH='git@github.com:mascalx/pharos.git'" in code
    assert 'read -r -p "Install Git, Python 3 and python3-venv' in code
    assert 'answer </dev/tty' in code
    assert 'exec bash "$TARGET/scripts/install-ubuntu.sh" --language en "$@" </dev/tty' in code


def test_new_install_template_defaults_to_english_and_tiiny_direct():
    template = (ROOT / 'src/pharos/config.example.toml').read_text()
    assert 'language = "en"' in template
    assert 'provider = "tiiny_direct"' in template


def test_windows_installer_never_uses_unverified_qdrant_or_public_bind():
    code = (ROOT / 'install-pharos.ps1').read_text()
    runner = (ROOT / 'scripts/start-windows.ps1').read_text()

    assert "[string]$RepoUrl = 'https://github.com/mascalx/pharos.git'" in code
    assert "'git@github.com:mascalx/pharos.git'" in code
    assert 'Get-FileHash' in code and 'SHA256' in code
    assert 'qdrant-x86_64-pc-windows-msvc.zip' in code
    assert "KiwixManagedService(load_config(sys.argv[1])).install()" in code
    assert 'http_port: 6333' in code and 'host: 127.0.0.1' in code
    supervisor = (ROOT / 'scripts/windows_supervisor.py').read_text()
    assert 'windows_supervisor.py' in runner
    assert "'-m', 'pharos.cli', 'serve', '--config'" in supervisor
    assert 'AssignProcessToJobObject' in supervisor
    assert 'JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE' in supervisor
    assert 'msvcrt.LK_NBLCK' in supervisor
    assert '/readyz' in supervisor
    assert 'qdrant.log' in supervisor
    assert 'stop(core)' in supervisor and 'stop(q)' in supervisor
    assert 'Register-ScheduledTask' not in code


def test_windows_shortcuts_use_current_user_and_existing_launcher():
    code = (ROOT / 'install-pharos.ps1').read_text()
    assert '[switch]$NoDesktopShortcut' in code
    assert '[Environment+SpecialFolder]::Programs' in code
    assert '[Environment+SpecialFolder]::DesktopDirectory' in code
    assert 'if (-not $NoDesktopShortcut)' in code
    assert 'PHAROS.lnk' in code and 'WScript.Shell' in code
    assert '$Link.Arguments = $LinkArgs' in code
    assert '$Link.IconLocation = $Icon' in code
    assert 'pharos-icon.png' in code
    assert 'ExecutionPolicy Bypass' not in code


def test_windows_bootstrap_offers_missing_prerequisites_with_consent():
    code = (ROOT / 'install-pharos.ps1').read_text()
    assert 'Read-Host' in code
    assert 'winget.exe install --id $Id --exact --source winget' in code
    assert "'Git.Git'" in code and "'Python.Python.3.13'" in code
    assert 'Test-CompatiblePython' in code
    assert 'Update-ProcessPath' in code


def test_windows_installer_has_one_complete_body():
    code = (ROOT / 'install-pharos.ps1').read_text()
    assert code.count("$Repo = Join-Path $InstallRoot 'source'") == 1
    assert code.count('PHAROS Windows preview installed without modifying existing data.') == 1
    assert "if ($Consent -notin @('y', 'Y')) { throw" in code
    assert code.index('Install-Prerequisite \'Git.Git\'') < code.index('$Repo = Join-Path $InstallRoot')


def test_windows_python_probe_avoids_native_argument_nested_quotes():
    import subprocess
    import sys
    code = (ROOT / 'install-pharos.ps1').read_text()
    line = next(line for line in code.splitlines() if '& py.exe -3 -c ' in line)
    probe = line.split("'", 2)[1]
    assert '"' not in probe
    assert 'sys.version_info >= (3,11)' in probe
    assert 'sys.maxsize > 2**32' in probe
    expected = 0 if sys.version_info >= (3,11) and sys.maxsize > 2**32 else 1
    assert subprocess.run([sys.executable, '-c', probe]).returncode == expected
