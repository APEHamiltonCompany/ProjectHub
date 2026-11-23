param([switch]$Console)

$ErrorActionPreference = "Stop"

$root = $PSScriptRoot
Set-Location $root
$total = 8

function Write-Step($number, $title) {
    Write-Output ""
    Write-Output ("[{0}/{1}] {2}" -f $number, $total, $title)
}

function Get-PythonVersion($exe, $prefix) {
    $cmdArgs = @()
    if ($prefix) { $cmdArgs += $prefix }
    $cmdArgs += @("-c", "import sys;print('%d.%d' % sys.version_info[:2])")
    try {
        $text = (& $exe @cmdArgs 2>$null | Select-Object -First 1)
    } catch {
        return $null
    }
    if (-not $text) { return $null }
    $text = "$text".Trim()
    if ($text -match '^(\d+)\.(\d+)$') {
        if ([int]$Matches[1] -eq 3 -and [int]$Matches[2] -ge 10) { return $text }
    }
    return $null
}

function Find-Python {
    foreach ($spec in @(
        @{ Exe = "py"; Args = @("-3.12") },
        @{ Exe = "py"; Args = @("-3") },
        @{ Exe = "python"; Args = @() }
    )) {
        $command = Get-Command $spec.Exe -ErrorAction SilentlyContinue
        if (-not $command) { continue }
        $version = Get-PythonVersion $command.Source $spec.Args
        if ($version) {
            return @{ Path = $command.Source; Args = $spec.Args; Version = $version }
        }
    }
    $local = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
    if (Test-Path $local) {
        $version = Get-PythonVersion $local @()
        if ($version) { return @{ Path = $local; Args = @(); Version = $version } }
    }
    return $null
}

Write-Step 1 "Python"
$python = Find-Python
if (-not $python) {
    Write-Output "No Python 3.10 or newer found."
    $answer = Read-Host "Install Python 3.12 now with winget? [Y/n]"
    if ($answer -eq "" -or $answer -match '^[Yy]') {
        $winget = Get-Command winget -ErrorAction SilentlyContinue
        if (-not $winget) {
            Write-Output "winget is not available. Download Python 3.12 from https://www.python.org/downloads/windows/ and run setup.cmd again."
            exit 1
        }
        & $winget.Source install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
        $local = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
        if (Test-Path $local) {
            $version = Get-PythonVersion $local @()
            if ($version) { $python = @{ Path = $local; Args = @(); Version = $version } }
        }
    }
}
if (-not $python) {
    Write-Output "Python 3.10 or newer is required. Download it from https://www.python.org/downloads/windows/ and run setup.cmd again."
    exit 1
}
Write-Output ("Using Python {0}: {1}" -f $python.Version, $python.Path)

Write-Step 2 "Virtual environment"
$venvDir = Join-Path $root ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    $pyExe = $python.Path
    $pyArgs = $python.Args
    & $pyExe @pyArgs -m venv $venvDir
    if (-not (Test-Path $venvPython)) {
        Write-Output "Could not create .venv. Check the Python installation and run setup.cmd again."
        exit 1
    }
}
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    Write-Output "Upgrading pip failed. See the pip output above."
    exit 1
}
& $venvPython -m pip install -r (Join-Path $root "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    Write-Output "Installing requirements failed. See the pip output above."
    exit 1
}
Write-Output "Dependencies installed in .venv"

Write-Step 3 "Java (for .mpp schedules)"
try {
    $java = $null
    if ($env:JAVA_HOME) {
        $candidate = Join-Path $env:JAVA_HOME "bin\java.exe"
        if (Test-Path $candidate) { $java = $candidate }
    }
    if (-not $java -and $env:LOCALAPPDATA) {
        $jreRoot = Join-Path $env:LOCALAPPDATA "ProjectHub\jre"
        if (Test-Path $jreRoot) {
            $found = Get-ChildItem -Path $jreRoot -Filter "java.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($found) { $java = $found.FullName }
        }
    }
    if (-not $java) {
        $command = Get-Command java -ErrorAction SilentlyContinue
        if ($command) { $java = $command.Source }
    }
    if ($java) {
        Write-Output "Java found: $java"
    } else {
        $answer = Read-Host "Download a portable Java runtime (Microsoft OpenJDK 21, ~200 MB) for reading .mpp schedules? [y/N]"
        if ($answer -match '^[Yy]') {
            $zip = Join-Path ([System.IO.Path]::GetTempPath()) "projecthub-jdk21.zip"
            $target = Join-Path $env:LOCALAPPDATA "ProjectHub\jre"
            Invoke-WebRequest -Uri "https://aka.ms/download-jdk/microsoft-jdk-21-windows-x64.zip" -OutFile $zip -UseBasicParsing
            New-Item -ItemType Directory -Force -Path $target | Out-Null
            Expand-Archive -Path $zip -DestinationPath $target -Force
            Remove-Item $zip -Force -ErrorAction SilentlyContinue
            $found = Get-ChildItem -Path $target -Filter "java.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($found) {
                Write-Output "Java installed: $($found.FullName)"
            } else {
                Write-Warning "The Java download finished but java.exe was not found under $target."
            }
        } else {
            Write-Output ".mpp schedules need Java 11 or newer. A Project 'Save as XML' copy works without it."
        }
    }
} catch {
    Write-Warning "Java setup was skipped: $($_.Exception.Message)"
}

