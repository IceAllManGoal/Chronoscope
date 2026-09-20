#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Поднимает Core и Agent в dev-режиме одной командой (§63).

.DESCRIPTION
    §63 описывает dev-режим как два терминала. Скрипт делает то же самое, но в
    одном шаге: приводит окружение Core в порядок, применяет миграции и запускает
    оба компонента — каждый в своём окне.

    Окна отдельные намеренно. У каждого компонента свой поток структурированных
    JSON-логов (§35), и в одном окне две такие ленты перемешались бы так, что
    разбирать их стало бы работой. Окно остаётся открытым и после завершения
    компонента: иначе причина падения исчезла бы вместе с окном.

    Чего скрипт не делает: не удаляет данные (для этого scripts/reset-dev-data.ps1)
    и не создаёт файл конфигурации.

    Конфигурация выбирается так: `-Config`, иначе `chronoscope.toml` в корне
    репозитория, иначе — ничего, и тогда каждый компонент ищет свой файл в своём
    каталоге. Один файл на весь проект — то, что предполагает §36, поэтому корневой
    файл предпочтён двум раздельным.

.PARAMETER Core
    Поднять только Core.

.PARAMETER Agent
    Поднять только Agent.

.PARAMETER Config
    Путь к chronoscope.toml. Оба компонента получат его через CHRONOSCOPE_CONFIG.

.PARAMETER SkipSync
    Не выполнять `uv sync --all-groups`. Окружение уже готово.

.PARAMETER SkipMigrate
    Не применять миграции. Пригодится, когда база заведомо в порядке.

.PARAMETER HealthTimeout
    Сколько секунд ждать ответа `/health` от Core. По умолчанию 30.

.PARAMETER DryRun
    Напечатать, что будет запущено, и ничего не запускать.

.OUTPUTS
    Код возврата: 0 — компоненты запущены и Core отвечает на `/health`;
    1 — Core не ответил за отведённое время либо упали миграции;
    2 — не выполнено предусловие (нет окружения Core или .NET SDK).

.EXAMPLE
    pwsh scripts/dev.ps1

    Поднимает Core и Agent, применив миграции.

.EXAMPLE
    pwsh scripts/dev.ps1 -Config .\chronoscope.toml -SkipSync

    Один файл конфигурации на весь проект, без синхронизации зависимостей.

.EXAMPLE
    pwsh scripts/dev.ps1 -DryRun

    Показывает команды, ничего не запуская.
#>
[CmdletBinding()]
param(
    [switch]$Core,
    [switch]$Agent,
    [string]$Config,
    [switch]$SkipSync,
    [switch]$SkipMigrate,
    [int]$HealthTimeout = 30,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$coreDir = Join-Path $repoRoot 'core'
$agentDir = Join-Path $repoRoot 'agent'
$defaultPort = 7342

# Без ключей поднимаются оба компонента; с ключом — только названный.
$runCore = -not $Agent -or $Core
$runAgent = -not $Core -or $Agent

function Resolve-CoreRunner {
    <#
        Как запускать что-то в окружении Core: uv из PATH, uv из .venv или сам
        интерпретатор .venv. Возвращает $null, если нет ничего.
    #>
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if ($uv) {
        return [pscustomobject]@{ Kind = 'uv'; Path = $uv.Source }
    }

    $venvUv = Join-Path $coreDir '.venv/Scripts/uv.exe'
    if (Test-Path -LiteralPath $venvUv) {
        return [pscustomobject]@{ Kind = 'uv'; Path = $venvUv }
    }

    $venvPython = Join-Path $coreDir '.venv/Scripts/python.exe'
    if (Test-Path -LiteralPath $venvPython) {
        return [pscustomobject]@{ Kind = 'python'; Path = $venvPython }
    }

    return $null
}

function Resolve-ConfigPath {
    param([string]$Requested)

    if ($Requested) {
        if (-not (Test-Path -LiteralPath $Requested)) {
            throw "Файл конфигурации не найден: $Requested"
        }
        return (Resolve-Path -LiteralPath $Requested).Path
    }

    $rootConfig = Join-Path $repoRoot 'chronoscope.toml'
    if (Test-Path -LiteralPath $rootConfig) {
        return $rootConfig
    }

    return $null
}

function Get-CorePort {
    <#
        Порт для проверки /health. Читается из файла конфигурации, если он есть,
        иначе берётся значение по умолчанию. Разбор нарочно простой: вытащить
        одну строку из своей же конфигурации, а не повторить парсер TOML
        (второй парсер рано или поздно разошёлся бы с первым).
    #>
    param([string]$ConfigPath)

    if (-not $ConfigPath) {
        return $defaultPort
    }

    $inCoreSection = $false
    foreach ($line in Get-Content -LiteralPath $ConfigPath) {
        $trimmed = $line.Trim()

        if ($trimmed -match '^\[(?<section>[^\]]+)\]') {
            $inCoreSection = ($Matches['section'].Trim() -eq 'core')
            continue
        }

        if ($inCoreSection -and $trimmed -match '^port\s*=\s*(?<port>\d+)') {
            return [int]$Matches['port']
        }
    }

    return $defaultPort
}

if ($runCore) {
    $runner = Resolve-CoreRunner
    if ($null -eq $runner) {
        Write-Warning 'Окружение Core не найдено: нет ни uv, ни core/.venv. Выполни: cd core; uv sync --all-groups'
        exit 2
    }
}

if ($runAgent) {
    $dotnet = Get-Command dotnet -ErrorAction SilentlyContinue
    if (-not $dotnet) {
        Write-Warning 'dotnet не найден: Agent требует .NET SDK 8+ (§64).'
        exit 2
    }
}

$configPath = Resolve-ConfigPath -Requested $Config
$corePort = Get-CorePort -ConfigPath $configPath

if ($configPath) {
    Write-Host ("Конфигурация: {0}" -f $configPath) -ForegroundColor DarkGray
}
else {
    Write-Host 'Конфигурация: не найдена, каждый компонент ищет свой файл в своём каталоге' -ForegroundColor DarkGray
}

# ── Окружение и схема ────────────────────────────────────────────────

if ($runCore -and -not $SkipSync -and -not $DryRun) {
    if ($runner.Kind -eq 'uv') {
        Write-Host 'uv sync --all-groups' -ForegroundColor Cyan
        Push-Location -LiteralPath $coreDir
        try {
            & $runner.Path sync --all-groups
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "uv sync завершился с кодом $LASTEXITCODE — окружение не готово."
                exit 2
            }
        }
        finally {
            Pop-Location
        }
    }
    else {
        Write-Warning 'uv не найден: синхронизация зависимостей пропущена, используется готовый core/.venv.'
    }
}

