/* Базовая страница Chronoscope (§77).
 *
 * Обычный скрипт без сборки и без модулей: страница отдаётся Core как статика
 * (ADR-0012), и всё, что ей нужно, — таблица, фильтры, страницы и панель
 * подробностей.
 *
 * Данные из API вставляются только через textContent, никогда через innerHTML:
 * имена процессов и командные строки приходят из внешнего мира, и собирать из
 * них разметку значило бы позволить источнику событий выполнять код в интерфейсе
 * наблюдателя.
 */
(function () {
  'use strict';

  var API = '/api/v1';

  var el = {
    coreState: document.getElementById('core-state'),
    filters: document.getElementById('filters'),
    type: document.getElementById('filter-type'),
    source: document.getElementById('filter-source'),
    from: document.getElementById('filter-from'),
    to: document.getElementById('filter-to'),
    limit: document.getElementById('filter-limit'),
    reset: document.getElementById('reset'),
    message: document.getElementById('message'),
    body: document.getElementById('events-body'),
    empty: document.getElementById('events-empty'),
    prev: document.getElementById('prev'),
    next: document.getElementById('next'),
    pageInfo: document.getElementById('page-info'),
    detail: document.getElementById('detail')
  };

  /* Стек курсоров, а не одно значение: API отдаёт курсор только вперёд (§32),
     поэтому «Назад» без запоминания пройденных позиций невозможно. */
  var cursors = [null];
  var cursorIndex = 0;
  var nextCursor = null;
  var selectedId = null;

  function showMessage(text) {
    el.message.textContent = text;
    el.message.hidden = false;
  }

  function clearMessage() {
    el.message.hidden = true;
    el.message.textContent = '';
  }

  function api(path, params) {
    var url = API + path;

    if (params) {
      var search = new URLSearchParams();
      Object.keys(params).forEach(function (key) {
        var value = params[key];
        if (value !== null && value !== undefined && value !== '') {
          search.set(key, value);
        }
      });
      if (search.toString()) {
        url += '?' + search.toString();
      }
    }

    return fetch(url, { headers: { Accept: 'application/json' } }).then(function (response) {
      return response
        .json()
        .catch(function () {
          return null;
        })
        .then(function (body) {
          if (!response.ok) {
            var detail = body && body.error ? body.error : null;
            var error = new Error(detail ? '[' + detail.code + '] ' + detail.message : 'HTTP ' + response.status);
            error.fromCore = Boolean(detail);
            throw error;
          }
          return body;
        });
    });
  }

  function explain(error) {
    if (error.fromCore) {
      return error.message;
    }

    return (
      'Core не отвечает: ' +
      error.message +
      '\nЗапусти его: cd core && uv run python -m chronoscope'
    );
  }

  function formatTime(value) {
    if (!value) {
      return '—';
    }
    var moment = new Date(value);
    if (isNaN(moment.getTime())) {
      return value;
    }
    /* Хранилище отдаёт UTC (§24); местное время — забота интерфейса. */
    return moment.toLocaleString('ru-RU', { hour12: false });
  }

  function entityName(entity) {
    if (!entity) {
      return '—';
    }
    return entity.name || entity.id || '—';
  }

  function cell(row, text, className) {
    var td = document.createElement('td');
    td.textContent = text;
    if (className) {
      td.className = className;
    }
    row.appendChild(td);
    return td;
  }

  function currentFilters() {
    return {
      type: el.type.value.trim(),
      source: el.source.value.trim(),
      from: el.from.value ? new Date(el.from.value).toISOString() : '',
      to: el.to.value ? new Date(el.to.value).toISOString() : '',
      limit: el.limit.value || 25
    };
  }

  function loadEvents() {
    clearMessage();
    el.next.disabled = true;
    el.prev.disabled = true;

    return api('/events', Object.assign(currentFilters(), { cursor: cursors[cursorIndex] }))
      .then(function (page) {
        nextCursor = page.next_cursor || null;
        renderEvents(page.events || []);
        renderPager(page);
      })
      .catch(function (error) {
        showMessage(explain(error));
        el.body.replaceChildren();
        el.empty.hidden = true;
        el.pageInfo.textContent = '';
      });
  }

  function renderEvents(events) {
    el.body.replaceChildren();
    el.empty.hidden = events.length > 0;

    events.forEach(function (event) {
      var row = document.createElement('tr');
      if (event.id === selectedId) {
        row.className = 'selected';
      }

      cell(row, formatTime(event.timestamp), 'nowrap mono');
      cell(row, event.type || '—', 'mono');
      cell(row, entityName(event.subject));
      cell(row, event.id || '—', 'id');

      row.addEventListener('click', function () {
        selectEvent(event.id);
      });

      el.body.appendChild(row);
    });
  }

  function renderPager(page) {
    var shown = page.count || 0;
    var pageNumber = cursorIndex + 1;

    el.pageInfo.textContent = 'Страница ' + pageNumber + ' · событий на странице: ' + shown;
    el.prev.disabled = cursorIndex === 0;
    el.next.disabled = !nextCursor;
  }

  function selectEvent(eventId) {
    if (!eventId) {
      return;
    }

    selectedId = eventId;
    Array.prototype.forEach.call(el.body.children, function (row) {
      var id = row.lastChild ? row.lastChild.textContent : '';
      row.className = id === eventId ? 'selected' : '';
    });

    clearMessage();

    api('/events/' + encodeURIComponent(eventId))
      .then(renderDetail)
      .catch(function (error) {
        showMessage(explain(error));
      });
  }

  function renderDetail(event) {
    el.detail.replaceChildren();

    var heading = document.createElement('h2');
    heading.textContent = event.type || 'Событие';
    el.detail.appendChild(heading);

    var pairs = [
      ['Время', formatTime(event.timestamp)],
      ['Замечено', formatTime(event.observed_at)],
      ['Источник', event.source || '—'],
      ['Узел', event.host_id || '—'],
      ['Загрузка ОС', event.boot_id || '—'],
      ['Актор', event.actor ? entityName(event.actor) + (event.actor.id ? ' (' + event.actor.id + ')' : '') : '—'],
      ['Субъект', event.subject ? entityName(event.subject) + (event.subject.id ? ' (' + event.subject.id + ')' : '') : '—'],
      ['Сырое событие', event.raw_event_id || '—']
    ];

    var table = document.createElement('table');
    table.className = 'kv';
    pairs.forEach(function (pair) {
      var row = document.createElement('tr');
      var th = document.createElement('th');
      th.textContent = pair[0];
      row.appendChild(th);
      var td = document.createElement('td');
      td.textContent = pair[1];
      row.appendChild(td);
      table.appendChild(row);
    });
    el.detail.appendChild(table);

    var section = document.createElement('section');
    var subheading = document.createElement('h3');
    subheading.textContent = 'Атрибуты';
    section.appendChild(subheading);

    var attributes = event.attributes || {};
    var names = Object.keys(attributes).sort();

    if (!names.length) {
      var none = document.createElement('p');
      none.className = 'hint';
      none.textContent = '—';
      section.appendChild(none);
    } else {
      var attributeTable = document.createElement('table');
      attributeTable.className = 'kv';
      names.forEach(function (name) {
        var row = document.createElement('tr');
        var th = document.createElement('th');
        th.textContent = name;
        row.appendChild(th);
        var td = document.createElement('td');
        td.textContent = String(attributes[name]);
        row.appendChild(td);
        attributeTable.appendChild(row);
      });
      section.appendChild(attributeTable);
    }

    el.detail.appendChild(section);
  }

  function loadCoreState() {
    return api('/health')
      .then(function (health) {
        var ok = health.status === 'ok' && health.database === 'ok';
        el.coreState.className = 'badge ' + (ok ? 'badge-ok' : 'badge-warn');
        el.coreState.textContent = 'Core ' + (health.version || '?') + ' · база ' + (health.database || '?');
      })
      .catch(function () {
        el.coreState.className = 'badge badge-bad';
        el.coreState.textContent = 'Core недоступен';
      });
  }

  el.filters.addEventListener('submit', function (event) {
    event.preventDefault();
    cursors = [null];
    cursorIndex = 0;
    selectedId = null;
    loadEvents();
  });

  el.reset.addEventListener('click', function () {
    el.type.value = '';
    el.source.value = '';
    el.from.value = '';
    el.to.value = '';
    el.limit.value = '25';
    cursors = [null];
    cursorIndex = 0;
    selectedId = null;
    loadEvents();
  });

  el.next.addEventListener('click', function () {
    if (!nextCursor) {
      return;
    }
    cursors = cursors.slice(0, cursorIndex + 1);
    cursors.push(nextCursor);
    cursorIndex += 1;
    loadEvents();
  });

  el.prev.addEventListener('click', function () {
    if (cursorIndex === 0) {
      return;
    }
    cursorIndex -= 1;
    loadEvents();
  });

  loadCoreState();
  loadEvents();
})();
