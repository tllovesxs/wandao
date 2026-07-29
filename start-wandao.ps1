param(
  [switch]$InstallOnly,
  [switch]$ForceInstall
)

$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppDir = Join-Path $RootDir "wandao_electron"
$RuntimeDir = Join-Path $RootDir ".dev-runtime"
$NodeDir = Join-Path $RuntimeDir "node"
$NodeVersion = "v22.12.0"
$RustVersion = "1.88.0"
$RustToolchain = "1.88.0"
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

function Invoke-NativeCapture($executable, [string[]]$arguments) {
  $previousErrorActionPreference = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    $output = (& $executable @arguments 2>&1 | Out-String).Trim()
    $exitCode = $LASTEXITCODE
    return [pscustomobject]@{ Output = $output; ExitCode = $exitCode }
  } finally {
    $ErrorActionPreference = $previousErrorActionPreference
  }
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

function Get-WindowsArchitecture {
  $arch = $env:PROCESSOR_ARCHITEW6432
  if (-not $arch) { $arch = $env:PROCESSOR_ARCHITECTURE }
  if ($arch -match "ARM64") { return "arm64" }
  if ($arch -match "AMD64") { return "x64" }
  throw "Unsupported Windows architecture: $arch. Wandao supports Windows x64 and ARM64 development hosts."
}

function Get-WindowsNodePackageName {
  if ((Get-WindowsArchitecture) -eq "arm64") {
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
    & node -e "const [major, minor] = process.versions.node.split('.').map(Number); process.exit(major > 22 || (major === 22 && minor >= 12) ? 0 : 1)"
    if ($LASTEXITCODE -eq 0) {
      Write-Ok "Node.js found: $(& node --version)"
      Write-Ok "npm found: $(& npm --version)"
      return
    }
    Write-Host "Installed Node.js is older than 22.12.0. Switching to the pinned local runtime." -ForegroundColor Yellow
  }

  Install-LocalNode
  $nodePath = Get-CommandPath "node"
  $npmPath = Get-CommandPath "npm"
  if (-not $nodePath -or -not $npmPath) {
    throw "Node.js/npm auto install failed. Please install Node.js 22 LTS manually and retry."
  }
}

function Ensure-RustToolchain {
  Write-Step "Checking Rust $RustVersion toolchain"
  $rustupPath = Get-CommandPath "rustup"
  if ($rustupPath) {
    $rustcResult = Invoke-NativeCapture $rustupPath @("run", $RustToolchain, "rustc", "--version")
    if ($rustcResult.ExitCode -ne 0 -or $rustcResult.Output -notmatch "^rustc $([regex]::Escape($RustVersion))(?:\s|$)") {
      throw "Rust $RustVersion is required but the '$RustToolchain' toolchain is unavailable. Run 'rustup toolchain install $RustToolchain' and retry."
    }
    $cargoResult = Invoke-NativeCapture $rustupPath @("run", $RustToolchain, "cargo", "--version")
    if ($cargoResult.ExitCode -ne 0) {
      throw "Cargo for Rust $RustVersion is unavailable. Run 'rustup toolchain install $RustToolchain' and retry."
    }
    $env:RUSTUP_TOOLCHAIN = $RustToolchain
    Write-Ok "$($rustcResult.Output)"
    Write-Ok "$($cargoResult.Output)"
    return
  }

  $rustcPath = Get-CommandPath "rustc"
  $cargoPath = Get-CommandPath "cargo"
  if (-not $rustcPath -or -not $cargoPath) {
    throw "Rust $RustVersion and Cargo are required for Tauri development. Install rustup from https://rustup.rs/ and retry."
  }
  $rustcResult = Invoke-NativeCapture $rustcPath @("--version")
  if ($rustcResult.ExitCode -ne 0 -or $rustcResult.Output -notmatch "^rustc $([regex]::Escape($RustVersion))(?:\s|$)") {
    throw "Rust $RustVersion is required, but '$($rustcResult.Output)' is active. Install or activate Rust $RustVersion and retry."
  }
  $cargoResult = Invoke-NativeCapture $cargoPath @("--version")
  if ($cargoResult.ExitCode -ne 0) { throw "Cargo could not run: $($cargoResult.Output)" }
  Write-Ok "$($rustcResult.Output)"
  Write-Ok "$($cargoResult.Output)"
}

