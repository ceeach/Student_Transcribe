# Student Transcribe - Build Executable & Windows Installer
# Automated PowerShell Build Script

$ErrorActionPreference = "Stop"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " Building Student Transcribe Desktop Executable & Installer " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

# 1. Ensure Icon Assets
Write-Host ""
Write-Host "[1/4] Checking application icons..." -ForegroundColor Yellow
if (-not (Test-Path "assets/icon.ico")) {
    Write-Host "Generating application icon..."
    python generate_icon.py
}
Write-Host "[OK] Icons ready." -ForegroundColor Green

# 2. Compile with PyInstaller
Write-Host ""
Write-Host "[2/4] Bundling application with PyInstaller..." -ForegroundColor Yellow
python -m PyInstaller notion_transcribe.spec --clean --noconfirm

if (-not (Test-Path "dist/Student Transcribe/Student Transcribe.exe")) {
    Write-Error "PyInstaller build failed: Student Transcribe.exe not found in dist/Student Transcribe/"
    exit 1
}
Write-Host "[OK] PyInstaller compilation succeeded!" -ForegroundColor Green

# 3. Locate Inno Setup Compiler (ISCC)
Write-Host ""
Write-Host "[3/4] Locating Inno Setup compiler..." -ForegroundColor Yellow
$IsccPaths = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 7\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 7\ISCC.exe",
    "C:\Program Files (x86)\Inno Setup 7\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe",
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
)

$IsccExe = $null
foreach ($path in $IsccPaths) {
    if (Test-Path $path) {
        $IsccExe = $path
        break
    }
}

if (-not $IsccExe) {
    Write-Host "Inno Setup compiler (ISCC.exe) not found in standard paths." -ForegroundColor Red
    Write-Host "Standalone portable folder available at: dist\Student Transcribe\" -ForegroundColor Cyan
    exit 0
}

Write-Host "Found ISCC at: $IsccExe" -ForegroundColor Green

# 4. Compile Installer Wizard
Write-Host ""
Write-Host "[4/4] Compiling Windows Setup Installer (.exe)..." -ForegroundColor Yellow
New-Item -ItemType Directory -Force -Path "output" | Out-Null
& $IsccExe "installer.iss"

$InstallerPath = "output\StudentTranscribe-Setup-v1.4.4.exe"
if (Test-Path $InstallerPath) {
    $SizeMB = [math]::Round((Get-Item $InstallerPath).Length / 1MB, 2)
    Write-Host ""
    Write-Host "==========================================================" -ForegroundColor Green
    Write-Host " BUILD COMPLETED SUCCESSFULLY! " -ForegroundColor Green
    Write-Host " Installer generated: $InstallerPath ($SizeMB MB)" -ForegroundColor Green
    Write-Host " Standalone folder:   dist\Student Transcribe\" -ForegroundColor Green
    Write-Host "==========================================================" -ForegroundColor Green
} else {
    Write-Warning "Setup executable was not created. Please check Inno Setup logs."
}
