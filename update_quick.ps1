$python  = "C:\Users\balka\AppData\Local\Python\bin\python.exe"
$ckeiba  = "C:\Users\balka\Desktop\Ckeiba"
$logFile = "$ckeiba\update_quick.log"

function Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content $logFile "$ts  $msg"
}

Log "=== Quick Update Start (report only) ==="

# レポート生成 + GitHub push
# オッズ=keibadata(JvLinkImporter常駐), 馬体重=keibadata+JV-Link補完 から取得するため
# PC-KEIBAの通常データ登録は不要（朝のKeibaTotalUpdateで実施）
Set-Location $ckeiba
$out = & $python training_analyzer.py 2>&1
$out | ForEach-Object { Log $_ }
$code = $LASTEXITCODE

Log "=== Quick Update End (exit=$code) ==="
exit $code
