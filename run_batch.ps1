# Local batch runner — invoked by Windows Task Scheduler.
# Generates videos + uploads YouTube (publish_at) + uploads TikTok (native schedule).
# Idempotent: generate_batch.py skips slots that already have queued pairs.
#
# Log: G:\YoutubeShorts\run_batch.log (rotated when over ~5MB)

$ErrorActionPreference = "Continue"
Set-Location "G:\YoutubeShorts"

# Load .env into the current PowerShell session so child python sees the vars
if (Test-Path ".env") {
    Get-Content ".env" | ForEach-Object {
        if ($_ -match '^\s*([^#=][^=]*?)\s*=\s*(.*)\s*$') {
            $name  = $matches[1]
            $value = $matches[2]
            Set-Item -Path "Env:$name" -Value $value
        }
    }
}

$env:PYTHONIOENCODING = "utf-8"

$log = "run_batch.log"
if ((Test-Path $log) -and ((Get-Item $log).Length -gt 5MB)) {
    Move-Item $log "$log.1" -Force
}

$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content $log "`n===== $stamp — batch start ====="

python generate_batch.py 3 *>&1 | Tee-Object -FilePath $log -Append

$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content $log "===== $stamp — batch end ====="
