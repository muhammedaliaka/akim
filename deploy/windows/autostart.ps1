<#
.SYNOPSIS
  Akım'ı Windows'ta oturum açıldığında otomatik ve görünmez başlatır (Görev Zamanlayıcı).

.DESCRIPTION
  pythonw.exe ile konsolsuz çalışır; log data\akim.log dosyasına yazılır. Yönetici yetkisi gerekmez
  (görev kendi kullanıcın adına çalışır). Çökerse Görev Zamanlayıcı 1 dakika sonra yeniden başlatır.
  Bilgisayar uyumasın diye config.yaml içindeki general.keep_awake (varsayılan: açık) kullanılır.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File deploy\windows\autostart.ps1            # kur ve başlat
  powershell -ExecutionPolicy Bypass -File deploy\windows\autostart.ps1 -Status    # durum
  powershell -ExecutionPolicy Bypass -File deploy\windows\autostart.ps1 -Remove    # kaldır
#>
param(
  [switch]$Remove,
  [switch]$Status,
  [string]$TaskName = "Akim"
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

if ($Status) {
  $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  if (-not $task) { Write-Host "Görev kurulu değil: $TaskName"; exit 1 }
  $info = Get-ScheduledTaskInfo -TaskName $TaskName
  Write-Host "Görev: $TaskName  durum: $($task.State)  son çalışma: $($info.LastRunTime)  son sonuç: $($info.LastTaskResult)"
  Write-Host "Log:   $(Join-Path $root 'data\akim.log')"
  exit 0
}

if ($Remove) {
  if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Görev kaldırıldı: $TaskName"
  } else {
    Write-Host "Görev zaten kurulu değil: $TaskName"
  }
  exit 0
}

$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) { throw "Önce kurulum yap: deploy\windows\install.cmd" }

$configPath = Join-Path $root "config.yaml"
$taskArgs = "-m akim"
if (Test-Path $configPath) { $taskArgs += " -c `"$configPath`"" }
$taskArgs += " run"

$me = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $pythonw -Argument $taskArgs -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $me
$principal = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RunOnlyIfNetworkAvailable `
  -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
  -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal `
  -Settings $settings -Description "Akım: Steam/Epic -> Roblox akım izleme" -Force | Out-Null
Write-Host "Görev kuruldu: $TaskName (oturum açılınca başlar)."

Start-ScheduledTask -TaskName $TaskName
Write-Host "Başlatıldı. Log: $(Join-Path $root 'data\akim.log')"
Write-Host "Panel: http://localhost:8080   Durum: autostart.ps1 -Status   Kaldır: autostart.ps1 -Remove"
