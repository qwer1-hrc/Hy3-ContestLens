$ErrorActionPreference = "Stop"
$BaseUrl = if ($env:HY3_CONTESTLENS_URL) { $env:HY3_CONTESTLENS_URL } else { "http://127.0.0.1:8000" }
Invoke-RestMethod "$BaseUrl/healthz" | ConvertTo-Json -Depth 8
Invoke-RestMethod "$BaseUrl/api/v1/system/capabilities" | ConvertTo-Json -Depth 8
Invoke-RestMethod "$BaseUrl/api/v1/datasets/noip2018/problems" | ConvertTo-Json -Depth 8

