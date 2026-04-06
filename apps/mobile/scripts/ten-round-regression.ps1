param(
  [int]$Rounds = 10,
  [string]$NodePath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-RepoRoot {
  return (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

function Resolve-NodeExecutable {
  param(
    [string]$PreferredPath
  )

  $candidates = @(
    $PreferredPath,
    "D:\software\node\node.exe",
    "D:\软件\node\node.exe",
    "C:\Program Files\nodejs\node.exe",
    "C:\Program Files (x86)\nodejs\node.exe"
  ) | Where-Object { $_ }

  foreach ($candidate in $candidates) {
    if (Test-Path -LiteralPath $candidate) {
      return (Resolve-Path -LiteralPath $candidate).Path
    }
  }

  throw "node.exe was not found. Pass -NodePath explicitly."
}

function Ensure-CleanDirectory {
  param(
    [string]$PathToClean,
    [string]$RepoRoot
  )

  $resolvedRoot = [System.IO.Path]::GetFullPath($RepoRoot)
  $resolvedTarget = [System.IO.Path]::GetFullPath($PathToClean)
  if (-not $resolvedTarget.StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to clean a directory outside the repo: $resolvedTarget"
  }

  if (Test-Path -LiteralPath $resolvedTarget) {
    Remove-Item -LiteralPath $resolvedTarget -Recurse -Force
  }
  New-Item -ItemType Directory -Path $resolvedTarget | Out-Null
}

function Invoke-LoggedCommand {
  param(
    [string]$NodeExe,
    [string]$ScriptPath,
    [string[]]$Arguments,
    [string]$LogPath
  )

  $output = & $NodeExe $ScriptPath @Arguments 2>&1
  $output | Out-File -LiteralPath $LogPath -Encoding utf8
  if ($LASTEXITCODE -ne 0) {
    if ($output) {
      $output | ForEach-Object { Write-Output $_ }
    }
    throw "Command failed: $ScriptPath $($Arguments -join ' ')"
  }
}

$repoRoot = Resolve-RepoRoot
Set-Location $repoRoot

$nodeExe = Resolve-NodeExecutable -PreferredPath $NodePath
$tscScript = Join-Path $repoRoot "node_modules\typescript\bin\tsc"
$expoCli = Join-Path $repoRoot "node_modules\expo\bin\cli"
$reportRoot = Join-Path $repoRoot ".smoke-regression"

$modules = @(
  @{ name = "BrandSplash"; file = "src/screens/BrandSplashScreen.tsx" },
  @{ name = "Login"; file = "src/screens/LoginScreen.tsx" },
  @{ name = "Register"; file = "src/screens/RegisterScreen.tsx" },
  @{ name = "AIWorkspace"; file = "src/screens/AIWorkspaceScreen.tsx" },
  @{ name = "WardOverview"; file = "src/screens/WardOverviewScreen.tsx" },
  @{ name = "TaskHub"; file = "src/screens/TaskHubScreen.tsx" },
  @{ name = "PatientDetail"; file = "src/screens/PatientDetailScreen.tsx" },
  @{ name = "DocumentEditor"; file = "src/screens/DocumentEditorScreen.tsx" },
  @{ name = "MessageThread"; file = "src/screens/MessageThreadScreen.tsx" },
  @{ name = "Profile"; file = "src/screens/ProfileScreen.tsx" }
)

Ensure-CleanDirectory -PathToClean $reportRoot -RepoRoot $repoRoot

$roundResults = @()

for ($round = 1; $round -le $Rounds; $round++) {
  $roundDir = Join-Path $reportRoot ("round-{0:00}" -f $round)
  $exportDir = Join-Path $roundDir "web-export"
  Ensure-CleanDirectory -PathToClean $roundDir -RepoRoot $repoRoot

  $typecheckLog = Join-Path $roundDir "typecheck.log"
  $exportLog = Join-Path $roundDir "expo-export.log"

  Invoke-LoggedCommand -NodeExe $nodeExe -ScriptPath $tscScript -Arguments @("-p", (Join-Path $repoRoot "tsconfig.json"), "--noEmit") -LogPath $typecheckLog
  Invoke-LoggedCommand -NodeExe $nodeExe -ScriptPath $expoCli -Arguments @("export", "--platform", "web", "--output-dir", $exportDir) -LogPath $exportLog

  $indexHtml = Join-Path $exportDir "index.html"
  $metadataJson = Join-Path $exportDir "metadata.json"
  $bundleDir = Join-Path $exportDir "_expo\static\js\web"

  if (-not (Test-Path -LiteralPath $indexHtml)) {
    throw "Round $round did not produce index.html"
  }
  if (-not (Test-Path -LiteralPath $metadataJson)) {
    throw "Round $round did not produce metadata.json"
  }
  $bundleFiles = @(Get-ChildItem -Path $bundleDir -Filter "*.js" -File -Recurse -ErrorAction Stop)
  if (-not $bundleFiles.Count) {
    throw "Round $round did not produce any web bundle files"
  }

  foreach ($module in $modules) {
    $modulePath = Join-Path $repoRoot $module.file
    if (-not (Test-Path -LiteralPath $modulePath)) {
      throw "Round $round is missing module file: $($module.file)"
    }
  }

  $roundResults += [PSCustomObject]@{
    round = $round
    status = "passed"
    bundle_count = $bundleFiles.Count
    export_dir = $exportDir
  }
}

$moduleSummary = foreach ($module in $modules) {
  [PSCustomObject]@{
    module = $module.name
    file = $module.file
    rounds_passed = $Rounds
    status = "passed"
  }
}

$report = [ordered]@{
  generated_at = (Get-Date).ToString("o")
  node_executable = $nodeExe
  rounds = $Rounds
  checks = @(
    "TypeScript full-project typecheck",
    "Expo web export bundle",
    "Core module file presence check"
  )
  modules = $moduleSummary
  rounds_detail = $roundResults
}

$reportPath = Join-Path $reportRoot "report.json"
$report | ConvertTo-Json -Depth 6 | Out-File -LiteralPath $reportPath -Encoding utf8

Write-Output "Ten-round regression finished. Report: $reportPath"
