$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
& "$ProjectDir\.venv\Scripts\cryptobox.exe" @args
exit $LASTEXITCODE

