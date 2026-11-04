param(
    [ValidateSet('run', 'prepare', 'train', 'test', 'setup', 'setup-training')]
    [string]$Action = 'run',
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ExtraArgs
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = '1'
$pythonExe = Join-Path $PSScriptRoot '.venv-local\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    $pythonExe = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
}
if ($Action -eq 'setup') {
    if (-not (Test-Path -LiteralPath $pythonExe)) {
        & py -3.12 -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.12, then retry setup.' }
    }
    & $pythonExe -m pip install torch==2.11.0 torchvision==0.26.0 --index-url https://download.pytorch.org/whl/cu128
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $pythonExe -m pip install -r requirements.txt
    exit $LASTEXITCODE
}
if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw 'Run: powershell -ExecutionPolicy Bypass -File .\run.ps1 setup'
}
switch ($Action) {
    'run'     { & $pythonExe main.py @ExtraArgs }
    'prepare' { & $pythonExe models.py --download --prepare-only @ExtraArgs }
    'train'   { & $pythonExe train_weights.py @ExtraArgs }
    'setup-training' { & $pythonExe -m pip install -r requirements-rfdetr-training.txt }
    'test'    { & $pythonExe -m unittest discover -s tests -v }
}
exit $LASTEXITCODE
