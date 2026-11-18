# Catfish Gaming -botin herätykset Windowsin Tehtävien ajoitukseen.
# Aja itse PowerShellissa (ei vaadi järjestelmänvalvojaa):
#   powershell -ExecutionPolicy Bypass -File E:\gta6-bot\tools\windows\install_wake_tasks.ps1
# Poisto: Unregister-ScheduledTask -TaskName "Catfish*" -Confirm:$false
$script = Join-Path $PSScriptRoot "keep_awake.ps1"
$settings = New-ScheduledTaskSettingsSet -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 5)

function Add-WakeTask($name, $trigger, $minutes) {
    $action = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`" -Minutes $minutes"
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
    Write-Host "OK: $name"
}

# Joka aamu klo 4.20: herää ja pysy hereillä 4 h (uutisvideo 4.30, vinkkivideo 6.30, julkaisut 8.00/10.00)
Add-WakeTask "Catfish aamuajot" (New-ScheduledTaskTrigger -Daily -At "04:20") 240
# Julkaisupäivän erikoisvideo 18.11. klo 15: herää 14.50 ja pysy hereillä 3 h
Add-WakeTask "Catfish julkaisupaiva" (New-ScheduledTaskTrigger -Once -At "2026-11-18 14:50") 180

Get-ScheduledTask -TaskName "Catfish*" | Select-Object TaskName, State
Write-Host "Tarkista vielä: Virranhallinta -> Lisäasetukset -> Lepotila -> Salli herätysajastimet = Käytössä"
