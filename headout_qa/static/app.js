
let busy = false;
let lastScenarioCount = -1;
let lastBookingsPreview = [];
const $ = (id) => document.getElementById(id);
const _statCache = {};
function setStat(id, value) {
  const el = $(id);
  if (_statCache[id] === value) return;
  _statCache[id] = value;
  el.textContent = value;
  el.classList.remove('pop'); void el.offsetWidth; el.classList.add('pop');
}
const RING_CIRCUMFERENCE = 2 * Math.PI * 50;
let _lastPct = undefined;
function updateRing(pct) {
  if (_lastPct === pct) return;
  _lastPct = pct;
  const ring = $('ringFill');
  const pctEl = $('pct');
  if (pct === null) {
    ring.style.strokeDashoffset = RING_CIRCUMFERENCE;
    ring.classList.remove('tier-good', 'tier-ok', 'tier-bad');
    pctEl.innerHTML = '<span class="rh-dash">&#8212;</span>';
    return;
  }
  const offset = RING_CIRCUMFERENCE * (1 - pct / 100);
  ring.style.strokeDashoffset = offset;
  ring.classList.toggle('tier-good', pct >= 80);
  ring.classList.toggle('tier-ok', pct >= 50 && pct < 80);
  ring.classList.toggle('tier-bad', pct < 50);
  pctEl.textContent = pct + '%';
  pctEl.classList.remove('pop'); void pctEl.offsetWidth; pctEl.classList.add('pop');
}

const EMPTY_RESULTS =
  '<div class="empty-state"><div class="empty-ico"><svg class="ico" viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg></div><p>No run yet</p>' +
  '<span>Press <b>Start simulation</b> to test your scenarios against the live agent.</span></div>';

/* ── Brand hover animation ── */
(() => {
  const mark = $('brandMark');
  const video = mark && mark.querySelector('.brand-video');
  if (!mark || !video) return;
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  mark.addEventListener('mouseenter', () => { video.currentTime = 0; video.play().catch(() => {}); });
  mark.addEventListener('mouseleave', () => { video.pause(); video.currentTime = 0; });
})();

/* ── Drawer ── */
function openDrawer() { $('drawer').classList.add('open'); $('scrim').classList.add('open'); document.body.style.overflow = 'hidden'; }
function closeDrawer() { $('drawer').classList.remove('open'); $('scrim').classList.remove('open'); document.body.style.overflow = ''; }
$('settingsBtn').addEventListener('click', openDrawer);
$('drawerClose').addEventListener('click', closeDrawer);
$('scrim').addEventListener('click', closeDrawer);
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeDrawer(); });

/* ── Toasts ── */
function toast(msg, type) {
  const t = document.createElement('div');
  t.className = 'toast ' + (type || '');
  t.textContent = msg;
  $('toasts').appendChild(t);
  setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 320); }, 3800);
}

