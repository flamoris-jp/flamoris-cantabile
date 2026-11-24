# A local operator helper, not an MCP/general script execution surface.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Configuration,
    [ValidatePattern('^[A-Za-z0-9_-]{1,64}$')][string]$LibraryId = 'original',
    [ValidateLength(1,128)][string]$DisplayName = 'Kontakt library',
    [string]$Root
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
function Assert-NoLinks([string]$Path) {
    $current = [IO.Path]::GetFullPath($Path)
    while ($current) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Linked configuration/content paths are unsupported.' }
        }
        $parent = Split-Path -Parent $current
        if ($parent -eq $current) { break }
        $current = $parent
    }
}
$Configuration = [IO.Path]::GetFullPath($Configuration)
Assert-NoLinks $Configuration
$libraries = @()
$exists = Test-Path -LiteralPath $Configuration -PathType Leaf
if ($exists) {
    if ((Get-Item -LiteralPath $Configuration).Length -gt 65536) { throw 'Configuration exceeds 64 KiB.' }
    $saved = Get-Content -LiteralPath $Configuration -Raw -Encoding UTF8 | ConvertFrom-Json
    if (@($saved.PSObject.Properties.Name | Where-Object { $_ -notin @('version','libraries') }).Count -or $saved.version -ne 1) { throw 'Unsupported configuration; it was not changed.' }
    $libraries = @($saved.libraries)
    if ($libraries.Count -gt 32) { throw 'Too many registered libraries.' }
    if (@($libraries | Where-Object { $_.id -eq $LibraryId }).Count) {
        Write-Host ('Library already registered: ' + $LibraryId)
        return
    }
}
if (-not $Root) {
    # Optional read-only installer hint, not a license or library API. Confirm
    # the directory in the picker; never edit Native Access/registry settings.
    $suggested = ''
    if ($LibraryId -eq 'playbox') {
        foreach ($key in @('Registry::HKEY_LOCAL_MACHINE\SOFTWARE\Native Instruments\Playbox',
                           'Registry::HKEY_CURRENT_USER\SOFTWARE\Native Instruments\Playbox')) {
            try {
                $candidate = [string](Get-ItemProperty -LiteralPath $key -Name ContentDir -ErrorAction Stop).ContentDir
                if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Container)) {
                    Assert-NoLinks $candidate
                    $suggested = [IO.Path]::GetFullPath($candidate)
                    break
                }
            } catch { }
        }
    }
    Add-Type -AssemblyName System.Windows.Forms
    $picker = New-Object System.Windows.Forms.FolderBrowserDialog
    try {
        $picker.Description = 'Choose the installed content folder for ' + $DisplayName + '. No samples or presets will be copied.'
        $picker.ShowNewFolderButton = $false
        if ($suggested) { $picker.SelectedPath = $suggested }
        if ($picker.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { throw 'Registration cancelled; existing configuration was not changed.' }
        $Root = $picker.SelectedPath
    }
    finally { $picker.Dispose() }
}
if ($Root -notmatch '^(?:[A-Za-z]:[\\/]|\\\\[^\\]+\\[^\\]+\\)') { throw 'An absolute Windows content directory is required.' }
$Root = [IO.Path]::GetFullPath($Root)
Assert-NoLinks $Root
if (-not (Test-Path -LiteralPath $Root -PathType Container) -or $Root.TrimEnd('\','/') -eq [IO.Path]::GetPathRoot($Root).TrimEnd('\','/')) { throw 'Choose a library content folder, not a drive root.' }
if ($libraries.Count -ge 32) { throw 'At most 32 library roots can be registered.' }
$libraries += [pscustomobject]@{ id=$LibraryId; name=$DisplayName; root=$Root }
$json = @{version=1;libraries=@($libraries)} | ConvertTo-Json -Depth 8
if ([Text.Encoding]::UTF8.GetByteCount($json) -gt 65536) { throw 'Configuration exceeds 64 KiB.' }
$parent = Split-Path -Parent $Configuration
[IO.Directory]::CreateDirectory($parent) | Out-Null
Assert-NoLinks $parent
$temporary = Join-Path $parent ('.kontakt-' + [guid]::NewGuid().ToString('N') + '.json')
try {
    $stream = [IO.File]::Open($temporary,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try { $bytes=[Text.Encoding]::UTF8.GetBytes($json); $stream.Write($bytes,0,$bytes.Length); $stream.Flush($true) }
    finally { $stream.Dispose() }
    Assert-NoLinks $Configuration
    if ($exists) { [IO.File]::Replace($temporary,$Configuration,($Configuration+'.backup-'+[guid]::NewGuid().ToString('N'))) }
    else { [IO.File]::Move($temporary,$Configuration) }
}
finally { if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary } }
Write-Host ('Library registered: ' + $DisplayName + '. Kontakt Developer features must be enabled separately in Kontakt.')
