param(
  [ValidateSet("cpu", "cuda")]
  [string]$Runtime = "cpu",
  [string]$ProjectRoot = "",
  [string]$AssetsRoot = ""
)

$ErrorActionPreference = "Stop"

if (-not $ProjectRoot) {
  $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

if (-not $AssetsRoot) {
  $AssetsRoot = Join-Path $ProjectRoot "ai model\local_cn"
}

function Ensure-Directory([string]$Path) {
  if (-not (Test-Path $Path)) {
    New-Item -Path $Path -ItemType Directory | Out-Null
  }
}

$toolsRoot = Join-Path $AssetsRoot "tools"
$runtimeRoot = Join-Path $toolsRoot "llama.cpp"
$zipRoot = Join-Path $toolsRoot "packages"

Ensure-Directory $runtimeRoot
Ensure-Directory $zipRoot

$releaseJson = & curl.exe -k -sL "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"
if (-not $releaseJson) {
  throw "Failed to fetch llama.cpp release metadata."
}
$release = $releaseJson | ConvertFrom-Json

function Find-Asset($assets, [string[]]$Patterns) {
  foreach ($pattern in $Patterns) {
    $hit = $assets | Where-Object { $_.name -like $pattern } | Select-Object -First 1
    if ($hit) {
      return $hit
    }
  }
  return $null
}

function Download-And-Extract($asset, [string]$destination) {
  if (-not $asset) {
    throw "Runtime download asset is missing."
  }
  $zipPath = Join-Path $zipRoot $asset.name
  Write-Host "Downloading llama.cpp runtime: $($asset.name)" -ForegroundColor Cyan
  & curl.exe -k -L --fail --retry 3 --retry-delay 4 -C - -o $zipPath $asset.browser_download_url
  if ($LASTEXITCODE -ne 0) {
    throw "Runtime download failed: $($asset.browser_download_url)"
  }

  Write-Host "Extracting runtime to: $destination" -ForegroundColor Cyan
  Expand-Archive -Path $zipPath -DestinationPath $destination -Force
}

if ($Runtime -eq "cuda") {
  $mainAsset = Find-Asset $release.assets @(
    "llama-b*-bin-win-cuda-12.4-x64.zip",
    "llama-b*-bin-win-cuda-13.1-x64.zip",
    "llama-b*-bin-win-cuda-*-x64.zip"
  )
  if (-not $mainAsset) {
    throw "Cannot find llama.cpp Windows CUDA binary package."
  }

  $cudaSuffix = $mainAsset.name -replace '^llama-b\d+-bin-win-(cuda-[^-]+-x64)\.zip$', '$1'
  $cudaRuntimeAsset = Find-Asset $release.assets @(
    "cudart-llama-bin-win-$cudaSuffix.zip"
  )

  Download-And-Extract $mainAsset $runtimeRoot
  if ($cudaRuntimeAsset) {
    Download-And-Extract $cudaRuntimeAsset $runtimeRoot
  } else {
    Write-Host "Warning: matching CUDA runtime DLL package not found; proceeding with main package only." -ForegroundColor Yellow
  }
} else {
  $cpuAsset = Find-Asset $release.assets @(
    "llama-b*-bin-win-cpu-x64.zip"
  )
  if (-not $cpuAsset) {
    throw "Cannot find llama.cpp Windows CPU binary package."
  }
  Download-And-Extract $cpuAsset $runtimeRoot
}

$server = Get-ChildItem -Path $runtimeRoot -Recurse -Filter "llama-server.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $server) {
  throw "llama-server.exe not found after extraction."
}

Write-Host "[OK] llama-server ready: $($server.FullName)" -ForegroundColor Green