/* ── Status ── */
async function refresh() {
  let s;
  try {
    const res = await fetch('/api/status');
    s = await res.json();
  } catch (e) { return; }

  const textVals = [
    ['sheetIdInput', s.sheet.id],
    ['llmModel', s.llm.model],
    ['zkSubdomain', s.zendesk.subdomain],
    ['zkEmail', s.zendesk.user_email],
    ['zkBookingField', s.zendesk.booking_field_id],
    ['zkEmailField', s.zendesk.email_field_id],
    ['scBaseUrl', s.sunshine.base_url],
    ['scAppId', s.sunshine.app_id],
    ['scKeyId', s.sunshine.key_id],
    ['scSwitchboard', s.sunshine.switchboard_id],
  ];
  for (const [id, v] of textVals) {
    const el = $(id);
    if (document.activeElement !== el) el.value = v || '';
  }

  $('sheetInfo').innerHTML =
    `<span class="id">${s.sheet.id}</span> &middot; source ${s.sheet.source} &middot; tab ${s.sheet.bookings_tab}` +
    (s.sheet.scenarios_tab ? ' + ' + s.sheet.scenarios_tab : '') +
    ` &mdash; <a href="${s.sheet.url}" target="_blank">open sheet</a>`;

  setStat('total', s.total);
  setStat('inprog', s.running ? Math.max(0, s.total - s.done) : 0);
  setStat('passed', s.passed);
  setStat('failed', s.failed);
  setStat('escalated', s.escalated);
  updateRing(s.total ? Math.round((s.passed / s.total) * 100) : null);

  const headTag = $('headTag');
  headTag.className = 'head-tag' + (s.running ? ' run' : s.report_ready ? ' done' : '');
  headTag.innerHTML = '<span class="dot"></span>' + (s.running ? `Running ${s.done}/${s.total}` : s.report_ready ? 'Report ready' : 'Idle');

  $('startBtn').disabled = s.running || busy;
  $('stopBtn').disabled = !s.running;
  $('startBtn').classList.toggle('running', !!s.running);
  $('startBtn').textContent = s.running ? 'Running...' : 'Start simulation';
  const clrBtn = $('clearBtn');
  if (clrBtn) clrBtn.disabled = s.running || (!s.total && !s.scenarios.length);
  document.title = s.running ? `Running ${s.done}/${s.total} · Headout AI Agent QA` : 'Headout AI Agent QA';
  const link = $('reportLink');
  link.style.display = s.report_ready ? 'inline-flex' : 'none';
  const badge = $('runBadge');
  badge.style.display = s.run_id ? 'inline-flex' : 'none';
  badge.textContent = s.run_id || '';

  const prog = $('progressWrap');
  prog.classList.toggle('active', !!s.running);
  $('progressFill').style.width = s.total ? Math.round((s.done / s.total) * 100) + '%' : '0%';

  const llm = s.llm.key_set
    ? `<span class="pill on" title="LLM API key configured"><span class="dot"></span>LLM: ${s.llm.model || s.llm.provider}</span>`
    : `<span class="pill off" title="No LLM key - deterministic scripted engine used"><span class="dot"></span>LLM: scripted</span>`;

  const llmKeyEl = $('llmKey');
  if (document.activeElement !== llmKeyEl) {
    llmKeyEl.placeholder = s.llm.key_set ? '•••••••• (saved — leave blank to keep)' : 'sk-...';
  }
  const zk = s.zendesk.basic_auth_configured
    ? `<a class="pill on" href="${s.zendesk.agent_url}" target="_blank" title="Ticket lookup as: ${escapeHtml(s.zendesk.user_email || 'no email set')}"><span class="dot"></span>Zendesk: ${s.zendesk.subdomain || 'configured'}</a>`
    : `<span class="pill off" title="Set credentials in Settings to enable ticket lookup"><span class="dot"></span>ticket lookup off</span>`;
  const state = s.running
    ? `<span class="pill pulsing" title="Simulation is running"><span class="dot"></span>running &middot; ${s.done}/${s.total}</span>`
    : (s.report_ready
      ? `<span class="pill on"><span class="dot"></span>report ready</span>`
      : `<span class="pill"><span class="dot"></span>idle</span>`);
  const statusHtml = state + ' ' + llm + ' ' + zk +
    (s.error ? '<span class="pill off">' + escapeHtml(s.error) + '</span>' : '');
  if ($('status').dataset.sig !== statusHtml) {
    $('status').innerHTML = statusHtml;
    $('status').dataset.sig = statusHtml;
  }

  const scenarioSig = s.scenarios.map(r => r.scenario_id + ':' + r.status + ':' + r.passed + ':' + r.escalated).join('|');
  if (lastScenarioCount !== scenarioSig) {
    renderResults(s.scenarios);
    renderSummary(s.scenarios);
  }
  lastScenarioCount = scenarioSig;
}

const FACT_LABELS = { cancellable: 'cancellability', reschedulable: 'reschedulability', extendable: 'extendability', ticket: 'ticket delivery' };
const REASON_LABELS = {
  fact_cancellable: 'wrong cancellability answer', fact_reschedulable: 'wrong reschedulability answer',
  fact_extendable: 'wrong extendability answer',
  missing_fact_cancellable: 'never stated whether cancellable', missing_fact_reschedulable: 'never stated whether reschedulable',
  missing_fact_extendable: 'never stated extendability', missing_fact_ticket: 'never answered ticket question',
  not_a_dead_end: 'dead-end - bot could not answer',
  bot_replied: 'no bot reply', language: 'language / noise issues',
};

