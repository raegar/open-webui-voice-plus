<#
    ComfyUI watchdog guard.

    Run by the scheduled task ComfyUI-H3-Autostart under an S4U (non-interactive)
    principal, so this check never puts a console window on the desktop. When it
    decides ComfyUI is down it hands off to ComfyUI-H3-Launch, which runs
    interactively and is the only part allowed to show a window.

    Splitting the two is what stops a console flashing up every 5 minutes; the
    old single task ran cmd.exe in the user's session on every tick.

    Two failures are handled:
    - Down: nothing is listening on the port. Launch it.
    - Hung: something holds the port but does not answer HTTP. On 2026-10-08 a
      ComfyUI process stuck in a GPU driver call (while loading a model) kept the
      port for 14 hours and answered nothing, and the old guard, which only looked
      at the port, saw it as healthy. Now each tick asks /system_stats; after
      FailuresBeforeRestart unanswered ticks in a row (about 10 minutes) it ends
      the process holding the port and relaunches. One slow tick during a heavy
      render is never enough.

    Restarts and failed checks are logged to user\watchdog.log beside ComfyUI's log.
#>
param(
    # Overridable so the guard can be tested without touching ComfyUI: point it at
    # another port, launch task and state folder.
    [int]$Port = 8188,
    [string]$LaunchTask = 'ComfyUI-H3-Launch',
    [int]$HttpTimeoutSec = 20,
    [int]$FailuresBeforeRestart = 3,
    # A process this young is still starting up; leave it alone.
    [int]$StartupGraceMinutes = 10,
    [string]$StateDir = (Join-Path $PSScriptRoot 'user')
)

$ErrorActionPreference = 'Stop'
$statePath = Join-Path $StateDir 'watchdog_state.json'
$logPath = Join-Path $StateDir 'watchdog.log'

function Write-GuardLog([string]$message) {
    try {
        Add-Content -Path $logPath -Value ('[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $message)
    } catch {}
}

function Get-Failures {
    try { return [int](Get-Content -Path $statePath -Raw | ConvertFrom-Json).failures } catch { return 0 }
}

function Set-Failures([int]$count) {
    try { (@{ failures = $count } | ConvertTo-Json) | Set-Content -Path $statePath } catch {}
}

function Test-Answering {
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:${Port}/system_stats" -UseBasicParsing -TimeoutSec $HttpTimeoutSec
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Start-LaunchTask {
    # Exit code, not stderr: see the note on taskkill below.
    schtasks.exe /run /tn $LaunchTask | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-GuardLog "launched via $LaunchTask"
    } else {
        Write-GuardLog "could not start $LaunchTask (schtasks exit $LASTEXITCODE)"
    }
}

function Test-PortBound {
    return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

try {
    $listening = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    if ($listening.Count -gt 0) {
        if (Test-Answering) {
            $previous = Get-Failures
            if ($previous -gt 0) { Write-GuardLog "answering again after $previous unanswered check(s)" }
            Set-Failures 0
            exit 0  # serving; nothing to do
        }

        $ownerId = $listening[0].OwningProcess
        $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$ownerId" -ErrorAction SilentlyContinue
        if ($owner -and ((Get-Date) - $owner.CreationDate).TotalMinutes -lt $StartupGraceMinutes) {
            exit 0  # still starting up
        }

        $failures = (Get-Failures) + 1
        Set-Failures $failures
        Write-GuardLog "port $Port is held by PID $ownerId but did not answer within $HttpTimeoutSec s ($failures of $FailuresBeforeRestart)"
        if ($failures -lt $FailuresBeforeRestart) { exit 0 }

        # Hung. End the process holding the port and the venv launcher stub that
        # started it, and nothing else: other python.exe processes are not ours.
        Write-GuardLog "ComfyUI is hung: ending PID $ownerId and relaunching"
        $targets = @($ownerId)
        if ($owner) {
            $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($owner.ParentProcessId)" -ErrorAction SilentlyContinue
            if ($parent -and $parent.Name -eq 'python.exe' -and $parent.CommandLine -match 'main\.py') {
                $targets += $parent.ProcessId
            }
        }
        foreach ($target in $targets) {
            # No 2>&1: under ErrorActionPreference Stop, Windows PowerShell turns a
            # native command's stderr into a terminating error, and taskkill does
            # complain about a process stuck in a driver call. The wait below is the
            # real test of whether it went.
            taskkill.exe /F /T /PID $target | Out-Null
        }

        # A process stuck in a driver call can take a while to unwind; on
        # 2026-10-09 it held the port for about a minute after being ended.
        $deadline = (Get-Date).AddMinutes(2)
        while ((Get-Date) -lt $deadline -and (Test-PortBound)) { Start-Sleep -Seconds 5 }
        if (Test-PortBound) {
            # Leave the count at the threshold so the next tick tries again at once.
            Write-GuardLog "port $Port is still held after ending PID $ownerId; retrying next check"
            exit 0
        }

        Set-Failures 0
        Start-LaunchTask
        exit 0
    }

    Set-Failures 0

    # The port binds a little before the models finish loading. Without this a
    # tick during startup would fire a second copy that loads models, fails to
    # bind, and exits - wasted RAM and disk for nothing.
    $starting = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'main\.py' }
    if ($starting) { exit 0 }

    Write-GuardLog "nothing listening on port ${Port}"
    Start-LaunchTask
    exit 0
} catch {
    # Never let the guard fail loudly: a bad tick must not disable the watchdog.
    Write-GuardLog "guard error: $($_.Exception.Message)"
    exit 0
}
