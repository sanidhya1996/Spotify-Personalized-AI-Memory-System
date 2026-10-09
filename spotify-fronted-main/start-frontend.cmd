@echo off
rem Starts both frontend apps with one command, in this one window:
rem the operator console (http://localhost:3000) and the listener controls
rem (http://localhost:3001). Start the backend first. Ctrl+C stops both.
rem
rem   start-frontend.cmd

echo Starting the listener controls on http://localhost:3001 ...
cd /d "%~dp0apps\memory-controls"
start /b npm run dev

echo Starting the operator console on http://localhost:3000 ...
cd /d "%~dp0apps\memory-console"
npm run dev
