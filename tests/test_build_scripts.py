from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_windows_build_runs_tests_before_clean_single_file_build() -> None:
    script = (ROOT / "scripts" / "build.ps1").read_text(encoding="utf-8-sig")

    assert '-m pip install -e ".[dev]"' in script
    assert "-m pytest" in script
    assert "-m PyInstaller --clean --noconfirm" in script
    assert "--workpath $WorkBuildDir" in script
    assert script.index("-m pytest") < script.index("-m PyInstaller --clean --noconfirm")


def test_windows_build_records_and_scans_the_final_artifact() -> None:
    script = (ROOT / "scripts" / "build.ps1").read_text(encoding="utf-8-sig")

    for expected in (
        "Get-FileHash",
        "Get-AuthenticodeSignature",
        '"$FinalExePath.sha256"',
        "Start-MpScan",
        "Get-MpThreatDetection",
        "www.microsoft.com/en-us/wdsi/filesubmission",
    ):
        assert expected in script

    assert "Add-MpPreference" not in script
    assert "Set-MpPreference" not in script
    assert "ExclusionPath" not in script


def test_release_workflow_tests_before_building() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "run: python -m pytest" in workflow
    assert "python -m PyInstaller --clean --noconfirm cryptobox.spec" in workflow
    assert "hashlib.sha256" in workflow
    assert "dist/${{ steps.meta.outputs.bin }}.sha256" in workflow
    assert workflow.index("run: python -m pytest") < workflow.index("python -m PyInstaller")


def test_run_scripts_forward_network_arguments() -> None:
    shell = (ROOT / "scripts" / "run-dev.sh").read_text(encoding="utf-8")
    powershell = (ROOT / "scripts" / "run-dev.ps1").read_text(encoding="utf-8-sig")

    assert 'EXTRA_ARGS=("$@")' in shell
    assert '"${EXTRA_ARGS[@]}"' in shell
    assert "$ExtraArgs" in powershell
    assert "@ExtraArgs" in powershell


def test_root_dist_launchers_use_the_versioned_artifact_and_forward_arguments() -> None:
    macos = (ROOT / "start-cryptobox.command").read_text(encoding="utf-8")
    windows = (ROOT / "start-cryptobox.ps1").read_text(encoding="utf-8")
    wrapper = (ROOT / "start-cryptobox.cmd").read_text(encoding="utf-8")

    assert 'dist/cryptobox-$version' in macos
    assert '"$binary" --root "$vault" "$@"' in macos
    assert "$HOME/CryptoboxVault" in macos
    assert "-newer \"$binary\"" in macos

    assert 'dist\\cryptobox-$Version.exe' in windows
    assert "& $Binary --root $Vault @ExtraArgs" in windows
    assert 'Join-Path $env:USERPROFILE "CryptoboxVault"' in windows
    assert "LastWriteTimeUtc -gt $binaryTimestamp" in windows

    assert "start-cryptobox.ps1" in wrapper
    assert "%*" in wrapper
