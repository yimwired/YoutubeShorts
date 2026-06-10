# Daily wrapper for swap_thumbnails.py -- A/B swap + analytics-to-Notion.
# Invoked by Task Scheduler "FactSnapSwap" at 02:00.
# Log: G:\YoutubeShorts\run_swap.log (rotated when over ~5MB)

# ===== DISABLED 2026-06-10: full-cloud migration =====
# GitHub Actions (swap.yml, 02:00 Bangkok cron) runs the A/B swap now.
# Kill-switch because disabling the scheduled task needs admin.
Add-Content -Path "G:\YoutubeShorts\run_swap.log" -Value ("[{0}] skip -- disabled, GH Actions is primary (full-cloud)" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss")) -ErrorAction SilentlyContinue
exit 0
# ======================================================

$ErrorActionPreference = "Continue"
Set-Location "G:\YoutubeShorts"

$log = "run_swap.log"

if (Test-Path ".env") {
    Get-Content ".env" | ForEach-Object {
        if ($_ -match '^\s*([^#=][^=]*?)\s*=\s*(.*)\s*$') {
            Set-Item -Path "Env:$($matches[1])" -Value $matches[2]
        }
    }
}

$env:PYTHONIOENCODING = "utf-8"

if ((Test-Path $log) -and ((Get-Item $log).Length -gt 5MB)) {
    Move-Item $log "$log.1" -Force
}

$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $log -Value ""
Add-Content -Path $log -Value ("===== {0} -- swap start =====" -f $stamp)

python swap_thumbnails.py *>&1 | Tee-Object -FilePath $log -Append
$rc = $LASTEXITCODE

$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $log -Value ("===== {0} -- swap end (exit {1}) =====" -f $stamp, $rc)

exit $rc
