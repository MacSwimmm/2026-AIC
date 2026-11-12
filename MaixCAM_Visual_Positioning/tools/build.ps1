$ErrorActionPreference = 'Stop'
$visionRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $visionRoot
$visionWebPath = Join-Path $visionRoot 'host\web'
$visionAgentPath = Join-Path $visionRoot 'device\vision_agent.py'
python -m PyInstaller --noconfirm --clean --onefile --name MaixVisionConsole --distpath . --workpath build --specpath build --paths host --add-data "$visionWebPath;host/web" --add-data "$visionAgentPath;device" host/server.py
if ($LASTEXITCODE -ne 0) { throw '上位机打包失败' }
Get-Item -LiteralPath (Join-Path $visionRoot 'MaixVisionConsole.exe') | Select-Object Name,Length
