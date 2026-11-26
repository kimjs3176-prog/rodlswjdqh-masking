@echo off
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo 파이썬이 설치되어 있지 않습니다. https://www.python.org 에서 설치한 뒤 다시 실행하세요.
    echo 설치할 때 "Add python.exe to PATH" 를 꼭 체크하세요.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo 처음 실행: 필요한 구성 요소를 설치합니다. 잠시 기다려 주세요...
    python -m venv .venv || goto :fail
    ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt || goto :fail
)

start "" ".venv\Scripts\pythonw.exe" -m pdf_masker
exit /b 0

:fail
echo 설치에 실패했습니다. 인터넷 연결을 확인한 뒤 .venv 폴더를 지우고 다시 실행하세요.
pause
exit /b 1