function renderSummary(scenarios) {
  const box = $('summary');
  if (!scenarios || !scenarios.length) { box.innerHTML = ''; return; }
  const total = scenarios.length;
  const passed = scenarios.filter(r => r.passed === true);
  const failed = scenarios.filter(r => r.passed === false);
  const esc = scenarios.filter(r => r.escalated);
  const other = total - passed.length - failed.length - esc.length;

  let html = '<p style="font-size:14px;margin:0 0 14px;font-weight:600;color:var(--ink2);">' +
    'Out of <b>' + total + '</b> scenario' + (total === 1 ? '' : 's') + ', ' +
    '<span style="color:var(--green-deep);"><b>' + passed.length + ' passed</b></span>' +
    (failed.length ? ', <span style="color:var(--red-deep);"><b>' + failed.length + ' failed</b></span>' : '') +
    (esc.length ? ' and <span style="color:var(--purple-deep);"><b>' + esc.length + ' escalated</b></span>' : '') +
    (other ? ' <span style="color:var(--muted);font-weight:500;">(' + other + ' incomplete)</span>' : '') +
    '.</p>';

  if (failed.length) {
    const counts = {};
    failed.forEach(r => (r.reasons || []).forEach(rs => {
      const type = rs.includes(':') ? rs.split(':')[0] : 'other';
      const label = REASON_LABELS[type] || (type === 'other' ? rs : type.replace(/_/g, ' '));
      counts[label] = (counts[label] || 0) + 1;
    }));
    const rows = Object.entries(counts)
      .map(([label, n]) => '<div>&middot; <span class="count">' + n + 'x</span> ' + escapeHtml(label) + '</div>')
      .join('');
    html += '<div class="summary-card fail-card"><h4>Failures (' + failed.length + ') &mdash; combined</h4>' + rows + '</div>';
  }

  if (passed.length) {
    const facts = new Set();
    passed.forEach(r => (r.passed_facts || []).forEach(f => facts.add(f)));
    let line;
    if (facts.size) {
      const labels = [...facts].map(f => FACT_LABELS[f] || f).join(', ');
      line = 'All ' + passed.length + ' pass' + (passed.length === 1 ? 'es' : '') + ' answered correctly on: <b>' + escapeHtml(labels) + '</b> &mdash; no policy contradictions, no errors.';
    } else {
      line = 'All ' + passed.length + ' pass' + (passed.length === 1 ? 'es' : '') + ' delivered clean, helpful replies with no policy contradictions or errors.';
    }
    html += '<div class="summary-card pass-card"><h4>Passed (' + passed.length + ') &mdash; combined</h4>' + line + '</div>';
  }
  box.innerHTML = html;
}

function verdictClass(r) {
  if (r.escalated) return ['b-esc', 'Escalated'];
  if (r.passed === true) return ['b-ok', 'PASS'];
  if (r.passed === false) return ['b-bad', 'FAIL'];
  return ['b-warn', (r.status || '').toUpperCase()];
}

