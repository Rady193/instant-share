@echo off
rem Runs Text Share from the source (needs Python) and opens it in the browser. Close this window to stop it.
title Text Share
cd /d "%~dp0"
py -3 textshare.py %*
if errorlevel 9009 python textshare.py %*
pause
