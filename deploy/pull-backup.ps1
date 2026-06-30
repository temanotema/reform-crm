# pull-backup.ps1 — скачать свежий бэкап базы с сервера НА ЭТОТ КОМПЬЮТЕР.
#
# Сервер раз в день кладёт ~/backups/cosmo_db_latest.sql.gz (скрипт backup.sh).
# Этот скрипт забирает его к тебе в E:\BOTREFORM\backups с датой в имени.
#
# Запуск вручную:  правой кнопкой → "Запустить с помощью PowerShell"
#   или в PowerShell:  powershell -ExecutionPolicy Bypass -File E:\BOTREFORM\reform\deploy\pull-backup.ps1
#
# Автоматически каждый день (один раз выполнить в PowerShell, комп должен быть включён в это время):
#   schtasks /create /tn "ReformBackupPull" /sc daily /st 10:00 ^
#     /tr "powershell -NoProfile -ExecutionPolicy Bypass -File E:\BOTREFORM\reform\deploy\pull-backup.ps1"

$ErrorActionPreference = "Stop"

$Server = "reform@158.160.212.148"
$Remote = "~/backups/cosmo_db_latest.sql.gz"
$LocalDir = "E:\BOTREFORM\backups"

New-Item -ItemType Directory -Force -Path $LocalDir | Out-Null
$stamp = Get-Date -Format "yyyy-MM-dd_HHmm"
$dest = Join-Path $LocalDir "cosmo_db_$stamp.sql.gz"

scp "${Server}:${Remote}" "$dest"
if (-not (Test-Path $dest)) { throw "Не удалось скачать бэкап" }
Write-Host "OK: $dest  ($([math]::Round((Get-Item $dest).Length/1KB,1)) KB)"

# Чистка локальных копий старше 60 дней (на компе храним подольше, чем на сервере)
Get-ChildItem $LocalDir -Filter "cosmo_db_*.sql.gz" |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-60) } |
    Remove-Item -Force