function renderResults(scenarios) {
  const box = $('results');
  const insight = $('insight');
  if (!scenarios || !scenarios.length) {
    box.innerHTML = EMPTY_RESULTS;
    insight.style.display = 'none';
    insight.innerHTML = '';
    box._scenarios = [];
    return;
  }
  const counts = { total: scenarios.length, pass: 0, fail: 0, esc: 0, other: 0 };
  const seenTouchpoints = new Set();
  for (const r of scenarios) {
    if (r.passed === true) counts.pass++;
    else if (r.passed === false) counts.fail++;
    else if (r.escalated) counts.esc++;
    else counts.other++;
    if (r.l1) seenTouchpoints.add([r.l1, r.l2, r.l3].filter(Boolean).join(' › '));
  }
  const coverage = touchpointCoverageText(seenTouchpoints);
  insight.style.display = 'block';
  insight.innerHTML =
    '<strong>Summary:</strong> ' + counts.pass + ' passed, ' + counts.fail + ' failed, ' + counts.esc + ' escalated, ' + counts.other + ' incomplete of ' + counts.total + '. ' +
    (counts.fail ? 'Failures and escalations are expanded below - click a row for details.' : 'All scenarios are green. Tap a ticket ID to open it in Agent Workspace.') +
    (coverage ? ' ' + coverage : '');

  const rows = scenarios.map((r, i) => {
    const [cls, verdict] = verdictClass(r);
    const ticket = r.ticket_url
      ? '<a href="' + r.ticket_url + '" target="_blank" class="ticket-link">#' + r.ticket_id + '</a>'
      : '<span style="color:var(--subtle);font-size:12px;">&mdash;</span>';
    return '<tr class="row-toggle" data-i="' + i + '">' +
      '<td><span class="id-text" style="color:var(--ink2);">' + escapeHtml(r.scenario_id) + '</span></td>' +
      '<td><span class="id-text">' + escapeHtml(r.booking_id || '') + '</span></td>' +
      '<td>' + touchpointCell(r) + '</td>' +
      '<td><span class="badge ' + cls + '">' + verdict + '</span></td>' +
      '<td>' + ticket + '</td>' +
      '<td class="checks-cell">' + checksCell(r) + '</td>' +
      '<td class="dtoggle"><span class="chev">&#9654;</span></td>' +
      '</tr>';
  }).join('');

  box.innerHTML = '<table>' +
    '<thead><tr><th>Scenario</th><th>Booking</th><th>Touchpoint</th><th>Result</th><th>Ticket</th><th>Checks</th><th></th></tr></thead>' +
    '<tbody>' + rows + '</tbody></table>';
  box._scenarios = scenarios;
}

function touchpointCell(r) {
  if (r.l1) {
    const parts = [r.l1, r.l2, r.l3].filter(Boolean);
    return '<span class="node-tag" title="node: ' + escapeHtml(r.node || '') + '">' + escapeHtml(parts.join(' › ')) + '</span>';
  }
  return r.node ? '<span class="node-tag">' + escapeHtml(r.node) + '</span>' : '<span class="muted">&mdash;</span>';
}

function touchpointCoverageText(seenTouchpoints) {
  if (!lastBookingsPreview || !lastBookingsPreview.length) return '';
  const known = new Set();
  lastBookingsPreview.forEach(b => {
    if (b.l1) known.add([b.l1, b.l2, b.l3].filter(Boolean).join(' › '));
  });
  if (!known.size) return '';
  return 'Touchpoint coverage: <b>' + seenTouchpoints.size + '</b> of <b>' + known.size + '</b> known L1›L2›L3 combinations tested this run.';
}

function checksCell(r) {
  if (r.escalated) return '<span style="color:var(--purple-deep);font-size:12px;font-weight:600;">handoff to supervisor</span>';
  if (r.error) return '<span style="color:var(--red-deep);font-size:12px;" title="' + escapeHtml(r.error_detail || r.error) + '">' + escapeHtml(r.error) + '</span>';
  if (!r.checks || !r.checks.length) return '<span class="empty">&mdash;</span>';
  const chips = r.checks.map(c => {
    const cls = c.passed ? 'chip ok' : 'chip bad';
    const icon = c.passed ? '&#10003;' : '&#10005;';
    return '<span class="' + cls + '" title="' + escapeHtml(c.detail || c.name) + '">' + icon + ' ' + escapeHtml(c.name) + '</span>';
  }).join('');
  return '<div class="chips">' + chips + '</div>';
}

