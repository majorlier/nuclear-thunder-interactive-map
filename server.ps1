<#
.SYNOPSIS
    Serves this folder over HTTP so the Nuclear Thunder map can be opened.

.DESCRIPTION
    The page loads its data with fetch(), so index.html cannot run straight
    from disk. This script starts a static server in the folder it lives in
    and opens the default browser on it.

    Python's http.server is used when Python is available; otherwise a small
    built-in PowerShell server takes over, so no runtime has to be installed.

.PARAMETER Port
    Exact port to serve on. When it is busy the next 20 ports are tried.
    Omitted (the default) means "pick the first free port automatically".

.PARAMETER NoBrowser
    Start the server without opening a browser window.

.PARAMETER Internal
    Use the built-in PowerShell server even when Python is installed.

.EXAMPLE
    .\server.ps1

.EXAMPLE
    .\server.ps1 -Port 9000 -NoBrowser
#>
param(
    [int]$Port = 0,
    [switch]$NoBrowser,
    [switch]$Internal
)

$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
if (-not $root) { $root = (Get-Location).Path }
$root = [System.IO.Path]::GetFullPath($root).TrimEnd('\')

function Test-PortInUse([int]$candidatePort) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect('127.0.0.1', $candidatePort, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne(200)) { return $false }
        $client.EndConnect($async)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Find-FreePort([int]$first, [int]$last) {
    for ($candidate = $first; $candidate -le $last; $candidate++) {
        if (-not (Test-PortInUse $candidate)) { return $candidate }
    }
    return 0
}

$automaticPort = $Port -le 0
if ($automaticPort) {
    $Port = Find-FreePort 8080 8120
    if ($Port -eq 0) {
        Write-Host "No free port between 8080 and 8120. Pass -Port <number>." -ForegroundColor Red
        exit 1
    }
} else {
    $requestedPort = $Port
    while ((Test-PortInUse $Port) -and ($Port -lt ($requestedPort + 20))) { $Port++ }
    if (Test-PortInUse $Port) {
        Write-Host "Ports $requestedPort to $($requestedPort + 20) are busy. Pass another -Port." -ForegroundColor Red
        exit 1
    }
}

$url = "http://127.0.0.1:$Port/"

function Show-Banner {
    Write-Host ""
    Write-Host "  Nuclear Thunder Interactive Map" -ForegroundColor Cyan
    Write-Host "  Folder : $root"
    Write-Host "  Port   : $Port$(if ($automaticPort) { ' (picked automatically)' })"
    Write-Host "  Address: $url" -ForegroundColor Green
    Write-Host "  Stop   : press Ctrl+C in this window"
    Write-Host ""
}

function Open-Browser([string]$address) {
    if ($NoBrowser) { return }
    try {
        Start-Process $address | Out-Null
        Write-Host "  Browser: opening $address" -ForegroundColor DarkGray
    } catch {
        Write-Host "  Browser: launch failed, open $address manually" -ForegroundColor Yellow
    }
}

# Prefer Python's http.server: it is fast and handles parallel requests.
$python = $null
if (-not $Internal) {
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($command) {
        & $command.Source -c "import http.server" 2>$null
        if ($LASTEXITCODE -eq 0) { $python = $command.Source }
    }
}

if ($python) {
    Show-Banner
    $arguments = @('-m', 'http.server', "$Port", '--bind', '127.0.0.1')
    $process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $root -PassThru -NoNewWindow
    for ($attempt = 0; $attempt -lt 100; $attempt++) {
        if (Test-PortInUse $Port) { break }
        Start-Sleep -Milliseconds 100
    }
    Open-Browser $url
    Wait-Process -Id $process.Id
    return
}

# Fallback: a tiny static file server that needs nothing but Windows PowerShell.
function Send-Response($stream, [string]$status, [string]$contentType, [byte[]]$body) {
    $header = "HTTP/1.1 $status`r`nContent-Type: $contentType`r`nContent-Length: $($body.Length)`r`nCache-Control: no-cache`r`nConnection: close`r`n`r`n"
    $headerBytes = [System.Text.Encoding]::ASCII.GetBytes($header)
    $stream.Write($headerBytes, 0, $headerBytes.Length)
    if ($body.Length -gt 0) { $stream.Write($body, 0, $body.Length) }
    $stream.Flush()
}

$mime = @{
    '.html' = 'text/html; charset=utf-8'
    '.htm'  = 'text/html; charset=utf-8'
    '.js'   = 'text/javascript; charset=utf-8'
    '.json' = 'application/json; charset=utf-8'
    '.css'  = 'text/css; charset=utf-8'
    '.txt'  = 'text/plain; charset=utf-8'
    '.md'   = 'text/plain; charset=utf-8'
    '.png'  = 'image/png'
    '.webp' = 'image/webp'
    '.svg'  = 'image/svg+xml'
    '.ico'  = 'image/x-icon'
    '.dds'  = 'image/vnd-ms.dds'
}

$listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $Port)
$listener.Start()
Show-Banner
Write-Host "  Engine : built-in PowerShell server (Python not used)" -ForegroundColor DarkGray
Open-Browser $url

while ($true) {
    $client = $listener.AcceptTcpClient()
    try {
        $client.ReceiveTimeout = 5000
        $client.SendTimeout = 60000
        $stream = $client.GetStream()
        $reader = New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::ASCII, $false, 4096, $true)
        $requestLine = $reader.ReadLine()
        if ([string]::IsNullOrWhiteSpace($requestLine)) { continue }
        while ($true) {
            $line = $reader.ReadLine()
            if ([string]::IsNullOrEmpty($line)) { break }
        }

        $parts = $requestLine -split ' '
        $method = $parts[0]
        $target = $parts[1]
        if ($method -ne 'GET' -and $method -ne 'HEAD') {
            Send-Response $stream '405 Method Not Allowed' 'text/plain; charset=utf-8' ([System.Text.Encoding]::UTF8.GetBytes('Method not allowed'))
            continue
        }

        $path = $target.Split('?')[0]
        $path = [System.Uri]::UnescapeDataString($path)
        if ($path -eq '/') { $path = '/index.html' }
        $relative = $path.TrimStart('/').Replace('/', '\')
        $full = [System.IO.Path]::GetFullPath((Join-Path $root $relative))

        if (-not $full.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {
            Send-Response $stream '403 Forbidden' 'text/plain; charset=utf-8' ([System.Text.Encoding]::UTF8.GetBytes('Forbidden'))
            continue
        }
        if (Test-Path $full -PathType Container) {
            $full = Join-Path $full 'index.html'
        }
        if (-not (Test-Path $full -PathType Leaf)) {
            Write-Host "  404 $path" -ForegroundColor DarkYellow
            Send-Response $stream '404 Not Found' 'text/plain; charset=utf-8' ([System.Text.Encoding]::UTF8.GetBytes('Not found'))
            continue
        }

        $extension = [System.IO.Path]::GetExtension($full).ToLowerInvariant()
        $contentType = $mime[$extension]
        if (-not $contentType) { $contentType = 'application/octet-stream' }
        $bytes = [System.IO.File]::ReadAllBytes($full)
        if ($method -eq 'HEAD') { $bytes = New-Object byte[] 0 }
        Send-Response $stream '200 OK' $contentType $bytes
    } catch {
        Write-Host "  request failed: $($_.Exception.Message)" -ForegroundColor DarkRed
    } finally {
        $client.Close()
    }
}