Write-Step 4 "Outlook"
if (Test-Path "Registry::HKEY_CLASSES_ROOT\Outlook.Application") {
    Write-Output "Classic Outlook is registered."
} else {
    Write-Output "WARNING: classic Outlook was not found. Reading email and calendar needs classic Outlook, not New Outlook. Setup continues."
}

Write-Step 5 "Configuration"
if ($Console) {
    & $venvPython -m tracker.setup_wizard
} else {
    Write-Output "Opening the setup window (run setup.cmd -Console for the text prompts instead)..."
    & $venvPython -m tracker.setup_gui
}
if ($LASTEXITCODE -ne 0) {
    Write-Output "The configuration wizard failed. See the output above."
    exit 1
}

Write-Step 6 "Background services"
$configPath = Join-Path $root "tracker\config.yaml"
$port = 8765
try {
    $portText = & $venvPython -c "import sys,yaml;print((yaml.safe_load(open(sys.argv[1],encoding='utf-8')) or {}).get('hub',{}).get('port') or 8765)" $configPath
    if ($portText) { $port = [int]("$portText".Trim()) }
} catch {
    $port = 8765
}
$answer = Read-Host "Start the hub automatically at login and run the 6:30 AM weekday tracker? [Y/n]"
if ($answer -eq "" -or $answer -match '^[Yy]') {
    & (Join-Path $root "install_hub_startup.ps1")
    & (Join-Path $root "install_schedule.ps1")
    Stop-ScheduledTask -TaskName ProjectHub -ErrorAction SilentlyContinue
    Start-ScheduledTask -TaskName ProjectHub
    $url = "http://127.0.0.1:$port/api/status"
    $up = $false
    for ($i = 0; $i -lt 60; $i++) {
        try {
            $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 5
            if ($response.StatusCode -eq 200) { $up = $true; break }
        } catch {
        }
        Start-Sleep -Seconds 1
    }
    if ($up) {
        Write-Output "Hub is up at http://localhost:$port/"
    } else {
        Write-Output "Hub did not answer within 60 seconds. Check the ProjectHub scheduled task and output\hub.log."
    }
}

Write-Step 7 "Desktop shortcut"
$desktop = [Environment]::GetFolderPath("Desktop")
$shortcut = Join-Path $desktop "Project Hub.url"
Set-Content -Path $shortcut -Value @("[InternetShortcut]", "URL=http://localhost:$port/") -Encoding ASCII
Write-Output "Shortcut: $shortcut"

Write-Step 8 "Chrome extension (optional)"
$answer = Read-Host "Set up the Chrome tab-group extension now? [Y/n]"
if ($answer -eq "" -or $answer -match '^[Yy]') {
    $extension = Join-Path $root "chrome_extension"
    Start-Process explorer.exe -ArgumentList $extension
    try {
        Set-Clipboard -Value $extension
    } catch {
        Write-Output "Could not copy the folder path to the clipboard."
    }
    $candidates = @()
    if ($env:ProgramFiles) { $candidates += (Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe") }
    if (${env:ProgramFiles(x86)}) { $candidates += (Join-Path ${env:ProgramFiles(x86)} "Google\Chrome\Application\chrome.exe") }
    if ($env:LOCALAPPDATA) { $candidates += (Join-Path $env:LOCALAPPDATA "Google\Chrome\Application\chrome.exe") }
    $chrome = $null
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { $chrome = $candidate; break }
    }
    if ($chrome) { Start-Process $chrome -ArgumentList "chrome://extensions" }
    Write-Output "1. Turn on Developer mode (top right)."
    Write-Output "2. Click Load unpacked."
    Write-Output "3. Paste the folder path (already on the clipboard) and press Enter."
}

Start-Process "http://localhost:$port/"
Write-Output ""
Write-Output "Setup complete."
Write-Output "  config: $configPath"
Write-Output "  hub:    http://localhost:$port/"
Write-Output "  rerun:  setup.cmd (your answers become the new defaults)"
Write-Output "  guide:  $(Join-Path $root 'USER_GUIDE.md')"
