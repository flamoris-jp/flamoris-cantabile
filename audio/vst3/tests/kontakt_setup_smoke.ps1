$ErrorActionPreference='Stop'
$script=Join-Path $PSScriptRoot '..\Register-KontaktLibrary.ps1'
$tokens=$null; $errors=$null
[Management.Automation.Language.Parser]::ParseFile((Resolve-Path $script).Path,[ref]$tokens,[ref]$errors) | Out-Null
if ($errors.Count) { throw 'PowerShell setup helper failed to parse.' }
$temp=Join-Path ([IO.Path]::GetTempPath()) ('cantabile-kontakt-setup-'+[guid]::NewGuid().ToString('N'))
try {
    $first=Join-Path $temp 'original library'; $second=Join-Path $temp 'second library'
    [IO.Directory]::CreateDirectory($first) | Out-Null
    [IO.Directory]::CreateDirectory($second) | Out-Null
    $config=Join-Path $temp 'config\libraries.json'
    & $script -Configuration $config -LibraryId first -DisplayName Original -Root $first
    $before=Get-Content -LiteralPath $config -Raw
    & $script -Configuration $config -LibraryId first -DisplayName Original -Root $first
    if ((Get-Content -LiteralPath $config -Raw) -cne $before) { throw 'Existing registration was modified.' }
    & $script -Configuration $config -LibraryId second -DisplayName Second -Root $second
    $saved=Get-Content -LiteralPath $config -Raw | ConvertFrom-Json
    if ($saved.version -ne 1 -or @($saved.libraries).Count -ne 2 -or $saved.libraries[0].root -ne $first) { throw 'Existing library was not preserved.' }
    $before=Get-Content -LiteralPath $config -Raw
    try { & $script -Configuration $config -LibraryId forbidden -Root ([IO.Path]::GetPathRoot($temp)); throw 'drive-root-accepted' }
    catch { if ($_.Exception.Message -eq 'drive-root-accepted') { throw } }
    if ((Get-Content -LiteralPath $config -Raw) -cne $before) { throw 'Failed setup modified configuration.' }
    Write-Host 'PASS: setup helper preserves registered roots and fails before invalid writes.'
}
finally { if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Recurse -Force } }
