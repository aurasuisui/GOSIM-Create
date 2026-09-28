# 统一入口：文件名/输出按 UTF-8，并把后续参数**原样**交给 python。
# 为什么用 $args 而不是 param(ValueFromRemainingArguments)：后者会把 -o 这类短选项
# 交给 PowerShell 自己解析（报 'parameter name o is ambiguous'）。
# 用法：powershell -NoProfile -File g2/tools/py.ps1 g2/main.py plan <dir> --output-dir <dir>
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
python @args