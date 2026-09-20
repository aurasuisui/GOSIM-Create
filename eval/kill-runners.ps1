# 杀掉所有游离的 playwright runner。
#
# 为什么单独一个脚本：stop_backend 按"谁占着 $PORT"找进程，而 playwright
# runner 不占端口——它是个客户端。所以只要有一轮测试的 runner 没退干净，
# 它就会在下一轮重置数据库、换掉后端之后继续跑，产出一份看起来正常、
# 实际不可信的日志。实测事故见 eval/bench.sh 的注释。
#
# 输出：被杀掉的进程数（供 bash 侧判断是否需要提示）。

$patterns = @(
  '*run-playwright*',
  '*playwright*cli.js*',
  '*workerProcessEntry*'
)

$targets = Get-CimInstance Win32_Process -Filter "Name='node.exe'" -ErrorAction SilentlyContinue |
  Where-Object {
    $cmd = $_.CommandLine
    if (-not $cmd) { return $false }
    foreach ($p in $patterns) { if ($cmd -like $p) { return $true } }
    return $false
  }

$count = @($targets).Count
foreach ($t in $targets) {
  Stop-Process -Id $t.ProcessId -Force -ErrorAction SilentlyContinue
}

Write-Output $count
