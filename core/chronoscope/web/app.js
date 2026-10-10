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

  var detailHistory = [];
  var currentView = null;
  var detailRequest = 0;
  var listRequest = 0;

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
    var request = ++listRequest;
    clearMessage();
    el.next.disabled = true;
    el.prev.disabled = true;

    return api('/events', Object.assign(currentFilters(), { cursor: cursors[cursorIndex] }))
      .then(function (page) {
        if (request !== listRequest) { return; }
        nextCursor = page.next_cursor || null;
        renderEvents(page.events || []);
        renderPager(page);
      })
      .catch(function (error) {
        if (request !== listRequest) { return; }
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

      row.tabIndex = 0;
      row.setAttribute('aria-label', event.type + ' ' + event.id);
      row.addEventListener('click', function () { selectEvent(event.id); });
      row.addEventListener('keydown', function (key) {
        if (key.key === 'Enter' || key.key === ' ') { key.preventDefault(); selectEvent(event.id); }
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

  function navigate(view, restoring) {
    if (!restoring && currentView) {
      detailHistory.push(currentView);
    }
    currentView = view;
    var request = ++detailRequest;
    clearMessage();
    el.detail.textContent = 'Загрузка…';
    var path = '/processes/' + encodeURIComponent(view.id);
    var pending;
    if (view.kind === 'event') {
      pending = api('/events/' + encodeURIComponent(view.id));
    } else if (view.kind === 'process') {
      pending = Promise.all([api(path), api(path + '/relations')]);
    } else if (view.kind === 'tree') {
      pending = api(path + '/tree', { depth: view.depth, max_nodes: 200 });
    } else {
      pending = api('/events', { entity_id: view.id, limit: 25, cursor: view.cursor });
    }
    pending.then(function (data) {
      if (request !== detailRequest) { return; }
      if (view.kind === 'event') { renderDetail(data); }
      else if (view.kind === 'process') { renderProcessDetail(data[0], data[1]); }
      else if (view.kind === 'tree') { renderTree(data, view); }
      else { renderRelated(data, view); }
      renderBack();
      el.detail.focus({ preventScroll: true });
    }).catch(function (error) {
      if (request !== detailRequest) { return; }
      el.detail.textContent = 'Не удалось открыть данные.';
      showMessage(explain(error));
      renderBack();
    });
  }

  function renderBack() {
    if (detailHistory.length) {
      renderActions([{ label: '← Назад к предыдущему экрану', ghost: true, onClick: function () {
        navigate(detailHistory.pop(), true);
      } }]);
    }
  }

  function selectEvent(eventId) {
    if (!eventId) { return; }
    selectedId = eventId;
    navigate({ kind: 'event', id: eventId });
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
  function selectProcess(processInstanceId) {
    if (processInstanceId) { navigate({ kind: 'process', id: processInstanceId }); }
  }

  function parentValue(parent) {
    if (parent) { return processLink({ type: 'process', id: parent.id, name: parent.name }); }
    return 'Экземпляр не установлен';
  }

  function renderProcessDetail(detail, relations) {
    el.detail.replaceChildren();

    var heading = document.createElement('h2');
    heading.textContent = 'Экземпляр процесса';
    el.detail.appendChild(heading);

    var parent = relations.parent;

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
        ['Родитель', parentValue(relations.parent_process)],
        ['PID родителя', typeof parent.parent_pid === 'number' ? String(parent.parent_pid) : '—']
      ])
    );

    var notes = [];

    if (detail.observed_exit === false) {
      notes.push(
        'Завершение не наблюдалось. Это не значит, что процесс работает: ' +
          'Chronoscope не видел события выхода, а коллектор может его пропустить.'
      );
    }

    notes.push('Родитель: ' + resolutionLabel(parent) + '.');
    notes.push('История может быть неполной. Связи — вывод из доступных наблюдений.');
    if (relations.truncated) { notes.push('Выборка ограничена: ' + relations.reasons.join(', ')); }

    renderNotes(notes);

    renderEvidence(parent);
    renderConnections('Установленные дочерние связи', relations.children);
    renderConnections('Кандидаты: связь не установлена', relations.unresolved_children);
    renderActions([
      { label: 'Связанные события', onClick: function () { showProcessEvents(detail.id); } },
      { label: 'Открыть дерево', onClick: function () { navigate({ kind: 'tree', id: detail.id, depth: 3 }); } }
    ]);
  }

  var reasons = {
    unique_compatible_observation: 'единственный согласованный кандидат среди наблюдений',
    no_compatible_candidate: 'совместимый экземпляр родителя не наблюдался',
    missing_identity_or_parent_pid: 'недостаточно данных об идентичности или PID родителя',
    ambiguous_candidates: 'несколько допустимых экземпляров родителя',
    actor_not_supported: 'записанная ссылка actor не подтверждается наблюдениями',
    actor_candidate_mismatch: 'actor противоречит найденному экземпляру',
    conflicting_parent_pids: 'наблюдения называют разные PID родителя',
    conflicting_actor_references: 'наблюдения содержат разные actor',
    conflicting_timestamps: 'противоречащие времена',
    candidate_observations_conflict: 'свидетельства о кандидате противоречат друг другу',
    evidence_limit: 'достигнут предел чтения свидетельств',
    self_parent: 'процесс ссылается на себя как на родителя'
  };

  function resolutionLabel(relation) {
    var states = { resolved: 'Связь разрешена', unresolved: 'Неоднозначность',
      conflicting: 'Конфликт', insufficient_observation: 'Недостаточно наблюдений' };
    return states[relation.resolution] + ' — ' + (reasons[relation.reason] || relation.reason);
  }

  function heading(text) {
    var node = document.createElement('h3');
    node.textContent = text;
    el.detail.appendChild(node);
  }

  function renderEvidence(relation) {
    var ids = relation.evidence;
    var details = document.createElement('details');
    var summary = document.createElement('summary');
    summary.textContent = 'Свидетельства связи (' + relation.evidence_count + ')';
    details.appendChild(summary);
    ids.slice(0, 20).forEach(function (id) {
      var button = document.createElement('button');
      button.type = 'button'; button.className = 'link evidence'; button.textContent = id;
      button.addEventListener('click', function () { selectEvent(id); });
      details.appendChild(button);
    });
    if (relation.evidence_count > 20) {
      var note = document.createElement('p');
      note.textContent = 'Показаны первые 20 ссылок. Все доступные наблюдения учтены при разрешении; API relations отдаёт до 32 ссылок.';
      details.appendChild(note);
    }
    el.detail.appendChild(details);
  }

  function renderConnections(title, connections) {
    heading(title);
    if (!connections.length) { renderNotes(['В доступной истории не найдено.']); return; }
    var list = document.createElement('ul');
    list.className = 'connections';
    connections.forEach(function (item) {
      var row = document.createElement('li');
      row.appendChild(processLink({ type: 'process', id: item.process.id, name: item.process.name }));
      var status = document.createElement('p');
      status.className = 'hint'; status.textContent = resolutionLabel(item.relationship);
      row.appendChild(status); list.appendChild(row);
    });
    el.detail.appendChild(list);
  }

  function renderTree(tree, view) {
    el.detail.replaceChildren(); heading('Наблюдаемое дерево процессов');
    renderNotes(['История может быть неполной. В дерево входят только разрешённые связи.']);
    if (tree.truncated) { renderNotes(['Дерево ограничено: ' + tree.reasons.join(', ')]); }
    renderNotes(['Кандидатов с неустановленной связью: ' + tree.unresolved_children]);
    var depthLabel = document.createElement('label'); depthLabel.textContent = 'Глубина ';
    var depth = document.createElement('select'); depth.setAttribute('aria-label', 'Глубина дерева');
    for (var i = 0; i <= 5; i++) {
      var option = document.createElement('option'); option.value = String(i); option.textContent = String(i);
      option.selected = i === view.depth; depth.appendChild(option);
    }
    depth.addEventListener('change', function () { navigate({ kind: 'tree', id: view.id, depth: Number(depth.value) }); });
    depthLabel.appendChild(depth); el.detail.appendChild(depthLabel);
    var rootList = document.createElement('ul'); rootList.className = 'process-tree';
    var lists = {};
    tree.nodes.forEach(function (node) {
      var row = document.createElement('li');
      row.appendChild(processLink({ type: 'process', id: node.process.id, name: node.process.name }));
      var children = document.createElement('ul'); row.appendChild(children);
      lists[node.process.id] = children;
      (lists[node.parent_instance_id] || rootList).appendChild(row);
    });
    el.detail.appendChild(rootList);
    if (tree.nodes.length === 1) { renderNotes(['В выбранных пределах установленных потомков нет.']); }
    renderActions([{ label: 'Связанные события корня', onClick: function () { showProcessEvents(view.id); } }]);
  }

  function showProcessEvents(processInstanceId) {
    navigate({ kind: 'related', id: processInstanceId, cursor: null });
  }

  function renderRelated(page, view) {
    el.detail.replaceChildren(); heading('Связанные события');
    renderNotes(['Сохранённые ссылки actor или subject. Соседство по времени сюда не входит; исторический actor может быть оспорен новыми наблюдениями.']);
    if (!page.events.length) { renderNotes(['Связанных событий не найдено.']); }
    page.events.forEach(function (event) {
      var button = document.createElement('button');
      button.type = 'button'; button.className = 'link evidence';
      button.textContent = formatTime(event.timestamp) + ' · ' + event.type + ' · ' + event.id;
      button.addEventListener('click', function () { selectEvent(event.id); });
      el.detail.appendChild(button);
    });
    if (page.next_cursor) {
      renderActions([{ label: 'Следующие связанные события →', onClick: function () {
        navigate({ kind: 'related', id: view.id, cursor: page.next_cursor });
      } }]);
    }
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
