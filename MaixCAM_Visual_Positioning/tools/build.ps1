$ErrorActionPreference = 'Stop'
$visionRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $visionRoot
$visionWebPath = Join-Path $visionRoot 'host\web'
$visionDevicePath = Join-Path $visionRoot 'device'
$visionGeometryPath = Join-Path $visionRoot 'host\geometry.py'
$visionDataArgs = @('--add-data', "$visionWebPath;host/web", '--add-data', "$visionGeometryPath;host")
Get-ChildItem -LiteralPath $visionDevicePath -File | ForEach-Object { $visionDataArgs += @('--add-data', "$($_.FullName);device") }
python -m PyInstaller --noconfirm --clean --onefile --name MaixVisionConsole --distpath . --workpath build --specpath build --paths host @visionDataArgs host/server.py
if ($LASTEXITCODE -ne 0) { throw '上位机打包失败' }
Get-Item -LiteralPath (Join-Path $visionRoot 'MaixVisionConsole.exe') | Select-Object Name,Length
