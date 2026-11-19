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

    It also frees idle VRAM. ComfyUI keeps its models in video memory after a job
    finishes, with no timeout. On 2026-11-19 an idle ComfyUI held about 13 GB of a
    16 GB card for days, which starved games (RTX Remix fell back to paging
    textures every frame). Stopping ComfyUI by hand does not help either: this
    guard relaunches it within 5 minutes. So once ComfyUI has had nothing queued
    and finished nothing new for IdleMinutesBeforeUnload, and its process holds at
    least UnloadThresholdMB of dedicated VRAM, the guard POSTs /free to unload the
    models. That happens once per idle spell. The next job reloads them, which
    costs a little time on the first render after a quiet spell.
    Set IdleMinutesBeforeUnload to 0 to turn this off.

    Restarts, failed checks and unloads are logged to user\watchdog.log beside
    ComfyUI's log.
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
    # Idle unload. With the 5-minute tick, models go 10 to 15 minutes after the
    # last job. 0 disables it.
    [int]$IdleMinutesBeforeUnload = 10,
    # Below this, ComfyUI is holding little more than its CUDA context (about
    # 200 MB when empty), so there is nothing worth freeing.
    [int]$UnloadThresholdMB = 1024,
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

# State is one JSON object shared by the hang check (failures) and the idle
# unload (lastBusy, lastPromptId, unloaded). Each writer keeps the other's keys.
function Get-State {
    $state = @{ failures = 0; lastBusy = $null; lastPromptId = $null; unloaded = $false }
    try {
        $saved = Get-Content -Path $statePath -Raw | ConvertFrom-Json
        foreach ($property in $saved.PSObject.Properties) { $state[$property.Name] = $property.Value }
    } catch {}
    return $state
}

function Save-State([hashtable]$state) {
    try { ($state | ConvertTo-Json) | Set-Content -Path $statePath } catch {}
}

function Get-Failures {
    return [int](Get-State).failures
}

function Set-Failures([int]$count) {
    $state = Get-State
    $state.failures = $count
    Save-State $state
}

function Invoke-Api([string]$path, [string]$method = 'GET', [string]$body = $null) {
    $request = @{
        Uri = "http://127.0.0.1:${Port}$path"
        Method = $method
        UseBasicParsing = $true
        TimeoutSec = $HttpTimeoutSec
    }
    if ($body) {
        $request.Body = $body
        $request.ContentType = 'application/json'
    }
    return Invoke-WebRequest @request
}

function Get-ProcessVramMB([int]$processId) {
    # Dedicated VRAM held by one process, from the same counters Task Manager's
    # GPU columns use. Returns $null if the counters cannot be read.
    try {
        $samples = (Get-Counter '\GPU Process Memory(*)\Dedicated Usage' -ErrorAction Stop).CounterSamples |
            Where-Object { $_.InstanceName -like "pid_${processId}_*" }
        return [int](($samples | Measure-Object -Property CookedValue -Sum).Sum / 1MB)
    } catch {
        return $null
    }
}

function Invoke-IdleUnload([int]$processId) {
    if ($IdleMinutesBeforeUnload -le 0) { return }
    $state = Get-State
    $now = Get-Date

    # Busy: anything running or waiting, or a job finished since the last tick.
    # The history check catches short jobs (portraits take seconds) that start
    # and finish between two ticks without ever being seen in the queue.
    $queue = (Invoke-Api '/queue').Content | ConvertFrom-Json
    $history = (Invoke-Api '/history?max_items=1').Content | ConvertFrom-Json
    $newestPromptId = @($history.PSObject.Properties.Name)[0]
    $busy = (@($queue.queue_running).Count -gt 0) -or (@($queue.queue_pending).Count -gt 0) -or
        ($newestPromptId -and $newestPromptId -ne $state.lastPromptId)
    $state.lastPromptId = $newestPromptId

    if ($busy -or -not $state.lastBusy) {
        # No lastBusy yet means the guard has just started tracking (first run, or
        # ComfyUI relaunched with an empty history). Start the idle clock now
        # rather than unloading on the first tick.
        $state.lastBusy = $now.ToString('o')
        $state.unloaded = $false
        Save-State $state
        return
    }

    $idleMinutes = ($now - [datetime]$state.lastBusy).TotalMinutes
    if ($state.unloaded -or $idleMinutes -lt $IdleMinutesBeforeUnload) {
        Save-State $state
        return
    }

    $vramMB = Get-ProcessVramMB $processId
    if ($null -ne $vramMB -and $vramMB -lt $UnloadThresholdMB) {
        # Already small. Do not mark it unloaded: if a later job loads models
        # again, the history check starts a new idle spell anyway.
        Save-State $state
        return
    }

    # ComfyUI sets flags here, and its worker unloads straight away when idle.
    # A job queued at the same moment runs after the unload and reloads models.
    Invoke-Api '/free' 'POST' '{"unload_models": true, "free_memory": true}' | Out-Null
    $held = if ($null -eq $vramMB) { 'an unknown amount of' } else { "$vramMB MB of" }
    Write-GuardLog ("idle for {0:N0} min holding {1} VRAM: asked ComfyUI to unload its models" -f $idleMinutes, $held)
    $state.unloaded = $true
    Save-State $state
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
            try {
                Invoke-IdleUnload $listening[0].OwningProcess
            } catch {
                Write-GuardLog "idle unload check failed: $($_.Exception.Message)"
            }
            exit 0  # serving
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