function buildDetail(r, i) {
  const parts = [];
  parts.push('<div class="d-meta">' +
    '<span><b>status</b> ' + (r.status ? escapeHtml(r.status) : '<span class="muted">&mdash;</span>') + '</span>' +
    '<span><b>node</b> ' + (r.node ? escapeHtml(r.node) : '<span class="muted">&mdash;</span>') + '</span>' +
    '<span><b>booking</b> ' + (r.booking_id ? escapeHtml(r.booking_id) : '<span class="muted">&mdash;</span>') + '</span>' +
    '<span><b>ticket</b> ' + (r.ticket_url
      ? '<a href="' + r.ticket_url + '" target="_blank">#' + r.ticket_id + '</a>'
      : '&mdash;') + '</span>' +
    '<span><b>checks</b> ' + (r.checks ? r.checks.length : 0) + '</span>' +
    '</div>');

  if (r.escalated) {
    parts.push('<div class="dline">Escalated to a live supervisor (human handoff) &mdash; grading skipped for this scenario.</div>');
  } else if (r.error) {
    parts.push('<div class="fcheck">' + escapeHtml(r.error) + '</div>');
    if (r.error_detail && r.error_detail !== r.error) {
      parts.push('<div class="dline muted mono" style="font-size:11px;">' + escapeHtml(r.error_detail) + '</div>');
    }
  } else if (r.checks && r.checks.length) {
    const failed = r.checks.filter(c => !c.passed);
    if (failed.length) {
      parts.push('<div class="dline"><b>' + failed.length + ' check' + (failed.length === 1 ? '' : 's') + ' failed:</b></div>');
      failed.forEach(c => parts.push('<div class="fcheck">' + escapeHtml(c.name) + ': ' + escapeHtml(c.detail) + '</div>'));
    }
    parts.push('<div class="chips">' + r.checks.map(c => {
      const cls = c.passed ? 'chip ok' : 'chip bad';
      const icon = c.passed ? '&#10003;' : '&#10005;';
      return '<span class="' + cls + '">' + icon + ' ' + escapeHtml(c.name) + '</span>';
    }).join('') + '</div>');
  } else {
    parts.push('<div class="dline">No automated checks recorded for this scenario.</div>');
  }
  parts.push(
    '<button class="chat-toggle" id="chatToggle-' + i + '" type="button">' +
    '<svg class="ico" viewBox="0 0 24 24"><polyline points="9 18 15 12 9 6"/></svg>View conversation</button>' +
    '<div class="chat-slot" id="chatSlot-' + i + '" style="display:none;"></div>'
  );
  return '<div class="detail-inner">' + parts.join('') + '</div>';
}

function roleClass(role) { return role === 'user' ? 'from-user' : role === 'bot' ? 'from-bot' : 'system'; }

