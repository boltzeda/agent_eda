# Dot-source this file to expose eda.cmd in this PowerShell session.
$agentEdaBin = Join-Path $PSScriptRoot 'bin'
if (($env:Path -split ';') -notcontains $agentEdaBin) {
    $env:Path = "$agentEdaBin;$env:Path"
}
Remove-Variable agentEdaBin
