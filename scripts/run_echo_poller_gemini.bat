@echo off
setlocal
set PYTHONUTF8=1
if "%~1"=="" (
  REM keep CCCC_GROUP_ID unset to allow auto-discovery in echo_poller.py
) else (
  set CCCC_GROUP_ID=%~1
)
set CCCC_ACTOR_ID=perA
set CCCC_AGENT_RUNTIME=gemini
set CCCC_GEMINI_MODEL=gemini-2.5-flash-lite
set CCCC_API=http://127.0.0.1:8848/api/v1
cd /d C:\Users\zixun\dev\cccc
C:\Python313\python.exe scripts\echo_poller.py >> C:\Users\zixun\dev\cccc\echo_poller_gemini.log 2>> C:\Users\zixun\dev\cccc\echo_poller_gemini.err.log
