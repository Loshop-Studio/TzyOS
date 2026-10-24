@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

rem ============================================================
rem  build_all.bat -- Tzy OS unified packaging (source + embedded
rem  portable Python 3.8).
rem
rem  No Nuitka / no obfuscation: Win7 / Win10 / Win11 share one
rem  package. The target PC needs no Python and no internet.
rem
rem  Output:
rem     dist\TzyOS\        deployment tree (copy to target PC)
rem     dist\TzyOS.zip     same tree zipped (Optimal), for transfer
rem
rem  Usage:
rem     build_all.bat        build runtime if missing, assemble, zip
rem     build_all.bat /nort       skip runtime check (runtime already built)
rem     build_all.bat /repoall    include every historical app package
rem
rem
rem ============================================================

if not exist dist mkdir dist

set "PKG=dist\TzyOS"
set "ZIP=dist\TzyOS.zip"

rem ---- [1/4] ensure embedded runtime exists ----
if /i "%~1"=="/nort" goto :have_rt
if exist "runtime\python\python.exe" goto :have_rt
echo [1/4] Embedded runtime missing - building it (needs internet) ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_runtime.ps1"
if errorlevel 1 (
    echo [!] Failed to build runtime. See build_runtime.ps1 output.
    pause
    exit /b 1
)
goto :rt_ok
:have_rt
echo [1/4] Embedded runtime OK.
:rt_ok

rem ---- [2/4] assemble deployment tree ----
echo [2/4] Assembling %PKG% ...
if exist "%PKG%" rmdir /s /q "%PKG%"
mkdir "%PKG%"

rem dirs: server package / source package / pages / admin / apps / runtime / tools
for %%D in (novacore novelsrc novaos2 admin runtime tools) do (
    robocopy "%%D" "%PKG%\%%D" /E /XD __pycache__ .git /XF *.pyc *.log /NFL /NDL /NJH /NJS /NP >nul
    if errorlevel 8 (
        echo [!] robocopy failed on %%D
        pause
        exit /b 1
    )
)

rem ---- app repository: ship latest package per app by default ----
rem Historical .tzyp/.zip files are useful for development but make every
rem deployment grow. The selector reads app.json from each .tzyp using the
rem .NET ZIP API available in Windows PowerShell 5.1 (Win7 compatible).
mkdir "%PKG%\apps_repo"
if /i "%~1"=="/repoall" (
    robocopy "apps_repo" "%PKG%\apps_repo" /E /XF *.zip /XD __pycache__ .git /NFL /NDL /NJH /NJS /NP >nul
    if errorlevel 8 (
        echo [!] robocopy failed on apps_repo
        pause
        exit /b 1
    )
) else (
    echo [2/4] Selecting latest app packages ...
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_select_apps.ps1" -Source "%~dp0apps_repo" -Destination "%~dp0%PKG%\apps_repo"
    if errorlevel 1 (
        echo [!] app package selection failed
        pause
        exit /b 1
    )
)

rem files: entries / injection chain / config / launchers
for %%F in (config.json loader.js boot-stub.js bridge.js novaosd.py server.py novel_server.py requirements.txt README.md start.bat install.bat stop-hotspot.bat enable-hotspot.ps1 disable-hotspot.ps1 set-hotspot-credentials.ps1) do (
    if exist "%%F" (copy /y "%%F" "%PKG%\%%F" >nul) else (echo     [i] skip missing %%F)
)

rem Injection chain is mandatory. Fail early instead of producing a package
rem that starts but cannot hook the platform page.
for %%F in (config.json loader.js boot-stub.js bridge.js novaosd.py novaos2\nova.html) do (
    if not exist "%PKG%\%%F" (
        echo [!] Missing required injection file: %PKG%\%%F
        pause
        exit /b 1
    )
)

rem ---- [3/4] in-package smoke test (imports via bundled runtime) ----
rem PYTHONDONTWRITEBYTECODE keeps __pycache__ out of the shipped package.
echo [3/4] Smoke test packaged runtime ...
set "PYTHONDONTWRITEBYTECODE=1"
pushd "%PKG%"
"runtime\python\python.exe" -X utf8 -c "import sys,os; sys.path.insert(0,os.getcwd()); import novacore.service, novelsrc.facade, flask, requests, bs4, lxml.etree, Crypto, cryptography, ebooklib; print('package import ok')"
if errorlevel 1 (
    popd
    set "PYTHONDONTWRITEBYTECODE="
    echo [!] Packaged smoke test FAILED.
    pause
    exit /b 1
)
popd
set "PYTHONDONTWRITEBYTECODE="

rem ---- [4/4] zip with .NET Optimal (forward-slash entries, no top folder) ----
rem Note: ZipFile.CreateFromDirectory writes backslash entry names under
rem Windows PowerShell 5.1, which is non-standard, so entries are added
rem one by one with "/" separators.
echo [4/4] Zipping %ZIP% ...
powershell -NoProfile -Command "Add-Type -AssemblyName System.IO.Compression.FileSystem; $src=(Resolve-Path 'dist\TzyOS').Path; $dst=(Join-Path (Resolve-Path 'dist').Path 'TzyOS.zip'); if(Test-Path $dst){Remove-Item $dst -Force}; $zip=[System.IO.Compression.ZipFile]::Open($dst,'Create'); $root=$src+'\'; Get-ChildItem $src -Recurse -File | ForEach-Object { $n=$_.FullName.Substring($root.Length).Replace('\','/'); $en=$zip.CreateEntry($n,[System.IO.Compression.CompressionLevel]::Optimal); $s=$en.Open(); $f=[System.IO.File]::OpenRead($_.FullName); $f.CopyTo($s); $f.Dispose(); $s.Dispose() }; $zip.Dispose(); Write-Host ('[*] zip: '+$dst+' ('+[math]::Round((Get-Item $dst).Length/1MB,1)+' MB)')"
if errorlevel 1 (
    echo [!] Zip failed.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Package ready:
echo    %PKG%\        deployment tree (copy to target PC)
echo    %ZIP%     the zip
echo.
echo  On the target PC: unzip, then run install.bat (first time)
echo  or start.bat. Administrator is required (ports 53/80).
echo  Windows 7 SP1 needs the UCRT update KB2999226 - see BUILD.md.
echo ============================================================
pause
