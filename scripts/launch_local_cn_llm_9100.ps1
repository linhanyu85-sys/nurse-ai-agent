param(
  [switch]$UseGpu
)

$ErrorActionPreference = "Stop"

$starter = Join-Path $PSScriptRoot "start_local_cn_llm.ps1"
if (-not (Test-Path $starter)) {
  throw "Starter script not found: $starter"
}

if ($UseGpu.IsPresent) {
  & $starter -Profile "minicpm4b" -Port 9100 -ContextSize 2048 -StartupTimeoutSec 240 -UseGpu
} else {
  & $starter -Profile "minicpm4b" -Port 9100 -ContextSize 2048 -StartupTimeoutSec 240
}