if ($runCore -and -not $SkipMigrate -and -not $DryRun) {
    # Core не применяет миграции сам (инвариант 11 §81) и при отсутствующей схеме
    # остаётся отвечать только на health. Поэтому схема приводится в порядок до
    # запуска, а не после первой жалобы.
    Write-Host 'Применение миграций' -ForegroundColor Cyan
    Push-Location -LiteralPath $coreDir
    try {
        if ($runner.Kind -eq 'uv') {
            & $runner.Path run alembic upgrade head
        }
        else {
            & $runner.Path -m alembic upgrade head
        }
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Миграции не применились (код $LASTEXITCODE). Core без схемы не работает — исправь и запусти снова."
            exit 1
        }
    }
    finally {
        Pop-Location
    }
}

# ── Запуск ───────────────────────────────────────────────────────────

# Пути передаются дочерним процессам переменными окружения, а не подставляются в
# командную строку: Start-Process склеивает аргументы в строку, и путь с пробелом
# («C:\Program Files\...») развалил бы команду на лишние аргументы.
$shell = Join-Path $PSHOME 'pwsh.exe'
if (-not (Test-Path -LiteralPath $shell)) {
    $shell = Join-Path $PSHOME 'powershell.exe'
}

if ($configPath) {
    $env:CHRONOSCOPE_CONFIG = $configPath
}

$started = @()

if ($runCore) {
    $env:CHRONOSCOPE_UV = $runner.Path

    if ($runner.Kind -eq 'uv') {
        $coreCommand = '& $env:CHRONOSCOPE_UV run python -m chronoscope'
    }
    else {
        $coreCommand = '& $env:CHRONOSCOPE_UV -m chronoscope'
    }

    $coreCommand = "`$host.UI.RawUI.WindowTitle = 'Chronoscope Core'; $coreCommand"

    if ($DryRun) {
        Write-Host ("Core  : {0} {1}" -f $shell, $coreCommand) -ForegroundColor DarkGray
    }
    else {
        $coreProcess = Start-Process -FilePath $shell `
            -ArgumentList "-NoExit -NoProfile -Command `"$coreCommand`"" `
            -WorkingDirectory $coreDir `
            -PassThru
        $started += [pscustomobject]@{ Name = 'Core'; Id = $coreProcess.Id; Url = "http://127.0.0.1:$corePort/ui/" }
    }
}

if ($runAgent) {
    $env:CHRONOSCOPE_DOTNET = $dotnet.Source
    $agentCommand = "`$host.UI.RawUI.WindowTitle = 'Chronoscope Agent'; & `$env:CHRONOSCOPE_DOTNET run --project src/Chronoscope.Agent.Host"

    if ($DryRun) {
        Write-Host ("Agent : {0} {1}" -f $shell, $agentCommand) -ForegroundColor DarkGray
    }
    else {
        $agentProcess = Start-Process -FilePath $shell `
            -ArgumentList "-NoExit -NoProfile -Command `"$agentCommand`"" `
            -WorkingDirectory $agentDir `
            -PassThru
        $started += [pscustomobject]@{ Name = 'Agent'; Id = $agentProcess.Id; Url = '' }
    }
}

if ($DryRun) {
    Write-Host ''
    Write-Host 'DryRun: ничего не запущено.' -ForegroundColor DarkGray
    exit 0
}

# ── Проверка, что Core действительно поднялся ────────────────────────

$coreHealthy = $false
if ($runCore) {
    Write-Host ("Ожидание ответа Core на http://127.0.0.1:{0}/api/v1/health" -f $corePort) -ForegroundColor Cyan
    $deadline = (Get-Date).AddSeconds($HealthTimeout)

    while ((Get-Date) -lt $deadline) {
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:$corePort/api/v1/health" -TimeoutSec 2
            $coreHealthy = $true
            break
        }
        catch {
            Start-Sleep -Milliseconds 500
        }
    }
}

Write-Host ''
Write-Host 'Chronoscope поднят' -ForegroundColor Cyan

foreach ($component in $started) {
    if ($component.Url) {
        Write-Host ("  {0,-6} окно открыто (pid {1}), {2}" -f $component.Name, $component.Id, $component.Url)
    }
    else {
        Write-Host ("  {0,-6} окно открыто (pid {1})" -f $component.Name, $component.Id)
    }
}

Write-Host '  Остановить: Ctrl+C в окне компонента или закрыть окно.'
Write-Host '  История: uv run chronoscope events (из каталога core).'

if ($runCore -and -not $coreHealthy) {
    Write-Host ''
    Write-Warning ("Core не ответил за {0} с. Причина — в его окне: чаще всего это неприменённая схема или занятый порт." -f $HealthTimeout)
    exit 1
}

exit 0
