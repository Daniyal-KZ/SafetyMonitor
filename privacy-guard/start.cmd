@echo off
chcp 65001 >nul
title Privacy Guard
cd /d "%~dp0"
where node >nul 2>nul
if errorlevel 1 (
  echo Установите Node.js 22.12 или новее и запустите этот файл снова.
  pause
  exit /b 1
)
if not exist "node_modules\nuxt\package.json" (
  call npm.cmd ci --no-audit --no-fund
  if errorlevel 1 (
    echo Не удалось установить зависимости.
    pause
    exit /b 1
  )
)
echo Privacy Guard: http://127.0.0.1:4173
echo Для остановки нажмите Ctrl+C.
call npm.cmd run dev
if errorlevel 1 pause
