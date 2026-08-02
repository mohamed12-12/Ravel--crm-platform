param(
    [string]$Python = "C:\Python314\python.exe",
    [int]$ApiPort = 3000,
    [int]$DemoPort = 5001,
    [int]$GatewayPort = 8080,
    [switch]$NoNgrok,
    [switch]$NoRestart
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ApiScript = Join-Path $RepoRoot "apps\api\run.py"
$DemoScript = Join-Path $RepoRoot "demo_web\app.py"
$GatewayScript = Join-Path $RepoRoot "scripts\dev_gateway.py"
$LogDir = Join-Path $RepoRoot ".tmp-run"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Stop-MatchingProcess {
    param(
        [string]$ProcessName,
        [string[]]$Patterns
    )

    if ($NoRestart) {
        return
    }

    Get-CimInstance Win32_Process |
        Where-Object {
            if ($_.Name -ne $ProcessName) {
                return $false
            }
            $commandLine = ($_.CommandLine -replace "\\", "/")
            foreach ($pattern in $Patterns) {
                $normalizedPattern = ($pattern -replace "\\", "/")
                if ($commandLine -notlike "*$normalizedPattern*") {
                    return $false
                }
            }
            return $true
        } |
        ForEach-Object {
            Write-Host "Stopping existing $ProcessName process $($_.ProcessId)"
            Stop-Process -Id $_.ProcessId -Force
        }
}

function Start-WithEnvironment {
    param(
        [string]$Name,
        [string]$FilePath,
        [string[]]$Arguments,
        [hashtable]$Environment,
        [string]$OutLog,
        [string]$ErrLog
    )

    $previous = @{}
    foreach ($key in $Environment.Keys) {
        $previous[$key] = [Environment]::GetEnvironmentVariable($key, "Process")
        [Environment]::SetEnvironmentVariable($key, [string]$Environment[$key], "Process")
    }

    try {
        $process = Start-Process `
            -FilePath $FilePath `
            -ArgumentList $Arguments `
            -WorkingDirectory $RepoRoot `
            -WindowStyle Hidden `
            -RedirectStandardOutput $OutLog `
            -RedirectStandardError $ErrLog `
            -PassThru
        Write-Host "Started $Name (PID $($process.Id))"
        return $process
    }
    finally {
        foreach ($key in $Environment.Keys) {
            [Environment]::SetEnvironmentVariable($key, $previous[$key], "Process")
        }
    }
}

function Wait-ForPort {
    param(
        [int]$Port,
        [int]$TimeoutSeconds = 20
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        if ($listener) {
            return $true
        }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

if (-not (Test-Path $Python)) {
    throw "Python executable not found: $Python"
}

Stop-MatchingProcess -ProcessName "python.exe" -Patterns @("apps/api/run.py")
Stop-MatchingProcess -ProcessName "python.exe" -Patterns @("demo_web/app.py")
Stop-MatchingProcess -ProcessName "python.exe" -Patterns @("scripts/dev_gateway.py")
Stop-MatchingProcess -ProcessName "ngrok.exe" -Patterns @("http")

$api = Start-WithEnvironment `
    -Name "Rahma API" `
    -FilePath $Python `
    -Arguments @("`"$ApiScript`"") `
    -Environment @{
        "PORT" = $ApiPort
        "FLASK_CONFIG" = "development"
        "FLASK_USE_RELOADER" = "false"
    } `
    -OutLog (Join-Path $LogDir "api.out.log") `
    -ErrLog (Join-Path $LogDir "api.err.log")

$demo = Start-WithEnvironment `
    -Name "Rahma demo web" `
    -FilePath $Python `
    -Arguments @("`"$DemoScript`"") `
    -Environment @{
        "APP_HOST" = "127.0.0.1"
        "APP_PORT" = $DemoPort
        "APP_USE_RELOADER" = "false"
    } `
    -OutLog (Join-Path $LogDir "demo-web.out.log") `
    -ErrLog (Join-Path $LogDir "demo-web.err.log")

$gateway = Start-WithEnvironment `
    -Name "Rahma dev gateway" `
    -FilePath $Python `
    -Arguments @(
        "`"$GatewayScript`"",
        "--port",
        $GatewayPort,
        "--p3000-target",
        $ApiPort,
        "--p5001-target",
        $DemoPort
    ) `
    -Environment @{} `
    -OutLog (Join-Path $LogDir "gateway.out.log") `
    -ErrLog (Join-Path $LogDir "gateway.err.log")

$ngrokUrl = $null
if (-not $NoNgrok) {
    $ngrok = Start-Process `
        -FilePath "ngrok" `
        -ArgumentList "http $GatewayPort --pooling-enabled --log `"$($LogDir)\ngrok-gateway.log`"" `
        -WorkingDirectory $RepoRoot `
        -WindowStyle Hidden `
        -PassThru
    Write-Host "Started ngrok for gateway port $GatewayPort (PID $($ngrok.Id))"

    Start-Sleep -Seconds 4
    try {
        $tunnels = Invoke-RestMethod -Uri "http://127.0.0.1:4040/api/tunnels" -TimeoutSec 5
        $ngrokUrl = ($tunnels.tunnels | Where-Object { $_.config.addr -like "*:$GatewayPort" } | Select-Object -First 1).public_url
    }
    catch {
        Write-Host "Ngrok started, but the local ngrok API did not respond yet."
    }
}

$apiReady = Wait-ForPort -Port $ApiPort
$demoReady = Wait-ForPort -Port $DemoPort
$gatewayReady = Wait-ForPort -Port $GatewayPort

Write-Host ""
Write-Host "Rahma dev services:"
Write-Host "  API:      http://127.0.0.1:$ApiPort"
Write-Host "  Demo web: http://127.0.0.1:$DemoPort"
Write-Host "  Gateway:  http://127.0.0.1:$GatewayPort"
if ($ngrokUrl) {
    Write-Host "  Ngrok:    $ngrokUrl -> http://127.0.0.1:$GatewayPort"
    Write-Host "  Public API/3000:  $ngrokUrl/p3000/"
    Write-Host "  Public demo/5001: $ngrokUrl/p5001/"
    Write-Host "  Public CRM alias: $ngrokUrl/crm/"
    Write-Host "  Public demo alias: $ngrokUrl/demo/"
}
Write-Host ""
Write-Host "Logs:"
Write-Host "  $LogDir\api.out.log"
Write-Host "  $LogDir\api.err.log"
Write-Host "  $LogDir\demo-web.out.log"
Write-Host "  $LogDir\demo-web.err.log"
Write-Host "  $LogDir\gateway.out.log"
Write-Host "  $LogDir\gateway.err.log"
if (-not $NoNgrok) {
    Write-Host "  $LogDir\ngrok-gateway.log"
}

if (-not $apiReady -or -not $demoReady -or -not $gatewayReady) {
    Write-Host ""
    Write-Host "One or more ports did not start listening before the timeout. Check the logs above."
    exit 1
}
