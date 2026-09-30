<#
.SYNOPSIS
  Akım kurulumu (Windows 10/11): sanal ortam, bağımlılıklar, config.yaml ve .env şablonları.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File deploy\windows\install.ps1
  powershell -ExecutionPolicy Bypass -File deploy\windows\install.ps1 -WithLaya   # yerel Laya modeli (PyTorch CPU, ~700 MB)
#>
param(
  [switch]$WithLaya,  # Laya için PyTorch (CPU) ve laya paketini de kur
  [switch]$NoLlm      # Claude doğrulama eklentisini (anthropic paketi) kurma
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $root

function Test-Python([string[]]$cmd) {
  try {
    $exe = $cmd[0]
    $rest = @()
    if ($cmd.Count -gt 1) { $rest = $cmd[1..($cmd.Count - 1)] }
    $v = & $exe @rest -c "import sys; print('{}.{}'.format(*sys.version_info[:2]))" 2>$null
    return ($LASTEXITCODE -eq 0 -and [version]$v -ge [version]"3.10")
  } catch { return $false }
}

function Invoke-Python([string[]]$arguments) {
  $exe = $script:python[0]
  $pre = @()
  if ($script:python.Count -gt 1) { $pre = $script:python[1..($script:python.Count - 1)] }
  & $exe @pre @arguments
  if ($LASTEXITCODE -ne 0) { throw "Python komutu başarısız oldu: $($arguments -join ' ')" }
}

$python = $null
foreach ($c in @(@("py", "-3.13"), @("py", "-3.12"), @("py", "-3.11"), @("py", "-3.10"), @("python"), @("python3"))) {
  if (Test-Python $c) { $python = $c; break }
}
if (-not $python) {
  throw "Python 3.10 veya üstü bulunamadı. https://www.python.org/downloads/ adresinden kur ('Add python.exe to PATH' kutusunu işaretle)."
}
Write-Host "Python: $($python -join ' ')"

$venvPy = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
  Write-Host "Sanal ortam oluşturuluyor…"
  Invoke-Python @("-m", "venv", (Join-Path $root ".venv"))
}

& $venvPy -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip güncellenemedi" }

$extras = @()
if (-not $NoLlm) { $extras += "llm" }
if ($WithLaya) { $extras += "laya" }
$spec = "."
if ($extras.Count -gt 0) { $spec = ".[" + ($extras -join ",") + "]" }

if ($WithLaya) {
  Write-Host "PyTorch (CPU) kuruluyor…"
  & $venvPy -m pip install torch --index-url https://download.pytorch.org/whl/cpu
  if ($LASTEXITCODE -ne 0) { throw "PyTorch kurulamadı" }
}
Write-Host "Akım kuruluyor ($spec)…"
& $venvPy -m pip install -e $spec
if ($LASTEXITCODE -ne 0) { throw "Akım kurulamadı" }

foreach ($pair in @(@("config.example.yaml", "config.yaml"), @(".env.example", ".env"))) {
  if (-not (Test-Path $pair[1])) {
    Copy-Item $pair[0] $pair[1]
    Write-Host "$($pair[1]) oluşturuldu (örnekten kopyalandı)."
  }
}

& $venvPy -m akim --version
if ($LASTEXITCODE -ne 0) { throw "Kurulum doğrulanamadı" }

Write-Host ""
Write-Host "Kurulum tamam. Sıradaki adımlar:"
Write-Host "  1. .env dosyasını not defteriyle aç, Telegram veya ntfy bilgisini gir"
Write-Host "  2. Bildirimleri dene:  .venv\Scripts\akim.exe test-notify"
Write-Host "  3. Çalıştır:           deploy\windows\run.cmd          (çökerse kendiliğinden yeniden başlar)"
Write-Host "  4. Açılışta otomatik:  powershell -ExecutionPolicy Bypass -File deploy\windows\autostart.ps1"