function Test-WebView2Runtime {
  $clientId = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
  $registryPaths = @(
    "Registry::HKEY_LOCAL_MACHINE\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\$clientId",
    "Registry::HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\EdgeUpdate\Clients\$clientId",
    "Registry::HKEY_CURRENT_USER\SOFTWARE\Microsoft\EdgeUpdate\Clients\$clientId"
  )
  foreach ($registryPath in $registryPaths) {
    $runtime = Get-ItemProperty -LiteralPath $registryPath -ErrorAction SilentlyContinue
    if ($runtime -and $runtime.pv) { return $true }
  }
  return $false
}

function Ensure-WindowsPrerequisites {
  Write-Step "Checking Windows Tauri prerequisites"
  $nativeArch = Get-WindowsArchitecture
  $vcComponent = if ($nativeArch -eq "arm64") {
    "Microsoft.VisualStudio.Component.VC.Tools.ARM64"
  } else {
    "Microsoft.VisualStudio.Component.VC.Tools.x86.x64"
  }
  $vswherePath = Join-Path ([Environment]::GetFolderPath("ProgramFilesX86")) "Microsoft Visual Studio\Installer\vswhere.exe"
  $visualCppReady = $false
  if (Test-Path -LiteralPath $vswherePath) {
    $vsInstall = (& $vswherePath -latest -products "*" -requires $vcComponent -property installationPath 2>$null | Select-Object -First 1)
    $visualCppReady = -not [string]::IsNullOrWhiteSpace($vsInstall)
  }
  if (-not $visualCppReady -and (Get-CommandPath "cl.exe")) {
    $visualCppReady = $true
  }
  if (-not $visualCppReady) {
    throw "Microsoft C++ Build Tools are required for Tauri. Install Visual Studio 2022 Build Tools with 'Desktop development with C++' (including the $nativeArch compiler) and retry."
  }

  $sdkRoot = $env:WindowsSdkDir
  if (-not $sdkRoot) {
    $sdkRegistry = Get-ItemProperty -LiteralPath "Registry::HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows Kits\Installed Roots" -ErrorAction SilentlyContinue
    if ($sdkRegistry) { $sdkRoot = $sdkRegistry.KitsRoot10 }
  }
  if (-not $sdkRoot) {
    $sdkRoot = Join-Path ([Environment]::GetFolderPath("ProgramFilesX86")) "Windows Kits\10"
  }
  $windowsSdkReady = $false
  if ($sdkRoot) {
    $includeRoot = Join-Path $sdkRoot "Include"
    $windowsSdkReady = @(Get-ChildItem -LiteralPath $includeRoot -Directory -ErrorAction SilentlyContinue | Where-Object {
      Test-Path -LiteralPath (Join-Path $_.FullName "um\Windows.h")
    }).Count -gt 0
  }
  if (-not $windowsSdkReady) {
    throw "Windows 10/11 SDK headers were not found. Add a Windows SDK through Visual Studio Installer and retry."
  }
  if (-not (Test-WebView2Runtime)) {
    throw "Microsoft Edge WebView2 Runtime is required to display the Tauri window. Install the Evergreen Runtime from https://developer.microsoft.com/microsoft-edge/webview2/ and retry."
  }
  Write-Ok "Visual C++ Build Tools, Windows SDK, and WebView2 Runtime found"
}

