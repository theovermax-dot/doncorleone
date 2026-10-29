@echo off
chcp 65001 >nul
python -m pip install -q -r requirements.txt
python bot.py
pause
