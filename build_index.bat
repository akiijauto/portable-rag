@echo off
cd /d %~dp0
python build_index.py %*
pause
