param(
    [ValidateSet('run', 'prepare', 'train', 'test', 'setup', 'setup-rfdetr', 'setup-training')]
    [string]$Action = 'run',
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ExtraArgs
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = '1'
$pythonExe = $null
foreach ($relative in @('.venv-local\Scripts\python.exe', '.venv\Scripts\python.exe')) {
    $candidate = Join-Path $PSScriptRoot $relative
    if (Test-Path -LiteralPath $candidate) {
        # A copied virtual environment can exist while its base Python is gone.
        try { & $candidate -c 'import sys' *> $null } catch { continue }
        if ($LASTEXITCODE -eq 0) {
            $pythonExe = $candidate
            break
        }
    }
}
if ($Action -eq 'setup') {
    if (-not $pythonExe) {
        & py -3.12 -m venv .venv-local
        if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.12, then retry setup.' }
        $pythonExe = Join-Path $PSScriptRoot '.venv-local\Scripts\python.exe'
    }
    & $pythonExe -m pip install -r requirements.txt
    exit $LASTEXITCODE
}
if (-not $pythonExe) {
    throw 'Run: powershell -ExecutionPolicy Bypass -File .\run.ps1 setup'
}
switch ($Action) {
    'run'     { & $pythonExe main.py @ExtraArgs }
    'prepare' { & $pythonExe models.py --download --prepare-only @ExtraArgs }
    'train'   { & $pythonExe train_weights.py @ExtraArgs }
    'setup-rfdetr' { & $pythonExe -m pip install -r requirements-rfdetr.txt }
    'setup-training' { & $pythonExe -m pip install -r requirements-rfdetr-training.txt }
    'test'    { & $pythonExe -m unittest discover -s tests -v }
}
exit $LASTEXITCODE
