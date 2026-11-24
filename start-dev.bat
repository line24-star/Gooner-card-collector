@echo off
rem Developer mode: does NOT auto-close when browser tabs close.
rem Stop with the in-app Stop button, Ctrl+C, or by closing this window.
call "%~dp0start.bat" --no-auto-close %*
