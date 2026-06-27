@echo off
cd /d "%~dp0"
echo ============================================
echo  総評つきレポート生成（Gemini APIを使用）
echo ============================================
echo.
echo 各馬の総評をGeminiで生成します（数十秒〜数分）。
echo APIコールが発生します。
echo.
"C:\Users\balka\AppData\Local\Python\bin\python.exe" training_analyzer.py --comment
echo.
pause
