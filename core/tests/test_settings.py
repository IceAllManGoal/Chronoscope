"""Тесты конфигурации (§33, §36, ADR-0007).

Главная проверка здесь — не «TOML читается», а то, что небезопасная
конфигурация **не может** возникнуть: bind на не-loopback адрес отвергается
до запуска сервера, а не после.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chronoscope.domain.errors import ConfigurationError
from chronoscope.infrastructure.config.settings import (
    DEFAULT_CONFIG_NAME,
    ENV_CONFIG_PATH,
    LOOPBACK_HOSTS,
    CoreSettings,
    load_settings,
)


def write_config(tmp_path: Path, contents: str, *, encoding: str = "utf-8") -> Path:
    path = tmp_path / DEFAULT_CONFIG_NAME
    path.write_text(contents, encoding=encoding)
    return path


class TestDefaults:
    def test_defaults_are_loopback(self) -> None:
        settings = CoreSettings()
        assert settings.host in LOOPBACK_HOSTS
        assert settings.port == 7342
        assert settings.database_path == Path("./data/chronoscope.db")

    def test_defaults_without_config_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv(ENV_CONFIG_PATH, raising=False)
        assert load_settings() == CoreSettings()


class TestLoopbackEnforcement:
    """ADR-0007: bind только на loopback, и это не пожелание, а запрет."""

    @pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "::", "example.com", ""])
    def test_non_loopback_host_is_rejected(self, host: str) -> None:
        with pytest.raises(ConfigurationError, match="loopback"):
            CoreSettings(host=host)

    @pytest.mark.parametrize("host", sorted(LOOPBACK_HOSTS))
    def test_loopback_hosts_are_accepted(self, host: str) -> None:
        assert CoreSettings(host=host).host == host

    def test_rejection_message_explains_why(self) -> None:
        """Сообщение должно называть причину, иначе пользователь обойдёт запрет наугад."""
        with pytest.raises(ConfigurationError) as excinfo:
            CoreSettings(host="0.0.0.0")
        assert "ADR-0007" in str(excinfo.value)

    def test_config_file_cannot_bypass_loopback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = write_config(tmp_path, '[core]\nhost = "0.0.0.0"\n')
        monkeypatch.setenv(ENV_CONFIG_PATH, str(path))

        with pytest.raises(ConfigurationError, match="loopback"):
            load_settings()


class TestConfigLoading:
    def test_values_are_applied(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = write_config(
            tmp_path,
            '[core]\nport = 8000\nlog_level = "DEBUG"\n\n[storage]\ndatabase_path = "./tmp/x.db"\n',
        )
        monkeypatch.setenv(ENV_CONFIG_PATH, str(path))

        settings = load_settings()
        assert settings.port == 8000
        assert settings.log_level == "DEBUG"
        assert settings.database_path == Path("./tmp/x.db")

    def test_database_path_is_a_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = write_config(tmp_path, '[storage]\ndatabase_path = "./a/b.db"\n')
        monkeypatch.setenv(ENV_CONFIG_PATH, str(path))
        assert isinstance(load_settings().database_path, Path)

    def test_missing_explicit_file_is_an_error(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError, match="не найден"):
            load_settings(tmp_path / "absent.toml")

    def test_unknown_key_in_own_section_is_rejected(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Опечатка в имени параметра не должна выглядеть как «настройка не применилась»."""
        path = write_config(tmp_path, "[core]\nprot = 8000\n")
        monkeypatch.setenv(ENV_CONFIG_PATH, str(path))

        with pytest.raises(ConfigurationError, match="prot"):
            load_settings()

    def test_agent_sections_are_ignored(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """§36 предполагает единый файл: секции Agent Core не касаются."""
        path = write_config(
            tmp_path,
            '[core]\nport = 7000\n\n[agent]\nbatch_size = 50\n\n'
            "[collectors.process]\nenabled = true\n\n[privacy]\nredact_command_line_patterns = []\n",
        )
        monkeypatch.setenv(ENV_CONFIG_PATH, str(path))

        assert load_settings().port == 7000

    def test_broken_toml_is_reported_as_configuration_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = write_config(tmp_path, "[core\nport = ")
        monkeypatch.setenv(ENV_CONFIG_PATH, str(path))

        with pytest.raises(ConfigurationError, match="разобрать"):
            load_settings()

    def test_utf8_bom_is_tolerated(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """PowerShell 5.1 и Блокнот пишут BOM; конфигурация всё равно должна читаться."""
        path = write_config(tmp_path, '[core]\nport = 7100\n', encoding="utf-8-sig")
        monkeypatch.setenv(ENV_CONFIG_PATH, str(path))

        assert load_settings().port == 7100

    def test_default_config_in_working_directory(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        write_config(tmp_path, "[core]\nport = 7200\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv(ENV_CONFIG_PATH, raising=False)

        assert load_settings().port == 7200

    def test_env_var_wins_over_default_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        write_config(tmp_path, "[core]\nport = 7200\n")
        explicit = tmp_path / "other.toml"
        explicit.write_text("[core]\nport = 7300\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv(ENV_CONFIG_PATH, str(explicit))

        assert load_settings().port == 7300


class TestValueValidation:
    @pytest.mark.parametrize("port", [0, -1, 65536, 100000])
    def test_port_range(self, port: int) -> None:
        with pytest.raises(ConfigurationError, match="port"):
            CoreSettings(port=port)

    def test_busy_timeout_must_be_positive(self) -> None:
        with pytest.raises(ConfigurationError, match="busy_timeout"):
            CoreSettings(busy_timeout_ms=0)

    def test_max_request_bytes_must_be_positive(self) -> None:
        with pytest.raises(ConfigurationError, match="max_request_bytes"):
            CoreSettings(max_request_bytes=0)
