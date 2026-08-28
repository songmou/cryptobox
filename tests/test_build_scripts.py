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
