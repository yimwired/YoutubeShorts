# Local batch runner -- invoked by Windows Task Scheduler.
# Generates videos + uploads YouTube (publish_at) + uploads TikTok (native schedule).
#
# Flexibility:
#   - Fires on logon (+1min), boot (+3min), and twice daily (12:00, 18:00).
#   - daily-once guard via marker file -- if today's run already succeeded,
#     subsequent fires the same day exit early so the queue does not bloat.
#   - StartWhenAvailable=true in the task XML catches missed CalendarTriggers
#     once the PC is awake.
#
# Logs: G:\YoutubeShorts\run_batch.log (rotated when over ~5MB)

$ErrorActionPreference = "Continue"
Set-Location "G:\YoutubeShorts"

$log    = "run_batch.log"
$marker = "run_batch.lastrun"

# ----- daily-once guard -----
$today = (Get-Date).ToString("yyyy-MM-dd")
if (Test-Path $marker) {
    $prev = (Get-Content $marker -Raw).Trim()
    if ($prev -eq $today) {
        Add-Content -Path $log -Value ("[{0}] skip -- already ran today ({1})" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $prev)
        exit 0
    }
}

# ----- load .env into the current session so child python sees the vars -----
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

# ----- rotate log -----
if ((Test-Path $log) -and ((Get-Item $log).Length -gt 5MB)) {
    Move-Item $log "$log.1" -Force
}

$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $log -Value ""
Add-Content -Path $log -Value ("===== {0} -- batch start =====" -f $stamp)

python generate_batch.py 3 *>&1 | Tee-Object -FilePath $log -Append
$rc = $LASTEXITCODE

$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $log -Value ("===== {0} -- batch end (exit {1}) =====" -f $stamp, $rc)

# Only mark today as done if python actually succeeded -- failed runs should
# be retried on the next trigger fire.
if ($rc -eq 0) {
    Set-Content -Path $marker -Value $today -Encoding ASCII
}

exit $rc
