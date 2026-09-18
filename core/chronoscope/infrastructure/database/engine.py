"""Создание SQLAlchemy engine для SQLite (§25).

Настройки соединения, которые §25 требует подтверждать benchmark'ами, заданы
здесь явно:

```text
WAL mode             журнал упреждающей записи: читатели не блокируют писателя
foreign_keys = ON    ссылочная целостность, по умолчанию в SQLite выключена
synchronous = NORMAL безопасный компромисс между durability и скоростью в WAL
busy_timeout         сколько ждать снятия блокировки записи
```
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine, event

from chronoscope.domain.errors import StorageError
from chronoscope.infrastructure.config.settings import CoreSettings


def ensure_database_directory(path: Path) -> None:
    """Создать каталог для файла БД, если его нет."""
    parent = path.parent
    if str(parent) in ("", "."):
        return
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StorageError(f"не удалось создать каталог для БД {parent}: {exc}") from exc


def build_database_url(path: Path) -> str:
    """Собрать URL SQLite для абсолютного или относительного пути.

    Путь приводится к абсолютному заранее: иначе он зависел бы от текущего
    каталога процесса в момент открытия каждого нового соединения.
    """
    return f"sqlite+pysqlite:///{path.expanduser().resolve().as_posix()}"


def _apply_pragmas(engine: Engine, busy_timeout_ms: int) -> None:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        finally:
            cursor.close()


def create_database_engine(settings: CoreSettings) -> Engine:
    """Создать engine и применить настройки SQLite.

    ``check_same_thread=False`` нужен потому, что FastAPI выполняет
    синхронные обработчики в пуле потоков, а соединение может быть
    переиспользовано другим потоком. Безопасность обеспечивается тем, что
    каждый обработчик берёт соединение из пула на время одной транзакции.
    """
    ensure_database_directory(settings.database_path)

    try:
        engine = create_engine(
            build_database_url(settings.database_path),
            connect_args={
                "check_same_thread": False,
                "timeout": settings.busy_timeout_ms / 1000,
            },
            future=True,
        )
    except Exception as exc:  # pragma: no cover - защита от неверного URL
        raise StorageError(f"не удалось создать engine для {settings.database_path}: {exc}") from exc

    _apply_pragmas(engine, settings.busy_timeout_ms)
    return engine


def missing_tables(engine: Engine) -> frozenset[str]:
    """Перечислить ожидаемые таблицы, которых нет в базе.

    Приложение не применяет миграции автоматически: §81 (инвариант 11) требует
    менять схему только через миграции, а неявное изменение схемы при старте
    процесса — это ровно тот случай, когда «удобно» расходится с предсказуемостью.
    Вместо этого Core проверяет схему и сообщает, что нужно сделать.

    Пустое множество означает, что схема на месте.
    """
    from sqlalchemy import inspect

    from chronoscope.infrastructure.database.models import metadata

    existing = set(inspect(engine).get_table_names())
    return frozenset(metadata.tables) - existing
