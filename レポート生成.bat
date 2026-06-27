@echo off
cd /d "%~dp0"
echo ============================================
echo  レポート生成（総評は前回のまま・API不使用）
echo ============================================
echo.
echo 馬体重・オッズ・減点・印を最新化します。
echo 総評文は前回生成したものを表示します（Gemini APIは叩きません）。
echo 総評も最新にしたい場合は「総評生成.bat」を使ってください。
echo.
"C:\Users\balka\AppData\Local\Python\bin\python.exe" training_analyzer.py
echo.
pause
