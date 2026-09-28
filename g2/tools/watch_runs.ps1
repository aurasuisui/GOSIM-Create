$s = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$null = Invoke-WebRequest -Uri 'https://arc-bench.com/api/auth/login' -Method POST -WebSession $s -ContentType 'application/json' -Body '{"email":"2436448088@qq.com","password":"xbao2436"}' -UseBasicParsing -TimeoutSec 30
$sub = '87bd1f7e0aef'
for ($i = 0; $i -lt 90; $i++) {
  try {
    $runs = (Invoke-WebRequest -Uri 'https://arc-bench.com/api/runs' -WebSession $s -UseBasicParsing -TimeoutSec 25).Content | ConvertFrom-Json
    $mine = $runs | Where-Object { $_.submission_id -eq $sub }
    $busy = ($mine | Where-Object { $_.status -eq 'RUNNING' } | Measure-Object).Count
    $line = ($mine | ForEach-Object { $_.requirement_id + '=' + $_.status + '(' + $_.passed_count + '/' + ($_.passed_count + $_.failed_count) + ',score=' + $_.score + ')' }) -join '  '
    '[' + (Get-Date -Format 'HH:mm:ss') + '] ' + $line
    if ($busy -eq 0) { 'ALL SETTLED'; break }
  } catch { 'poll error: ' + $_.Exception.Message.Substring(0, [Math]::Min(60, $_.Exception.Message.Length)) }
  Start-Sleep -Seconds 60
}