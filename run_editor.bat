@echo off
chcp 65001 >nul
rem 清除可能冲突的 Tcl/Tk 环境变量（例如从其他打包程序继承的路径）
set "TCL_LIBRARY="
set "TK_LIBRARY="
cd /d "%~dp0"
python item_editor.py %*
if errorlevel 1 pause
