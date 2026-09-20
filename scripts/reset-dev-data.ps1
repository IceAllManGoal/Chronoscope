#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Удаляет локальные dev-данные Chronoscope.

.DESCRIPTION
    Удаляет содержимое каталога data/ в корне репозитория: SQLite-базу и её
    служебные файлы (WAL, SHM), а также архивные подкаталоги, если они есть.

    Скрипт намеренно ограничен каталогом data/ и не трогает исходный код,
    миграции, фикстуры и конфигурацию. Путь вычисляется относительно самого
    скрипта, поэтому запускать его можно из любого каталога.

    Что скрипт НЕ делает: не останавливает запущенный Core. Если Core работает
    и держит базу открытой, удаление файла на Windows может завершиться
    ошибкой — сначала останови Core.

.PARAMETER KeepGitkeep
    Сохранить файл .gitkeep в каталоге data/. По умолчанию сохраняется:
    иначе git перестанет отслеживать каталог.

.EXAMPLE
    pwsh scripts/reset-dev-data.ps1

    Удаляет локальную базу и служебные файлы.

.EXAMPLE
    pwsh scripts/reset-dev-data.ps1 -WhatIf

    Показывает, что было бы удалено, ничего не удаляя.

.EXAMPLE
    pwsh scripts/reset-dev-data.ps1 -Confirm

    Запрашивает подтверждение перед каждым удалением.
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [switch]$KeepGitkeep = $true
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$dataDir = Join-Path $repoRoot 'data'

if (-not (Test-Path -LiteralPath $dataDir)) {
    Write-Host "Каталог данных отсутствует: $dataDir"
    Write-Host 'Нечего сбрасывать.'
    exit 0
}

# Страховка: удаляем только внутри каталога data/ репозитория.
$resolvedData = (Resolve-Path -LiteralPath $dataDir).Path
$resolvedRoot = (Resolve-Path -LiteralPath $repoRoot).Path
if (-not $resolvedData.StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Отказ: каталог данных ($resolvedData) находится вне репозитория ($resolvedRoot)."
}
if ((Split-Path -Leaf $resolvedData) -ne 'data') {
    throw "Отказ: ожидался каталог с именем 'data', получено '$resolvedData'."
}

$removed = 0
$skipped = 0
$failed = 0

Get-ChildItem -LiteralPath $resolvedData -Force | ForEach-Object {
    $item = $_

    if ($KeepGitkeep -and $item.Name -eq '.gitkeep') {
        $skipped++
        Write-Verbose "Пропущен: $($item.Name)"
        return
    }

    if ($PSCmdlet.ShouldProcess($item.FullName, 'Remove')) {
        try {
            Remove-Item -LiteralPath $item.FullName -Recurse -Force
            Write-Host "Удалено: $($item.Name)"
            $removed++
        }
        catch {
            Write-Warning "Не удалось удалить $($item.Name): $($_.Exception.Message)"
            $failed++
        }
    }
}

Write-Host ''
Write-Host "Удалено объектов: $removed"
if ($skipped -gt 0) { Write-Host "Пропущено (.gitkeep): $skipped" }
if ($failed -gt 0) {
    Write-Host "Не удалось удалить: $failed"
    Write-Host 'Если Core запущен — останови его и повтори.'
    exit 1
}

Write-Host 'Dev-данные сброшены.'