function Select-NpmInstallMode {
  Write-Step "Checking npm network"
  $official = Test-Url "https://registry.npmjs.org/@tauri-apps%2fcli" 5
  $mirror = Test-Url "https://registry.npmmirror.com/@tauri-apps%2fcli" 5

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

function Get-TauriLockVersion($lockPath) {
  $nodeScript = "try { const fs=require('fs'); const lock=JSON.parse(fs.readFileSync(process.argv[1], 'utf8')); const entry=lock.packages && lock.packages['node_modules/@tauri-apps/cli']; if (entry && typeof entry.version === 'string') process.stdout.write(entry.version); } catch (_) {}"
  $result = Invoke-NativeCapture "node" @("-e", $nodeScript, $lockPath)
  if ($result.ExitCode -ne 0 -or -not $result.Output) { return $null }
  return $result.Output
}

function Get-TauriManifestVersion($manifestPath) {
  $nodeScript = "try { const fs=require('fs'); const manifest=JSON.parse(fs.readFileSync(process.argv[1], 'utf8')); const version=manifest.devDependencies && manifest.devDependencies['@tauri-apps/cli']; if (typeof version === 'string') process.stdout.write(version); } catch (_) {}"
  $result = Invoke-NativeCapture "node" @("-e", $nodeScript, $manifestPath)
  if ($result.ExitCode -ne 0 -or -not $result.Output) { return $null }
  return $result.Output
}

function Test-TauriDependenciesReady {
  $appManifest = Join-Path $AppDir "package.json"
  $projectLock = Join-Path $AppDir "package-lock.json"
  $installedLock = Join-Path $AppDir "node_modules\.package-lock.json"
  $tauriPackage = Join-Path $AppDir "node_modules\@tauri-apps\cli\package.json"
  $tauriScript = Join-Path $AppDir "node_modules\@tauri-apps\cli\tauri.js"
  $tauriShim = Join-Path $AppDir "node_modules\.bin\tauri.cmd"
  if (-not (Test-Path -LiteralPath $appManifest) -or
      -not (Test-Path -LiteralPath $projectLock) -or
      -not (Test-Path -LiteralPath $installedLock) -or
      -not (Test-Path -LiteralPath $tauriPackage) -or
      -not (Test-Path -LiteralPath $tauriScript) -or
      -not (Test-Path -LiteralPath $tauriShim)) {
    return $false
  }

  $declaredVersion = Get-TauriManifestVersion $appManifest
  $lockedVersion = Get-TauriLockVersion $projectLock
  $installedLockVersion = Get-TauriLockVersion $installedLock
  try {
    $installedVersion = [string](Get-Content -Raw -LiteralPath $tauriPackage | ConvertFrom-Json).version
  } catch {
    return $false
  }
  if (-not $declaredVersion -or $declaredVersion -ne $lockedVersion -or
      $lockedVersion -ne $installedLockVersion -or $lockedVersion -ne $installedVersion) {
    return $false
  }

  $cliResult = Invoke-NativeCapture "node" @($tauriScript, "--version")
  return $cliResult.ExitCode -eq 0
}

function Install-Dependencies {
  $appManifest = Join-Path $AppDir "package.json"
  $projectLock = Join-Path $AppDir "package-lock.json"
  $declaredVersion = Get-TauriManifestVersion $appManifest
  $lockedVersion = Get-TauriLockVersion $projectLock
  if (-not $declaredVersion -or -not $lockedVersion -or $declaredVersion -ne $lockedVersion) {
    throw "package.json and package-lock.json must declare the same pinned @tauri-apps/cli version. Refusing an unlocked desktop dependency install."
  }
  if (-not $ForceInstall -and (Test-TauriDependenciesReady)) {
    Write-Ok "Tauri CLI matches package-lock.json. Skipping npm install"
    return
  }

  $mode = Select-NpmInstallMode
  $registry = if ($mode -eq "cn") { "https://registry.npmmirror.com/" } else { "https://registry.npmjs.org/" }
  Write-Step "Installing Tauri desktop dependencies"
  Push-Location $AppDir
  try {
    & npm ci "--registry=$registry" "--replace-registry-host=always" --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) {
      throw "npm ci failed. Exit code: $LASTEXITCODE"
    }
  } finally {
    Pop-Location
  }
  if (-not (Test-TauriDependenciesReady)) {
    throw "npm completed, but @tauri-apps/cli does not match package-lock.json or cannot run on this platform."
  }
}

function Start-Wandao {
  Write-Step "Starting Wandao with Tauri"
  Push-Location $AppDir
  try {
    & npm run dev
    if ($LASTEXITCODE -ne 0) {
      throw "Tauri development process failed. Exit code: $LASTEXITCODE"
    }
  } finally {
    Pop-Location
  }
}

if (-not (Test-Path -LiteralPath $AppDir)) {
  throw "wandao_electron folder not found. Please run this script from the Wandao project root."
}

Ensure-NodeAndNpm
Ensure-RustToolchain
Ensure-WindowsPrerequisites
Install-Dependencies

if ($InstallOnly) {
  Write-Ok "Node.js, Tauri CLI, Rust, and Windows prerequisite checks completed. Desktop app was not started."
  exit 0
}

Start-Wandao
