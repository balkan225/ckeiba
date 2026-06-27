$python  = "C:\Users\balka\AppData\Local\Python\bin\python.exe"
$ckeiba  = "C:\Users\balka\Desktop\Ckeiba"
$logFile = "$ckeiba\update_all.log"

function Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content $logFile "$ts  $msg"
    Write-Host "$ts  $msg"
}

$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content $logFile "============================================"
Add-Content $logFile "$ts  START"

# [1/3] PC-KEIBA Database (通常データ登録)
Log "[1/3] PC-KEIBA Database normal data registration..."
& powershell.exe -ExecutionPolicy Bypass -File "$ckeiba\run_pckeiba_update.ps1"
if ($LASTEXITCODE -ne 0) { Log "[WARN] PC-KEIBA step had issues (continuing)" }

# [2/3] クッション値
Log "[2/3] Cushion value update..."
Set-Location "$ckeiba\cushion"
$out = & $python fetch_live_data.py 2>&1
$out | ForEach-Object { Add-Content $logFile $_ }
if ($LASTEXITCODE -ne 0) { Log "[WARN] Cushion update had issues (continuing)" }

# [3/3] レポート生成
Log "[3/3] Report generation + GitHub push..."
Set-Location $ckeiba
$out2 = & $python training_analyzer.py --comment 2>&1
$out2 | ForEach-Object { Add-Content $logFile $_ }
$code = $LASTEXITCODE

$ts2 = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content $logFile "$ts2  END (exit=$code)"
Add-Content $logFile "============================================"
exit $code
