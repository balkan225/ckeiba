Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms

$exeName  = "Com.Pckeiba.Database"
$appref   = "C:\Users\balka\Desktop\PC-KEIBA Database.appref-ms"
$winTitle = "PC-KEIBA Database"
$logFile  = "C:\Users\balka\Desktop\Ckeiba\update_all.log"

function Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content $logFile "$ts  [PCKEIBA] $msg"
    Write-Host "$ts  [PCKEIBA] $msg"
}

Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win32Helper {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
    [DllImport("user32.dll")] public static extern void mouse_event(int f, int x, int y, int d, int e);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
}
"@

function Find-ByName($root, $name) {
    $cond = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::NameProperty, $name)
    return $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cond)
}

function Click-Element($elem) {
    try {
        $inv = $elem.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
        $inv.Invoke()
        return
    } catch {}
    $rect = $elem.Current.BoundingRectangle
    $cx = [int]($rect.X + $rect.Width / 2)
    $cy = [int]($rect.Y + $rect.Height / 2)
    [Win32Helper]::SetCursorPos($cx, $cy) | Out-Null
    Start-Sleep -Milliseconds 100
    [Win32Helper]::mouse_event(0x0002, 0, 0, 0, 0)
    Start-Sleep -Milliseconds 50
    [Win32Helper]::mouse_event(0x0004, 0, 0, 0, 0)
}

Log "Start"

# ── 起動確認 ───────────────────────────────────────────────
$proc = Get-Process -Name $exeName -ErrorAction SilentlyContinue
if (-not $proc) {
    Log "Launching PC-KEIBA Database..."
    Start-Process $appref
    for ($i=0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 2
        $proc = Get-Process -Name $exeName -ErrorAction SilentlyContinue
        if ($proc) { Log "Launched ($($i*2)s)"; break }
    }
    Start-Sleep -Seconds 5
}
if (-not $proc) { Log "ERROR: launch failed"; exit 1 }

# ── フォーカス ──────────────────────────────────────────────
[Win32Helper]::ShowWindow($proc.MainWindowHandle, 9) | Out-Null
[Win32Helper]::SetForegroundWindow($proc.MainWindowHandle) | Out-Null
Start-Sleep -Milliseconds 1000
Log "Focused"

# ── Alt+D → Enter で通常データ登録ダイアログを開く ────────────
[System.Windows.Forms.SendKeys]::SendWait("%d")
Start-Sleep -Milliseconds 800
[System.Windows.Forms.SendKeys]::SendWait("{ENTER}")
Log "Menu: Data -> Normal Registration sent"

# ── 通常データ登録ダイアログ待ち ─────────────────────────────
$root = [System.Windows.Automation.AutomationElement]::RootElement
$regDlg = $null
for ($i=0; $i -lt 20; $i++) {
    Start-Sleep -Seconds 1
    $regDlg = Find-ByName $root "通常データ登録"
    if ($regDlg) { Log "Registration dialog found (${i}s)"; break }
}
if (-not $regDlg) { Log "ERROR: dialog not found"; exit 1 }

# ── 開始ボタンクリック ────────────────────────────────────────
$kaisiBtn = Find-ByName $regDlg "開始"
if (-not $kaisiBtn) { Log "ERROR: Kaisi button not found"; exit 1 }
Click-Element $kaisiBtn
Log "Kaisi (Start) button clicked - waiting for completion"

# ── 完了待ち：「閉じる」ボタンが出るまで最大15分 ───────────────
$waited = 0
$done = $false
while ($waited -lt 900 -and -not $done) {
    Start-Sleep -Seconds 10
    $waited += 10
    try {
        $allWins = $root.FindAll(
            [System.Windows.Automation.TreeScope]::Children,
            [System.Windows.Automation.Condition]::TrueCondition)
        foreach ($w in $allWins) {
            if ($w.Current.ProcessId -ne $proc.Id) { continue }
            if ($w.Current.Name -eq $winTitle)      { continue }
            # 「閉じる」ボタンを探す（完了ダイアログ）
            $closeBtn = Find-ByName $w "閉じる"
            if ($closeBtn) {
                Log "Close button found after ${waited}s - clicking"
                Click-Element $closeBtn
                $done = $true
                break
            }
            # 念のため「OK」も探す
            $okBtn = Find-ByName $w "OK"
            if ($okBtn) {
                Log "OK button found after ${waited}s - clicking"
                Click-Element $okBtn
                $done = $true
                break
            }
        }
    } catch {}
}

if ($done) { Log "Completed OK" } else { Log "Timeout (900s) - assuming complete" }
exit 0
