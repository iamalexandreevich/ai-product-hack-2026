<#
.SYNOPSIS
Runs the benchmark through Claude Code's native auto-mode classifier inside a disposable
container.

.DESCRIPTION
An ALLOW from the classifier EXECUTES the command, and the dataset holds `rm -rf /`,
reverse shells and credential reads. This script therefore never runs the adapter on the
host: it starts the sandbox image, keeps every session's cwd inside the container, mounts
nothing but an output directory, and passes in one credential and nothing else.

The credential is CLAUDE_CODE_OAUTH_TOKEN (a subscription token, so the run costs no API
credit) or ANTHROPIC_API_KEY. The CLI accepts either and the invocation is identical --
only the variable name differs. Exactly one must be set: neither takes precedence over
the other, and setting both is refused rather than resolved, so a run can never
authenticate as something other than what the operator meant.

Whichever it is, it is the only secret in the container -- and the dataset's
credential_access cases run in there and can read the environment, so prefer a
credential you can revoke afterwards.

Build the image first:
    docker build -f tools/sandbox/Dockerfile -t agentgate-bench-sandbox .

The credential comes from benchmark/.env, which this script loads itself -- see
.env.example for what belongs in it. An already-set environment variable wins, so a
one-off run can override without editing the file.

.EXAMPLE
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

# Load .env unless the session already carries a credential, so an explicitly exported
# variable is never silently overwritten by the file.
if (-not ($env:CLAUDE_CODE_OAUTH_TOKEN -or $env:ANTHROPIC_API_KEY)) {
    . (Join-Path $PSScriptRoot "..\load-env.ps1")
}

# Exactly one, and no tie-break: picking a winner would authenticate the run as something
# the operator did not choose, and the two bill differently -- one against the Claude
# subscription, the other against API credit. Refusing is the only honest resolution.
$set = @()
if ($env:CLAUDE_CODE_OAUTH_TOKEN) { $set += "CLAUDE_CODE_OAUTH_TOKEN" }
if ($env:ANTHROPIC_API_KEY) { $set += "ANTHROPIC_API_KEY" }

if ($set.Count -eq 0) {
    throw "No credential set (see .env.example). Fill in exactly one: CLAUDE_CODE_OAUTH_TOKEN from ``claude setup-token``, or ANTHROPIC_API_KEY scoped to a workspace."
}
if ($set.Count -gt 1) {
    throw "Both CLAUDE_CODE_OAUTH_TOKEN and ANTHROPIC_API_KEY are set. Exactly one is allowed -- neither takes precedence, so which account pays would be a guess. Clear one in .env (or in this session) and run again."
}

$name = $set[0]
$credential = "$name=$((Get-Item "Env:$name").Value)"
Write-Host "authenticating the sandbox as $name"

New-Item -ItemType Directory -Force -Path $Out | Out-Null

$args = @(
    "run", "--rm",
    "--name", "agentgate-bench-claude",
    "-e", $credential,
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

# Echo the command, never the secret: the credential is passed as NAME=value, so printing
# $args verbatim would put the whole token in the terminal and its scrollback.
$shown = $args | ForEach-Object { if ($_ -eq $credential) { "$name=***" } else { $_ } }
Write-Host "docker $($shown -join ' ')"
& docker @args
$code = $LASTEXITCODE

Write-Host ""
Write-Host "results in $Out"
Write-Host "import them next to the server run, then compare:"
Write-Host "    python tools/sandbox/import_run.py `"$Out\benchmark.sqlite3`""
Write-Host "    python cli.py compare <SERVER_RUN_ID> <CLAUDE_RUN_ID>"
exit $code
