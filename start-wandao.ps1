param(
  [switch]$InstallOnly,
  [switch]$ForceInstall
)

$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ElectronDir = Join-Path $RootDir "wandao_electron"
$RuntimeDir = Join-Path $RootDir ".dev-runtime"
$NodeDir = Join-Path $RuntimeDir "node"
$NodeVersion = "v22.12.0"
$NodeChecksums = @{
  "node-v22.12.0-win-x64.zip" = "2b8f2256382f97ad51e29ff71f702961af466c4616393f767455501e6aece9b8"
  "node-v22.12.0-win-arm64.zip" = "17401720af48976e3f67c41e8968a135fb49ca1f88103a92e0e8c70605763854"
}

function Write-Step($message) {
  Write-Host ""
  Write-Host "==> $message" -ForegroundColor Cyan
}

function Write-Ok($message) {
  Write-Host "[OK] $message" -ForegroundColor Green
}

function Get-CommandPath($name) {
  $cmd = Get-Command $name -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  return $null
}

function Test-Url($url, $timeoutSeconds = 6, [switch]$Head) {
  $watch = [System.Diagnostics.Stopwatch]::StartNew()
  try {
    $method = if ($Head) { "Head" } else { "Get" }
    Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec $timeoutSeconds -Method $method | Out-Null
    $watch.Stop()
    return [pscustomobject]@{ Ok = $true; Ms = $watch.ElapsedMilliseconds; Url = $url }
  } catch {
    $watch.Stop()
    return [pscustomobject]@{ Ok = $false; Ms = 999999; Url = $url; Error = $_.Exception.Message }
  }
}

function Add-LocalNodeToPath {
  $localBin = $NodeDir
  if (Test-Path -LiteralPath (Join-Path $localBin "node.exe")) {
    $env:PATH = "$localBin;$env:PATH"
  }
}

function Get-WindowsNodePackageName {
  $arch = $env:PROCESSOR_ARCHITECTURE
  if ($arch -match "ARM64") {
    return "node-$NodeVersion-win-arm64.zip"
  }
  return "node-$NodeVersion-win-x64.zip"
}

function Install-LocalNode {
  Write-Step "Node.js/npm not found. Downloading local portable Node.js"
  New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null

  $packageName = Get-WindowsNodePackageName
  $expectedHash = $NodeChecksums[$packageName]
  if (-not $expectedHash) { throw "No trusted SHA-256 is configured for $packageName." }
  $mirrorUrl = "https://npmmirror.com/mirrors/node/$NodeVersion/$packageName"
  $officialUrl = "https://nodejs.org/dist/$NodeVersion/$packageName"
  $mirrorProbe = Test-Url $mirrorUrl 5 -Head
  $officialProbe = Test-Url $officialUrl 5 -Head
  $downloadUrl = $mirrorUrl

  if ($officialProbe.Ok -and (-not $mirrorProbe.Ok -or $officialProbe.Ms -lt $mirrorProbe.Ms)) {
    $downloadUrl = $officialUrl
  }

  $zipPath = Join-Path $RuntimeDir $packageName
  $extractDir = Join-Path $RuntimeDir "node-extract"
  if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
  if (Test-Path -LiteralPath $extractDir) { Remove-Item -LiteralPath $extractDir -Recurse -Force }
  if (Test-Path -LiteralPath $NodeDir) { Remove-Item -LiteralPath $NodeDir -Recurse -Force }

  Write-Host "Download URL: $downloadUrl"
  Invoke-WebRequest -Uri $downloadUrl -OutFile $zipPath -UseBasicParsing
  $actualHash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($actualHash -ne $expectedHash) {
    Remove-Item -LiteralPath $zipPath -Force
    throw "Node.js SHA-256 verification failed for $packageName."
  }
  Write-Ok "Node.js SHA-256 verified"
  Expand-Archive -LiteralPath $zipPath -DestinationPath $extractDir -Force
  $expanded = Get-ChildItem -LiteralPath $extractDir -Directory | Select-Object -First 1
  if (-not $expanded) { throw "Node.js extraction failed: extracted folder not found." }
  Move-Item -LiteralPath $expanded.FullName -Destination $NodeDir
  Remove-Item -LiteralPath $zipPath -Force
  Remove-Item -LiteralPath $extractDir -Recurse -Force
  Add-LocalNodeToPath
  Write-Ok "Local Node.js installed: $NodeDir"
}

function Ensure-NodeAndNpm {
  Write-Step "Checking Node.js/npm"
  Add-LocalNodeToPath

  $nodePath = Get-CommandPath "node"
  $npmPath = Get-CommandPath "npm"
  if ($nodePath -and $npmPath) {
    Write-Ok "Node.js found: $(& node --version)"
    Write-Ok "npm found: $(& npm --version)"
    return
  }

  Install-LocalNode
  $nodePath = Get-CommandPath "node"
  $npmPath = Get-CommandPath "npm"
  if (-not $nodePath -or -not $npmPath) {
    throw "Node.js/npm auto install failed. Please install Node.js 22 LTS manually and retry."
  }
}

function Select-NpmInstallMode {
  Write-Step "Checking npm network"
  $official = Test-Url "https://registry.npmjs.org/electron" 5
  $mirror = Test-Url "https://registry.npmmirror.com/electron" 5

  if ($official.Ok -and $mirror.Ok) {
    if ($official.Ms -le [int]($mirror.Ms * 1.3)) {
      Write-Ok "Using official npm registry, about $($official.Ms)ms"
      return "official"
    }
    Write-Ok "Using China npmmirror registry, about $($mirror.Ms)ms"
    return "cn"
  }

  if ($official.Ok) {
    Write-Ok "Using official npm registry"
    return "official"
  }

  if ($mirror.Ok) {
    Write-Ok "Using China npmmirror registry"
    return "cn"
  }

  Write-Host "Network probe failed. Falling back to China npmmirror registry." -ForegroundColor Yellow
  return "cn"
}

function Install-Dependencies($mode) {
  $electronModule = Join-Path $ElectronDir "node_modules\electron"
  $builderModule = Join-Path $ElectronDir "node_modules\electron-builder"
  if (-not $ForceInstall -and (Test-Path -LiteralPath $electronModule) -and (Test-Path -LiteralPath $builderModule)) {
    Write-Ok "Desktop dependencies already exist. Skipping npm install"
    return
  }

  Write-Step "Installing desktop dependencies"
  Push-Location $ElectronDir
  try {
    if ($mode -eq "cn") {
      & npm run install:cn
    } else {
      $env:npm_config_audit = "false"
      $env:npm_config_fund = "false"
      & npm install --no-audit --no-fund
    }
    if ($LASTEXITCODE -ne 0) {
      throw "npm install failed. Exit code: $LASTEXITCODE"
    }
  } finally {
    Pop-Location
  }
}

function Start-Wandao {
  Write-Step "Starting Wandao"
  Push-Location $ElectronDir
  try {
    & npm start
  } finally {
    Pop-Location
  }
}

if (-not (Test-Path -LiteralPath $ElectronDir)) {
  throw "wandao_electron folder not found. Please run this script from the Wandao project root."
}

Ensure-NodeAndNpm
$installMode = Select-NpmInstallMode
Install-Dependencies $installMode

if ($InstallOnly) {
  Write-Ok "Dependency check completed. Desktop app was not started."
  exit 0
}

Start-Wandao
