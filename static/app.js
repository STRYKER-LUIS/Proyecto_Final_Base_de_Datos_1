/* ==========================================================================
   Mini IDE · Base de Datos 1 — lógica del frontend
   Organización:
     1. Plantillas y estado
     2. Utilidades (DOM, almacenamiento, formato)
     3. Consola de logs y toasts
     4. Cliente HTTP y estados de conexión
     5. Editor y pestañas
     6. Conexiones
     7. Ejecución y renderizado de resultados
     8. Eventos e inicialización
   ========================================================================== */

(() => {
  'use strict';

  // -----------------------------------------------------------------------
  // 1. Plantillas y estado
  // -----------------------------------------------------------------------
  const SAMPLES = {
    postgresql: {
      demo: `-- Mini IDE · PostgreSQL
-- Crea una tabla, inserta datos y muestra el resultado.
DROP TABLE IF EXISTS estudiantes;

CREATE TABLE estudiantes (
    id          SERIAL PRIMARY KEY,
    nombre      VARCHAR(100) NOT NULL,
    correo      VARCHAR(150) UNIQUE,
    nota        NUMERIC(4,2) DEFAULT 0,
    creado_en   TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO estudiantes (nombre, correo, nota) VALUES
    ('Ana Torres',  'ana.torres@universidad.edu',  18.50),
    ('Luis Pérez',  'luis.perez@universidad.edu',  15.75),
    ('María Gómez', 'maria.gomez@universidad.edu', 17.20);

SELECT id, nombre, correo, nota, creado_en
FROM estudiantes
ORDER BY nota DESC;`,
      select: `-- Consulta de solo lectura
SELECT id, nombre, correo, nota
FROM estudiantes
WHERE nota >= 16
ORDER BY nota DESC;`,
    },
    mongodb: {
      demo: `// Mini IDE · MongoDB
// Inserta documentos, actualiza uno y consulta el resultado.
db.estudiantes.insertMany([
  {
    nombre: "Ana Torres",
    correo: "ana.torres@universidad.edu",
    nota: 18.5,
    cursos: ["Base de Datos 1", "Programación II"],
    activo: true,
    creado: ISODate("2025-03-10T09:00:00Z")
  },
  {
    nombre: "Luis Pérez",
    correo: "luis.perez@universidad.edu",
    nota: 15.75,
    cursos: ["Base de Datos 1"],
    activo: true,
    creado: ISODate("2025-03-11T14:30:00Z")
  },
  {
    nombre: "María Gómez",
    correo: "maria.gomez@universidad.edu",
    nota: 17.2,
    cursos: ["Base de Datos 1", "Álgebra Lineal"],
    activo: true,
    creado: ISODate("2025-03-12T08:15:00Z")
  }
]);

db.estudiantes.updateOne(
  { correo: "luis.perez@universidad.edu" },
  { $set: { nota: 16.5, actualizado: ISODate("2025-04-01T10:00:00Z") } }
);

db.estudiantes.find({ activo: true })
  .sort({ nota: -1 })
  .limit(10);`,
      find: `// Consulta con filtro, proyección y orden
db.estudiantes.find(
  { nota: { $gte: 16 } },
  { nombre: 1, correo: 1, nota: 1, _id: 0 }
)
.sort({ nota: -1 })
.limit(20);`,
    },
  };

  const SNIPPETS = {
    'sql-demo': { engine: 'postgresql', name: 'Demo SQL', text: SAMPLES.postgresql.demo },
    'sql-select': { engine: 'postgresql', name: 'Consulta SQL', text: SAMPLES.postgresql.select },
    'mongo-demo': { engine: 'mongodb', name: 'Demo MongoDB', text: SAMPLES.mongodb.demo },
    'mongo-find': { engine: 'mongodb', name: 'Consulta MongoDB', text: SAMPLES.mongodb.find },
  };

  const ENGINE_META = {
    postgresql: {
      label: 'PostgreSQL',
      short: 'PG',
      mode: 'text/x-sql',
      endpoint: '/api/sql/execute',
      payloadKey: 'query',
      hint: 'Escribe una o varias sentencias SQL separadas por “;”. Ctrl/⌘ + Enter para ejecutar.',
    },
    mongodb: {
      label: 'MongoDB',
      short: 'Mongo',
      mode: 'javascript',
      endpoint: '/api/nosql/execute',
      payloadKey: 'command',
      hint: 'Usa el shell de Mongo (db.coleccion.find({...})) o un comando JSON. Ctrl/⌘ + Enter para ejecutar.',
    },
  };

  const STORAGE = {
    settings: 'miniide.settings.v1',
    queries: 'miniide.queries.v1',
    active: 'miniide.activeEngine.v1',
    resultsHeight: 'miniide.resultsHeight.v1',
  };

  const MAX_LOGS = 250;

  const state = {
    activeEngine: 'postgresql',
    connections: {
      postgresql: { connected: false, details: null },
      mongodb: { connected: false, details: null },
    },
    queries: { postgresql: '', mongodb: '' },
    logs: [],
    view: 'table',
    lastResponse: null,
    lastError: null,
    lastIsError: false,
    lastMeta: '',
    busy: false,
    limits: { sql: 1000, mongo: 100 },
    settings: {
      pg: { host: 'localhost', port: '5432', user: 'postgres', dbname: 'postgres' },
      mongo: { database: 'mini_ide_bd1' },
    },
  };

  // -----------------------------------------------------------------------
  // 2. Utilidades
  // -----------------------------------------------------------------------
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  const els = {
    sidebar: $('#sidebar'),
    sidebarOverlay: $('#sidebar-overlay'),
    btnSidebarToggle: $('#btn-sidebar-toggle'),
    btnSidebarClose: $('#btn-sidebar-close'),

    pgCard: $('#pg-card'),
    pgForm: $('#pg-form'),
    pgHost: $('#pg-host'),
    pgPort: $('#pg-port'),
    pgUser: $('#pg-user'),
    pgPassword: $('#pg-password'),
    pgDbname: $('#pg-dbname'),
    pgConnect: $('#pg-connect'),
    pgDisconnect: $('#pg-disconnect'),
    pgStatus: $('#pg-status'),
    pgDetail: $('#pg-detail'),
    pgError: $('#pg-error'),

    mongoCard: $('#mongo-card'),
    mongoForm: $('#mongo-form'),
    mongoUri: $('#mongo-uri'),
    mongoDb: $('#mongo-db'),
    mongoConnect: $('#mongo-connect'),
    mongoDisconnect: $('#mongo-disconnect'),
    mongoStatus: $('#mongo-status'),
    mongoDetail: $('#mongo-detail'),
    mongoError: $('#mongo-error'),

    pillPg: $('#pill-pg'),
    pillPgText: $('#pill-pg-text'),
    pillMongo: $('#pill-mongo'),
    pillMongoText: $('#pill-mongo-text'),

    tabs: $$('.engine-tab'),
    editorHint: $('#editor-hint'),
    limitNote: $('#limit-note'),
    currentDbBadge: $('#current-db-badge'),
    editorTextarea: $('#editor'),

    btnExecute: $('#btn-execute'),
    btnSample: $('#btn-sample'),
    btnClearEditor: $('#btn-clear-editor'),
    execStatus: $('#exec-status'),

    resultsResizer: $('#results-resizer'),
    resultsPanel: $('#results-panel'),
    resultsBody: $('#results-body'),
    resultsMeta: $('#results-meta'),
    viewTable: $('#view-table'),
    viewJson: $('#view-json'),
    btnCopyJson: $('#btn-copy-json'),
    btnDownloadJson: $('#btn-download-json'),
    btnClearResults: $('#btn-clear-results'),

    logsPanel: $('#logs-panel'),
    logs: $('#logs'),
    logsCount: $('#logs-count'),
    btnClearLogs: $('#btn-clear-logs'),
    btnToggleLogs: $('#btn-toggle-logs'),
    logsChevron: $('#logs-chevron'),

    toastContainer: $('#toast-container'),
  };

  function readJSON(key, fallback) {
    try {
      const raw = window.localStorage.getItem(key);
      return raw ? JSON.parse(raw) : fallback;
    } catch (_) {
      return fallback;
    }
  }

  function writeJSON(key, value) {
    try {
      window.localStorage.setItem(key, JSON.stringify(value));
    } catch (_) {
      /* almacenamiento no disponible */
    }
  }

  function formatTime(date) {
    return date.toLocaleTimeString('es-ES', { hour12: false });
  }

  function formatMs(value) {
    const number = Number(value || 0);
    if (number < 1) return '<1 ms';
    if (number < 1000) return `${Math.round(number)} ms`;
    return `${(number / 1000).toFixed(2)} s`;
  }

  function truncate(text, limit = 150) {
    const compact = String(text || '').replace(/\s+/g, ' ').trim();
    return compact.length > limit ? `${compact.slice(0, limit)}…` : compact;
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, (char) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[char]));
  }

  function getQuery() {
    return editor ? editor.getValue() : els.editorTextarea.value;
  }

  function setQuery(value) {
    if (editor) editor.setValue(value);
    else els.editorTextarea.value = value;
    state.queries[state.activeEngine] = value;
    scheduleSaveQueries();
  }

  let saveQueriesTimer = null;
  function scheduleSaveQueries() {
    window.clearTimeout(saveQueriesTimer);
    saveQueriesTimer = window.setTimeout(() => writeJSON(STORAGE.queries, state.queries), 350);
  }

  function saveSettings() {
    state.settings.pg = {
      host: els.pgHost.value.trim(),
      port: els.pgPort.value.trim(),
      user: els.pgUser.value.trim(),
      dbname: els.pgDbname.value.trim(),
    };
    state.settings.mongo = { database: els.mongoDb.value.trim() };
    writeJSON(STORAGE.settings, state.settings);
  }

  // -----------------------------------------------------------------------
  // 3. Consola de logs y toasts
  // -----------------------------------------------------------------------
  const LOG_LABELS = {
    info: 'INFO',
    success: 'OK',
    error: 'ERROR',
    warn: 'WARN',
    query: 'RUN',
  };

  function addLog(level, message, detail = '') {
    const entry = {
      level: LOG_LABELS[level] ? level : 'info',
      message: String(message || ''),
      detail: detail ? String(detail) : '',
      at: new Date(),
    };
    state.logs.push(entry);
    if (state.logs.length > MAX_LOGS) state.logs.shift();

    const row = el('div', `log-line log-${entry.level}`);
    row.append(
      el('span', 'log-time', formatTime(entry.at)),
      el('span', 'log-badge', LOG_LABELS[entry.level]),
    );

    const body = el('div', 'log-body');
    body.append(el('p', 'log-message', entry.message));
    if (entry.detail) body.append(el('pre', 'log-detail', entry.detail));
    row.append(body);

    els.logs.append(row);
    while (els.logs.children.length > MAX_LOGS) els.logs.firstElementChild.remove();
    els.logs.scrollTop = els.logs.scrollHeight;
    updateLogsCount();
  }

  function updateLogsCount() {
    const total = els.logs.children.length;
    els.logsCount.textContent = String(total);
  }

  function clearLogs() {
    state.logs = [];
    els.logs.replaceChildren();
    updateLogsCount();
  }

  function toast(type, message, timeout = 3600) {
    const node = el('div', `toast toast-${type}`, message);
    els.toastContainer.append(node);
    window.setTimeout(() => {
      node.classList.add('is-leaving');
      window.setTimeout(() => node.remove(), 220);
    }, timeout);
  }

  // -----------------------------------------------------------------------
  // 4. Cliente HTTP y estados de conexión
  // -----------------------------------------------------------------------
  async function api(path, { method = 'GET', body } = {}) {
    const options = { method, headers: {} };
    if (body !== undefined) {
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(body);
    }

    let response;
    try {
      response = await fetch(path, options);
    } catch (cause) {
      const error = new Error('No se pudo contactar con el servidor. ¿Está Uvicorn en ejecución?');
      error.info = {
        type: 'connection',
        message: error.message,
        detail: String(cause),
        hint: 'Arranca la aplicación con: uvicorn main:app --reload',
      };
      throw error;
    }

    const raw = await response.text();
    let payload = null;
    try {
      payload = raw ? JSON.parse(raw) : null;
    } catch (_) {
      payload = null;
    }

    if (!response.ok || (payload && payload.success === false)) {
      const info =
        (payload && payload.error) ||
        (payload && payload.detail && payload.detail.error) || {
          type: 'server',
          message:
            (payload && typeof payload.detail === 'string' && payload.detail) ||
            `El servidor respondió HTTP ${response.status}.`,
        };
      const error = new Error(info.message || `Error HTTP ${response.status}`);
      error.info = info;
      error.status = response.status;
      error.payload = payload;
      throw error;
    }
    return payload || {};
  }

  function setConnectionState(engine, connected, details = null) {
    state.connections[engine] = {
      connected: Boolean(connected),
      details: connected ? details || {} : null,
    };
  }

  function updateConnectionUI() {
    const configs = {
      postgresql: {
        card: els.pgCard,
        status: els.pgStatus,
        detail: els.pgDetail,
        pill: els.pillPg,
        pillText: els.pillPgText,
        connect: els.pgConnect,
        disconnect: els.pgDisconnect,
        fallbackDetail: () => `localhost:${els.pgPort.value || '5432'}`,
      },
      mongodb: {
        card: els.mongoCard,
        status: els.mongoStatus,
        detail: els.mongoDetail,
        pill: els.pillMongo,
        pillText: els.pillMongoText,
        connect: els.mongoConnect,
        disconnect: els.mongoDisconnect,
        fallbackDetail: () => els.mongoUri.value.trim() || 'mongodb://localhost:27017',
      },
    };

    Object.entries(configs).forEach(([engine, config]) => {
      const connection = state.connections[engine];
      const details = connection.details || {};
      const connected = connection.connected;

      $$(`[data-conn-dot="${engine}"]`).forEach((dot) => {
        dot.classList.toggle('is-on', connected);
        dot.classList.toggle('is-off', !connected);
      });

      config.card.classList.toggle('is-connected', connected);
      config.pill.classList.toggle('is-on', connected);
      config.pill.classList.toggle('is-off', !connected);
      config.pillText.textContent = `${ENGINE_META[engine].short} · ${connected ? 'Conectado' : 'Desconectado'}`;

      if (connected) {
        const database = details.database || '—';
        config.status.textContent = `Conectado · ${database}`;
        if (engine === 'postgresql') {
          config.detail.textContent = `${details.user || ''}@${details.host || ''}:${details.port || ''}${details.server_version ? ` · ${details.server_version}` : ''}`;
        } else {
          config.detail.textContent = `${details.hosts || 'localhost'}${details.server_version ? ` · ${details.server_version}` : ''}`;
        }
      } else {
        config.status.textContent = 'Sin conexión';
        config.detail.textContent = config.fallbackDetail();
      }

      config.connect.textContent = connected ? 'Reconectar' : 'Conectar';
      config.disconnect.classList.toggle('hidden', !connected);
    });

    const activeConnection = state.connections[state.activeEngine];
    const activeDetails = activeConnection.details || {};
    els.currentDbBadge.textContent = activeConnection.connected
      ? activeDetails.database || 'conectado'
      : 'Sin conexión';
    els.currentDbBadge.classList.toggle('is-on', activeConnection.connected);
    els.limitNote.textContent = state.activeEngine === 'postgresql'
      ? `Vista limitada a ${state.limits.sql} filas`
      : `Vista limitada a ${state.limits.mongo} documentos`;
  }

  function showConnectionError(engine, message) {
    const node = engine === 'postgresql' ? els.pgError : els.mongoError;
    if (!message) {
      node.textContent = '';
      node.classList.add('hidden');
      return;
    }
    node.textContent = message;
    node.classList.remove('hidden');
  }

  function clearConnectionErrors() {
    showConnectionError('postgresql', '');
    showConnectionError('mongodb', '');
  }

  function errorTitle(type) {
    const titles = {
      connection: 'Error de conexión',
      syntax: 'Error de sintaxis',
      schema: 'Error de esquema',
      conflict: 'Conflicto de integridad',
      permission: 'Permiso denegado',
      auth: 'Autenticación fallida',
      validation: 'Datos inválidos',
      timeout: 'Tiempo de espera agotado',
      query: 'Error en la consulta',
      config: 'Configuración inválida',
      database: 'Error de base de datos',
      server: 'Error del servidor',
      http: 'Error HTTP',
    };
    return titles[type] || 'Error';
  }

  function errorDetailText(info) {
    const chunks = [];
    if (info.detail) {
      if (Array.isArray(info.detail)) {
        info.detail.forEach((item) => {
          chunks.push(`${item.field ? `${item.field}: ` : ''}${item.message || JSON.stringify(item)}`);
        });
      } else {
        chunks.push(String(info.detail));
      }
    }
    if (info.code !== undefined && info.code !== null && info.code !== '') {
      chunks.push(`Código: ${info.code}${info.position ? ` · Posición: ${info.position}` : ''}`);
    } else if (info.position) {
      chunks.push(`Posición: ${info.position}`);
    }
    return chunks.join('\n');
  }

  function handleApiError(error, context) {
    const info = error.info || { type: 'server', message: error.message };
    const details = errorDetailText(info);
    const hint = info.hint ? `Sugerencia: ${info.hint}` : '';
    addLog('error', `${context}: ${info.message}`, [details, hint].filter(Boolean).join('\n'));
    return info;
  }

  function applyConfig(config) {
    const sqlDefaults = config.sql || {};
    const mongoDefaults = config.mongodb || {};
    if (config.limits) {
      state.limits.sql = Number(config.limits.sql) || state.limits.sql;
      state.limits.mongo = Number(config.limits.mongo) || state.limits.mongo;
    }
    const saved = state.settings || {};
    const pg = saved.pg || {};
    const mongo = saved.mongo || {};

    els.pgHost.value = pg.host || sqlDefaults.host || 'localhost';
    els.pgPort.value = pg.port || String(sqlDefaults.port || 5432);
    els.pgUser.value = pg.user || sqlDefaults.user || 'postgres';
    els.pgDbname.value = pg.dbname || sqlDefaults.dbname || 'postgres';
    els.mongoUri.value = mongoDefaults.uri || 'mongodb://localhost:27017';
    els.mongoDb.value = mongo.database || mongoDefaults.database || 'mini_ide_bd1';
    updateConnectionUI();
  }

  function applyServerStatus(payload) {
    ['postgresql', 'mongodb'].forEach((engine) => {
      const info = payload[engine] || {};
      if (info.connected) setConnectionState(engine, true, info);
      else setConnectionState(engine, false);
    });
    updateConnectionUI();
  }

  // -----------------------------------------------------------------------
  // 5. Editor y pestañas
  // -----------------------------------------------------------------------
  let editor = null;
  let loadingEditor = false;

  function initEditor() {
    const initialQuery = state.queries[state.activeEngine] || SAMPLES[state.activeEngine].demo;

    if (window.CodeMirror) {
      editor = window.CodeMirror.fromTextArea(els.editorTextarea, {
        mode: ENGINE_META[state.activeEngine].mode,
        theme: 'material-darker',
        lineNumbers: true,
        lineWrapping: true,
        autoCloseBrackets: true,
        matchBrackets: true,
        styleActiveLine: true,
        indentUnit: 2,
        tabSize: 2,
        extraKeys: {
          'Ctrl-Enter': () => executeQuery(),
          'Cmd-Enter': () => executeQuery(),
        },
      });
      loadingEditor = true;
      editor.setValue(initialQuery);
      loadingEditor = false;
      editor.on('change', () => {
        if (loadingEditor) return;
        state.queries[state.activeEngine] = editor.getValue();
        scheduleSaveQueries();
      });
    } else {
      els.editorTextarea.hidden = false;
      els.editorTextarea.classList.add('editor-fallback');
      els.editorTextarea.value = initialQuery;
      let changeHandler = null;
      editor = {
        getValue: () => els.editorTextarea.value,
        setValue: (value) => {
          els.editorTextarea.value = value;
          if (changeHandler) changeHandler();
        },
        setOption: () => {},
        focus: () => els.editorTextarea.focus(),
        refresh: () => {},
        on: (_event, handler) => {
          changeHandler = handler;
          els.editorTextarea.addEventListener('input', handler);
        },
      };
      addLog('warn', 'No se pudo cargar CodeMirror desde el CDN. Se usará el editor de texto básico.');
    }
    state.queries[state.activeEngine] = initialQuery;
  }

  function updateTabsUI() {
    els.tabs.forEach((tab) => {
      const active = tab.dataset.engine === state.activeEngine;
      tab.classList.toggle('is-active', active);
      tab.setAttribute('aria-selected', String(active));
    });
    els.editorHint.textContent = ENGINE_META[state.activeEngine].hint;
    updateConnectionUI();
  }

  function switchEngine(engine, { focus = true } = {}) {
    if (!ENGINE_META[engine]) return;
    if (engine === state.activeEngine) {
      if (focus && editor) editor.focus();
      return;
    }
    state.activeEngine = engine;
    try {
      window.localStorage.setItem(STORAGE.active, engine);
    } catch (_) { /* ignorar */ }

    if (editor) {
      loadingEditor = true;
      editor.setOption('mode', ENGINE_META[engine].mode);
      editor.setValue(state.queries[engine] || SAMPLES[engine].demo);
      loadingEditor = false;
      state.queries[engine] = state.queries[engine] || SAMPLES[engine].demo;
      editor.refresh();
      if (focus) editor.focus();
    }
    updateTabsUI();
  }

  function loadSnippet(text, engine) {
    if (engine !== state.activeEngine) switchEngine(engine, { focus: false });
    const current = getQuery().trim();
    if (current && current !== text.trim() && !window.confirm('Esto reemplazará el contenido actual del editor. ¿Continuar?')) {
      return;
    }
    setQuery(text);
    if (editor) {
      editor.refresh();
      editor.focus();
    }
    addLog('info', `Plantilla de ${ENGINE_META[engine].label} cargada.`);
  }

  // -----------------------------------------------------------------------
  // 6. Conexiones
  // -----------------------------------------------------------------------
  function setButtonLoading(button, loading, label) {
    if (loading) {
      button.dataset.originalHtml = button.innerHTML;
      button.disabled = true;
      button.innerHTML = `<span class="spinner"></span><span>${escapeHtml(label)}</span>`;
    } else {
      button.disabled = false;
      if (button.dataset.originalHtml) {
        button.innerHTML = button.dataset.originalHtml;
        delete button.dataset.originalHtml;
      }
    }
  }

  async function connectPostgres(event) {
    event.preventDefault();
    showConnectionError('postgresql', '');
    if (!els.pgForm.reportValidity()) return;

    const payload = {
      host: els.pgHost.value.trim(),
      port: Number(els.pgPort.value),
      user: els.pgUser.value.trim(),
      password: els.pgPassword.value,
      dbname: els.pgDbname.value.trim(),
    };
    setButtonLoading(els.pgConnect, true, 'Conectando…');
    addLog('info', `Conectando a PostgreSQL en ${payload.host}:${payload.port}/${payload.dbname}…`);

    try {
      const data = await api('/api/sql/connect', { method: 'POST', body: payload });
      setConnectionState('postgresql', true, data.connection || {});
      saveSettings();
      updateConnectionUI();
      addLog('success', 'Conexión establecida con PostgreSQL.', [
        `Base de datos: ${(data.connection || {}).database || payload.dbname}`,
        `Servidor: ${(data.connection || {}).server_version || 'PostgreSQL'}`,
        `Usuario: ${(data.connection || {}).user || payload.user}`,
      ].join('\n'));
      toast('success', 'PostgreSQL conectado.');
    } catch (error) {
      const info = handleApiError(error, 'PostgreSQL');
      showConnectionError('postgresql', info.message);
      toast('error', info.message);
    } finally {
      setButtonLoading(els.pgConnect, false);
      updateConnectionUI();
    }
  }

  async function disconnectPostgres() {
    setButtonLoading(els.pgDisconnect, true, 'Cerrando…');
    try {
      await api('/api/sql/disconnect', { method: 'POST' });
      setConnectionState('postgresql', false);
      updateConnectionUI();
      addLog('info', 'Conexión con PostgreSQL cerrada.');
      toast('info', 'PostgreSQL desconectado.');
    } catch (error) {
      const info = handleApiError(error, 'PostgreSQL');
      toast('error', info.message);
    } finally {
      setButtonLoading(els.pgDisconnect, false);
      updateConnectionUI();
    }
  }

  async function connectMongo(event) {
    event.preventDefault();
    showConnectionError('mongodb', '');
    if (!els.mongoForm.reportValidity()) return;

    const payload = {
      uri: els.mongoUri.value.trim(),
      database: els.mongoDb.value.trim() || null,
    };
    setButtonLoading(els.mongoConnect, true, 'Conectando…');
    addLog('info', `Conectando a MongoDB (${payload.uri})…`);

    try {
      const data = await api('/api/nosql/connect', { method: 'POST', body: payload });
      setConnectionState('mongodb', true, data.connection || {});
      saveSettings();
      updateConnectionUI();
      addLog('success', 'Conexión establecida con MongoDB.', [
        `Base de datos: ${(data.connection || {}).database || payload.database || '—'}`,
        `Servidor: ${(data.connection || {}).server_version || 'MongoDB'}`,
      ].join('\n'));
      toast('success', 'MongoDB conectado.');
    } catch (error) {
      const info = handleApiError(error, 'MongoDB');
      showConnectionError('mongodb', info.message);
      toast('error', info.message);
    } finally {
      setButtonLoading(els.mongoConnect, false);
      updateConnectionUI();
    }
  }

  async function disconnectMongo() {
    setButtonLoading(els.mongoDisconnect, true, 'Cerrando…');
    try {
      await api('/api/nosql/disconnect', { method: 'POST' });
      setConnectionState('mongodb', false);
      updateConnectionUI();
      addLog('info', 'Conexión con MongoDB cerrada.');
      toast('info', 'MongoDB desconectado.');
    } catch (error) {
      const info = handleApiError(error, 'MongoDB');
      toast('error', info.message);
    } finally {
      setButtonLoading(els.mongoDisconnect, false);
      updateConnectionUI();
    }
  }

  // -----------------------------------------------------------------------
  // 7. Ejecución (continúa más abajo)
  // -----------------------------------------------------------------------

  // -----------------------------------------------------------------------
  // 7. Ejecución y renderizado de resultados
  // -----------------------------------------------------------------------
  function setExecuting(value) {
    state.busy = value;
    if (value) {
      if (!els.btnExecute.dataset.originalHtml) {
        els.btnExecute.dataset.originalHtml = els.btnExecute.innerHTML;
      }
      els.btnExecute.disabled = true;
      els.btnExecute.innerHTML = '<span class="spinner"></span><span>Ejecutando…</span>';
      els.execStatus.textContent = 'Ejecutando…';
    } else {
      els.btnExecute.disabled = false;
      if (els.btnExecute.dataset.originalHtml) {
        els.btnExecute.innerHTML = els.btnExecute.dataset.originalHtml;
        delete els.btnExecute.dataset.originalHtml;
      }
    }
  }

  async function executeQuery() {
    if (state.busy) return;
    const engine = state.activeEngine;
    const meta = ENGINE_META[engine];
    const text = getQuery().trim();

    if (!text) {
      addLog('warn', 'La consola está vacía. Escribe una consulta antes de ejecutar.');
      toast('warn', 'No hay ninguna consulta para ejecutar.');
      if (editor) editor.focus();
      return;
    }

    if (!state.connections[engine].connected) {
      addLog('warn', `No hay conexión activa a ${meta.label}. Pulsa "Conectar" primero.`);
      toast('warn', `Conecta ${meta.label} antes de ejecutar.`);
      return;
    }

    setExecuting(true);
    addLog('query', `Ejecutando en ${meta.label}…`, truncate(text, 400));

    try {
      const payload = { [meta.payloadKey]: text };
      const data = await api(meta.endpoint, { method: 'POST', body: payload });
      const results = Array.isArray(data.results) ? data.results : [];
      renderResponse(data);

      addLog('success', `Consulta ejecutada en ${formatMs(data.duration_ms)} · ${results.length} resultado(s).`);
      results.forEach((result) => {
        if (result.type === 'query') {
          addLog('success', `${result.kind || 'SELECT'}: ${result.row_count} fila(s), ${(result.columns || []).length} columna(s).`);
        } else if (result.type === 'documents') {
          const total = result.total !== undefined ? ` de ${result.total}` : '';
          addLog('success', `${result.operation}: ${result.count} documento(s)${total}.`);
        } else if (result.type === 'write' || result.type === 'command' || result.type === 'value') {
          addLog('success', result.message || 'Operación completada.');
        }
      });
      els.execStatus.textContent = `Última ejecución: ${formatMs(data.duration_ms)}`;
    } catch (error) {
      const info = handleApiError(error, meta.label);
      renderError(error, info);
      els.execStatus.textContent = 'La última ejecución falló';
      toast('error', info.message);
    } finally {
      setExecuting(false);
    }
  }

  function updateResultsMeta() {
    els.resultsMeta.textContent = state.lastMeta || 'Sin ejecuciones';
    els.resultsMeta.title = state.lastMeta || '';
  }

  function setView(view) {
    state.view = view;
    els.viewTable.classList.toggle('is-active', view === 'table');
    els.viewJson.classList.toggle('is-active', view === 'json');

    if (state.lastIsError) {
      renderErrorPanel();
      return;
    }
    if (!state.lastResponse) {
      els.resultsBody.replaceChildren(createEmptyState());
      return;
    }
    if (view === 'json') renderJsonView();
    else renderTableResults(state.lastResponse.results || []);
  }

  function renderResponse(data) {
    state.lastResponse = data;
    state.lastError = null;
    state.lastIsError = false;
    state.lastMeta = `${(data.results || []).length} resultado(s) · ${formatMs(data.duration_ms)} · ${ENGINE_META[data.engine] ? ENGINE_META[data.engine].label : data.engine}`;
    els.btnCopyJson.disabled = false;
    els.btnDownloadJson.disabled = false;
    updateResultsMeta();
    setView('table');
  }

  function renderError(error, info) {
    state.lastError = { info, status: error.status || null };
    state.lastIsError = true;
    state.lastResponse = error.payload || {
      success: false,
      error: info,
    };
    state.lastMeta = `Error${error.status ? ` · HTTP ${error.status}` : ''}`;
    els.btnCopyJson.disabled = false;
    els.btnDownloadJson.disabled = false;
    updateResultsMeta();
    setView('table');
  }

  function createEmptyState(title = 'Aún no hay resultados', subtitle = 'Conecta una base de datos y ejecuta una consulta para ver la salida aquí.') {
    const wrapper = el('div', 'empty-state');
    wrapper.innerHTML = `
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" class="h-8 w-8">
        <rect x="3" y="4" width="18" height="16" rx="2"></rect>
        <path d="M3 9h18M9 9v11" stroke-linecap="round"></path>
      </svg>`;
    wrapper.append(el('p', '', title), el('p', 'sub', subtitle));
    return wrapper;
  }

  function normaliseColumns(columns) {
    return (columns || []).map((column) => {
      if (typeof column === 'string') return { name: column, type: null };
      return { name: column.name || 'columna', type: column.type || null };
    });
  }

  function cellInfo(value) {
    if (value === null || value === undefined) return { text: 'NULL', cls: 'cell-null' };
    if (typeof value === 'boolean') return { text: String(value), cls: 'cell-bool' };
    if (typeof value === 'number') return { text: String(value), cls: 'cell-number' };
    if (typeof value === 'object') {
      let text;
      try { text = JSON.stringify(value); } catch (_) { text = String(value); }
      return { text, cls: 'cell-object' };
    }
    const text = String(value);
    const cls = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(text) ? 'cell-date' : '';
    return { text, cls };
  }

  function createTable(columns, rows) {
    const wrap = el('div', 'table-wrap');
    const table = el('table', 'result-table');
    const thead = el('thead');
    const headRow = el('tr');
    headRow.append(el('th', 'row-index', '#'));
    columns.forEach((column) => {
      const th = el('th');
      th.append(document.createTextNode(column.name));
      if (column.type) th.append(el('span', 'col-type', column.type));
      headRow.append(th);
    });
    thead.append(headRow);

    const tbody = el('tbody');
    rows.forEach((row, rowIndex) => {
      const tr = el('tr');
      tr.append(el('td', 'row-index', String(rowIndex + 1)));
      columns.forEach((column, index) => {
        const value = Array.isArray(row)
          ? row[index]
          : Object.prototype.hasOwnProperty.call(row, column.name) ? row[column.name] : null;
        const info = cellInfo(value);
        const td = el('td', info.cls, info.text);
        td.title = info.text;
        tr.append(td);
      });
      tbody.append(tr);
    });

    table.append(thead, tbody);
    wrap.append(table);
    return wrap;
  }

  function createResultHead(operation, statement, meta, variant = '') {
    const head = el('div', 'result-card-head');
    head.append(el('span', `op-badge ${variant}`.trim(), operation));
    const preview = el('code', 'stmt-preview', truncate(statement || '', 170));
    preview.title = statement || '';
    head.append(preview);
    if (meta) head.append(el('span', 'result-meta', meta));
    return head;
  }

  function createResultCard() {
    return el('section', 'result-card');
  }

  function buildQueryBlock(result) {
    const card = createResultCard();
    const operation = (result.kind || 'SELECT').toUpperCase();
    const columns = normaliseColumns(result.columns);
    const rows = result.rows || [];
    card.append(createResultHead(operation, result.statement, `${result.row_count} fila(s)`));

    const body = el('div', 'result-card-body');
    if (rows.length === 0) {
      body.append(createEmptyState('La consulta no devolvió filas', 'Prueba con otros filtros o revisa los datos insertados.'));
    } else {
      body.append(createTable(columns, rows));
    }
    card.append(body);

    const footer = el('div', 'result-footer');
    footer.append(
      el('span', '', `${rows.length} fila(s) · ${columns.length} columna(s)`),
      el('span', '', '·'),
      el('span', '', 'SELECT'),
    );
    if (result.truncated) {
      footer.append(el('span', 'warning', `· Límite de ${result.max_rows} filas alcanzado; refina la consulta.`));
    }
    card.append(footer);
    return card;
  }

  const STAT_LABELS = {
    inserted_id: 'ID insertado',
    inserted_ids: 'IDs insertados',
    inserted_count: 'Insertados',
    matched_count: 'Coincidencias',
    modified_count: 'Modificados',
    deleted_count: 'Eliminados',
    upserted_id: 'ID upsert',
    acknowledged: 'Confirmado',
    index_name: 'Índice',
  };

  function formatStatValue(value) {
    if (value === null || value === undefined) return '—';
    if (typeof value === 'boolean') return value ? 'sí' : 'no';
    if (typeof value === 'object') {
      try { return JSON.stringify(value); } catch (_) { return String(value); }
    }
    return String(value);
  }

  function createStatsGrid(stats) {
    const grid = el('div', 'stats-grid');
    Object.entries(stats || {}).forEach(([key, value]) => {
      if (value === undefined) return;
      const item = el('div', 'stat-item');
      item.append(
        el('span', 'stat-label', STAT_LABELS[key] || key.replace(/_/g, ' ')),
        el('span', 'stat-value', formatStatValue(value)),
      );
      item.title = formatStatValue(value);
      grid.append(item);
    });
    return grid;
  }

  function buildCommandBlock(result) {
    const card = createResultCard();
    const operation = (result.kind || result.operation || 'DDL/DML').toString().toUpperCase();
    card.append(createResultHead(operation, result.statement, result.collection || '', 'is-write'));

    const body = el('div', 'result-card-body');
    const notice = el('div', 'command-notice');
    const icon = el('div', 'command-icon');
    icon.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" class="h-4 w-4"><path d="m5 12 4.5 4.5L19 7" stroke-linecap="round" stroke-linejoin="round"/></svg>';

    const content = el('div', 'min-w-0 flex-1');
    content.append(el('p', 'command-message', result.message || 'Comando ejecutado correctamente.'));

    const stats = {};
    if (result.affected_rows !== undefined) stats.affected_rows = result.affected_rows;
    if (result.command) stats.command = result.command;
    if (result.collection) stats.collection = result.collection;
    if (Object.keys(stats).length) content.append(createStatsGrid(stats));

    notice.append(icon, content);
    body.append(notice);
    card.append(body);
    return card;
  }

  function buildWriteBlock(result) {
    const card = createResultCard();
    const operation = result.operation || 'write';
    card.append(createResultHead(operation, result.statement, result.collection || '', 'is-write'));

    const body = el('div', 'result-card-body');
    const notice = el('div', 'command-notice');
    const icon = el('div', 'command-icon');
    icon.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" class="h-4 w-4"><path d="m5 12 4.5 4.5L19 7" stroke-linecap="round" stroke-linejoin="round"/></svg>';

    const content = el('div', 'min-w-0 flex-1');
    content.append(el('p', 'command-message', result.message || 'Operación completada.'));
    content.append(createStatsGrid(result.stats || {}));

    notice.append(icon, content);
    body.append(notice);
    card.append(body);
    return card;
  }

  function buildDocumentsBlock(result) {
    const card = createResultCard();
    const operation = result.operation || 'find';
    const documents = result.documents || [];
    const columns = normaliseColumns(result.columns);
    card.append(createResultHead(operation, result.statement, `${result.count} documento(s)`));

    const body = el('div', 'result-card-body');
    if (documents.length === 0) {
      body.append(createEmptyState('No se encontraron documentos', 'Prueba con otro filtro o inserta datos primero.'));
    } else {
      body.append(createTable(columns, documents));
    }
    card.append(body);

    const footer = el('div', 'result-footer');
    footer.append(el('span', '', `Colección: ${result.collection || '—'}`));
    if (result.total !== undefined) {
      footer.append(el('span', '', `· Mostrando ${documents.length} de ${result.total}`));
    }
    if (result.truncated) {
      footer.append(el('span', 'warning', `· Resultado truncado por el límite de ${result.limit}`));
    }
    card.append(footer);
    return card;
  }

  function buildValueBlock(result) {
    const card = createResultCard();
    const operation = result.operation || 'resultado';
    card.append(createResultHead(operation, result.statement, result.collection || ''));

    const body = el('div', 'result-card-body value-card');
    const value = result.value;
    if (typeof value === 'number') {
      body.append(el('p', 'value-big', String(value)));
    } else {
      body.append(el('pre', 'value-json', JSON.stringify(value, null, 2)));
    }
    body.append(el('p', 'mt-2 text-[11px] text-slate-400', result.message || ''));
    card.append(body);
    return card;
  }

  function renderTableResults(results) {
    if (!results.length) {
      els.resultsBody.replaceChildren(createEmptyState());
      return;
    }
    const fragment = document.createDocumentFragment();
    results.forEach((result) => {
      if (result.type === 'query') fragment.append(buildQueryBlock(result));
      else if (result.type === 'documents') fragment.append(buildDocumentsBlock(result));
      else if (result.type === 'write') fragment.append(buildWriteBlock(result));
      else if (result.type === 'value') fragment.append(buildValueBlock(result));
      else fragment.append(buildCommandBlock(result));
    });
    els.resultsBody.replaceChildren(fragment);
    els.resultsBody.scrollTop = 0;
  }

  function renderErrorPanel() {
    if (!state.lastError) {
      els.resultsBody.replaceChildren(createEmptyState());
      return;
    }
    const { info, status } = state.lastError;
    const card = el('div', 'error-card');

    const head = el('div', 'error-head');
    head.innerHTML = `
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-4 w-4 text-rose-400">
        <circle cx="12" cy="12" r="9"></circle>
        <path d="M12 8v5M12 16.5v.1" stroke-linecap="round"></path>
      </svg>`;
    head.append(el('span', 'error-title', errorTitle(info.type)));
    if (status) head.append(el('span', 'error-status', `HTTP ${status}`));
    card.append(head);

    const body = el('div', 'error-body');
    body.append(el('p', 'error-message', info.message || 'Ocurrió un error inesperado.'));

    const details = errorDetailText(info);
    if (details) {
      const block = el('div', 'error-block');
      block.append(el('span', 'error-label', 'Detalle del servidor'));
      block.append(document.createTextNode(details));
      body.append(block);
    }
    if (info.statement) {
      const statementBlock = el('div', 'error-block');
      statementBlock.append(el('span', 'error-label', 'Sentencia con error'));
      statementBlock.append(document.createTextNode(truncate(info.statement, 500)));
      body.append(statementBlock);
    }
    if (info.hint) body.append(el('p', 'error-hint', info.hint));

    if (info.type === 'connection') {
      body.append(el('p', 'mt-2 text-[10px] text-slate-500', 'Revisa las credenciales, la pestaña activa y que el servidor de base de datos esté iniciado.'));
    }

    card.append(body);
    els.resultsBody.replaceChildren(card);
  }

  function highlightJson(data) {
    let json;
    try {
      json = JSON.stringify(data, null, 2);
    } catch (_) {
      json = String(data);
    }
    const escaped = json
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
    return escaped.replace(
      /("(\\u[a-fA-F0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)/g,
      (match) => {
        let className = 'json-number';
        if (match.startsWith('"')) {
          className = match.trimEnd().endsWith(':') ? 'json-key' : 'json-string';
        } else if (match === 'true' || match === 'false') {
          className = 'json-boolean';
        } else if (match === 'null') {
          className = 'json-null';
        }
        return `<span class="${className}">${match}</span>`;
      },
    );
  }

  function renderJsonView() {
    const pre = el('pre', 'json-view');
    pre.innerHTML = highlightJson(state.lastResponse);
    els.resultsBody.replaceChildren(pre);
  }

  function clearResults() {
    state.lastResponse = null;
    state.lastError = null;
    state.lastIsError = false;
    state.lastMeta = '';
    state.view = 'table';
    els.btnCopyJson.disabled = true;
    els.btnDownloadJson.disabled = true;
    els.viewTable.classList.add('is-active');
    els.viewJson.classList.remove('is-active');
    els.resultsBody.replaceChildren(createEmptyState());
    updateResultsMeta();
    els.execStatus.textContent = '';
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (_) {
      try {
        const helper = document.createElement('textarea');
        helper.value = text;
        helper.setAttribute('readonly', '');
        helper.style.position = 'fixed';
        helper.style.opacity = '0';
        document.body.append(helper);
        helper.select();
        const ok = document.execCommand('copy');
        helper.remove();
        return ok;
      } catch (_) {
        return false;
      }
    }
  }

  async function copyJson() {
    if (!state.lastResponse) return;
    const ok = await copyText(JSON.stringify(state.lastResponse, null, 2));
    if (ok) {
      toast('success', 'Resultado copiado como JSON.');
      addLog('info', 'Resultado copiado al portapapeles.');
    } else {
      toast('error', 'No se pudo copiar al portapapeles.');
    }
  }

  function downloadJson() {
    if (!state.lastResponse) return;
    const blob = new Blob([JSON.stringify(state.lastResponse, null, 2)], { type: 'application/json;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
    link.href = url;
    link.download = `resultado-${state.activeEngine}-${stamp}.json`;
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    addLog('info', 'Resultado descargado como JSON.');
  }

  // -----------------------------------------------------------------------
  // 8. Interacciones auxiliares, eventos e inicialización
  // -----------------------------------------------------------------------
  function applyResultsHeight(value) {
    if (window.innerWidth < 768) {
      els.resultsPanel.style.removeProperty('height');
      return;
    }
    const max = Math.min(window.innerHeight * 0.68, 640);
    const height = Math.max(140, Math.min(Number(value) || 320, max));
    els.resultsPanel.style.height = `${Math.round(height)}px`;
  }

  let draggingResizer = false;

  function startResize(event) {
    if (window.innerWidth < 768) return;
    event.preventDefault();
    draggingResizer = true;
    els.resultsResizer.classList.add('is-dragging');
    document.body.classList.add('is-resizing');
    if (els.resultsResizer.setPointerCapture) {
      try { els.resultsResizer.setPointerCapture(event.pointerId); } catch (_) { /* ignorar */ }
    }
  }

  function moveResize(event) {
    if (!draggingResizer) return;
    const mainRect = els.resultsPanel.parentElement.getBoundingClientRect();
    const logsTop = els.logsPanel.getBoundingClientRect().top;
    const maximum = Math.max(140, logsTop - mainRect.top - 130);
    const height = Math.max(140, Math.min(logsTop - event.clientY, maximum));
    els.resultsPanel.style.height = `${Math.round(height)}px`;
  }

  function endResize() {
    if (!draggingResizer) return;
    draggingResizer = false;
    els.resultsResizer.classList.remove('is-dragging');
    document.body.classList.remove('is-resizing');
    const height = els.resultsPanel.getBoundingClientRect().height;
    writeJSON(STORAGE.resultsHeight, Math.round(height));
    if (editor) editor.refresh();
  }

  function toggleLogs(collapsed) {
    const shouldCollapse = collapsed === undefined
      ? !els.logsPanel.classList.contains('is-collapsed')
      : collapsed;
    els.logsPanel.classList.toggle('is-collapsed', shouldCollapse);
    els.btnToggleLogs.setAttribute('aria-expanded', String(!shouldCollapse));
    els.btnToggleLogs.title = shouldCollapse ? 'Expandir consola' : 'Contraer consola';
    els.btnToggleLogs.setAttribute('aria-label', els.btnToggleLogs.title);
  }

  function openSidebar() {
    els.sidebar.classList.add('is-open');
    els.sidebarOverlay.classList.remove('hidden');
  }

  function closeSidebar() {
    els.sidebar.classList.remove('is-open');
    els.sidebarOverlay.classList.add('hidden');
  }

  function bindEvents() {
    els.tabs.forEach((tab) => {
      tab.addEventListener('click', () => switchEngine(tab.dataset.engine));
    });

    els.pgForm.addEventListener('submit', connectPostgres);
    els.pgDisconnect.addEventListener('click', disconnectPostgres);
    els.mongoForm.addEventListener('submit', connectMongo);
    els.mongoDisconnect.addEventListener('click', disconnectMongo);

    [els.pgHost, els.pgPort, els.pgUser, els.pgDbname, els.mongoDb].forEach((input) => {
      input.addEventListener('change', saveSettings);
    });

    els.btnExecute.addEventListener('click', executeQuery);
    els.btnClearEditor.addEventListener('click', () => {
      setQuery('');
      if (editor) editor.focus();
      addLog('info', 'Editor limpiado.');
    });
    els.btnSample.addEventListener('click', () => {
      const sample = state.activeEngine === 'postgresql' ? SAMPLES.postgresql.demo : SAMPLES.mongodb.demo;
      loadSnippet(sample, state.activeEngine);
    });
    $$('.snippet-btn').forEach((button) => {
      button.addEventListener('click', () => {
        const snippet = SNIPPETS[button.dataset.snippet];
        if (!snippet) return;
        loadSnippet(snippet.text, snippet.engine);
        closeSidebar();
      });
    });

    els.viewTable.addEventListener('click', () => setView('table'));
    els.viewJson.addEventListener('click', () => setView('json'));
    els.btnCopyJson.addEventListener('click', copyJson);
    els.btnDownloadJson.addEventListener('click', downloadJson);
    els.btnClearResults.addEventListener('click', clearResults);

    els.btnClearLogs.addEventListener('click', clearLogs);
    els.btnToggleLogs.addEventListener('click', () => toggleLogs());

    els.btnSidebarToggle.addEventListener('click', openSidebar);
    els.btnSidebarClose.addEventListener('click', closeSidebar);
    els.sidebarOverlay.addEventListener('click', closeSidebar);

    els.resultsResizer.addEventListener('pointerdown', startResize);
    window.addEventListener('pointermove', moveResize);
    window.addEventListener('pointerup', endResize);
    window.addEventListener('pointercancel', endResize);
    els.resultsResizer.addEventListener('keydown', (event) => {
      if (!['ArrowUp', 'ArrowDown'].includes(event.key)) return;
      event.preventDefault();
      const current = els.resultsPanel.getBoundingClientRect().height;
      applyResultsHeight(current + (event.key === 'ArrowUp' ? 24 : -24));
      writeJSON(STORAGE.resultsHeight, Math.round(els.resultsPanel.getBoundingClientRect().height));
      if (editor) editor.refresh();
    });

    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') closeSidebar();
      if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
        if (event.target && event.target.closest && event.target.closest('.CodeMirror')) return;
        event.preventDefault();
        executeQuery();
      }
    });

    let resizeTimer = null;
    window.addEventListener('resize', () => {
      window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(() => {
        if (editor) editor.refresh();
        if (window.innerWidth >= 768) {
          const height = els.resultsPanel.getBoundingClientRect().height;
          applyResultsHeight(height);
        } else {
          els.resultsPanel.style.removeProperty('height');
        }
      }, 120);
    });
  }

  function restoreLocalState() {
    const savedQueries = readJSON(STORAGE.queries, null);
    if (savedQueries && typeof savedQueries === 'object') {
      if (typeof savedQueries.postgresql === 'string') state.queries.postgresql = savedQueries.postgresql;
      if (typeof savedQueries.mongodb === 'string') state.queries.mongodb = savedQueries.mongodb;
    }

    const savedSettings = readJSON(STORAGE.settings, null);
    if (savedSettings && typeof savedSettings === 'object') {
      state.settings.pg = { ...state.settings.pg, ...(savedSettings.pg || {}) };
      state.settings.mongo = { ...state.settings.mongo, ...(savedSettings.mongo || {}) };
    }

    let savedActive = null;
    try { savedActive = window.localStorage.getItem(STORAGE.active); } catch (_) { savedActive = null; }
    if (savedActive && ENGINE_META[savedActive]) state.activeEngine = savedActive;

    if (!state.queries[state.activeEngine]) {
      state.queries[state.activeEngine] = SAMPLES[state.activeEngine].demo;
    }
  }

  async function init() {
    restoreLocalState();
    initEditor();
    bindEvents();
    updateTabsUI();
    updateConnectionUI();
    applyResultsHeight(readJSON(STORAGE.resultsHeight, 320));
    updateResultsMeta();
    els.resultsBody.replaceChildren(createEmptyState());
    updateLogsCount();

    addLog('info', 'Mini IDE de Base de Datos 1 iniciado.');
    addLog('info', 'Configura tus credenciales y pulsa «Conectar» en cada motor que vayas a usar.');

    const [statusResult, configResult] = await Promise.allSettled([
      api('/api/status'),
      api('/api/config'),
    ]);

    if (configResult.status === 'fulfilled') {
      applyConfig(configResult.value);
    } else {
      handleApiError(configResult.reason, 'Configuración');
      addLog('warn', 'Se usarán valores por defecto en el formulario de conexión.');
    }

    if (statusResult.status === 'fulfilled') {
      applyServerStatus(statusResult.value);
      const pg = state.connections.postgresql.connected ? 'conectado' : 'desconectado';
      const mongo = state.connections.mongodb.connected ? 'conectado' : 'desconectado';
      addLog('info', `Estado del servidor: PostgreSQL ${pg} · MongoDB ${mongo}.`);
    } else {
      handleApiError(statusResult.reason, 'Estado del servidor');
    }

    updateConnectionUI();
    if (editor) editor.refresh();
  }

  init();
})();

