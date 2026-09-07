$backendDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$frontendDir = Join-Path $backendDir "frontend"

Write-Host "Starting backend on http://127.0.0.1:8000 ..."
Start-Process powershell.exe -ArgumentList @(
    "-NoExit",
    "-Command",
    "Set-Location '$backendDir'; py -3.13 -m uvicorn backend.main:app --host 127.0.0.1 --port 8000"
)

Start-Sleep -Seconds 2

Write-Host "Starting frontend on http://127.0.0.1:5173 ..."
Start-Process powershell.exe -ArgumentList @(
    "-NoExit",
    "-Command",
    "Set-Location '$frontendDir'; npm.cmd run dev -- --host 127.0.0.1 --port 5173"
)

Write-Host ""
Write-Host "Frontend: http://127.0.0.1:5173"
Write-Host "Backend : http://127.0.0.1:8000"
