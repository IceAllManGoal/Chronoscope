# Dev-скрипты

PowerShell-скрипты для локальной разработки. Все запускаются из корня репозитория:

```powershell
pwsh scripts/<имя-скрипта>.ps1
```

| Скрипт | Назначение | Состояние |
|---|---|---|
| `reset-dev-data.ps1` | Удаляет локальные dev-данные Chronoscope из `data/` | работает |
| `dev.ps1` | Запускает Core и Agent в dev-режиме | появится вместе с Core (§63) |
| `test.ps1` | Прогоняет тесты Core и Agent | появится вместе с тестами (§58) |

`docker-compose.yml` в репозитории нет и не планируется в 0.0.1 — см. [ADR-0010](../docs/decisions/0010-no-containerization-in-0.0.1.md).
