Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$appref = "C:\Users\balka\Desktop\PC-KEIBA Database.appref-ms"
$exeName = "Com.Pckeiba.Database"

# 既に起動しているか確認
$proc = Get-Process -Name $exeName -ErrorAction SilentlyContinue
if (-not $proc) {
    Write-Host "アプリ起動中..."
    Start-Process $appref
    $timeout = 30
    for ($i=0; $i -lt $timeout; $i++) {
        Start-Sleep -Seconds 1
        $proc = Get-Process -Name $exeName -ErrorAction SilentlyContinue
        if ($proc) { Write-Host "起動確認 (${i}秒後)"; break }
    }
    Start-Sleep -Seconds 3
}

if (-not $proc) { Write-Host "起動失敗"; exit 1 }

Write-Host "プロセス: $($proc.Id)"

# メインウィンドウを取得
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)

if (-not $win) { Write-Host "ウィンドウ取得失敗"; exit 1 }
Write-Host "ウィンドウ: $($win.Current.Name)"

# 子要素を列挙（ボタン・メニュー）
$walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
function Enum-Elements($elem, $depth=0) {
    if ($depth -gt 4) { return }
    $indent = "  " * $depth
    $name = $elem.Current.Name
    $type = $elem.Current.ControlType.ProgrammaticName
    if ($name -or $type -match "Button|Menu|MenuItem") {
        Write-Host "$indent[$type] '$name'"
    }
    $child = $walker.GetFirstChild($elem)
    while ($child) {
        Enum-Elements $child ($depth+1)
        $child = $walker.GetNextSibling($child)
    }
}
Enum-Elements $win
