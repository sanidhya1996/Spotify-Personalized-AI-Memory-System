@echo off
rem Starts the whole backend with one command, in this one window:
rem the four stores, the worker and the API. Ctrl+C stops it.
rem
rem   start-backend.cmd
rem
rem The container names are the ones on the machine this was built on, see
rem RUNNING.md "On the machine this was built on". On a fresh clone use
rem "docker compose up -d" instead of the two docker lines.

cd /d "%~dp0"

echo Starting the databases...
docker stop memory-neo4j >nul 2>&1
docker start memory_system_postgres memory_system_redis memory_system_redpanda memory_system_neo4j
echo Waiting 30 seconds for Neo4j to be ready...
timeout /t 30 /nobreak >nul

rem A worker started with "start /b" keeps running after Ctrl+C, so a restart
rem would leave an old one behind next to the new one. Stop any old worker first.
echo Stopping any worker left from a previous run...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'run_processor' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"

echo Starting the worker...
start /b python -u scripts/run_processor.py --forever

echo Starting the API on http://127.0.0.1:8000 ...
python -m uvicorn memory.api:app --port 8000
