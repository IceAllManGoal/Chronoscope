"""Тесты базовой страницы (§77, ADR-0012).

Страница — единственная часть продукта, у которой нет автоматических тестов
поведения: браузерной автоматизации в проекте нет. Поэтому проверяется всё, что
можно проверить без браузера, — и в первую очередь то, что ломается молча:
отдаётся ли статика, существуют ли файлы, на которые ссылается разметка, и
остаётся ли Core работоспособным API, если статики нет.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chronoscope.infrastructure.config.settings import CoreSettings
from chronoscope.main import WEB_DIRECTORY, WEB_MOUNT_PATH, create_app


@pytest.fixture
def client(migrated_database: CoreSettings) -> TestClient:  # noqa: ANN201
    with TestClient(create_app(migrated_database)) as test_client:
        yield test_client


class TestWebPageServed:
    def test_root_redirects_to_page(self, client: TestClient) -> None:
        """Иначе человек, открывший единственный известный адрес, получил бы 404."""
        response = client.get("/", follow_redirects=False)

        assert response.status_code == 307
        assert response.headers["location"] == f"{WEB_MOUNT_PATH}/"

    def test_page_is_html(self, client: TestClient) -> None:
        response = client.get(f"{WEB_MOUNT_PATH}/")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Chronoscope" in response.text

    def test_assets_are_served(self, client: TestClient) -> None:
        for name, content_type in (("app.js", "javascript"), ("style.css", "css")):
            response = client.get(f"{WEB_MOUNT_PATH}/{name}")

            assert response.status_code == 200, name
            assert content_type in response.headers["content-type"], name
            assert response.text.strip(), name

    def test_page_is_a_client_of_the_api(self, client: TestClient) -> None:
        """Инвариант 10 §81: UI не читает SQLite напрямую.

        Страница обязана обращаться к `/api/v1` по HTTP. Проверка ловит
        появление любого другого источника данных — например попытку открыть
        файл базы из браузера.
        """
        script = client.get(f"{WEB_MOUNT_PATH}/app.js").text

        assert "/api/v1" in script
        assert ".db" not in script
        assert "sqlite" not in script.lower()

    def test_api_still_works_alongside_page(self, client: TestClient) -> None:
        """Монтирование статики не должно задевать версионированный API."""
        assert client.get("/api/v1/health").status_code == 200
        assert client.get("/openapi.json").status_code == 200


class TestWebAssetsOnDisk:
    """Проверки по файлам, а не по HTTP: они ловят то, что сервер отдаст молча неверно."""

    def test_directory_exists(self) -> None:
        assert WEB_DIRECTORY.is_dir(), f"нет каталога статики: {WEB_DIRECTORY}"

    def test_every_referenced_asset_exists(self) -> None:
        """Разметка не должна ссылаться на файлы, которых нет.

        Переименование файла забывчивым коммитом дало бы страницу, которая
        открывается и не работает, — а это худший вид поломки интерфейса.
        """
        html = (WEB_DIRECTORY / "index.html").read_text(encoding="utf-8")
        references = re.findall(r'(?:src|href)="([^"]+)"', html)

        assert references, "в разметке нет ни одной ссылки на ресурс"

        for reference in references:
            if reference.startswith(("http://", "https://", "#")):
                continue
            assert (WEB_DIRECTORY / reference).is_file(), f"разметка ссылается на отсутствующий {reference}"

    def test_javascript_has_no_inner_html(self) -> None:
        """Данные из API вставляются только через textContent.

        Имена процессов и командные строки приходят из внешнего мира; собирать из
        них разметку значило бы позволить источнику событий выполнять код в
        интерфейсе наблюдателя.

        Проверяется обращение к свойству, а не само слово: оно встречается и в
        комментарии, где объяснено, почему так делать нельзя, поэтому проверка
        «слово отсутствует» падала бы на объяснении причины.
        """
        script = (WEB_DIRECTORY / "app.js").read_text(encoding="utf-8")

        for forbidden in (".innerHTML", ".outerHTML", "insertAdjacentHTML", "document.write"):
            assert forbidden not in script, f"страница собирает разметку через {forbidden}"


class TestProcessNavigation:
    """Переход «событие → экземпляр процесса» (§77.1).

    Поведение панели проверить нечем: браузерной автоматизации в проекте нет, и
    это ограничение записано в ADR-0012, а не замаскировано тестом, который её не
    заменяет. Здесь проверяется то, что ломается молча: есть ли фильтр по
    субъекту, обращается ли страница к эндпоинту процесса и не собирает ли она
    разметку из данных.
    """

    def test_subject_filter_exists(self, client: TestClient) -> None:
        """Без поля для `subject_id` ссылка «показать события процесса» некуда бы вела."""
        html = client.get(f"{WEB_MOUNT_PATH}/").text

        assert 'id="filter-subject"' in html

    def test_page_uses_process_endpoint(self, client: TestClient) -> None:
        script = client.get(f"{WEB_MOUNT_PATH}/app.js").text

        assert "/processes/" in script
        assert "subject_id" in script

    def test_process_panel_does_not_promise_state(self, client: TestClient) -> None:
        """Панель процесса — последнее место, где продукт мог бы выдумать состояние.

        Формулировка проверяется как факт содержимого, а не как красота текста:
        если она исчезнет, интерфейс снова начнёт выглядеть так, будто пустое
        завершение означает «процесс работает».
        """
        script = client.get(f"{WEB_MOUNT_PATH}/app.js").text

        assert "не значит, что процесс работает" in script

    def test_assets_are_served_with_the_page_that_uses_them(self, client: TestClient) -> None:
        """Стили перехода и заметок едут вместе со страницей, а не отдельным файлом."""
        styles = client.get(f"{WEB_MOUNT_PATH}/style.css").text

        assert "button.link" in styles
        assert ".note" in styles
        assert ".detail-actions" in styles


class TestMissingStaticDegradesGracefully:
    def test_core_stays_a_working_api_without_static(
        self, migrated_database: CoreSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Отсутствие статики не должно превращать Core в неработающий сервер.

        Это не гипотетический случай: каталог может не попасть в сборку, и тогда
        правильное поведение — остаться рабочим API, а не падать на импорте.
        """
        monkeypatch.setattr("chronoscope.main.WEB_DIRECTORY", Path("нет-такого-каталога"))
        app = create_app(migrated_database)

        with TestClient(app) as client:
            assert client.get("/api/v1/health").status_code == 200
            assert client.get(f"{WEB_MOUNT_PATH}/").status_code == 404
