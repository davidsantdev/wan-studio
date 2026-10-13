# Envia o projeto para a VM e roda a instalacao.
# Uso:  .\vm\deploy.ps1 -Target root@203.0.113.10 -Port 22 -Models 5b
# Models: 5b (12 GB de VRAM), 14b ou all (24 GB), free (16 GB: 5B + 14B em GGUF)
param(
    [Parameter(Mandatory = $true)][string]$Target,
    [int]$Port = 22,
    [string]$Key = "",
    [ValidateSet("5b", "14b", "all", "free")][string]$Models = "5b"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$archive = Join-Path $env:TEMP "wan-studio.tgz"

$sshArgs = @("-p", $Port)
$scpArgs = @("-P", $Port)
if ($Key) { $sshArgs += @("-i", $Key); $scpArgs += @("-i", $Key) }

Write-Host "Empacotando..."
tar -czf $archive -C $root --exclude "__pycache__" app vm
if ($LASTEXITCODE -ne 0) { throw "falha ao empacotar" }

Write-Host "Enviando para $Target..."
scp @scpArgs $archive "${Target}:wan-studio.tgz"
if ($LASTEXITCODE -ne 0) { throw "falha no scp" }

# sed remove CRLF caso os .sh tenham sido salvos no Windows.
$remote = 'mkdir -p ~/wan-studio && tar -xzf ~/wan-studio.tgz -C ~/wan-studio && rm ~/wan-studio.tgz && ' +
          "sed -i 's/\r`$//' ~/wan-studio/vm/*.sh && MODELS=$Models bash ~/wan-studio/vm/setup.sh"
ssh @sshArgs $Target $remote
if ($LASTEXITCODE -ne 0) { throw "a instalacao na VM falhou (veja a saida acima)" }

Write-Host ""
Write-Host "Instalado. Abra o estudio com:"
Write-Host "  .\vm\tunnel.ps1 -Target $Target -Port $Port"
