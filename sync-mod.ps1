# Requires -Version 5.1
[CmdletBinding()]
param (
    [Parameter(Mandatory=$false, Position=0)]
    [string]$ModDir
)

if (-not $ModDir) {
    Write-Host "Usage: .\sync-mod.ps1 <path-to-mod>"
    Write-Host "Example: .\sync-mod.ps1 ..\mod"
    exit 1
}

$BuildLogicDir = $PSScriptRoot

if (-not (Test-Path -Path $ModDir -PathType Container)) {
    Write-Error "Error: '$ModDir' does not exist."
    exit 1
}

Copy-Item -Path (Join-Path $BuildLogicDir "templates\gradle.properties") -Destination (Join-Path $ModDir "gradle.properties") -Force
Write-Host "gradle.properties synchronized in '$ModDir'."

Copy-Item -Path (Join-Path $BuildLogicDir "templates\settings.gradle.kts") -Destination (Join-Path $ModDir "settings.gradle.kts") -Force
Write-Host "settings.gradle.kts synchronized in '$ModDir'."

$GradlewPath = Join-Path $BuildLogicDir "gradlew"
if (-not (Test-Path -Path $GradlewPath)) {
    Write-Host "Warning: '$GradlewPath' does not exist yet -- cannot regenerate mod wrapper."
    Write-Host "Run 'gradle wrapper' once inside panzer-build-logic/ to generate it, then run this script again."
    exit 0
}

$WrapperDir = Join-Path $ModDir "gradle\wrapper"
New-Item -ItemType Directory -Force -Path $WrapperDir | Out-Null

Copy-Item -Path (Join-Path $BuildLogicDir "gradlew") -Destination (Join-Path $ModDir "gradlew") -Force
Copy-Item -Path (Join-Path $BuildLogicDir "gradlew.bat") -Destination (Join-Path $ModDir "gradlew.bat") -Force
Copy-Item -Path (Join-Path $BuildLogicDir "gradle\wrapper\gradle-wrapper.properties") -Destination (Join-Path $WrapperDir "gradle-wrapper.properties") -Force

$JarPath = Join-Path $BuildLogicDir "gradle\wrapper\gradle-wrapper.jar"
if (Test-Path -Path $JarPath) {
    Copy-Item -Path $JarPath -Destination (Join-Path $WrapperDir "gradle-wrapper.jar") -Force
}

Write-Host "Wrapper regenerated in '$ModDir'."
