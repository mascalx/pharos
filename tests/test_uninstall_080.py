"""Conservative uninstall source contracts; no host changes."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_ubuntu_uninstall_is_syntactically_valid_and_data_preserving():
    script = ROOT / 'uninstall-pharos.sh'
    subprocess.run(['bash', '-n', str(script)], check=True)
    code = script.read_text()
    assert 'REMOVE PHAROS' in code
    assert 'flock -n 9' in code
    assert 'systemctl --user disable --now' in code
    assert 'rm -rf --one-file-system -- "$VENV"' in code
    assert 'rm -rf --one-file-system -- "$STATE"' not in code
    assert 'sudo ' not in code


def test_windows_uninstall_preserves_data_and_checks_processes():
    code = (ROOT / 'uninstall-pharos.ps1').read_text()
    assert 'REMOVE PHAROS' in code
    assert '$Lock.Lock(0,1)' in code
    assert 'Get-CimInstance Win32_Process' in code
    assert 'ReparsePoint' in code
    assert 'Remove-Item -LiteralPath $Venv -Recurse -Force' in code
    assert 'Remove-Item -LiteralPath $Root -Recurse' not in code
    assert 'Stop-Process' not in code
