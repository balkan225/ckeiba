$python  = "C:\Users\balka\AppData\Local\Python\bin\python.exe"
$ckeiba  = "C:\Users\balka\Desktop\Ckeiba"
$logFile = "$ckeiba\update_quick.log"

function Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content $logFile "$ts  $msg"
}

Log "=== Quick Update Start ==="

# [1/2] PC-KEIBA Database
Log "[1/2] PC-KEIBA normal data registration..."
& powershell.exe -ExecutionPolicy Bypass -File "$ckeiba\run_pckeiba_update.ps1"
if ($LASTEXITCODE -ne 0) { Log "[WARN] PC-KEIBA step had issues (continuing)" }

# [2/2] レポート生成 + GitHub push
Log "[2/2] Report generation..."
Set-Location $ckeiba
$out = & $python training_analyzer.py 2>&1
$out | ForEach-Object { Log $_ }
$code = $LASTEXITCODE

Log "=== Quick Update End (exit=$code) ==="
exit $code
