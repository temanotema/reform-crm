@echo off
title Reform CRM - demo launcher
cd /d E:\BOTREFORM\reform

echo ============================================================
echo   Re.form CRM - demo launch
echo ============================================================
echo.
echo Window "Reform CRM"      = bot + web panel (port 5000)
echo Window "Cloudflare Tunnel" = public link https://XXXX.trycloudflare.com
echo Open that link on the clinic laptop. Keep both windows open.
echo.

start "Reform CRM" cmd /k "cd /d E:\BOTREFORM\reform && python run.py"
timeout /t 5 >nul
start "Cloudflare Tunnel" cmd /k "E:\cloudflared-windows-386.exe tunnel --url http://localhost:5000"

echo.
echo Done. The public address is shown in the "Cloudflare Tunnel" window.
echo.
pause
