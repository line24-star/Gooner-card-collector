@echo off
rem Isolated test profile: own DB, media folder and ports (8100 / 5273). Never touches your data.
set MCO_PROFILE=test
call "%~dp0start.bat" %*
