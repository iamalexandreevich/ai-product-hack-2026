<#
.SYNOPSIS
Loads benchmark/.env into the current PowerShell session.

.DESCRIPTION
The benchmark reads its configuration from the environment, not from a file: nothing in
the code depends on a dotenv library. So .env has to be loaded into the session before
`cli.py` or `run.ps1` will see it.

Dot-source it (the leading dot matters -- without it the variables are set in a child
session and vanish):

    . .\tools\load-env.ps1
    uv run python cli.py benchmark --path attacks/cases --allow-remote

`tools\sandbox\run.ps1` calls this itself, so a sandbox run needs no separate step.

Format: KEY=value per line, `#` comments and blank lines ignored, surrounding single or
double quotes stripped. A value may contain `=` -- only the first one splits the line.

.PARAMETER Path
The .env file to load. Defaults to benchmark/.env.

.PARAMETER Quiet
Suppress the summary of what was loaded.
#>
param(
    [string]$Path = (Join-Path (Resolve-Path "$PSScriptRoot\..").Path ".env"),
    [switch]$Quiet
)

if (-not (Test-Path $Path)) {
    throw "No .env at $Path. Copy .env.example to .env and fill it in."
}

$loaded = @()
foreach ($line in Get-Content -Path $Path -Encoding UTF8) {
    $trimmed = $line.Trim()
    if ($trimmed -eq "" -or $trimmed.StartsWith("#")) { continue }

    $split = $trimmed.IndexOf("=")
    if ($split -lt 1) { continue }

    $name = $trimmed.Substring(0, $split).Trim()
    $value = $trimmed.Substring($split + 1).Trim()
    if ($value.Length -ge 2) {
        $first = $value[0]
        if (($first -eq '"' -or $first -eq "'") -and $value[-1] -eq $first) {
            $value = $value.Substring(1, $value.Length - 2)
        }
    }

    Set-Item -Path "Env:$name" -Value $value
    $loaded += $name
}

if (-not $Quiet) {
    # Names only. The values are the secrets this file exists to keep out of the terminal.
    Write-Host "loaded from $Path : $($loaded -join ', ')"
}
