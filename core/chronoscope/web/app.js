/* Базовая страница Chronoscope (§77).
 *
 * Обычный скрипт без сборки и без модулей: страница отдаётся Core как статика
 * (ADR-0012), и всё, что ей нужно, — таблица, фильтры, страницы, панель
 * подробностей события и панель экземпляра процесса (§77.1).
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
    subject: document.getElementById('filter-subject'),
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

  /* Событие, из которого пришли в панель процесса: нужно, чтобы был путь назад.
     Одно значение, а не стек: переход «событие → процесс» — единственный, и
     притворяться, что их может быть много, значило бы писать навигацию, которой
     никто не пользуется. */
  var eventBehindProcess = null;

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

  /* Длительность жизни процесса. Core отдаёт её в миллисекундах, а null означает
     «известно только одно из времён» — и тогда показывается прочерк, а не ноль. */
  function formatDuration(value) {
    if (typeof value !== 'number' || value < 0) {
      return '—';
    }
    if (value < 1000) {
      return value + ' мс';
    }

    var seconds = value / 1000;
    if (seconds < 60) {
      return seconds.toFixed(1) + ' с';
    }

    var minutes = Math.floor(seconds / 60);
    if (minutes < 60) {
      return minutes + ' мин ' + Math.floor(seconds % 60) + ' с';
    }

    return Math.floor(minutes / 60) + ' ч ' + (minutes % 60) + ' мин';
  }

  /* «Время известно» и «событие наблюдалось» — разные вещи (§77.1). */
  function observedLabel(flag) {
    if (flag === true) {
      return 'да';
    }
    if (flag === false) {
      return 'нет';
    }
    return '—';
  }

  function processLink(entity, fromEventId) {
    if (!entity || entity.type !== 'process' || !entity.id) {
      return null;
    }

    var link = document.createElement('button');
    link.type = 'button';
    link.className = 'link';
    link.textContent = entityName(entity) + ' (' + entity.id + ')';
    link.addEventListener('click', function () {
      selectProcess(entity.id, fromEventId);
    });

    return link;
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

  /* Значение строки «ключ — значение»: текст или уже собранный узел.

     Переход к экземпляру процесса — это кнопка, а не разметка, собранная из
     данных: имя процесса приходит из внешнего мира, и вставлять его как HTML
     нельзя ни здесь, ни в любом другом месте страницы. */
  function cellValue(row, value, className) {
    var td = cell(row, '', className);

    if (value instanceof Node) {
      td.appendChild(value);
    } else {
      td.textContent = value;
    }

    return td;
  }

  function renderKv(pairs) {
    var table = document.createElement('table');
    table.className = 'kv';

    pairs.forEach(function (pair) {
      var row = document.createElement('tr');
      var th = document.createElement('th');
      th.textContent = pair[0];
      row.appendChild(th);
      cellValue(row, pair[1]);
      table.appendChild(row);
    });

    return table;
  }

  function renderNotes(notes) {
    notes.forEach(function (text) {
      var note = document.createElement('p');
      note.className = 'note';
      note.textContent = text;
      el.detail.appendChild(note);
    });
  }

  function renderActions(actions) {
    if (!actions.length) {
      return;
    }

    var row = document.createElement('div');
    row.className = 'detail-actions';

    actions.forEach(function (action) {
      var button = document.createElement('button');
      button.type = 'button';
      button.className = action.ghost ? 'ghost' : '';
      button.textContent = action.label;
      button.addEventListener('click', action.onClick);
      row.appendChild(button);
    });

    el.detail.appendChild(row);
  }

  function currentFilters() {
    return {
      type: el.type.value.trim(),
      source: el.source.value.trim(),
      subject_id: el.subject.value.trim(),
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

  /* Значение строки актора или субъекта: процесс становится переходом к его
     экземпляру (§77.1), всё остальное остаётся текстом. */
  function entityValue(entity, fromEventId) {
    if (!entity) {
      return '—';
    }

    var link = processLink(entity, fromEventId);
    if (link) {
      return link;
    }

    return entityName(entity) + (entity.id ? ' (' + entity.id + ')' : '');
  }

  function renderDetail(event) {
    el.detail.replaceChildren();

    var heading = document.createElement('h2');
    heading.textContent = event.type || 'Событие';
    el.detail.appendChild(heading);

    el.detail.appendChild(
      renderKv([
        ['Время', formatTime(event.timestamp)],
        ['Замечено', formatTime(event.observed_at)],
        ['Источник', event.source || '—'],
        ['Узел', event.host_id || '—'],
        ['Загрузка ОС', event.boot_id || '—'],
        ['Актор', entityValue(event.actor, event.id)],
        ['Субъект', entityValue(event.subject, event.id)],
        ['Сырое событие', event.raw_event_id || '—']
      ])
    );

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

  /* Переход «событие → экземпляр процесса» (§77.1).

     Панель процесса занимает место панели события: отдельного экрана для одного
     процесса не нужно, а список событий остаётся слева. */
  function selectProcess(processInstanceId, fromEventId) {
    if (!processInstanceId) {
      return;
    }

    eventBehindProcess = fromEventId || null;
    clearMessage();

    api('/processes/' + encodeURIComponent(processInstanceId))
      .then(renderProcessDetail)
      .catch(function (error) {
        showMessage(explain(error));
      });
  }

  function parentValue(parent) {
    if (!parent) {
      return '—';
    }

    if (parent.resolved && parent.id) {
      /* Путь назад к событию сохраняется и при переходе по цепочке: «событие →
         процесс → родитель» не должен оставлять пользователя без возврата. */
      return processLink({ type: 'process', id: parent.id, name: parent.name }, eventBehindProcess);
    }

    if (parent.name) {
      return parent.name;
    }

    return parent.id || '—';
  }

  function renderProcessDetail(detail) {
    el.detail.replaceChildren();

    var heading = document.createElement('h2');
    heading.textContent = 'Экземпляр процесса';
    el.detail.appendChild(heading);

    var parent = detail.parent || {};

    el.detail.appendChild(
      renderKv([
        ['Идентификатор', detail.id || '—'],
        ['Имя', detail.name || '—'],
        ['PID', typeof detail.pid === 'number' ? String(detail.pid) : '—'],
        ['Загрузка ОС', detail.boot_id || '—'],
        /* Время и признак наблюдения — разные строки: событие завершения несёт
           время старта, поэтому «когда» может быть известно без «наблюдался»
           (docs/EVENT_MODEL.md §2.4.1). */
        ['Запуск', formatTime(detail.started_at)],
        ['Наблюдался запуск', observedLabel(detail.observed_start)],
        ['Завершение', formatTime(detail.exited_at)],
        ['Наблюдался выход', observedLabel(detail.observed_exit)],
        ['Длительность', formatDuration(detail.duration_ms)],
        ['Родитель', parentValue(parent)],
        ['PID родителя', typeof parent.pid === 'number' ? String(parent.pid) : '—']
      ])
    );

    var notes = [];

    if (detail.observed_exit === false) {
      notes.push(
        'Завершение не наблюдалось. Это не значит, что процесс работает: ' +
          'Chronoscope не видел события выхода, а коллектор может его пропустить.'
      );
    }

    if (typeof parent.pid === 'number' && !parent.resolved) {
      notes.push('Связь с родителем не установлена: событие старта родительского процесса не наблюдалось.');
    }

    renderNotes(notes);

    var actions = [
      {
        label: 'Показать события процесса',
        onClick: function () {
          showProcessEvents(detail.id);
        }
      }
    ];

    if (eventBehindProcess) {
      actions.push({
        label: '← к событию ' + eventBehindProcess,
        ghost: true,
        onClick: function () {
          selectEvent(eventBehindProcess);
        }
      });
    }

    renderActions(actions);
  }

  /* События экземпляра — существующим фильтром `subject_id` (§31, §32):
     отдельного способа получить их у страницы нет и не должно быть. */
  function showProcessEvents(processInstanceId) {
    if (!processInstanceId) {
      return;
    }

    el.subject.value = processInstanceId;
    cursors = [null];
    cursorIndex = 0;
    selectedId = null;
    loadEvents();
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
    el.subject.value = '';
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