function renderChatThread(data) {
  const parts = [];
  if (data.scenario_text) {
    parts.push('<div class="chat-context"><b>Scenario:</b> ' + escapeHtml(data.scenario_text) + '</div>');
  }
  if (data.pass_criteria && data.pass_criteria.length) {
    parts.push('<div class="chat-context"><b>Pass criteria:</b> ' + escapeHtml(data.pass_criteria.join('; ')) + '</div>');
  }
  const transcript = data.transcript;
  if (!transcript || !transcript.length) {
    parts.push('<div class="chat-empty">No conversation was recorded for this scenario.</div>');
    return parts.join('');
  }
  const rows = transcript.map(e => {
    const time = e.ts ? new Date(e.ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '';
    if (e.role === 'user' || e.role === 'bot') {
      return '<div class="chat-row from-' + e.role + '"><div class="chat-bubble">' +
        escapeHtml(e.text || '') +
        (time ? '<span class="chat-time">' + time + '</span>' : '') +
        '</div></div>';
    }
    const escalated = /escalat/i.test(e.text || '');
    return '<div class="chat-system' + (escalated ? ' escalated' : '') + '">' + escapeHtml(e.text || '') + '</div>';
  }).join('');
  parts.push('<div class="chat-thread">' + rows + '</div>');
  return parts.join('');
}

async function loadTranscript(scenarioId, slotId, toggleId) {
  const slot = document.getElementById(slotId);
  const toggle = document.getElementById(toggleId);
  if (!slot || !toggle) return;
  const isOpen = slot.style.display !== 'none';
  if (isOpen) {
    slot.style.display = 'none';
    toggle.classList.remove('open');
    return;
  }
  slot.style.display = 'block';
  toggle.classList.add('open');
  if (slot.dataset.loaded) return;
  slot.innerHTML = '<div class="chat-loading">Loading conversation&hellip;</div>';
  try {
    const res = await fetch('/api/transcript/' + encodeURIComponent(scenarioId));
    if (!res.ok) throw new Error('not available');
    const data = await res.json();
    slot.innerHTML = renderChatThread(data);
    slot.dataset.loaded = '1';
  } catch (e) {
    slot.innerHTML = '<div class="chat-empty">Couldn\'t load the conversation for this scenario.</div>';
  }
}

$('results').addEventListener('click', (e) => {
  if (e.target.closest('a')) return;
  const tr = e.target.closest('tr.row-toggle');
  if (!tr || tr.dataset.i === undefined) return;
  const box = $('results');
  const r = box._scenarios[+tr.dataset.i];
  const detId = 'rowdetail-' + tr.dataset.i;
  const existing = document.getElementById(detId);
  if (existing) { existing.remove(); tr.classList.remove('row-open'); return; }
  tr.classList.add('row-open');
  const idx = tr.dataset.i;
  const det = document.createElement('tr');
  det.id = detId;
  det.className = 'detail-row';
  det.innerHTML = '<td colspan="7">' + buildDetail(r, idx) + '</td>';
  tr.insertAdjacentElement('afterend', det);
  const toggle = document.getElementById('chatToggle-' + idx);
  if (toggle) {
    toggle.addEventListener('click', () => loadTranscript(r.scenario_id, 'chatSlot-' + idx, 'chatToggle-' + idx));
  }
});

/* ── Run controls ── */
function getFilters() {
  const v = (id) => { const el = $(id); return el && el.value ? el.value : null; };
  return { l1: v('fL1'), l2: v('fL2'), l3: v('fL3') };
}
function populateFilters(filters) {
  const fill = (selId, items) => {
    const sel = $(selId);
    if (!sel) return;
    const cur = sel.value;
    sel.innerHTML = '';
    const all = document.createElement('option');
    all.value = '';
    all.textContent = selId === 'fL1' ? 'All L1' : selId === 'fL2' ? 'All L2' : 'All L3';
    sel.appendChild(all);
    (items || []).forEach(it => {
      const o = document.createElement('option');
      o.value = it.value;
      o.textContent = it.value + ' (' + it.count + ')';
      sel.appendChild(o);
    });
    if (cur && [...sel.options].some(o => o.value === cur)) sel.value = cur;
    else sel.value = '';
  };
  fill('fL1', filters && filters.l1);
  fill('fL2', filters && filters.l2);
  fill('fL3', filters && filters.l3);
  updateFilterPreview();
}
function updateFilterPreview() {
  const f = getFilters();
  const badge = $('filterPreview');
  if (!badge) return;
  const active = [f.l1, f.l2, f.l3].filter(Boolean);
  if (!active.length) { badge.style.display = 'none'; badge.textContent = ''; return; }
  let count = lastBookingsPreview.length;
  if (count) {
    count = lastBookingsPreview.filter(b => {
      if (f.l1 && (f.l1 === '(blank)' ? b.l1 !== '' : b.l1 !== f.l1)) return false;
      if (f.l2 && (f.l2 === '(blank)' ? b.l2 !== '' : b.l2 !== f.l2)) return false;
      if (f.l3 && (f.l3 === '(blank)' ? b.l3 !== '' : b.l3 !== f.l3)) return false;
      return true;
    }).length;
    badge.textContent = count + ' / ' + lastBookingsPreview.length + ' scenarios';
  } else {
    badge.textContent = active.join(' · ');
  }
  badge.style.display = 'inline-flex';
}
async function start() {
  busy = true;
  const f = getFilters();
  const body = {
    llm_api_key: $('llmKey').value || null,
    llm_model: $('llmModel').value || null,
    l1: f.l1,
    l2: f.l2,
    l3: f.l3,
  };
  const res = await fetch('/api/start', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!data.ok) {
    toast(data.error || 'could not start simulation', 'err');
  } else {
    toast('Simulation started', 'ok');
  }
  busy = false;
  await refresh();
}

async function stop() {
  await fetch('/api/stop', { method: 'POST' });
  toast('Run stopped', 'ok');
  await refresh();
}

async function clearResults() {
  if (!confirm('Clear all results and start fresh? This will wipe the current run summary and table.')) return;
  const btn = $('clearBtn');
  btn.disabled = true;
  btn.textContent = 'Clearing...';
  try {
    const res = await fetch('/api/clear', { method: 'POST' });
    const d = await res.json();
    if (!d.ok) toast(d.error || 'could not clear', 'err');
    else toast('Results cleared — ready for a fresh run', 'ok');
    await refresh();
  } catch (e) { toast('Clear failed: ' + e, 'err'); }
  finally { btn.disabled = false; btn.textContent = '✕ Clear results'; }
}

async function refreshData(silent) {
  const btn = $('refreshBtn');
  const note = $('dataNote');
  btn.disabled = true;
  btn.textContent = 'Refreshing...';
  if (!silent) {
    note.className = 'databox loading';
    note.textContent = 'Fetching latest data from Google Sheets...';
  }
  try {
    const res = await fetch('/api/refresh', { method: 'POST' });
    const d = await res.json();
    if (d.ok) {
      const nodes = Object.entries(d.nodes || {}).map(([k, v]) => k + ': ' + v).join(' &middot; ');
      const statuses = Object.entries(d.statuses || {}).map(([k, v]) => k.toLowerCase() + ' ' + v).join(' &middot; ');
      note.className = 'databox ok';
      note.innerHTML = '&#10003; Loaded <b>' + d.bookings + '</b> bookings &rarr; <b>' + d.scenarios + '</b> scenarios &middot; nodes: ' + nodes + ' &middot; ' + statuses + ' &middot; cancellable ' + d.cancellable + '/' + d.bookings + ' &middot; reschedulable ' + d.reschedulable;
      lastBookingsPreview = d.bookings_preview || [];
      populateFilters(d.filters || {});
      updateFilterPreview();
      const box = $('results');
      if (box._scenarios && box._scenarios.length) renderResults(box._scenarios);
      if (!silent) toast('Loaded ' + d.scenarios + ' scenarios from the sheet', 'ok');
    } else {
      note.className = 'databox err';
      note.innerHTML = 'Refresh failed: ' + escapeHtml(d.error || 'unknown error');
      if (!silent) toast('Refresh failed: ' + (d.error || 'unknown error'), 'err');
    }
  } catch (e) {
    note.className = 'databox err';
    note.innerHTML = 'Refresh failed: ' + e;
    if (!silent) toast('Refresh failed: ' + e, 'err');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Refresh data';
  }
}

/* ── Settings saves ── */
async function saveLlm() {
  const btn = $('llmSave');
  const note = $('llmNote');
  btn.disabled = true;
  btn.textContent = 'Saving...';
  note.className = 'databox loading';
  note.textContent = 'Saving & verifying...';
  const body = {
    api_key: $('llmKey').value || null,
    model: $('llmModel').value.trim() || null,
  };
  try {
    const res = await fetch('/api/settings/llm', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const d = await res.json();
    if (!d.ok) {
      note.className = 'databox err';
      note.innerHTML = 'Save failed: ' + escapeHtml(d.error || 'unknown error');
      toast('Save failed: ' + (d.error || 'unknown error'), 'err');
    } else if (d.verified) {
      note.className = 'databox ok';
      note.innerHTML = '&#10003; Verified &mdash; <b>' + escapeHtml(d.model || '') + '</b> responded';
      toast('LLM judge active: ' + (d.model || ''), 'ok');
    } else {
      note.className = 'databox err';
      note.innerHTML = 'Saved, but verification failed: ' + escapeHtml(d.error || 'unknown error');
    }
    $('llmKey').value = '';
    await refresh();
  } catch (e) {
    note.className = 'databox err';
    note.innerHTML = 'Save failed: ' + e;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Save & verify';
  }
}

async function saveZendesk() {
  const btn = $('zkSave');
  const note = $('zkNote');
  btn.disabled = true;
  btn.textContent = 'Saving...';
  note.className = 'databox loading';
  note.textContent = 'Saving &amp; verifying connectivity...';
  const body = {
    subdomain: $('zkSubdomain').value.trim() || null,
    user_email: $('zkEmail').value.trim() || null,
    api_token: $('zkToken').value || null,
    booking_field_id: $('zkBookingField').value.trim() || null,
    email_field_id: $('zkEmailField').value.trim() || null,
  };
  try {
    const res = await fetch('/api/settings/zendesk', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const d = await res.json();
    if (!d.ok) {
      note.className = 'databox err';
      note.innerHTML = 'Save failed: ' + escapeHtml(d.error || 'unknown error');
      toast('Save failed: ' + (d.error || 'unknown error'), 'err');
    } else if (d.verified) {
      note.className = 'databox ok';
      note.innerHTML = '&#10003; Verified &mdash; connected as <b>' + escapeHtml(d.user || d.email || '') + '</b> on <b>' + escapeHtml(d.subdomain) + '</b>';
      toast('Connected to Zendesk: ' + (d.subdomain || ''), 'ok');
    } else {
      note.className = 'databox err';
      note.innerHTML = 'Saved to <b>' + escapeHtml(d.subdomain || '?') + '</b>, but verification failed: ' + escapeHtml(d.error || 'unknown error');
    }
    $('zkToken').value = '';
    await refresh();
  } catch (e) {
    note.className = 'databox err';
    note.innerHTML = 'Save failed: ' + e;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Save & verify';
  }
}

async function postSettings(btnId, noteId, body, okText) {
  const btn = $(btnId);
  const note = $(noteId);
  btn.disabled = true;
  btn.textContent = 'Saving...';
  note.className = 'databox loading';
  note.textContent = 'Saving settings...';
  try {
    const res = await fetch('/api/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const d = await res.json();
    if (!d.ok) {
      note.className = 'databox err';
      note.innerHTML = 'Save failed: ' + escapeHtml(d.error || 'unknown error');
      toast('Save failed: ' + (d.error || 'unknown error'), 'err');
    } else if (d.saved && d.saved.length) {
      note.className = 'databox ok';
      note.innerHTML = '&#10003; ' + okText + ' <span class="id-text">' + escapeHtml(d.saved.join(', ')) + '</span>';
      toast(okText + ' ' + d.saved.join(', '), 'ok');
    } else {
      note.className = 'databox';
      note.innerHTML = 'Nothing to save &mdash; all fields unchanged or blank.';
    }
    await refresh();
    return d;
  } catch (e) {
    note.className = 'databox err';
    note.innerHTML = 'Save failed: ' + e;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Save';
  }
}

async function saveSheetId() {
  const d = await postSettings(
    'sheetIdSave', 'sheetNote',
    { sheet_id: $('sheetIdInput').value.trim() || null },
    'Sheet updated:'
  );
  if (d && d.ok) refreshData(true);
}

async function saveSunshine() {
  const d = await postSettings(
    'scSave', 'scNote',
    {
      sunco_base_url: $('scBaseUrl').value.trim() || null,
      sunco_app_id: $('scAppId').value.trim() || null,
      sunco_key_id: $('scKeyId').value.trim() || null,
      sunco_key_secret: $('scKeySecret').value || null,
      ultimate_switchboard_id: $('scSwitchboard').value.trim() || null,
    },
    'Sunshine settings saved:'
  );
  if (d && d.ok) $('scKeySecret').value = '';
}

function escapeHtml(v) {
  const d = document.createElement('div');
  d.textContent = v;
  return d.innerHTML;
}

$('startBtn').addEventListener('click', start);
$('stopBtn').addEventListener('click', stop);
$('refreshBtn').addEventListener('click', () => refreshData(false));
const clearBtn=$('clearBtn'); if(clearBtn) clearBtn.addEventListener('click', clearResults);
['fL1','fL2','fL3'].forEach(id => { const el=$(id); if(el) el.addEventListener('change', updateFilterPreview); });
const clr=$('clearFilters'); if(clr) clr.addEventListener('click', () => { ['fL1','fL2','fL3'].forEach(id=>{const e=$(id); if(e) e.value='';}); updateFilterPreview(); });
$('llmSave').addEventListener('click', saveLlm);
$('zkSave').addEventListener('click', saveZendesk);
$('sheetIdSave').addEventListener('click', saveSheetId);
$('scSave').addEventListener('click', saveSunshine);
setInterval(refresh, 2500);
refresh();
refreshData(true);
