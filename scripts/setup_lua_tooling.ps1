param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
)

$ErrorActionPreference = "Stop"

$toolsRoot = Join-Path $RepoRoot "tools"
$luaRoot = Join-Path $toolsRoot "lua"
$mingwRoot = Join-Path $toolsRoot "mingw"
$luaModulesRoot = Join-Path $toolsRoot "lua_modules"
$styluaRoot = Join-Path $toolsRoot "stylua"
$styluaZip = Join-Path $styluaRoot "stylua-windows-x86_64.zip"
$styluaExe = Join-Path $styluaRoot "stylua.exe"
$luaExe = Join-Path $luaRoot "bin\\lua.exe"
$luaRocksExe = Join-Path $luaRoot "bin\\luarocks.exe"
$gccBin = Join-Path $mingwRoot "mingw64\\bin"

New-Item -ItemType Directory -Force -Path $toolsRoot, $styluaRoot | Out-Null

if (-not (Test-Path $luaExe)) {
    winget install --id DEVCOM.Lua --exact --location $luaRoot --silent --accept-source-agreements --accept-package-agreements
}

if (-not (Test-Path (Join-Path $gccBin "x86_64-w64-mingw32-gcc.exe"))) {
    winget install --id BrechtSanders.WinLibs.POSIX.UCRT --exact --location $mingwRoot --silent --accept-source-agreements --accept-package-agreements
}

if (-not (Test-Path $styluaExe)) {
    $release = Invoke-RestMethod -Uri "https://api.github.com/repos/JohnnyMorganz/StyLua/releases/latest"
    $asset = $release.assets | Where-Object { $_.name -eq "stylua-windows-x86_64.zip" } | Select-Object -First 1
    if (-not $asset) {
        throw "Could not find a Windows x86_64 StyLua asset in the latest release."
    }

    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $styluaZip
    Expand-Archive -LiteralPath $styluaZip -DestinationPath $styluaRoot -Force
    Remove-Item -LiteralPath $styluaZip
}

$env:PATH = "$gccBin;$env:PATH"
& $luaRocksExe --tree=$luaModulesRoot --lua-version=5.4 install luacheck
