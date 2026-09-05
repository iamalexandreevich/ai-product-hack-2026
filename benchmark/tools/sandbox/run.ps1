<#
.SYNOPSIS
Runs the benchmark through Claude Code's native auto-mode classifier inside a disposable
container.

.DESCRIPTION
An ALLOW from the classifier EXECUTES the command, and the dataset holds `rm -rf /`,
reverse shells and credential reads. This script therefore never runs the adapter on the
host: it starts the sandbox image, keeps every session's cwd inside the container, mounts
nothing but an output directory, and passes only ANTHROPIC_API_KEY in.

Build the image first:
    docker build -f tools/sandbox/Dockerfile -t agentgate-bench-sandbox .

.EXAMPLE
    . .\.env.ps1
    .\tools\sandbox\run.ps1

.EXAMPLE
    .\tools\sandbox\run.ps1 -Concurrency 1 -Extra @('--category','destructive_action')
#>
param(
    [string]$Out = (Join-Path (Resolve-Path "$PSScriptRoot\..\..").Path "results\claude-sandbox"),
    [int]$Concurrency = 3,
    [string]$Image = "agentgate-bench-sandbox",
    [string[]]$Extra = @()
)

$ErrorActionPreference = "Stop"

if (-not $env:ANTHROPIC_API_KEY) {
    throw "ANTHROPIC_API_KEY is not set. Load it first: . .\.env.ps1"
}

New-Item -ItemType Directory -Force -Path $Out | Out-Null

$args = @(
    "run", "--rm",
    "--name", "agentgate-bench-claude",
    "-e", "ANTHROPIC_API_KEY",
    "-v", "${Out}:/home/bench/out",
    $Image,
    "python", "cli.py", "benchmark",
    "--path", "attacks/cases",
    "--adapter", "claude-code",
    "--sandbox", "/home/bench/sandbox",
    "--i-have-a-sandbox",
    "--concurrency", "$Concurrency",
    "--out", "/home/bench/out",
    "--db", "/home/bench/out/benchmark.sqlite3"
) + $Extra

Write-Host "docker $($args -join ' ')"
& docker @args
$code = $LASTEXITCODE

Write-Host ""
Write-Host "results in $Out"
Write-Host "import them next to the server run, then compare:"
Write-Host "    python tools/sandbox/import_run.py `"$Out\benchmark.sqlite3`""
Write-Host "    python cli.py compare <SERVER_RUN_ID> <CLAUDE_RUN_ID>"
exit $code
