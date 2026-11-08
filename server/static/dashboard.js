/* ============================================================
   CTBU 乐跑 · 主页管理系统 — 前端逻辑（无框架 / 无外部依赖）
   单一事实源：GET /api/dashboard；所有统计、账号、时间均来自接口。
   禁止：整页刷新、外部资源、绝对地址、写死业务数据、完整 token 回显。
   ============================================================ */
(function () {
  'use strict';

  /* ----------------------------------------------------------
     0. 常量与工具
     ---------------------------------------------------------- */
  var API = {
    dashboard: '/api/dashboard',
    token: '/api/token',
    submit: function (studentNum) {
      return '/api/submit/' + encodeURIComponent(String(studentNum));
    }
  };

  var VIEWS = {
    overview: '概览工作台',
    accounts: '账号与凭证',
    records: '记录与统计',
    schedule: '运行与调度',
    logs: '运行日志',
    settings: '系统设置'
  };
  var LEVEL_KEYS = ['all', 'error', 'attention', 'ok', 'running', 'pending', 'disabled'];
  var SORT_KEYS = ['name', 'credential', 'today', 'last', 'enabled'];
  var DEFAULT_SORT = { key: 'name', dir: 'asc' };
  var REQUEST_TIMEOUT_MS = 12000;
  var DEBOUNCE_MS = 300;

  // 六类状态徽章：色点（.badge::before）+ 文字双重编码，名称与颜色全站一致
  var STATUS = {
    ok:        { label: '正常',   cls: 'badge--ok' },
    pending:   { label: '待执行', cls: 'badge--pending' },
    running:   { label: '运行中', cls: 'badge--running' },
    attention: { label: '需关注', cls: 'badge--attention' },
    error:     { label: '失败',   cls: 'badge--error' },
    disabled:  { label: '已停用', cls: 'badge--disabled' }
  };
  var STATUS_ORDER = ['error', 'attention', 'running', 'pending', 'ok', 'disabled'];

  var WINDOW_LABELS = { noon: '午间窗口', evening: '晚间窗口' };

  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  var arr = function (v) { return Array.isArray(v) ? v : []; };
  var obj = function (v) { return (v && typeof v === 'object' && !Array.isArray(v)) ? v : {}; };
  var str = function (v) { return (v === null || v === undefined) ? '' : String(v); };
  var disp = function (v, fb) { var s = str(v).trim(); return s === '' ? (fb === undefined ? '—' : fb) : s; };
  var esc = function (v) {
    return str(v).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  };
  var E = function (v, fb) { return esc(disp(v, fb)); };
  var asNum = function (v) {
    if (v === null || v === undefined || v === '' || typeof v === 'boolean') return null;
    var n = Number(v);
    return Number.isFinite(n) ? n : null;
  };
  var fmtNum = function (v) {
    var n = asNum(v);
    return n === null ? '—' : String(n);
  };
  var ratio = function (a, b) {
    var x = asNum(a), y = asNum(b);
    if (x === null || y === null || y <= 0) return null;
    return Math.max(0, Math.min(100, Math.round((x / y) * 100)));
  };
  var pad2 = function (n) { return String(n).padStart(2, '0'); };

  function cssDuration(varName, fallback) {
    var raw = '';
    try { raw = getComputedStyle(document.documentElement).getPropertyValue(varName).trim(); } catch (e) { raw = ''; }
    var m = /^([\d.]+)\s*(ms|s)$/.exec(raw);
    if (!m) return fallback;
    var n = Number(m[1]);
    return Number.isFinite(n) ? (m[2] === 'ms' ? n : n * 1000) : fallback;
  }

  function parseHHMM(v) {
    var m = /(\d{1,2}):(\d{2})(?::(\d{2}))?/.exec(str(v));
    if (!m) return null;
    var h = Number(m[1]), mi = Number(m[2]);
    if (h > 23 || mi > 59) return null;
    return h * 60 + mi;
  }
  function formatMin(m) { return pad2(Math.floor(m / 60)) + ':' + pad2(m % 60); }
  function parseNaiveTime(v) {
    var m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})/.exec(str(v));
    if (!m) return null;
    return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]).getTime();
  }
  function formatClock(ms) {
    var d = new Date(ms);
    return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()) + ' ' +
      pad2(d.getHours()) + ':' + pad2(d.getMinutes()) + ':' + pad2(d.getSeconds());
  }

  function badge(key) {
    var s = STATUS[key] || STATUS.disabled;
    return '<span class="badge ' + s.cls + '" title="' + esc(s.label) + '">' + esc(s.label) + '</span>';
  }
  function levelToStatus(level) {
    var s = str(level).toLowerCase();
    if (/error|fatal|critical|fail|dead|异常|失败/.test(s)) return 'error';
    if (/warn|attention|alert|degraded|警告|关注/.test(s)) return 'attention';
    if (/running|run|progress|start|进行中/.test(s)) return 'running';
    if (/pending|queue|wait|待/.test(s)) return 'pending';
    if (/disable|stop|pause|停用|暂停/.test(s)) return 'disabled';
    return 'ok';
  }
  function inferLevel(line) {
    if (/error|fail|dead|异常|失败|TOKEN_DEAD/i.test(line)) return 'error';
    if (/warn|警告|关注|额度已满/i.test(line)) return 'warn';
    return 'info';
  }

  // 退出码 → 业务语言（规格 §5）；不暴露原始异常栈
  function exitInfo(code) {
    var n = asNum(code);
    if (n === null) {
      return { key: 'disabled', title: '暂无执行结果', hint: '尚未产生执行记录。', retry: false, submit: true, reason: '' };
    }
    switch (n) {
      case 0:
        return { key: 'ok', title: '审查有效', hint: '已计入成绩；距离·用时·配速·记录号见执行记录。', retry: false, submit: false, reason: '今日已完成，无需重复提交' };
      case 2:
        return { key: 'error', title: '凭证失效', hint: '重新抓号后可恢复 → 请更新凭证。', retry: false, submit: false, reason: '凭证失效，请先更新凭证', action: 'cred' };
      case 3:
        return { key: 'attention', title: '当日额度已满', hint: '无需重试，次日自动恢复。', retry: false, submit: false, reason: '当日额度已满，次日自动恢复' };
      case 4:
        return { key: 'error', title: '接口异常', hint: '已生成告警 → 查看日志 / 重试。', retry: true, submit: true, reason: '' };
      default:
        return { key: 'error', title: '执行异常', hint: '退出码 ' + n + '：请查看日志或重试（不展示原始异常栈）。', retry: true, submit: true, reason: '' };
    }
  }
  var EXIT_LEGEND = [0, 2, 3, 4];

  /* ----------------------------------------------------------
     1. 应用状态（服务端数据 / 客户端 UI 状态分开）
     ---------------------------------------------------------- */
  var state = {
    data: null,          // 最近一次「全量」刷新载荷，供所有数据区使用
    live: null,          // 最近一次 30s 局部刷新载荷，仅 M11 使用
    error: null,
    backendFailure: null,
    readOnly: false,
    view: 'overview',
    filter: '',
    logLevel: 'all',
    autoScroll: true,
    sort: { key: DEFAULT_SORT.key, dir: DEFAULT_SORT.dir },
    selected: new Set(),
    busy: new Set(),
    clock: null,
    drawer: null,
    drawerTrigger: null,
    confirm: null
  };

  var REFRESH_MS = 30000;
  var TOAST_MS = 4000;
  var nextRefreshAt = 0;
  var timers = { clock: null, debounce: null };
  var toastTimers = new Map();
  var toastActions = new Map();
  var toastSeq = 0;
  var activeController = null;
  var requestSeq = 0;
  var listeners = [];
  var destroyed = false;

  function addListener(target, type, fn, opts) {
    target.addEventListener(type, fn, opts);
    listeners.push({ target: target, type: type, fn: fn, opts: opts });
  }

  /* ----------------------------------------------------------
     2. API 层：唯一发请求的地方（超时 / 取消 / 错误规范化）
     ---------------------------------------------------------- */
  function ApiError(status, message, payload) {
    this.name = 'ApiError';
    this.status = status;
    this.message = message;
    this.payload = payload || null;
  }
  ApiError.prototype = Object.create(Error.prototype);
  ApiError.prototype.constructor = ApiError;

  function requestJSON(url, opts) {
    var o = opts || {};
    var method = o.method || 'GET';
    var timeout = o.timeout || REQUEST_TIMEOUT_MS;
    var ctrl = new AbortController();
    var timedOut = false;
    var timer = setTimeout(function () { timedOut = true; ctrl.abort(); }, timeout);
    var onAbort = function () { ctrl.abort(); };
    var external = o.signal;
    if (external) {
      if (external.aborted) ctrl.abort();
      else external.addEventListener('abort', onAbort, { once: true });
    }
    var init = {
      method: method,
      headers: {},
      credentials: 'same-origin',
      cache: 'no-store',
      signal: ctrl.signal
    };
    if (o.body !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(o.body);
    }
    return fetch(url, init).then(function (res) {
      return res.text().then(function (raw) {
        var payload = null;
        if (raw) { try { payload = JSON.parse(raw); } catch (e) { payload = null; } }
        if (!res.ok) {
          var msg = (payload && (payload.error || payload.message)) || ('HTTP ' + res.status);
          throw new ApiError(res.status, String(msg), payload);
        }
        if (payload === null) throw new ApiError(res.status, '服务端返回的不是有效 JSON', null);
        return payload;
      });
    }).catch(function (err) {
      // 超时必须与「新请求取消旧请求」区分：超时属于失败，不能静默忽略
      if (timedOut && err && err.name === 'AbortError') {
        throw new ApiError(0, '请求超时（' + Math.round(timeout / 1000) + 's）', null);
      }
      throw err;
    }).finally(function () {
      clearTimeout(timer);
      if (external) external.removeEventListener('abort', onAbort);
    });
  }

  function fetchDashboard(scope) {
    var isLive = scope === 'events';
    var mySeq = ++requestSeq;
    if (activeController) activeController.abort();
    activeController = new AbortController();
    setRefreshBusy(true);
    return requestJSON(API.dashboard, { signal: activeController.signal }).then(function (d) {
      if (destroyed || mySeq !== requestSeq) return;
      if (!d || typeof d !== 'object' || Array.isArray(d)) {
        throw new ApiError(200, '数据结构异常：期望 JSON 对象', null);
      }
      var recovered = !state.error;
      applyServerClock(d.server_time);
      evaluateReadOnly(d);

      // 后端聚合异常时返回的是 HTTP 200 + health=error 的固定键载荷。
      // 若不显式识别，会被当成「成功但没数据」渲染成「暂无账号 + 新增账号」，属于误导性失败。
      var backendFailure = str(d.health).toLowerCase() === 'error' || !!d.error;
      if (backendFailure) {
        var firstAlert = obj(arr(d.alerts)[0]);
        var reason = str(d.error) || str(firstAlert.message) || '服务端看板聚合失败（health=error）';
        state.data = d;
        state.live = d;
        state.backendFailure = reason;
        state.error = new ApiError(200, reason, d);
        updateEnvHealth(d);
        updateTopbarMeta(d);
        updateConnectionBanner('服务端数据聚合失败：' + reason + '。页面已切换为错误态，未填充任何占位数据。');
        if (recovered) {
          showToast({
            type: 'error', sticky: true, title: '看板聚合失败',
            message: reason + '。请检查服务端数据目录后重试，提示保持至处理。',
            actions: [{ label: '重试加载', key: 'refresh' }]
          });
        }
        // 后端故障属于例外：整页数据区切错误态（仍不整页刷新）
        renderInitialError(reason);
        return;
      }

      state.backendFailure = null;
      state.error = null;
      updateConnectionBanner(null);
      clearErrorToasts(); // 任何一次成功取数都视为「已处理」，清掉保持中的失败提示
      if (isLive) {
        state.live = d;
        updateEnvHealth(d);
        renderEventsRegion(d);
        // 仅此区局部刷新：顶栏「数据时点」仍对应最近一次全量刷新
      } else {
        state.data = d;
        state.live = d;
        updateTopbarMeta(d);
        renderAll();
      }
    }).catch(function (err) {
      if (destroyed || mySeq !== requestSeq || (err && err.name === 'AbortError')) return;
      if (err instanceof ApiError && (err.status === 401 || err.status === 403)) setReadOnly(true);
      var firstFailure = !state.error;
      state.error = err;
      var reason = (err && err.message) ? err.message : '未知错误';
      updateConnectionBanner('数据刷新失败：' + reason + '。已保留最近一次成功快照，未写入任何占位数据。');
      if (isLive) {
        renderEventsError(reason);
      } else if (!state.data) {
        renderInitialError(reason);
      }
      if (firstFailure) {
        showToast({
          type: 'error', sticky: true, title: isLive ? '动态刷新失败' : '数据加载失败',
          message: reason + '。请检查服务端 /api/dashboard 后重试。',
          actions: [{ label: '重试加载', key: 'refresh' }]
        });
      }
    }).finally(function () {
      if (mySeq === requestSeq) setRefreshBusy(false);
    });
  }

  function setRefreshBusy(busy) {
    var btn = $('#manual-refresh');
    if (!btn) return;
    btn.disabled = !!busy && !!state.data; // 首次加载也允许手动重试
    btn.textContent = busy ? '加载中…' : '刷新数据';
    btn.setAttribute('aria-busy', busy ? 'true' : 'false');
  }

  function updateConnectionBanner(message) {
    var el = $('#connection-banner');
    if (!el) return;
    el.hidden = !message;
    var text = $('#connection-banner-text');
    if (text) text.textContent = message || '';
  }

  /* ----------------------------------------------------------
     3. 顶栏：环境 / 健康 / 服务器时间 / 数据时点 / 只读态
     ---------------------------------------------------------- */
  function healthStatus(v) {
    var s = str(v).toLowerCase();
    if (s === 'ok' || s === 'healthy') return 'ok';
    if (s === 'degraded' || s === 'warn') return 'attention';
    if (s === 'error' || s === 'down') return 'error';
    return 'attention';
  }

  function updateEnvHealth(d) {
    var env = $('#env-value');
    if (env) env.textContent = disp(d.env);
    var health = $('#health-value');
    if (health) {
      var key = healthStatus(d.health);
      health.className = 'badge ' + STATUS[key].cls;
      health.textContent = STATUS[key].label;
      health.title = 'health=' + disp(d.health);
    }
  }

  function updateTopbarMeta(d) {
    updateEnvHealth(d);
    var dt = $('#data-time');
    if (dt) {
      dt.textContent = disp(d.data_time);
      dt.title = '最近一次全量刷新时点';
    }
    var st = $('#server-time');
    if (st && !state.clock) st.textContent = disp(d.server_time);
  }

  function applyServerClock(serverTime) {
    var base = parseNaiveTime(serverTime);
    if (base === null) {
      state.clock = null;
      var el = $('#server-time');
      if (el) el.textContent = disp(serverTime);
      return;
    }
    state.clock = { base: base, at: Date.now() };
    renderClock();
  }

  function renderClock() {
    var el = $('#server-time');
    if (!el || !state.clock) return;
    el.textContent = formatClock(state.clock.base + (Date.now() - state.clock.at));
  }

  function updateCountdown() {
    var el = $('#refresh-countdown');
    if (!el) return;
    if (document.hidden) { el.textContent = '已暂停（页面隐藏）'; return; }
    var sec = Math.max(0, Math.ceil((nextRefreshAt - Date.now()) / 1000));
    el.textContent = '局部刷新 ' + sec + 's';
  }

  function evaluateReadOnly(d) {
    var ro = d.read_only === true ||
      (d.permissions && d.permissions.write === false) ||
      d.readonly === true;
    setReadOnly(ro);
  }

  function setReadOnly(flag) {
    var next = !!flag;
    if (next === state.readOnly) return;
    state.readOnly = next;
    document.body.dataset.readonly = next ? 'true' : 'false';
    var chip = $('#readonly-chip');
    if (chip) chip.hidden = !next;
    var tokenBtn = $('#open-token-form');
    var tokenReason = $('#token-form-reason');
    if (tokenBtn) {
      tokenBtn.disabled = next;
      if (next) { tokenBtn.title = '只读模式：当前会话无写权限'; tokenBtn.setAttribute('aria-disabled', 'true'); }
      else { tokenBtn.removeAttribute('title'); tokenBtn.removeAttribute('aria-disabled'); }
    }
    if (tokenReason) tokenReason.hidden = !next;
    if (state.data) renderAll();
  }

  /* ----------------------------------------------------------
     4. 五态渲染：骨架 / 空 / 错 / 只读 / 内容
     ---------------------------------------------------------- */
  function skeleton(lines) {
    if (lines) {
      return '<div class="skeleton skeleton--lines" aria-hidden="true"></div>' +
        '<p class="state-skeleton-label">正在从 /api/dashboard 加载数据…</p>';
    }
    return '<div class="skeleton skeleton--card" aria-hidden="true"></div>';
  }

  function stateBlock(kind, cfg) {
    var c = cfg || {};
    var cls = kind === 'error' ? 'state-block--error' : (kind === 'readonly' ? 'state-block--readonly' : '');
    var btn = c.actionLabel
      ? '<button type="button" class="btn-secondary" data-action="' + esc(c.action || 'refresh') + '"' +
        (c.actionAttr || '') + '>' + esc(c.actionLabel) + '</button>'
      : '';
    return '<div class="state-block ' + cls + '" role="' + (kind === 'error' ? 'alert' : 'status') + '">' +
      '<p class="state-title">' + esc(c.title) + '</p>' +
      '<p class="state-desc">' + esc(c.desc) + '</p>' + btn + '</div>';
  }

  function errorBlock(reason, action) {
    return stateBlock('error', {
      title: '数据加载失败',
      desc: '接口 /api/dashboard 返回：' + reason + '。页面不展示任何占位数据，也不会静默失败；请重试或联系运维检查服务端。',
      actionLabel: '重试加载', action: action || 'refresh'
    });
  }

  function readOnlyBlock() {
    return stateBlock('readonly', {
      title: '只读 / 无权限态',
      desc: '当前会话无写权限（服务端下发 read_only 或返回 401/403）。所有提交、凭证更新等写操作已禁用，数据仍可查看。'
    });
  }

  function setRegion(id, html, busy) {
    var el = document.getElementById(id);
    if (!el) return;
    el.innerHTML = html;
    el.setAttribute('aria-busy', busy ? 'true' : 'false');
  }

  function renderInitialError(reason) {
    ['summary-region', 'timeline-region', 'account-region', 'alerts-region', 'events-region',
      'accounts-full-region', 'schedule-region', 'records-region', 'logs-region',
      'settings-region'].forEach(function (id) { setRegion(id, errorBlock(reason), false); });
    var head = $('#overall-status');
    if (head) head.innerHTML = stateBlock('error', { title: '无法判断今日状态', desc: '数据接口未返回可用载荷。' });
  }

  function renderEventsError(reason) {
    var el = $('#events-region');
    if (!el) return;
    // 保留最近一次成功的事件列表，追加持久错误条；不重复叠加错误块
    $$('.state-block--error', el).forEach(function (node) { node.remove(); });
    el.insertAdjacentHTML('beforeend', errorBlock(reason, 'retry-live'));
    el.setAttribute('aria-busy', 'false');
  }

  function renderAll() {
    var d = state.data;
    if (!d) return;
    renderOverall(d);
    renderSummary(d);
    renderTimeline(d);
    renderAccountTableRegion(d);
    renderAccountsFullRegion(d);
    renderAlerts(d);
    renderEventsRegion(state.live || d);
    renderScheduleRegion(d);
    renderRecordsRegion(d);
    renderLogsRegion(d);
    renderSettingsRegion(d);
    // 用户正在输入凭证时不重绘抽屉，避免清空未提交内容
    var tokenForm = $('#token-form');
    if (state.drawer && !(tokenForm && tokenForm.contains(document.activeElement))) renderDrawerBody();
  }

  /* ----------------------------------------------------------
     5. 派生：摘要 / 账号状态 / 提交可用性
     ---------------------------------------------------------- */
  function deriveSummary(d) {
    var accounts = arr(d.accounts), alerts = arr(d.alerts), s = obj(d.summary);
    function pick(key, fallback) { var v = asNum(s[key]); return v === null ? fallback : v; }
    return {
      accounts_total: pick('accounts_total', accounts.length),
      enabled: pick('enabled', accounts.filter(function (a) { return obj(a).enabled !== false; }).length),
      today_done: pick('today_done', accounts.filter(function (a) { return obj(obj(a).today).done === true; }).length),
      today_total: pick('today_total', accounts.length),
      alerts_count: pick('alerts_count', alerts.length),
      term_total: pick('term_total', null),
      term_valid: pick('term_valid', null),
      term_target: pick('term_target', null),
      statCaliber: str(s.stat_caliber),
      hasTerm: ['term_total', 'term_valid', 'term_target'].some(function (k) { return asNum(s[k]) !== null; })
    };
  }

  function credentialStatus(a) {
    if (a.enabled === false) return { key: 'disabled', detail: '账号已停用，不参与调度' };
    var c = str(a.credential).toLowerCase();
    if (c === 'dead' || c === 'expired' || c === 'invalid') return { key: 'error', detail: '凭证失效 · 需更新凭证' };
    if (c === 'ok' || c === 'valid') return { key: 'ok', detail: '凭证可用' };
    return { key: 'attention', detail: '凭证状态未校验' };
  }

  function todayStatus(a) {
    if (a.enabled === false) return { key: 'disabled', detail: '账号已停用' };
    var t = obj(a.today);
    var s = str(t.status).toLowerCase();
    var last = exitInfo(obj(a.last_result).exit_code);

    // 后端按规格 §5 下发稳定机器键：ok / pending / running / attention / error / disabled
    if (Object.prototype.hasOwnProperty.call(STATUS, s)) {
      var detailMap = {
        ok: '今日已完成',
        pending: '等待今日目标时刻',
        running: '正在执行提交',
        attention: (last.key === 'error' || last.key === 'attention') ? last.hint : '需关注',
        error: '今日执行失败',
        disabled: '账号已停用'
      };
      var detail = detailMap[s];
      if (str(t.status_label)) detail = str(t.status_label) + (detail ? ' · ' + detail : '');
      return { key: s, detail: detail };
    }

    // 兼容旧值 / 中文文案 / 字段缺省
    if (t.done === true || s === 'done' || s === '完成' || s === 'success') {
      if (last.key === 'attention' || last.key === 'error') return { key: last.key, detail: last.hint };
      return { key: 'ok', detail: '今日已完成' };
    }
    if (s === 'running' || s === '提交中') return { key: 'running', detail: '正在执行提交' };
    if (s === 'failed' || s === '失败') return { key: 'error', detail: '今日执行失败' };
    if (s === 'warn' || s === 'attention') return { key: 'attention', detail: '需关注' };
    var cred = str(a.credential).toLowerCase();
    if (cred === 'dead' || cred === 'expired' || cred === 'invalid') return { key: 'error', detail: '凭证失效 · 需更新凭证' };
    if (cred === 'unknown' || !cred) return { key: 'attention', detail: '凭证状态未校验' };
    return { key: 'pending', detail: s ? disp(t.status) : '等待今日目标时刻' };
  }

  function accountStatus(a) {
    var c = credentialStatus(a), t = todayStatus(a);
    var rank = function (k) { var i = STATUS_ORDER.indexOf(k); return i === -1 ? 99 : i; };
    var key = rank(t.key) <= rank(c.key) ? t.key : c.key;
    return { key: key, detail: t.detail };
  }

  function overallStatus(d) {
    var accounts = arr(d.accounts);
    if (!accounts.length) return { key: 'attention', detail: '暂无账号' };
    var best = 'disabled';
    var rank = function (k) { return STATUS_ORDER.indexOf(k); };
    accounts.forEach(function (raw) {
      var k = accountStatus(obj(raw)).key;
      if (rank(k) < rank(best)) best = k;
    });
    return { key: best, detail: '' };
  }

  function submitBlockReason(a) {
    if (state.readOnly) return '只读模式：当前会话无写权限';
    var sn = str(a.student_num);
    if (!sn) return '服务端未下发学号，无法定位账号';
    if (state.busy.has(sn)) return '提交进行中';
    if (a.enabled === false) return '账号已停用，不参与提交';
    var cred = str(a.credential).toLowerCase();
    if (cred === 'dead' || cred === 'expired' || cred === 'invalid') return '凭证失效，请先更新凭证';
    var t = obj(a.today);
    if (t.done === true) return '今日已完成，无需重复提交';
    var last = exitInfo(obj(a.last_result).exit_code);
    if (!last.submit) return last.reason;
    return '';
  }

  /* ----------------------------------------------------------
     6. M02 状态摘要卡组
     ---------------------------------------------------------- */
  function renderOverall(d) {
    var el = $('#overall-status');
    if (!el) return;
    var s = overallStatus(d);
    el.innerHTML = '<span class="meta-chip">今日总体 <span class="badge ' + STATUS[s.key].cls + '">' +
      esc(STATUS[s.key].label) + '</span></span>' +
      '<span class="meta-chip">数据时点 <span class="mono">' + E(d.data_time) + '</span></span>';
  }

  function statCard(cfg) {
    var bar = cfg.pct === null ? '' :
      '<div class="progress-track" role="img" aria-label="进度 ' + cfg.pct + '%">' +
      '<div class="progress-fill ' + (cfg.bar || '') + '" style="width:' + cfg.pct + '%"></div></div>';
    return '<article class="card stat-card">' +
      '<p class="stat-label">' + esc(cfg.label) + '</p>' +
      '<p class="stat-value ' + (cfg.tone || '') + '">' + esc(cfg.value) +
      (cfg.unit ? '<span class="stat-unit">' + esc(cfg.unit) + '</span>' : '') + '</p>' +
      bar + '<p class="stat-foot">' + esc(cfg.foot) + '</p></article>';
  }

  function renderSummary(d) {
    var s = deriveSummary(d);
    var scope = $('#summary-scope');
    if (scope && s.statCaliber) scope.textContent = '统计口径（服务端下发）：' + s.statCaliber;
    var pAccounts = ratio(s.enabled, s.accounts_total);
    var pToday = ratio(s.today_done, s.today_total);
    var pTerm = ratio(s.term_valid, s.term_target);
    var html = [
      statCard({
        label: '账号 / 已启用',
        value: fmtNum(s.enabled) + ' / ' + fmtNum(s.accounts_total),
        foot: pAccounts === null ? '服务端未下发账号总数，无法计算占比' : '已启用占比 ' + pAccounts + '% · 停用账号不参与调度',
        pct: pAccounts, bar: 'progress-fill--ok'
      }),
      statCard({
        label: '今日完成率',
        value: fmtNum(s.today_done) + ' / ' + fmtNum(s.today_total),
        foot: pToday === null ? '服务端未下发今日总数' : '按 done 字段统计 · 完成 ' + pToday + '%',
        pct: pToday, bar: pToday === 100 ? 'progress-fill--ok' : ''
      }),
      statCard({
        label: '待处理告警',
        value: fmtNum(s.alerts_count),
        tone: asNum(s.alerts_count) > 0 ? 'stat-value--alert' : '',
        foot: '凭证失效 / 额度已满 / 接口异常聚合去重后的条数'
      }),
      statCard({
        label: '学期达标进度',
        value: s.hasTerm ? fmtNum(s.term_valid) + ' / ' + fmtNum(s.term_target) : '—',
        tone: 'stat-value--data',
        foot: s.hasTerm
          ? '总次数 ' + fmtNum(s.term_total) + ' · 仅有效记录计入达标，目标由服务端下发'
          : '服务端未下发学期统计（term_total / term_valid / term_target）',
        pct: pTerm, bar: pTerm === 100 ? 'progress-fill--ok' : ''
      })
    ].join('');
    setRegion('summary-region', html, false);
  }

  /* ----------------------------------------------------------
     7. M03 今日运行时间轴
     ---------------------------------------------------------- */
  function renderTimeline(d) {
    var windows = arr(d.windows).map(function (raw) {
      var w = obj(raw);
      return {
        name: str(w.name),
        startText: str(w.start),
        endText: str(w.end),
        startMin: parseHHMM(w.start),
        endMin: parseHHMM(w.end)
      };
    }).filter(function (w) {
      return w.startMin !== null && w.endMin !== null && w.endMin > w.startMin;
    });

    if (!windows.length) {
      setRegion('timeline-region', stateBlock('empty', {
        title: '暂无时间窗数据',
        desc: '服务端未在 windows 字段下发可用的时间窗（需含 name / start / end）。窗口、今日目标与下次触发全部由真实字段渲染，页面不写死时间。',
        actionLabel: '重试加载', action: 'refresh'
      }), false);
      return;
    }

    var accounts = arr(d.accounts).map(obj);
    var axisMin = Math.min.apply(null, windows.map(function (w) { return w.startMin; }));
    var axisMax = Math.max.apply(null, windows.map(function (w) { return w.endMin; }));
    var span = axisMax - axisMin;
    var pos = function (m) { return Math.max(0, Math.min(100, ((m - axisMin) / span) * 100)); };
    var nowMin = parseHHMM(d.server_time);

    var targets = [];
    accounts.forEach(function (a) {
      var m = parseHHMM(obj(a.today).target_time);
      if (m === null) return;
      targets.push({ min: m, label: disp(a.name, disp(a.student_num)), time: disp(obj(a.today).target_time), done: obj(a.today).done === true });
    });
    targets.sort(function (x, y) { return x.min - y.min; });

    var pending = targets.filter(function (t) { return !t.done && (nowMin === null || t.min > nowMin); });
    var nextText = pending.length
      ? formatMin(pending[0].min) + ' · ' + pending[0].label
      : (targets.length ? '目标时刻已过或未下发学号' : '服务端未下发今日目标时刻');

    var targetText = targets.length
      ? targets.map(function (t) { return '<span class="mono">' + E(t.time) + '</span> ' + E(t.label); }).join('　')
      : '服务端未下发今日目标时刻';

    var segments = windows.map(function (w) {
      return '<div class="timeline-segment" style="left:' + pos(w.startMin).toFixed(2) + '%;width:' +
        (pos(w.endMin) - pos(w.startMin)).toFixed(2) + '%" title="' +
        E(WINDOW_LABELS[w.name] || w.name, '时间窗') + ' ' + E(w.startText) + '–' + E(w.endText) + '"></div>';
    }).join('');

    var tickSet = new Set();
    windows.forEach(function (w) { tickSet.add(w.startMin); tickSet.add(w.endMin); });
    var ticks = Array.from(tickSet).sort(function (a, b) { return a - b; }).map(function (m) {
      return '<span class="timeline-tick" style="left:' + pos(m).toFixed(2) + '%">' + esc(formatMin(m)) + '</span>';
    }).join('');

    var nowMarker = (nowMin !== null && nowMin >= axisMin && nowMin <= axisMax)
      ? '<div class="timeline-now" style="left:' + pos(nowMin).toFixed(2) + '%"></div>' : '';

    var targetDots = targets.filter(function (t) { return t.min >= axisMin && t.min <= axisMax; }).map(function (t) {
      return '<span class="timeline-target" style="left:' + pos(t.min).toFixed(2) + '%" title="' +
        E(t.label) + ' · ' + E(t.time) + (t.done ? ' · 已完成' : ' · 待执行') + '"></span>';
    }).join('');

    var chips = windows.map(function (w) {
      return '<span class="window-chip">' + E(WINDOW_LABELS[w.name] || w.name, '时间窗') +
        ' <span class="mono">' + E(w.startText) + '–' + E(w.endText) + '</span></span>';
    }).join('');

    var html =
      '<dl class="timeline-meta">' +
        '<div><dt>今日目标</dt><dd>' + targetText + '</dd></div>' +
        '<div><dt>当前状态</dt><dd>' + badge(overallStatus(d).key) + '</dd></div>' +
        '<div><dt>下次触发</dt><dd class="mono">' + E(nextText) + '</dd></div>' +
        '<div><dt>时区</dt><dd class="mono">Asia/Shanghai（服务端 TZ）</dd></div>' +
        '<div><dt>数据时点</dt><dd class="mono">' + E(d.data_time) + '</dd></div>' +
      '</dl>' +
      '<div class="timeline-rail" aria-hidden="true">' +
        '<div class="timeline-segments">' + segments + nowMarker + targetDots + '</div>' +
        '<div class="timeline-axis">' + ticks + '</div>' +
      '</div>' +
      '<div class="timeline-window-list">' + chips + '</div>' +
      '<p class="timeline-legend">圆点＝账号今日目标时刻，竖线＝服务器当前时刻；目标按日期种子生成、全天恒定，窗口末预留余量。每日提交上限与状态机由服务端控制。</p>';

    setRegion('timeline-region', html, false);
  }

  /* ----------------------------------------------------------
     8. M04 账号状态表 / M12 批量操作
     ---------------------------------------------------------- */
  function filteredAccounts(d) {
    var list = arr(d.accounts).map(function (a, i) { return { a: obj(a), i: i }; });
    var q = state.filter.trim().toLowerCase();
    if (q) {
      list = list.filter(function (x) {
        return (str(x.a.name) + ' ' + str(x.a.student_num)).toLowerCase().indexOf(q) !== -1;
      });
    }
    var key = state.sort.key, mul = state.sort.dir === 'desc' ? -1 : 1;
    var rankKey = function (k) { var i = STATUS_ORDER.indexOf(k); return i === -1 ? 99 : i; };
    list.sort(function (x, y) {
      var ax = x.a, ay = y.a, r = 0;
      if (key === 'name') {
        r = (disp(ax.name, disp(ax.student_num))).localeCompare(disp(ay.name, disp(ay.student_num)), 'zh-CN');
      } else if (key === 'credential') {
        r = rankKey(credentialStatus(ax).key) - rankKey(credentialStatus(ay).key);
      } else if (key === 'today') {
        r = rankKey(todayStatus(ax).key) - rankKey(todayStatus(ay).key);
      } else if (key === 'last') {
        var cx = asNum(obj(ax.last_result).exit_code), cy = asNum(obj(ay.last_result).exit_code);
        r = (cx === null ? 9999 : cx) - (cy === null ? 9999 : cy);
      } else if (key === 'enabled') {
        r = (ax.enabled === false ? 1 : 0) - (ay.enabled === false ? 1 : 0);
      }
      if (r === 0) r = str(ax.student_num).localeCompare(str(ay.student_num), 'zh-CN');
      if (r === 0) r = x.i - y.i;
      return r * mul;
    });
    return list;
  }

  function th(label, key, extra) {
    var sortAttr = key
      ? ' aria-sort="' + (state.sort.key === key ? (state.sort.dir === 'asc' ? 'ascending' : 'descending') : 'none') + '"'
      : '';
    var inner = key
      ? '<button type="button" data-action="sort" data-sort="' + esc(key) + '">' + esc(label) + '</button>'
      : esc(label);
    return '<th scope="col"' + sortAttr + (extra || '') + '>' + inner + '</th>';
  }

  // 行内只保留短原因，完整原因在 title / 抽屉「操作」页 / 系统设置中给出
  function shortReason(reason) {
    if (reason.indexOf('只读') !== -1) return '只读';
    if (reason.indexOf('停用') !== -1) return '已停用';
    if (reason.indexOf('凭证失效') !== -1) return '凭证失效';
    if (reason.indexOf('已完成') !== -1) return '已完成';
    if (reason.indexOf('额度已满') !== -1) return '额度已满';
    if (reason.indexOf('提交进行中') !== -1) return '提交中';
    if (reason.indexOf('学号') !== -1) return '无学号';
    return reason.length > 6 ? reason.slice(0, 6) + '…' : reason;
  }

  function actionButtons(a) {
    var sn = str(a.student_num);
    var reason = submitBlockReason(a);
    var out = '<div class="cell-actions">';
    out += '<button type="button" class="btn-ghost" data-action="detail" data-sn="' + esc(sn) + '">详情</button>';
    if (reason) {
      out += '<button type="button" class="btn-danger btn-mini" disabled aria-disabled="true" title="' + esc(reason) + '">提交</button>' +
        '<span class="disabled-reason">' + esc(shortReason(reason)) + '</span>';
    } else {
      out += '<button type="button" class="btn-danger btn-mini" data-action="submit" data-sn="' + esc(sn) +
        '" title="手动触发一次提交（需二次确认）">提交</button>';
    }
    if (state.readOnly) {
      out += '<button type="button" class="btn-secondary btn-mini" disabled aria-disabled="true" title="只读模式：当前会话无写权限">更新凭证</button>';
    } else {
      out += '<button type="button" class="btn-secondary btn-mini" data-action="cred" data-sn="' + esc(sn) +
        '" title="通过 /api/token 下发新凭证">更新凭证</button>';
    }
    out += '<button type="button" class="btn-ghost" disabled aria-disabled="true" title="服务端未提供干跑验证端点（契约未定义），详见「系统设置」">干跑验证</button>';
    out += '<button type="button" class="btn-ghost" data-action="more" data-sn="' + esc(sn) +
      '" title="在抽屉「操作」页查看全部操作与禁用原因">更多</button>';
    out += '</div>';
    return out;
  }

  function accountTableHtml(d, mode) {
    var all = arr(d.accounts);
    if (!all.length) {
      return stateBlock('empty', {
        title: '暂无账号',
        desc: '抓号客户端通过 /api/token 上报凭证后服务端会自动创建账号；也可点下方按钮直接下发凭证新增账号。',
        actionLabel: '通过凭证下发新增账号', action: 'new-account'
      });
    }
    var list = filteredAccounts(d);
    if (!list.length) {
      return stateBlock('empty', {
        title: '没有匹配的账号',
        desc: '当前筛选「' + state.filter + '」无结果。清空筛选后查看全部账号。',
        actionLabel: '清空筛选', action: mode === 'full' ? 'clear-filter-full' : 'clear-filter'
      });
    }

    var head = '<tr class="table-head">' +
      (mode === 'full' ? '<th scope="col"><span class="sr-only">选择</span></th>' : '') +
      th('成员 / 学号', 'name') + th('凭证状态', 'credential') + th('今日执行', 'today') +
      th('上次结果', 'last') + th('启用状态', 'enabled') + '<th scope="col">行内操作</th></tr>';

    var rows = list.map(function (item) {
      var a = item.a;
      var sn = str(a.student_num);
      var cred = credentialStatus(a);
      var today = todayStatus(a);
      var last = exitInfo(obj(a.last_result).exit_code);
      var t = obj(a.today);
      var selected = state.selected.has(sn);
      var cls = 'table-row' + (selected ? ' table-row--selected' : '') +
        (a.enabled === false ? ' table-row--disabled' : '');
      var check = mode === 'full'
        ? '<td data-label="选择"><label class="check-field"><input type="checkbox" data-action="select" data-sn="' +
          esc(sn) + '"' + (selected ? ' checked' : '') + ' aria-label="选择账号 ' + esc(disp(a.name, sn)) + '"><span></span></label></td>'
        : '';
      return '<tr class="' + cls + '">' + check +
        '<td data-label="成员 / 学号"><div class="cell-stack"><span class="cell-primary">' + E(a.name, '未命名账号') +
          '</span><span class="cell-sub mono">' + E(a.student_num) + '</span></div></td>' +
        '<td data-label="凭证状态"><div class="cell-stack">' + badge(cred.key) +
          '<span class="cell-sub mono">' + (str(a.token_prefix) ? '前缀 ' + E(a.token_prefix) + '…' : '无前缀') + '</span></div></td>' +
        '<td data-label="今日执行"><div class="cell-stack">' + badge(today.key) +
          '<span class="cell-sub mono">目标 ' + E(t.target_time) + ' · 失败 ' + fmtNum(t.failed_count) + '</span></div></td>' +
        '<td data-label="上次结果"><div class="cell-stack">' + badge(last.key) +
          '<span class="cell-sub mono">exit ' + fmtNum(obj(a.last_result).exit_code) + ' · ' + esc(last.title) + '</span>' +
          '<span class="cell-sub cell-truncate" title="' + E(obj(a.last_result).summary, '无摘要') + '">' +
            E(obj(a.last_result).summary, '无摘要') + '</span></div></td>' +
        '<td data-label="启用状态">' + badge(a.enabled === false ? 'disabled' : 'ok') + '</td>' +
        '<td data-label="行内操作">' + actionButtons(a) + '</td></tr>';
    }).join('');

    var foot = '<p class="table-foot">共 ' + list.length + ' 个账号' +
      (list.length !== all.length ? '（已按筛选显示，全部 ' + all.length + ' 个）' : '') +
      ' · 凭证 / 今日执行 / 上次结果均来自真实字段 · token 仅展示前缀 · 「干跑验证」端点未开放，原因见「系统设置」</p>';

    return '<div class="table-wrap"><table class="data-table">' +
      '<caption class="sr-only">账号状态表：成员、凭证、今日执行、上次结果、启用状态与行内操作</caption>' +
      '<thead>' + head + '</thead><tbody>' + rows + '</tbody></table></div>' + foot;
  }

  function renderAccountTableRegion(d) { setRegion('account-region', accountTableHtml(d || state.data, 'compact'), false); }
  function renderAccountsFullRegion(d) {
    var data = d || state.data;
    setRegion('accounts-full-region', accountTableHtml(data, 'full'), false);
    renderBatchBar(data);
  }

  function renderBatchBar(d) {
    var valid = new Set(arr(d.accounts).map(function (a) { return str(obj(a).student_num); }));
    Array.from(state.selected).forEach(function (sn) { if (!valid.has(sn)) state.selected.delete(sn); });
    var bar = $('#batch-bar');
    if (!bar) return;
    var n = state.selected.size;
    if (!n) { bar.hidden = true; bar.innerHTML = ''; return; }
    bar.hidden = false;
    bar.innerHTML = '<span>已选 <span class="mono">' + n + '</span> 个账号 · 影响范围以学号为准</span>' +
      '<span class="batch-actions">' +
        '<button type="button" class="btn-secondary btn-mini" disabled aria-disabled="true" title="契约未提供批量提交端点，请逐个提交">批量提交</button>' +
        '<span class="disabled-reason">批量端点未开放</span>' +
        '<button type="button" class="btn-secondary btn-mini" disabled aria-disabled="true" title="/api/token 为单账号端点，需逐个更新">批量更新凭证</button>' +
        '<span class="disabled-reason">需逐个更新</span>' +
        '<button type="button" class="btn-ghost" data-action="batch-clear">取消选择</button>' +
      '</span>';
  }

  /* ----------------------------------------------------------
     9. M05 告警与待办 / M11 最近执行动态
     ---------------------------------------------------------- */
  function renderAlerts(d) {
    var seen = new Set();
    var alerts = arr(d.alerts).filter(function (raw) {
      var o = obj(raw);
      var key = [str(o.level), str(o.message), str(o.student_num)].join('|');
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
    if (!alerts.length) {
      setRegion('alerts-region', stateBlock('empty', {
        title: '暂无待处理告警',
        desc: '凭证失效、额度已满、接口异常会聚合在此并去重；无需处理时此区保持为空。'
      }), false);
      return;
    }
    var accountNums = new Set(arr(d.accounts).map(function (a) { return str(obj(a).student_num); }));
    var items = alerts.map(function (raw) {
      var o = obj(raw);
      var sn = str(o.student_num);
      var canHandle = sn && accountNums.has(sn);
      var action = canHandle
        ? '<button type="button" class="btn-secondary btn-mini" data-action="detail" data-sn="' + esc(sn) + '">处理该账号</button>'
        : '<button type="button" class="btn-secondary btn-mini" disabled aria-disabled="true" title="服务端未给出可定位的关联账号">处理该账号</button>' +
          '<span class="disabled-reason">未关联可定位账号</span>';
      return '<div class="list-item list-item--alert">' +
        '<div class="list-item-top">' + badge(levelToStatus(o.level)) + '</div>' +
        '<div><p class="list-item-msg">' + E(o.message, '无告警正文') + '</p>' +
        '<div class="list-item-meta"><span class="mono">级别 ' + E(o.level) + '</span>' +
        '<span class="mono">账号 ' + E(o.student_num) + '</span>' +
        '<span class="mono">' + E(o.time) + '</span></div>' +
        '<div class="list-item-actions">' + action +
          '<button type="button" class="btn-ghost" data-action="goto" data-view="logs">查看日志</button></div>' +
        '</div></div>';
    }).join('');
    var foot = '<p class="table-foot">共 ' + alerts.length + ' 条（已按级别 / 正文 / 学号去重）· 失败项保持至处理</p>';
    setRegion('alerts-region', '<div class="list-scroll">' + items + '</div>' + foot, false);
  }

  function normalizedEvents(d) {
    return arr(d.recent_events).map(function (raw) {
      var o = obj(raw);
      return {
        time: str(o.time), level: str(o.level),
        student_num: str(o.student_num),
        message: str(o.message || o.msg || o.summary)
      };
    });
  }

  function renderEventsRegion(d) {
    var el = $('#events-region');
    var oldList = el ? el.querySelector('.list-scroll') : null;
    var keepScroll = oldList ? oldList.scrollTop : 0;
    var events = normalizedEvents(d);
    var body;
    if (!events.length) {
      body = stateBlock('empty', {
        title: '暂无最近执行动态',
        desc: '手动提交或调度触发后，事件会出现在此。本区按设定间隔局部刷新，不会触发整页刷新。'
      });
    } else {
      body = '<div class="list-scroll">' + events.map(function (o) {
        return '<div class="list-item"><div class="list-item-top">' + badge(levelToStatus(o.level)) + '</div>' +
          '<div><p class="list-item-msg">' + E(o.message, '无事件正文') + '</p>' +
          '<div class="list-item-meta"><span class="mono">' + E(o.time) + '</span>' +
          '<span class="mono">级别 ' + E(o.level) + '</span>' +
          '<span class="mono">' + E(o.student_num) + '</span></div></div></div>';
      }).join('') + '</div>';
    }
    var foot = '<p class="table-foot">本区数据时点 <span class="mono">' + E(d.data_time) +
      '</span> · 每 ' + Math.round(REFRESH_MS / 1000) + 's 局部刷新（仅此区）· 其余数据区点「刷新数据」更新</p>';
    setRegion('events-region', body + foot, false);
    var newList = el ? el.querySelector('.list-scroll') : null;
    if (newList) newList.scrollTop = keepScroll;
  }

  /* ----------------------------------------------------------
     10. P03 运行与调度 / P04 记录与统计 / P05 日志 / P06 设置
     ---------------------------------------------------------- */
  function renderScheduleRegion(d) {
    var windows = arr(d.windows).map(obj);
    var accounts = arr(d.accounts).map(obj);
    var winHtml = windows.length
      ? windows.map(function (w) {
          return '<span class="window-chip">' + E(WINDOW_LABELS[str(w.name)] || w.name, '时间窗') +
            ' <span class="mono">' + E(w.start) + '–' + E(w.end) + '</span></span>';
        }).join('')
      : '<p>服务端未下发时间窗配置（windows 为空）。</p>';

    var rows = accounts.map(function (a) {
      var t = obj(a.today);
      var last = exitInfo(obj(a.last_result).exit_code);
      var deadNotified = (t.dead_notified === undefined && a.dead_notified === undefined)
        ? '服务端未下发' : (t.dead_notified || a.dead_notified || false) ? '是' : '否';
      return '<tr class="table-row' + (a.enabled === false ? ' table-row--disabled' : '') + '">' +
        '<td data-label="账号"><div class="cell-stack"><span class="cell-primary">' + E(a.name, '未命名账号') +
          '</span><span class="cell-sub mono">' + E(a.student_num) + '</span></div></td>' +
        '<td data-label="done"><span class="mono">' + (t.done === true ? 'true' : t.done === false ? 'false' : '—') + '</span></td>' +
        '<td data-label="目标时刻"><span class="mono">' + E(t.target_time) + '</span></td>' +
        '<td data-label="今日状态">' + badge(todayStatus(a).key) + '</td>' +
        '<td data-label="失败次数"><span class="mono">' + fmtNum(t.failed_count) + '</span></td>' +
        '<td data-label="last_exit"><span class="mono">' + fmtNum(obj(a.last_result).exit_code) + '</span></td>' +
        '<td data-label="业务文案">' + esc(last.title) + '</td>' +
        '<td data-label="dead_notified"><span class="mono">' + esc(deadNotified) + '</span></td></tr>';
    }).join('');

    var stateHtml = accounts.length
      ? '<div class="table-wrap"><table class="data-table"><caption class="sr-only">今日调度状态机</caption>' +
        '<thead><tr class="table-head"><th scope="col">账号</th><th scope="col">done</th><th scope="col">目标时刻</th>' +
        '<th scope="col">今日状态</th><th scope="col">失败次数</th><th scope="col">last_exit</th>' +
        '<th scope="col">业务文案</th><th scope="col">dead_notified</th></tr></thead><tbody>' + rows + '</tbody></table></div>'
      : stateBlock('empty', { title: '暂无账号状态', desc: '服务端 accounts 为空，无法展示今日状态机。' });

    var html =
      '<div class="pad"><h3>M08 · 时间窗与状态机</h3><div class="timeline-window-list">' + winHtml + '</div>' +
        '<p>每日提交上限、目标时刻生成规则与窗口末余量均由服务端调度状态机控制；此处只展示真实字段，不写入配置。</p>' +
        '<div class="op-row"><div class="op-desc"><strong>暂停 / 恢复调度</strong><span>服务端未提供暂停 / 恢复端点（契约未定义），操作不可用。</span></div>' +
        '<div><button type="button" class="btn-secondary btn-mini" disabled aria-disabled="true" title="服务端未提供调度暂停 / 恢复端点">暂停调度</button> ' +
        '<span class="disabled-reason">端点未开放</span></div></div></div>' +
      '<div class="pad"><h3>今日状态机</h3>' + stateHtml + '</div>';
    setRegion('schedule-region', html, false);
  }

  function renderRecordsRegion(d) {
    var s = deriveSummary(d);
    var pTerm = ratio(s.term_valid, s.term_target);
    var termHtml = s.hasTerm
      ? '<dl class="kv-grid"><dt>学期总次数</dt><dd class="mono">' + fmtNum(s.term_total) + '</dd>' +
        '<dt>达标次数</dt><dd class="mono">' + fmtNum(s.term_valid) + '</dd>' +
        '<dt>达标目标</dt><dd class="mono">' + fmtNum(s.term_target) + '</dd>' +
        '<dt>完成度</dt><dd class="mono">' + (pTerm === null ? '—' : pTerm + '%') + '</dd></dl>' +
        (pTerm === null ? '' : '<div class="progress-track"><div class="progress-fill" style="width:' + pTerm + '%"></div></div>')
      : '<p>服务端未下发学期统计（term_total / term_valid / term_target），此处不填充占位数值。</p>';

    var accounts = arr(d.accounts).map(obj);
    var recordRows = accounts.map(function (a) {
      var last = exitInfo(obj(a.last_result).exit_code);
      var sn = str(a.student_num);
      var reason = submitBlockReason(a);
      var noRetry = reason || last.reason || (last.retry ? '' : '当前结果无需重试');
      var ops = (last.retry && !reason)
        ? '<button type="button" class="btn-danger btn-mini" data-action="submit" data-sn="' + esc(sn) + '">重试</button>'
        : '<button type="button" class="btn-secondary btn-mini" disabled aria-disabled="true" title="' + esc(noRetry) + '">重试</button>' +
          '<span class="disabled-reason">' + esc(noRetry ? shortReason(noRetry) : '无需重试') + '</span>';
      return '<tr class="table-row"><td data-label="账号"><div class="cell-stack"><span class="cell-primary">' +
        E(a.name, '未命名账号') + '</span><span class="cell-sub mono">' + E(a.student_num) + '</span></div></td>' +
        '<td data-label="exit_code"><span class="mono">' + fmtNum(obj(a.last_result).exit_code) + '</span></td>' +
        '<td data-label="业务结果">' + badge(last.key) + ' <span>' + esc(last.title) + '</span></td>' +
        '<td data-label="摘要"><span class="cell-truncate" title="' + E(obj(a.last_result).summary, '无摘要') + '">' +
          E(obj(a.last_result).summary, '无摘要') + '</span></td>' +
        '<td data-label="操作"><div class="cell-actions">' + ops +
          '<button type="button" class="btn-ghost" data-action="goto" data-view="logs">查看日志</button></div></td></tr>';
    }).join('');

    var legend = EXIT_LEGEND.map(function (code) {
      var info = exitInfo(code);
      return '<tr class="table-row"><td data-label="exit"><span class="mono">' + code + '</span></td>' +
        '<td data-label="徽章">' + badge(info.key) + '</td>' +
        '<td data-label="业务文案">' + esc(info.title) + '</td>' +
        '<td data-label="动作">' + esc(info.hint) + '</td></tr>';
    }).join('');

    var html =
      '<div class="pad"><h3>M10 · 学期统计</h3>' + termHtml +
        '<p>统计口径：仅 record_status = 1 计入成绩与达标；无效记录计入总次数但不计入达标与有效里程；目标次数由服务端下发。</p></div>' +
      '<div class="pad"><h3>M09 · 执行记录</h3>' + (accounts.length
        ? '<div class="table-wrap"><table class="data-table"><caption class="sr-only">账号上次执行结果</caption>' +
          '<thead><tr class="table-head"><th scope="col">账号</th><th scope="col">exit_code</th><th scope="col">业务结果</th>' +
          '<th scope="col">摘要</th><th scope="col">操作</th></tr></thead><tbody>' + recordRows + '</tbody></table></div>'
        : stateBlock('empty', { title: '暂无执行记录', desc: '服务端 accounts 为空，尚无上次结果可翻译。' })) +
        '<div class="op-row"><div class="op-desc"><strong>导出记录</strong><span>服务端未提供记录导出端点（契约未定义）。</span></div>' +
        '<div><button type="button" class="btn-secondary btn-mini" disabled aria-disabled="true" title="服务端未提供导出端点">导出</button> ' +
        '<span class="disabled-reason">导出端点未开放</span></div></div></div>' +
      '<div class="pad"><h3>退出码 → 业务语言（规格 §5）</h3>' +
        '<div class="table-wrap"><table class="data-table"><caption class="sr-only">退出码映射对照</caption>' +
        '<thead><tr class="table-head"><th scope="col">exit</th><th scope="col">状态</th><th scope="col">界面文案</th>' +
        '<th scope="col">动作</th></tr></thead><tbody>' + legend + '</tbody></table></div></div>';
    setRegion('records-region', html, false);
  }

  function normalizedLogs(d) {
    var out = [];
    arr(d.logs_tail).forEach(function (raw) {
      if (typeof raw === 'string') {
        out.push({ time: '', level: inferLevel(raw), student_num: '', message: raw, source: 'tail' });
      } else {
        var o = obj(raw);
        var msg = str(o.message || o.msg || o.line || o.text);
        out.push({
          time: str(o.time || o.timestamp),
          level: str(o.level) || inferLevel(msg),
          student_num: str(o.student_num),
          message: msg,
          source: 'tail'
        });
      }
    });
    arr(d.recent_events).forEach(function (raw) {
      var o = obj(raw);
      out.push({
        time: str(o.time), level: str(o.level), student_num: str(o.student_num),
        message: str(o.message || o.msg || o.summary), source: 'event'
      });
    });
    return out;
  }

  function renderLogsRegion(d) {
    var logs = normalizedLogs(d);
    if (state.logLevel !== 'all') {
      logs = logs.filter(function (o) { return levelToStatus(o.level) === state.logLevel; });
    }
    var body;
    if (!logs.length) {
      body = stateBlock('empty', {
        title: '暂无运行日志',
        desc: '服务端 logs_tail 与 recent_events 为空，或当前级别筛选无匹配。可切换「全部」或点「刷新数据」重试。'
      });
    } else {
      body = '<div class="list-scroll" id="logs-list">' + logs.map(function (o) {
        return '<div class="log-line"><span class="mono">' + E(o.time, '—') + '</span>' +
          badge(levelToStatus(o.level)) +
          '<span class="mono">' + E(o.student_num) + '</span>' +
          '<pre>' + E(o.message, '无日志正文') + '</pre></div>';
      }).join('') + '</div>';
    }
    var foot = '<p class="table-foot">共 ' + logs.length + ' 条 · 来源 logs_tail / recent_events · 服务端日志记录时间与结果，操作人当前未记录、手动提交不写日志（已知边界）；界面不展示原始异常栈</p>';
    setRegion('logs-region', body + foot, false);
    var list = $('#logs-list');
    if (list && state.autoScroll && state.view === 'logs') list.scrollTop = list.scrollHeight;
  }

  function renderSettingsRegion(d) {
    var refreshSec = Math.round(REFRESH_MS / 1000);
    var html =
      (state.readOnly ? readOnlyBlock() : '') +
      '<div class="pad"><h3>部署环境与参数（只读）</h3><dl class="kv-grid">' +
        '<dt>环境标识</dt><dd class="mono">' + E(d.env) + '</dd>' +
        '<dt>服务健康</dt><dd>' + badge(healthStatus(d.health)) + ' <span class="mono">' + E(d.health) + '</span></dd>' +
        '<dt>服务器时间</dt><dd class="mono">' + E(d.server_time) + '（接口下发值，顶栏按此本地推进）</dd>' +
        '<dt>数据时点</dt><dd class="mono">' + E(d.data_time) + '</dd>' +
        '<dt>局部刷新</dt><dd class="mono">每 ' + refreshSec + 's（前端令牌 --refresh-interval，仅 M11 数据区）</dd>' +
        '<dt>时区</dt><dd class="mono">由服务端 TZ 决定（容器默认 Asia/Shanghai）</dd>' +
        '<dt>数据接口</dt><dd class="mono">GET /api/dashboard</dd>' +
        '<dt>写接口</dt><dd class="mono">POST /api/submit/&lt;student_num&gt; · POST /api/token</dd>' +
        '<dt>兼容保留</dt><dd class="mono">GET /api/status</dd>' +
      '</dl></div>' +
      '<div class="pad"><h3>未开放能力（禁用态说明）</h3>' +
        '<div class="op-row"><div class="op-desc"><strong>干跑验证</strong><span>服务端未提供零写入自检端点（契约未定义）。</span></div>' +
        '<button type="button" class="btn-secondary btn-mini" disabled aria-disabled="true" title="服务端未提供干跑验证端点">干跑验证</button></div>' +
        '<div class="op-row"><div class="op-desc"><strong>停用 / 删除账号</strong><span>服务端未提供停用 / 删除端点（契约未定义），不可伪装成功。</span></div>' +
        '<button type="button" class="btn-danger btn-mini" disabled aria-disabled="true" title="服务端未提供停用 / 删除端点">停用 / 删除</button></div>' +
        '<div class="op-row"><div class="op-desc"><strong>日志导出 / 批量操作</strong><span>契约未提供对应端点；请在服务端实现后再开放。</span></div>' +
        '<button type="button" class="btn-secondary btn-mini" disabled aria-disabled="true" title="契约未提供对应端点">导出 / 批量</button></div>' +
        '<div class="op-row"><div class="op-desc"><strong>浅色模式 / 通知配置 / 学期趋势</strong><span>设计规划中的扩展项，当前版本未实现。</span></div>' +
        '<button type="button" class="btn-secondary btn-mini" disabled aria-disabled="true" title="当前版本未实现">未开放</button></div>' +
      '</div>' +
      '<div class="pad"><h3>合规边界</h3><p>仅本人或已明确同意的成员；凭证 / token 仅本机使用、不外传、不提交仓库，界面不展示完整 token（只显示前缀）。' +
        '破坏性操作需二次确认并说明是否可撤销；真实部署前需确认使用边界与授权范围。</p></div>';
    setRegion('settings-region', html, false);
  }

  /* ----------------------------------------------------------
     11. L2 账号详情抽屉（Tab / 凭证更新 / 操作）
     ---------------------------------------------------------- */
  function findAccount(sn) {
    var found = null;
    arr(state.data && state.data.accounts).forEach(function (raw) {
      var a = obj(raw);
      if (str(a.student_num) === sn) found = a;
    });
    return found;
  }

  function openDrawer(sn, tab, trigger) {
    var dlg = $('#account-drawer');
    if (!dlg) return;
    var acc = sn ? findAccount(sn) : null;
    if (sn && !acc) {
      showToast({ type: 'error', sticky: true, category: 'operation', title: '账号不可定位',
        message: '该学号已不在最近一次全量数据中，点「刷新数据」后重试。',
        actions: [{ label: '刷新数据', key: 'refresh' }] });
      return;
    }
    state.drawer = { sn: sn || '', tab: acc ? (tab || 'base') : 'cred' };
    state.drawerTrigger = trigger || null;
    var title = $('#drawer-title');
    if (title) title.textContent = acc ? (disp(acc.name, disp(acc.student_num)) + ' · 账号详情') : '新增账号 / 下发凭证';
    $$('#drawer-tabs [data-drawer-tab]').forEach(function (t) {
      t.hidden = !acc; // 无账号时只保留凭证 Tab
    });
    renderDrawerBody();
    if (!dlg.open) dlg.showModal();
    var focusTarget = $('#drawer-close');
    if (focusTarget) focusTarget.focus();
  }

  function closeDrawer() {
    var dlg = $('#account-drawer');
    if (dlg && dlg.open) dlg.close();
  }

  function setDrawerTab(tab) {
    if (!state.drawer) return;
    state.drawer.tab = tab;
    $$('#drawer-tabs [data-drawer-tab]').forEach(function (t) {
      var active = t.getAttribute('data-drawer-tab') === tab;
      t.classList.toggle('is-active', active);
      t.setAttribute('aria-selected', active ? 'true' : 'false');
      t.tabIndex = active ? 0 : -1;
    });
    $$('#drawer-body [role="tabpanel"]').forEach(function (p) { p.hidden = p.id !== 'dpanel-' + tab; });
  }

  function tokenFormHtml(acc) {
    var disabled = state.readOnly;
    var sn = acc ? str(acc.student_num) : '';
    return '<form class="dialog-form" id="token-form" novalidate>' +
      '<div class="form-field"><label for="tf-student">学号 student_num</label>' +
      '<input id="tf-student" name="student_num" type="text" value="' + esc(sn) + '"' +
      (acc ? ' readonly' : ' required') + ' autocomplete="off" spellcheck="false" placeholder="服务端账号学号"></div>' +
      '<div class="form-field"><label for="tf-token">新 token（本机输入，不回显、不保存完整值）</label>' +
      '<input id="tf-token" name="token" type="password" required autocomplete="off" spellcheck="false" placeholder="粘贴抓号客户端下发的 token"></div>' +
      '<div class="form-field"><label for="tf-uid">uid（可选）</label>' +
      '<input id="tf-uid" name="uid" type="text" inputmode="numeric" autocomplete="off" spellcheck="false" placeholder="留空表示不更新"></div>' +
      '<p class="form-error" id="tf-error" role="alert"></p>' +
      (disabled ? '<p class="drawer-note">只读模式：当前会话无写权限，凭证更新不可用。</p>' : '') +
      '<div class="form-actions">' +
        '<button type="submit" class="btn-secondary"' + (disabled ? ' disabled aria-disabled="true" title="只读模式：当前会话无写权限"' : '') + '>提交更新</button>' +
        '<button type="button" class="btn-ghost" data-action="token-cancel">清空输入</button>' +
      '</div></form>';
  }

  function renderDrawerBody() {
    var body = $('#drawer-body');
    if (!body || !state.drawer) return;
    var d = state.data || {};
    var acc = state.drawer.sn ? findAccount(state.drawer.sn) : null;
    var tab = state.drawer.tab;

    if (state.drawer.sn && !acc) {
      body.innerHTML = stateBlock('error', {
        title: '账号已不在最新数据中',
        desc: '学号 ' + state.drawer.sn + ' 在最近一次全量刷新中不存在。点「重试加载」同步数据后再查看详情。',
        actionLabel: '重试加载', action: 'refresh'
      });
      return;
    }

    if (!acc) {
      body.innerHTML = '<div role="tabpanel" id="dpanel-cred" aria-labelledby="dtab-cred" class="drawer-panel">' +
        '<h3>通过 /api/token 下发凭证</h3>' +
        '<p class="drawer-note">服务端在学号不存在时会自动创建账号。凭证仅本机使用，界面只展示前缀，不回显完整 token。' +
        '更新后旧凭证立即失效，请确认单活动会话已退出。</p>' +
        tokenFormHtml(null) + '</div>';
      setDrawerTab('cred');
      return;
    }

    var cred = credentialStatus(acc), today = todayStatus(acc);
    var last = exitInfo(obj(acc.last_result).exit_code);
    var t = obj(acc.today);
    var windows = arr(d.windows).map(obj);
    var winText = windows.length ? windows.map(function (w) {
      return E(WINDOW_LABELS[str(w.name)] || w.name, '时间窗') + ' <span class="mono">' + E(w.start) + '–' + E(w.end) + '</span>';
    }).join('　') : '服务端未下发时间窗配置';
    var events = normalizedEvents(d).filter(function (o) { return o.student_num === str(acc.student_num); });
    var reason = submitBlockReason(acc);

    var basePanel =
      '<div role="tabpanel" id="dpanel-base" aria-labelledby="dtab-base" class="drawer-panel"><h3>基本信息</h3>' +
      '<dl class="kv-grid">' +
        '<dt>成员</dt><dd>' + E(acc.name, '未命名账号') + '</dd>' +
        '<dt>学号</dt><dd class="mono">' + E(acc.student_num) + '</dd>' +
        '<dt>启用状态</dt><dd>' + badge(acc.enabled === false ? 'disabled' : 'ok') + '</dd>' +
        '<dt>凭证状态</dt><dd>' + badge(cred.key) + ' <span>' + esc(cred.detail) + '</span></dd>' +
        '<dt>Token 前缀</dt><dd class="mono">' + (str(acc.token_prefix) ? E(acc.token_prefix) + '…' : '—') + '（完整值不展示）</dd>' +
        '<dt>签发时间</dt><dd class="mono">' + E(acc.token_issued) + '</dd>' +
        '<dt>今日目标</dt><dd class="mono">' + E(t.target_time) + '</dd>' +
        '<dt>今日执行</dt><dd>' + badge(today.key) + ' <span>' + esc(today.detail) + '</span></dd>' +
        '<dt>失败次数</dt><dd class="mono">' + fmtNum(t.failed_count) + '</dd>' +
        '<dt>上次退出码</dt><dd class="mono">' + fmtNum(obj(acc.last_result).exit_code) + '</dd>' +
        '<dt>上次结果</dt><dd>' + badge(last.key) + ' <span>' + esc(last.title) + ' · ' + esc(last.hint) + '</span></dd>' +
        '<dt>结果摘要</dt><dd>' + E(obj(acc.last_result).summary, '无摘要') + '</dd>' +
      '</dl><p class="drawer-note">抽屉内容取自最近一次全量刷新时点 <span class="mono">' + E(d.data_time) +
        '</span>；仅 M11 会按间隔局部刷新，点「刷新数据」可同步此处。</p></div>';

    var credPanel =
      '<div role="tabpanel" id="dpanel-cred" aria-labelledby="dtab-cred" class="drawer-panel"><h3>凭证更新（M07）</h3>' +
      '<dl class="kv-grid"><dt>当前前缀</dt><dd class="mono">' + (str(acc.token_prefix) ? E(acc.token_prefix) + '…' : '—') + '</dd>' +
      '<dt>签发时间</dt><dd class="mono">' + E(acc.token_issued) + '</dd>' +
      '<dt>校验状态</dt><dd>' + badge(cred.key) + '</dd></dl>' +
      '<p class="drawer-note">更新后即时以新凭证参与调度；单活动会话提醒：旧凭证会立即失效。凭证仅本机使用、不外传、不提交仓库；界面不保存也不回显完整 token。</p>' +
      tokenFormHtml(acc) + '</div>';

    var policyPanel =
      '<div role="tabpanel" id="dpanel-policy" aria-labelledby="dtab-policy" class="drawer-panel"><h3>运行策略</h3>' +
      '<dl class="kv-grid"><dt>时间窗</dt><dd>' + winText + '</dd>' +
      '<dt>今日目标</dt><dd class="mono">' + E(t.target_time) + '</dd>' +
      '<dt>距离 / 配速</dt><dd>服务端未下发策略参数（接口契约未包含），请查看服务端配置文件。</dd>' +
      '<dt>每日上限</dt><dd>由服务端调度状态机控制。</dd></dl>' +
      '<p class="drawer-note">策略编辑入口需服务端提供配置写入端点，当前仅展示。</p>' +
      '<div class="op-row"><div class="op-desc"><strong>编辑策略</strong><span>服务端未提供策略写入端点（契约未定义）。</span></div>' +
      '<button type="button" class="btn-secondary btn-mini" disabled aria-disabled="true" title="服务端未提供策略写入端点">编辑</button></div></div>';

    var recordPanel =
      '<div role="tabpanel" id="dpanel-record" aria-labelledby="dtab-record" class="drawer-panel"><h3>近期结果</h3>' +
      '<dl class="kv-grid"><dt>exit_code</dt><dd class="mono">' + fmtNum(obj(acc.last_result).exit_code) + '</dd>' +
      '<dt>业务结果</dt><dd>' + badge(last.key) + ' <span>' + esc(last.title) + '</span></dd>' +
      '<dt>处理建议</dt><dd>' + esc(last.hint) + '</dd>' +
      '<dt>摘要</dt><dd>' + E(obj(acc.last_result).summary, '无摘要') + '</dd></dl>' +
      '<h3 class="mt-6">关联事件</h3>' +
      (events.length
        ? '<div class="list-scroll">' + events.map(function (o) {
            return '<div class="list-item"><div class="list-item-top">' + badge(levelToStatus(o.level)) + '</div>' +
              '<div><p class="list-item-msg">' + E(o.message, '无事件正文') + '</p>' +
              '<div class="list-item-meta"><span class="mono">' + E(o.time) + '</span></div></div></div>';
          }).join('') + '</div>'
        : '<p>暂无与该学号关联的最近事件。</p>') + '</div>';

    var opsPanel =
      '<div role="tabpanel" id="dpanel-ops" aria-labelledby="dtab-ops" class="drawer-panel"><h3>操作（M12）</h3>' +
      '<div class="op-row"><div class="op-desc"><strong>手动提交</strong><span>触发一次真实提交，需二次确认；接口仅返回 exit_code / summary，本页以 Toast 反馈，不回写本地 state。</span></div>' +
        (reason
          ? '<div><button type="button" class="btn-danger btn-mini" disabled aria-disabled="true" title="' + esc(reason) + '">提交</button> <span class="disabled-reason">' + esc(reason) + '</span></div>'
          : '<button type="button" class="btn-danger btn-mini" data-action="submit" data-sn="' + esc(acc.student_num) + '">提交</button>') + '</div>' +
      '<div class="op-row"><div class="op-desc"><strong>更新凭证</strong><span>通过 /api/token 下发新凭证。</span></div>' +
        (state.readOnly
          ? '<div><button type="button" class="btn-secondary btn-mini" disabled aria-disabled="true" title="只读模式：当前会话无写权限">更新凭证</button> <span class="disabled-reason">只读模式：无写权限</span></div>'
          : '<button type="button" class="btn-secondary btn-mini" data-action="drawer-tab" data-tab="cred">更新凭证</button>') + '</div>' +
      '<div class="op-row"><div class="op-desc"><strong>干跑验证</strong><span>服务端未提供零写入自检端点（契约未定义）。</span></div>' +
        '<button type="button" class="btn-secondary btn-mini" disabled aria-disabled="true" title="服务端未提供干跑验证端点">干跑验证</button></div>' +
      '<div class="op-row"><div class="op-desc"><strong>停用账号</strong><span>服务端未提供停用端点（契约未定义）。</span></div>' +
        '<button type="button" class="btn-danger btn-mini" disabled aria-disabled="true" title="服务端未提供停用端点">停用</button></div>' +
      '<div class="op-row"><div class="op-desc"><strong>删除账号</strong><span>服务端未提供删除端点（契约未定义）。</span></div>' +
        '<button type="button" class="btn-danger btn-mini" disabled aria-disabled="true" title="服务端未提供删除端点">删除</button></div>' +
      '<div class="op-row"><div class="op-desc"><strong>查看日志</strong><span>切换到运行日志视图。</span></div>' +
        '<button type="button" class="btn-ghost" data-action="goto" data-view="logs">查看日志</button></div></div>';

    body.innerHTML = basePanel + credPanel + policyPanel + recordPanel + opsPanel;
    setDrawerTab(tab);
  }

  /* ----------------------------------------------------------
     12. 危险操作二次确认 / 写操作
     ---------------------------------------------------------- */
  function openConfirm(action, sn) {
    var dlg = $('#confirm-dialog');
    if (!dlg) return;
    var acc = findAccount(sn) || {};
    var name = disp(acc.name, disp(acc.student_num, '未知账号'));
    state.confirm = { action: action, sn: sn };
    var title = $('#confirm-title');
    if (title) title.textContent = action === 'submit' ? '确认手动提交' : '确认操作';
    var lead = $('#confirm-desc');
    if (lead) lead.textContent = action === 'submit'
      ? '即将对账号「' + name + '」（学号 ' + disp(acc.student_num) + '）触发一次乐跑提交。服务端会立即调用校方接口执行一次真实提交；该端点不回写本地 state 文件，看板当日状态不会因此立即变化。'
      : '请确认以下操作的影响范围。';
    var list = $('#confirm-list');
    if (list) {
      list.innerHTML =
        '<dt>动作</dt><dd class="mono">POST /api/submit/&lt;student_num&gt; · 手动触发一次提交</dd>' +
        '<dt>影响范围</dt><dd>' + esc(name) + '（学号 <span class="mono">' + esc(disp(acc.student_num)) + '</span>），仅此一个账号</dd>' +
        '<dt>操作人</dt><dd>当前浏览器会话；服务端该端点未记录操作人，本页不收集身份信息</dd>' +
        '<dt>是否可撤销</dt><dd>不可撤销 —— 提交一旦成功即向校方接口产生一次真实记录，本页无法回滚</dd>' +
        '<dt>结果留痕</dt><dd>接口仅返回 exit_code / summary，本页以 Toast 即时反馈；不写服务端运行日志、不回写当日 state</dd>' +
        '<dt>已知边界</dt><dd>当日状态、日志与最近动态由调度器状态机维护；完成后虽会重新取数，仍可能显示提交前的快照</dd>';
    }
    var err = $('#confirm-error');
    if (err) err.textContent = '';
    var ok = $('#confirm-ok');
    if (ok) { ok.disabled = false; ok.textContent = '确认提交'; }
    if (!dlg.open) dlg.showModal();
    if (ok) ok.focus();
  }

  function closeConfirm() {
    var dlg = $('#confirm-dialog');
    if (dlg && dlg.open) dlg.close();
    state.confirm = null;
  }

  function runSubmit() {
    var ctx = state.confirm;
    if (!ctx || ctx.action !== 'submit') return;
    var ok = $('#confirm-ok');
    var err = $('#confirm-error');
    if (ok) { ok.disabled = true; ok.textContent = '提交中…'; }
    if (err) err.textContent = '';
    state.busy.add(ctx.sn);
    return requestJSON(API.submit(ctx.sn), { method: 'POST' }).then(function (res) {
      var payload = obj(res);
      var info = exitInfo(payload.exit_code);
      var caveat = '注意：手动提交不回写本地 state 文件，当日状态由调度器状态机维护，看板刷新后可能仍是提交前的快照。';
      var msg = info.title + '：' + info.hint +
        (str(payload.summary) ? ' 服务端摘要：' + str(payload.summary) : '') + ' ' + caveat;
      showToast({
        type: info.key === 'error' || info.key === 'attention' ? 'error' : 'success',
        sticky: info.key === 'error',
        category: 'operation',
        title: info.key === 'error' ? '提交未成功' : '提交已执行（结果以接口返回为准）',
        message: msg,
        actions: info.key === 'error'
          ? [{ label: '查看日志', key: 'view-logs' }, { label: '重新提交', key: 'retry-submit', sn: ctx.sn }]
          : [{ label: '查看台账', key: 'view-ledger' }]
      });
      closeConfirm();
      // 重新取数仍有用（调度器可能刚更新过），但不承诺反映本次手动提交
      return fetchDashboard('full');
    }).catch(function (e) {
      var reason = (e && e.message) ? e.message : '未知错误';
      if (err) err.textContent = '提交失败：' + reason + '（提示保持至处理）';
      if (ok) { ok.disabled = false; ok.textContent = '确认提交'; }
      if (e instanceof ApiError && (e.status === 401 || e.status === 403)) setReadOnly(true);
      showToast({
        type: 'error', sticky: true, category: 'operation', title: '提交失败',
        message: reason + '。该端点不写运行日志，请重试或联系运维检查服务端；错误不会自动消失。',
        actions: [{ label: '查看日志', key: 'view-logs' }, { label: '重新提交', key: 'retry-submit', sn: ctx.sn }]
      });
    }).finally(function () { state.busy.delete(ctx.sn); });
  }

  function submitTokenForm(form) {
    var err = $('#tf-error');
    var sn = String(form.elements.student_num.value || '').trim();
    var token = String(form.elements.token.value || '').trim();
    var uid = String(form.elements.uid.value || '').trim();
    if (err) err.textContent = '';
    if (!sn || !token) {
      if (err) err.textContent = '学号与 token 均为必填；token 不会回显或写入页面。';
      return;
    }
    if (state.readOnly) {
      if (err) err.textContent = '只读模式：当前会话无写权限。';
      return;
    }
    var btn = form.querySelector('button[type="submit"]');
    if (btn) { btn.disabled = true; btn.textContent = '提交中…'; }
    var body = { student_num: sn, token: token };
    if (uid) body.uid = uid;
    requestJSON(API.token, { method: 'POST', body: body }).then(function (res) {
      var payload = obj(res);
      form.elements.token.value = '';
      showToast({
        type: 'success', title: '凭证已下发',
        message: str(payload.msg || '服务端已接受凭证') + '。旧凭证立即失效，界面不会展示完整 token。',
        actions: [{ label: '查看台账', key: 'view-ledger' }]
      });
      closeDrawer();
      return fetchDashboard('full');
    }).catch(function (e) {
      var reason = (e && e.message) ? e.message : '未知错误';
      if (err) err.textContent = '凭证下发失败：' + reason + '（提示保持至处理）';
      if (e instanceof ApiError && (e.status === 401 || e.status === 403)) setReadOnly(true);
      showToast({
        type: 'error', sticky: true, category: 'operation', title: '凭证下发失败',
        message: reason + '。请检查学号与 token 后重试，或联系运维。',
        actions: [{ label: '重新打开凭证表单', key: 'open-cred', sn: sn }]
      });
    }).finally(function () {
      form.elements.token.value = '';
      if (btn) { btn.disabled = false; btn.textContent = '提交更新'; }
    });
  }

  /* ----------------------------------------------------------
     13. Toast（成功 3–5s；失败保持至处理）
     ---------------------------------------------------------- */
  function showToast(cfg) {
    var region = $('#toast-region');
    if (!region) return;
    var id = ++toastSeq;
    var el = document.createElement('div');
    el.className = 'toast toast--' + (cfg.type || 'info');
    el.setAttribute('role', cfg.type === 'error' ? 'alert' : 'status');
    el.dataset.toastId = String(id);
    // data = 取数失败（重试成功即视为已处理）；operation = 写操作失败（必须保持至用户处理）
    el.dataset.toastCategory = cfg.category || 'data';
    var actions = cfg.actions || [];
    toastActions.set(id, actions);
    var actionHtml = actions.length
      ? '<div class="toast-actions">' + actions.map(function (a, i) {
          return '<button type="button" class="btn-secondary btn-mini" data-action="toast-action" data-toast-id="' + id +
            '" data-toast-index="' + i + '">' + esc(a.label) + '</button>';
        }).join('') + '</div>'
      : '';
    el.innerHTML = '<span class="toast-title">' + esc(cfg.title) + '</span>' +
      '<button type="button" class="toast-close" data-action="toast-close" data-toast-id="' + id +
        '" aria-label="关闭提示">×</button>' +
      '<span class="toast-msg">' + esc(cfg.message) + '</span>' + actionHtml;
    region.appendChild(el);
    if (!cfg.sticky) {
      toastTimers.set(id, setTimeout(function () { dismissToast(id); }, TOAST_MS));
    }
  }

  function dismissToast(id) {
    var t = toastTimers.get(id);
    if (t) clearTimeout(t);
    toastTimers.delete(id);
    toastActions.delete(id);
    var el = $$('#toast-region .toast').filter(function (n) { return n.dataset.toastId === String(id); })[0];
    if (el) el.remove();
  }

  // 取数重试成功即视为「已处理」；写操作失败提示必须保持，不能被刷新冲掉
  function clearErrorToasts() {
    $$('#toast-region .toast--error[data-toast-category="data"]').forEach(function (el) {
      dismissToast(Number(el.dataset.toastId));
    });
  }

  function handleToastAction(id, index) {
    var actions = toastActions.get(Number(id)) || [];
    var a = actions[Number(index)];
    if (!a) return;
    var key = a.key;
    if (key === 'refresh') fetchDashboard('full');
    else if (key === 'view-logs') setView('logs');
    else if (key === 'view-ledger') setView('accounts');
    else if (key === 'view-events') {
      setView('overview');
      var target = $('#m11-title');
      if (target) target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    } else if (key === 'retry-submit') openConfirm('submit', a.sn);
    else if (key === 'open-cred') openDrawer(a.sn, 'cred');
    dismissToast(Number(id));
  }

  /* ----------------------------------------------------------
     14. 视图 / URL 状态 / 筛选 / 排序 / Tab 键盘
     ---------------------------------------------------------- */
  function readURLState() {
    var p = new URLSearchParams(location.search);
    var v = p.get('view');
    if (v && Object.prototype.hasOwnProperty.call(VIEWS, v)) state.view = v;
    var q = p.get('q');
    if (q) state.filter = q;
    var lv = p.get('level');
    if (lv && LEVEL_KEYS.indexOf(lv) !== -1) state.logLevel = lv;
    var sk = p.get('sort');
    if (sk && SORT_KEYS.indexOf(sk) !== -1) state.sort.key = sk;
    var sd = p.get('dir');
    if (sd === 'asc' || sd === 'desc') state.sort.dir = sd;
  }

  function syncURL(push) {
    var p = new URLSearchParams();
    if (state.view !== 'overview') p.set('view', state.view);
    if (state.filter) p.set('q', state.filter);
    if (state.logLevel !== 'all') p.set('level', state.logLevel);
    if (state.sort.key !== DEFAULT_SORT.key || state.sort.dir !== DEFAULT_SORT.dir) {
      p.set('sort', state.sort.key);
      p.set('dir', state.sort.dir);
    }
    var qs = p.toString();
    var url = location.pathname + (qs ? '?' + qs : '');
    try {
      if (push) history.pushState({ view: state.view }, '', url);
      else history.replaceState({ view: state.view }, '', url);
    } catch (e) { /* 忽略受限环境 */ }
  }

  function syncInputs() {
    ['#account-filter', '#account-filter-full'].forEach(function (sel) {
      var el = $(sel);
      if (el) el.value = state.filter;
    });
    ['#clear-filter', '#clear-filter-full'].forEach(function (sel) {
      var el = $(sel);
      if (el) el.hidden = !state.filter;
    });
    $$('#log-level-tabs [data-level]').forEach(function (t) {
      var active = t.getAttribute('data-level') === state.logLevel;
      t.classList.toggle('is-active', active);
      t.setAttribute('aria-selected', active ? 'true' : 'false');
      t.tabIndex = active ? 0 : -1;
    });
    var cb = $('#log-autoscroll');
    if (cb) cb.checked = state.autoScroll;
  }

  function setView(view, opts) {
    var o = opts || {};
    if (!Object.prototype.hasOwnProperty.call(VIEWS, view)) view = 'overview';
    state.view = view;
    $$('[data-view-panel]').forEach(function (panel) {
      panel.hidden = panel.getAttribute('data-view-panel') !== view;
    });
    $$('.nav-item[data-view]').forEach(function (btn) {
      var active = btn.getAttribute('data-view') === view;
      btn.classList.toggle('is-active', active);
      if (active) btn.setAttribute('aria-current', 'page');
      else btn.removeAttribute('aria-current');
    });
    var crumb = $('#crumb-current');
    if (crumb) crumb.textContent = VIEWS[view];
    document.title = VIEWS[view] + ' · CTBU 乐跑主页管理系统';
    closeMobileNav();
    if (o.sync !== false) syncURL(o.push !== false);
    if (o.focus !== false) {
      var main = $('#main');
      if (main) { main.scrollTop = 0; main.focus({ preventScroll: true }); }
    }
    if (state.data && view === 'logs') renderLogsRegion(state.data);
    if (state.data && view === 'accounts') renderAccountsFullRegion(state.data);
  }

  function setLogLevel(level, push) {
    if (LEVEL_KEYS.indexOf(level) === -1) level = 'all';
    state.logLevel = level;
    syncInputs();
    if (state.data) renderLogsRegion(state.data);
    if (push !== false) syncURL(false);
  }

  function closeMobileNav() {
    document.body.classList.remove('nav-open');
    var btn = $('#nav-toggle');
    if (btn) btn.setAttribute('aria-expanded', 'false');
  }

  function applyFilter(value) {
    state.filter = String(value || '').trim();
    syncInputs();
    if (state.data) { renderAccountTableRegion(state.data); renderAccountsFullRegion(state.data); }
    syncURL(false);
  }

  function tabKeyHandler(container, attrName, onSelect) {
    return function (ev) {
      var tabs = $$('[data-' + attrName + ']', container).filter(function (t) { return !t.hidden; });
      if (!tabs.length) return;
      var current = tabs.indexOf(document.activeElement);
      var next = -1;
      if (ev.key === 'ArrowRight') next = (current + 1) % tabs.length;
      else if (ev.key === 'ArrowLeft') next = (current - 1 + tabs.length) % tabs.length;
      else if (ev.key === 'Home') next = 0;
      else if (ev.key === 'End') next = tabs.length - 1;
      else return;
      ev.preventDefault();
      tabs[next].focus();
      onSelect(tabs[next].getAttribute('data-' + attrName));
    };
  }

  /* ----------------------------------------------------------
     15. 事件绑定
     ---------------------------------------------------------- */
  function handleAction(btn) {
    var action = btn.getAttribute('data-action');
    var sn = btn.getAttribute('data-sn');
    switch (action) {
      case 'refresh': case 'retry-live':
        fetchDashboard('full'); break;
      case 'new-account':
        openDrawer(null, 'cred', btn); break;
      case 'detail':
        openDrawer(sn, 'base', btn); break;
      case 'cred':
        openDrawer(sn, 'cred', btn); break;
      case 'more':
        openDrawer(sn, 'ops', btn); break;
      case 'submit':
        openConfirm('submit', sn); break;
      case 'goto':
        setView(btn.getAttribute('data-view')); break;
      case 'clear-filter': case 'clear-filter-full':
        applyFilter(''); break;
      case 'batch-clear':
        state.selected.clear(); renderAccountsFullRegion(state.data); break;
      case 'sort': {
        var key = btn.getAttribute('data-sort');
        if (SORT_KEYS.indexOf(key) === -1) break;
        if (state.sort.key === key) state.sort.dir = state.sort.dir === 'asc' ? 'desc' : 'asc';
        else state.sort = { key: key, dir: 'asc' };
        if (state.data) { renderAccountTableRegion(state.data); renderAccountsFullRegion(state.data); }
        syncURL(false);
        break;
      }
      case 'drawer-tab':
        setDrawerTab(btn.getAttribute('data-tab')); break;
      case 'token-cancel': {
        var form = $('#token-form');
        if (form) { form.elements.token.value = ''; form.elements.uid.value = ''; }
        break;
      }
      case 'toast-close':
        dismissToast(Number(btn.getAttribute('data-toast-id'))); break;
      case 'toast-action':
        handleToastAction(btn.getAttribute('data-toast-id'), btn.getAttribute('data-toast-index')); break;
      default: break;
    }
  }

  function bindEvents() {
    addListener(document, 'click', function (ev) {
      var target = ev.target;
      var navToggle = target.closest && target.closest('#nav-toggle');
      if (navToggle) {
        var open = !document.body.classList.contains('nav-open');
        document.body.classList.toggle('nav-open', open);
        navToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
        return;
      }
      if (document.body.classList.contains('nav-open') &&
          target === document.body && !target.closest('#sidebar')) {
        closeMobileNav();
        return;
      }
      var navBtn = target.closest && target.closest('.nav-item[data-view]');
      if (navBtn) { setView(navBtn.getAttribute('data-view')); return; }
      var tab = target.closest && target.closest('#drawer-tabs [data-drawer-tab]');
      if (tab) { setDrawerTab(tab.getAttribute('data-drawer-tab')); return; }
      var levelTab = target.closest && target.closest('#log-level-tabs [data-level]');
      if (levelTab) { setLogLevel(levelTab.getAttribute('data-level')); return; }
      var btn = target.closest && target.closest('[data-action]');
      if (btn && !btn.disabled && btn.getAttribute('aria-disabled') !== 'true') handleAction(btn);
    });

    addListener(document, 'change', function (ev) {
      var t = ev.target;
      if (!t) return;
      if (t.id === 'account-filter' || t.id === 'account-filter-full') return;
      if (t.getAttribute && t.getAttribute('data-action') === 'select') {
        var sn = t.getAttribute('data-sn');
        if (t.checked) state.selected.add(sn); else state.selected.delete(sn);
        renderAccountsFullRegion(state.data);
      } else if (t.id === 'log-autoscroll') {
        state.autoScroll = !!t.checked;
        if (state.autoScroll && state.data) renderLogsRegion(state.data);
      }
    });

    var debounceTimer = null;
    var onFilterInput = function (ev) {
      clearTimeout(debounceTimer);
      var value = ev.target.value;
      debounceTimer = setTimeout(function () { applyFilter(value); }, DEBOUNCE_MS);
      timers.debounce = debounceTimer;
    };
    ['#account-filter', '#account-filter-full'].forEach(function (sel) {
      var el = $(sel);
      if (el) addListener(el, 'input', onFilterInput);
    });

    addListener(document, 'submit', function (ev) {
      if (ev.target && ev.target.id === 'token-form') {
        ev.preventDefault();
        submitTokenForm(ev.target);
      }
      if (ev.target && ev.target.id === 'confirm-form') ev.preventDefault();
    });

    var manual = $('#manual-refresh');
    if (manual) addListener(manual, 'click', function () { fetchDashboard('full'); });
    var bannerRetry = $('#banner-retry');
    if (bannerRetry) addListener(bannerRetry, 'click', function () { fetchDashboard('full'); });
    var tokenBtn = $('#open-token-form');
    if (tokenBtn) addListener(tokenBtn, 'click', function () { openDrawer(null, 'cred', tokenBtn); });
    var drawerClose = $('#drawer-close');
    if (drawerClose) addListener(drawerClose, 'click', closeDrawer);
    var confirmCancel = $('#confirm-cancel');
    if (confirmCancel) addListener(confirmCancel, 'click', closeConfirm);
    var confirmOk = $('#confirm-ok');
    if (confirmOk) addListener(confirmOk, 'click', runSubmit);

    var drawer = $('#account-drawer');
    if (drawer) {
      addListener(drawer, 'close', function () {
        state.drawer = null;
        if (state.drawerTrigger && state.drawerTrigger.isConnected) state.drawerTrigger.focus();
        state.drawerTrigger = null;
      });
    }
    var confirmDlg = $('#confirm-dialog');
    if (confirmDlg) addListener(confirmDlg, 'close', function () { state.confirm = null; });

    var drawerTabs = $('#drawer-tabs');
    if (drawerTabs) {
      addListener(drawerTabs, 'keydown', tabKeyHandler(drawerTabs, 'drawer-tab', setDrawerTab));
    }
    var levelTabs = $('#log-level-tabs');
    if (levelTabs) {
      addListener(levelTabs, 'keydown', tabKeyHandler(levelTabs, 'level', function (lv) { setLogLevel(lv); }));
    }

    addListener(document, 'keydown', function (ev) {
      if (ev.key === 'Escape' && document.body.classList.contains('nav-open')) closeMobileNav();
    });
    addListener(window, 'popstate', function () {
      state.view = 'overview';
      state.filter = '';
      state.logLevel = 'all';
      state.sort = { key: DEFAULT_SORT.key, dir: DEFAULT_SORT.dir };
      readURLState();
      syncInputs();
      setView(state.view, { sync: false, focus: false, push: false });
      if (state.data) { renderAccountTableRegion(state.data); renderAccountsFullRegion(state.data); renderLogsRegion(state.data); }
    });
    addListener(window, 'pagehide', teardown);
  }

  /* ----------------------------------------------------------
     16. 刷新循环与清理
     ---------------------------------------------------------- */
  function tick() {
    renderClock();
    updateCountdown();
    if (!document.hidden && Date.now() >= nextRefreshAt) {
      nextRefreshAt = Date.now() + REFRESH_MS;
      // 仅 M11「最近执行动态」局部刷新；服务器时间由本 tick 常显推进
      fetchDashboard('events');
    }
  }

  function teardown() {
    if (destroyed) return;
    destroyed = true;
    if (timers.clock) clearInterval(timers.clock);
    if (timers.debounce) clearTimeout(timers.debounce);
    if (activeController) activeController.abort();
    toastTimers.forEach(function (t) { clearTimeout(t); });
    toastTimers.clear();
    toastActions.clear();
    listeners.forEach(function (l) { l.target.removeEventListener(l.type, l.fn, l.opts); });
    listeners.length = 0;
  }

  function init() {
    readURLState();
    REFRESH_MS = cssDuration('--refresh-interval', 30000);
    TOAST_MS = cssDuration('--toast-duration', 4000);
    bindEvents();
    syncInputs();
    setView(state.view, { sync: false, focus: false, push: false });
    nextRefreshAt = Date.now() + REFRESH_MS;
    timers.clock = setInterval(tick, 1000);
    updateCountdown();
    fetchDashboard('full');
  }

  if (document.readyState === 'loading') {
    addListener(document, 'DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
})();
