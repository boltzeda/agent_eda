$scriptPath = Join-Path $PSScriptRoot 'eda'
& python $scriptPath @args
exit $LASTEXITCODE
