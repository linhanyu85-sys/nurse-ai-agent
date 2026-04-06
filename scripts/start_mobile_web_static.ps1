param(
  [string]$MobileRoot = "",
  [string]$NodeRoot = "D:\软件\node",
  [int]$Port = 19007,
  [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = (Resolve-Path (Join-Path $scriptRoot "..")).Path

if ([string]::IsNullOrWhiteSpace($MobileRoot)) {
  $MobileRoot = Join-Path $projectRoot "apps\mobile"
}
$MobileRoot = (Resolve-Path $MobileRoot).Path

if (-not (Test-Path $NodeRoot)) {
  $npx = Get-Command npx.cmd -ErrorAction SilentlyContinue
  if ($npx) {
    $NodeRoot = Split-Path -Parent $npx.Source
  }
}

$npxCmd = Join-Path $NodeRoot "npx.cmd"
if (-not (Test-Path $npxCmd)) {
  throw "npx.cmd not found: $npxCmd"
}

function Test-PrivateLanIpv4 {
  param([string]$Address)
  return (
    $Address -match '^10\.' -or
    $Address -match '^192\.168\.' -or
    $Address -match '^172\.(1[6-9]|2\d|3[0-1])\.'
  )
}

function Stop-PortOwner {
  param([int]$TargetPort)

  $listeners = Get-NetTCPConnection -State Listen -LocalPort $TargetPort -ErrorAction SilentlyContinue
  if (-not $listeners) {
    return
  }

  $processIds = $listeners | Select-Object -ExpandProperty OwningProcess -Unique
  foreach ($processId in $processIds) {
    try {
      Stop-Process -Id $processId -Force -ErrorAction Stop
    } catch {
      Write-Host "[WARN] Failed to stop process using port $TargetPort (PID=$processId)." -ForegroundColor Yellow
    }
  }
  Start-Sleep -Milliseconds 600
}

function Get-PreferredLanIp {
  $configs = Get-NetIPConfiguration -ErrorAction SilentlyContinue | Where-Object {
    $_.IPv4Address -and $_.IPv4DefaultGateway
  }

  $candidates = foreach ($cfg in $configs) {
    $ipv4 = @($cfg.IPv4Address | Select-Object -ExpandProperty IPAddress -ErrorAction SilentlyContinue) |
      Where-Object {
        $_ -and
        $_ -notlike "169.254.*" -and
        $_ -ne "127.0.0.1" -and
        (Test-PrivateLanIpv4 $_)
      } |
      Select-Object -First 1

    if (-not $ipv4) {
      continue
    }

    $alias = [string]$cfg.InterfaceAlias
    $description = [string]$cfg.InterfaceDescription
    $metric = 9999
    try {
      $metric = [int](Get-NetIPInterface -AddressFamily IPv4 -InterfaceIndex $cfg.InterfaceIndex -ErrorAction Stop).InterfaceMetric
    } catch {
    }

    $score = 0
    if ($alias -match '(^|[\s_-])(WLAN|Wi-?Fi)([\s_-]|$)') {
      $score += 200
    }
    if ($description -match 'Wi-?Fi|Wireless|802\.11') {
      $score += 120
    }
    if ($alias -match 'Ethernet') {
      $score += 40
    }
    if ($ipv4 -like '192.168.*') {
      $score += 30
    }
    if ($alias -match 'vEthernet|Hyper-V|WSL|VMware|VirtualBox|Bluetooth|Loopback') {
      $score -= 1000
    }
    if ($description -match 'Hyper-V|VMware|VirtualBox|Bluetooth|Loopback') {
      $score -= 1000
    }

    [PSCustomObject]@{
      IPAddress = $ipv4
      InterfaceMetric = $metric
      Score = $score
    }
  }

  $best = $candidates |
    Sort-Object -Property @{ Expression = { $_.Score }; Descending = $true }, @{ Expression = { $_.InterfaceMetric }; Descending = $false } |
    Select-Object -First 1

  return $best.IPAddress
}

function Resolve-HostIp {
  $envFile = Join-Path $MobileRoot ".env"
  if (Test-Path $envFile) {
    $line = Get-Content $envFile | Where-Object { $_ -match "^EXPO_PUBLIC_API_BASE_URL=" } | Select-Object -First 1
    if ($line) {
      try {
        $uri = [System.Uri](($line -replace "^EXPO_PUBLIC_API_BASE_URL=", "").Trim())
        if ($uri.Host -match "^\d{1,3}(\.\d{1,3}){3}$") {
          $envHost = $uri.Host
          $exists = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { $_.IPAddress -eq $envHost } |
            Select-Object -First 1
          if ($exists) {
            return $envHost
          }
        }
      } catch {
      }
    }
  }

  return Get-PreferredLanIp
}

$outLog = Join-Path $MobileRoot ".web_static.out.log"
$errLog = Join-Path $MobileRoot ".web_static.err.log"
if (Test-Path $outLog) { Remove-Item $outLog -Force -ErrorAction SilentlyContinue }
if (Test-Path $errLog) { Remove-Item $errLog -Force -ErrorAction SilentlyContinue }

Stop-PortOwner -TargetPort $Port

if (-not $SkipBuild.IsPresent) {
  Write-Host "[WEB 1/3] Build web dist..." -ForegroundColor Cyan
  $oldPath = $env:Path
  $env:Path = "$NodeRoot;$env:Path"
  Push-Location $MobileRoot
  try {
    & $npxCmd expo export --platform web
  } finally {
    Pop-Location
    $env:Path = $oldPath
  }
}

Write-Host "[WEB 2/3] Start static web server..." -ForegroundColor Cyan
$oldPath = $env:Path
$env:Path = "$NodeRoot;$env:Path"
Start-Process -FilePath $npxCmd `
  -WorkingDirectory $MobileRoot `
  -ArgumentList @("serve", "-s", "dist", "-l", "$Port") `
  -RedirectStandardOutput $outLog `
  -RedirectStandardError $errLog `
  -WindowStyle Minimized | Out-Null
$env:Path = $oldPath

Write-Host "[WEB 3/3] Wait for service..." -ForegroundColor Cyan
$ready = $false
for ($i = 0; $i -lt 20; $i++) {
  Start-Sleep -Milliseconds 800
  try {
    $status = Invoke-WebRequest -UseBasicParsing ("http://127.0.0.1:{0}" -f $Port) -TimeoutSec 2
    if ($status.StatusCode -eq 200) {
      $ready = $true
      break
    }
  } catch {
  }
}

if (-not $ready) {
  throw "Static web server startup timed out. Check $errLog"
}

$hostIp = Resolve-HostIp
Write-Host ("[OK] Web: http://localhost:{0}" -f $Port) -ForegroundColor Green
if ($hostIp) {
  Write-Host ("[OK] LAN: http://{0}:{1}" -f $hostIp, $Port) -ForegroundColor Green
}
