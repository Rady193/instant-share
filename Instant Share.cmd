@echo off
rem Runs Instant Share from the source (needs Python) and opens it in the browser. Close this window to stop it.
title Instant Share
cd /d "%~dp0"
py -3 instantshare.py %*
if errorlevel 9009 python instantshare.py %*
pause
