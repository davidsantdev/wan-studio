# Abre um tunel SSH ate a VM e o Wan Studio no navegador (http://localhost:8080).
# Deixe esta janela aberta enquanto usa; Ctrl+C fecha o tunel.
param(
    [Parameter(Mandatory = $true)][string]$Target,
    [int]$Port = 22,
    [string]$Key = "",
    [int]$LocalPort = 8080
)

$sshArgs = @("-p", $Port, "-N", "-L", "${LocalPort}:127.0.0.1:8080", "-o", "ServerAliveInterval=30")
if ($Key) { $sshArgs += @("-i", $Key) }

Start-Process "http://localhost:$LocalPort"
ssh @sshArgs $Target
