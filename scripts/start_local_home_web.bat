@echo off
set CCCC_HOME=C:\Users\zixun\dev\cccc\.cccc
cd /d C:\Users\zixun\dev\cccc
.venv\Scripts\python.exe -m cccc.cli >> C:\Users\zixun\dev\cccc\.cccc\daemon\boot.log 2>&1
