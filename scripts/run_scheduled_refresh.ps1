param([int]$Season = 0)

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repoRoot
$python = Join-Path $repoRoot "sna-env\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    $python = Join-Path $repoRoot ".venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $python)) {
    $python = "python"
}
$arguments = @("-m", "sports_aggregator.scheduled_refresh")
if ($Season -gt 0) {
    $arguments += @("--season", $Season)
}
& $python @arguments
exit $LASTEXITCODE
