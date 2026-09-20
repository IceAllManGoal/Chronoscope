#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Прогоняет тесты Core и Agent (§58).

.DESCRIPTION
    Оба набора прогоняются всегда, даже если первый упал: смысл скрипта — увидеть
    полную картину, а не первую ошибку. Так же устроен CI, и расхождение между
    локальным прогоном и гейтом pull request было бы неприятным сюрпризом.

    Скрипт ничего не устанавливает, не применяет миграции и не трогает данные:
    тесты Core работают на временной базе (§58), тесты Agent базы не касаются.

    Окружение Core ищется так: сначала `uv` в PATH, затем `uv`, установленный в
    `core/.venv/Scripts`, затем интерпретатор `core/.venv`. Последний вариант —
    не украшение: `uv` может быть не в PATH, а окружение уже готово, и падать
    из-за этого скрипт не должен.

.PARAMETER Core
    Прогнать только тесты Core.

.PARAMETER Agent
    Прогнать только тесты Agent.

.OUTPUTS
    Код возврата: 0 — прошли все запрошенные наборы; 1 — хотя бы один упал;
    2 — не удалось запустить (нет окружения Core или .NET SDK). Второй код
    отделён от первого намеренно: «тесты упали» и «тесты не запускались» —
    разные новости, и смешивать их в один код возврата нельзя.

.EXAMPLE
    pwsh scripts/test.ps1

    Прогоняет Core (287 тестов) и Agent (97 тестов).

.EXAMPLE
    pwsh scripts/test.ps1 -Core

    Только Core — например, при работе над Core.

.EXAMPLE
    powershell -File scripts/test.ps1

    То же под Windows PowerShell 5.1: скрипты записаны в UTF-8 с BOM именно
    для того, чтобы 5.1 читал их правильно (см. .editorconfig).
#>
[CmdletBinding()]
param(
    [switch]$Core,
    [switch]$Agent
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$coreDir = Join-Path $repoRoot 'core'
$agentDir = Join-Path $repoRoot 'agent'

function Resolve-CoreRunner {
    <#
        Возвращает способ запустить что-то в окружении Core: сначала uv, затем
        интерпретатор из .venv. Возвращает $null, если ни того, ни другого нет.
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

# Без ключей прогоняются оба набора; с ключом — только названный.
$runCore = -not $Agent -or $Core
$runAgent = -not $Core -or $Agent

$results = @()
$notRun = @()

if ($runCore) {
    Write-Host 'Core tests' -ForegroundColor Cyan
    $runner = Resolve-CoreRunner

    if ($null -eq $runner) {
        Write-Warning 'Окружение Core не найдено: нет ни uv, ни core/.venv. Выполни: cd core; uv sync --all-groups'
        $notRun += 'Core'
    }
    else {
        if ($runner.Kind -eq 'uv') {
            Write-Host ("  запуск: {0} run pytest" -f $runner.Path) -ForegroundColor DarkGray
            $coreArguments = @('run', 'pytest')
        }
        else {
            Write-Host ("  uv не найден, запуск: {0} -m pytest" -f $runner.Path) -ForegroundColor DarkGray
            $coreArguments = @('-m', 'pytest')
        }

        # Как в CI: без этого консоль на Windows спотыкается о кириллицу в выводе pytest.
        $env:PYTHONUTF8 = '1'

        Push-Location -LiteralPath $coreDir
        try {
            & $runner.Path @coreArguments
            $coreExit = $LASTEXITCODE
        }
        finally {
            Pop-Location
        }

        $results += [pscustomobject]@{ Name = 'Core'; ExitCode = $coreExit }
    }
}

if ($runAgent) {
    Write-Host 'Agent tests' -ForegroundColor Cyan
    $dotnet = Get-Command dotnet -ErrorAction SilentlyContinue

    if (-not $dotnet) {
        Write-Warning 'dotnet не найден: тесты Agent требуют .NET SDK 8+ (§64).'
        $notRun += 'Agent'
    }
    else {
        Write-Host ("  запуск: {0} test Chronoscope.Agent.sln" -f $dotnet.Source) -ForegroundColor DarkGray
        Push-Location -LiteralPath $agentDir
        try {
            & $dotnet.Source test 'Chronoscope.Agent.sln' --nologo
            $agentExit = $LASTEXITCODE
        }
        finally {
            Pop-Location
        }

        $results += [pscustomobject]@{ Name = 'Agent'; ExitCode = $agentExit }
    }
}

Write-Host ''
Write-Host 'Итог' -ForegroundColor Cyan

foreach ($result in $results) {
    if ($result.ExitCode -eq 0) {
        Write-Host ("  {0,-6} прошёл" -f $result.Name) -ForegroundColor Green
    }
    else {
        Write-Host ("  {0,-6} упал (код {1})" -f $result.Name, $result.ExitCode) -ForegroundColor Red
    }
}

foreach ($name in $notRun) {
    Write-Host ("  {0,-6} не запускался" -f $name) -ForegroundColor Yellow
}

if ($notRun.Count -gt 0) {
    # Предусловие не выполнено: это не результат тестов, и код возврата другой.
    exit 2
}

if ($results | Where-Object { $_.ExitCode -ne 0 }) {
    exit 1
}

exit 0
