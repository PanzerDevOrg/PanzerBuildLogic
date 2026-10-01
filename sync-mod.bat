@echo off
setlocal enabledelayedexpansion

set "MOD_DIR=%~1"

if "%MOD_DIR%"=="" (
    echo Usage: sync-mod.bat ^<path-to-mod^>
    echo Example: sync-mod.bat ..\mod
    exit /b 1
)

set "BUILD_LOGIC_DIR=%~dp0"
if "%BUILD_LOGIC_DIR:~-1%"=="\" set "BUILD_LOGIC_DIR=%BUILD_LOGIC_DIR:~0,-1%"

if not exist "%MOD_DIR%\" (
    echo Error: '%MOD_DIR%' does not exist.
    exit /b 1
)

copy /y "%BUILD_LOGIC_DIR%\templates\gradle.properties" "%MOD_DIR%\gradle.properties" >nul
echo gradle.properties synchronized in '%MOD_DIR%'.

copy /y "%BUILD_LOGIC_DIR%\templates\settings.gradle.kts" "%MOD_DIR%\settings.gradle.kts" >nul
echo settings.gradle.kts synchronized in '%MOD_DIR%'.

if not exist "%BUILD_LOGIC_DIR%\gradlew" (
    echo Warning: '%BUILD_LOGIC_DIR%\gradlew' does not exist yet -- cannot regenerate mod wrapper.
    echo Run 'gradle wrapper' once inside panzer-build-logic\ to generate it, then run this script again.
    exit /b 0
)

mkdir "%MOD_DIR%\gradle\wrapper" 2>nul
copy /y "%BUILD_LOGIC_DIR%\gradlew" "%MOD_DIR%\gradlew" >nul
copy /y "%BUILD_LOGIC_DIR%\gradlew.bat" "%MOD_DIR%\gradlew.bat" >nul
copy /y "%BUILD_LOGIC_DIR%\gradle\wrapper\gradle-wrapper.properties" "%MOD_DIR%\gradle\wrapper\gradle-wrapper.properties" >nul

if exist "%BUILD_LOGIC_DIR%\gradle\wrapper\gradle-wrapper.jar" (
    copy /y "%BUILD_LOGIC_DIR%\gradle\wrapper\gradle-wrapper.jar" "%MOD_DIR%\gradle\wrapper\gradle-wrapper.jar" >nul
)

echo Wrapper regenerated in '%MOD_DIR%'.
exit /b 0
