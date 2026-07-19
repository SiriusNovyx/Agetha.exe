@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Medic_Checker.ps1"
if errorlevel 1 pause
