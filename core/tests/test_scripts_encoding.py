"""Кодировка dev-скриптов (§64, §69).

Проверяется одна вещь, но она уже приводила к отказу: ``scripts/*.ps1`` обязаны
быть в UTF-8 **с BOM**.

Причина не косметическая. Windows PowerShell 5.1 читает скрипт без BOM в
системной кодировке, а не в UTF-8. Русские комментарии и строки превращаются в
мусор, и файл падает на разборе ещё до первого оператора — `reset-dev-data.ps1`
под 5.1 не запускался **вовсе**, а под PowerShell 7 работал, потому что 7 читает
UTF-8 по умолчанию. То есть дефект был виден только на одном из двух хостов и
потому не находился.

Правило записано в ``.editorconfig``, но правило без проверки — это пожелание:
достаточно один раз сохранить файл в редакторе, который BOM снимает.
"""

from __future__ import annotations

import pytest

from tests.conftest import REPO_ROOT

SCRIPTS_DIR = REPO_ROOT / "scripts"

#: UTF-8 BOM. По нему PowerShell 5.1 определяет кодировку файла.
UTF8_BOM = b"\xef\xbb\xbf"


def _scripts() -> list:
    return sorted(SCRIPTS_DIR.glob("*.ps1"))


def test_scripts_directory_has_files() -> None:
    """Страховка от теста, который проходит потому, что ничего не проверил."""
    assert _scripts(), f"в {SCRIPTS_DIR} не найдено ни одного .ps1 — проверка вырождена"


@pytest.mark.parametrize("script", _scripts(), ids=lambda path: path.name)
def test_script_starts_with_utf8_bom(script):  # noqa: ANN001, ANN201
    head = script.read_bytes()[:3]

    assert head == UTF8_BOM, (
        f"{script.name} записан без UTF-8 BOM. Windows PowerShell 5.1 прочитает его "
        "в системной кодировке, и скрипт упадёт на разборе: см. .editorconfig, "
        "раздел [*.ps1]."
    )
