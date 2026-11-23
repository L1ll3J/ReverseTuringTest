param([switch]$Development, [switch]$VerifyOnly)

$ErrorActionPreference = 'Stop'
$artifact = Join-Path $PSScriptRoot 'dist\AlibAi-Playtest.exe'
$metadata = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'PLAYTEST.json') -Raw | ConvertFrom-Json
if ($metadata.schema_version -ne 1 -or $metadata.source_commit -isnot [string] -or
    $metadata.source_commit -cnotmatch '^[0-9a-f]{40}$') {
    throw 'Invalid playtest manifest version or source revision.'
}
foreach ($entry in @(@($artifact, $metadata.exe_sha256), @($PSCommandPath, $metadata.launcher_sha256))) {
    if ($entry[1] -isnot [string] -or $entry[1] -cnotmatch '^[0-9a-f]{64}$' -or
        (Get-FileHash -LiteralPath $entry[0] -Algorithm SHA256).Hash.ToLowerInvariant() -cne $entry[1]) {
        throw 'Playtest artifact integrity check failed.'
    }
}
if ($VerifyOnly) {
    Write-Output 'Playtest manifest and file hashes match. This is not code signing or acceptance testing.'
    return
}
$playtestKey = [Environment]::GetEnvironmentVariable('OPENAI_API_KEY', 'Process')
if ([string]::IsNullOrWhiteSpace($playtestKey)) {
    throw 'OPENAI_API_KEY must already be configured in the launch environment.'
}
$startInfo = New-Object System.Diagnostics.ProcessStartInfo
$startInfo.FileName = $artifact
$startInfo.WorkingDirectory = $PSScriptRoot
$startInfo.UseShellExecute = $false
$startInfo.EnvironmentVariables['OPENAI_API_KEY'] = $playtestKey
$startInfo.EnvironmentVariables['ALIBAI_BUILD_REVISION'] = $metadata.source_commit
$startInfo.EnvironmentVariables['ALIBAI_DEV_MODE'] = '0'
$startInfo.EnvironmentVariables.Remove('ALIBAI_DEV_VOTE_MINIMUM')
if ($Development) {
    $startInfo.EnvironmentVariables['ALIBAI_DEV_MODE'] = '1'
}
[System.Diagnostics.Process]::Start($startInfo) | Out-Null
