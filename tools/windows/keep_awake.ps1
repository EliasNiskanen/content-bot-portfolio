# Pitää koneen hereillä annetun ajan (botti renderöi videota), sitten kone saa taas nukahtaa normaalisti.
# Käyttö: powershell -ExecutionPolicy Bypass -File keep_awake.ps1 -Minutes 240
param([int]$Minutes = 240)

Add-Type -Namespace Win32 -Name Power -MemberDefinition @"
[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint esFlags);
"@
$ES_CONTINUOUS = [uint32]"0x80000000"
$ES_SYSTEM_REQUIRED = [uint32]"0x00000001"

# Varmistetaan, että Docker Desktop on käynnissä (kontti käynnistyy itse: restart: unless-stopped)
$docker = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
if (-not (Get-Process "Docker Desktop" -ErrorAction SilentlyContinue) -and (Test-Path $docker)) {
    Start-Process $docker
}

[Win32.Power]::SetThreadExecutionState($ES_CONTINUOUS -bor $ES_SYSTEM_REQUIRED) | Out-Null

# Näytöt pois heti herätyksen jälkeen (kone pysyy hereillä, näytöt eivät). Hiiren liike sytyttää ne taas.
Add-Type -Namespace Win32 -Name Monitor -MemberDefinition @"
[DllImport("user32.dll")] public static extern int SendMessage(int hWnd, int hMsg, int wParam, int lParam);
"@
Start-Sleep -Seconds 5
[Win32.Monitor]::SendMessage(0xFFFF, 0x0112, 0xF170, 2) | Out-Null
Start-Sleep -Seconds ($Minutes * 60)
[Win32.Power]::SetThreadExecutionState($ES_CONTINUOUS) | Out-Null
