/* Three-way matching workspace: selection, auto-population, preview and comparison.
   One PO is compared against one or more Invoices and one or more GRNs. */
(function () {
  const T = window.TWM;
  const DOCS = ['po', 'grn', 'invoice'];
  const MULTI = ['grn', 'invoice'];
  const LABEL = { po: 'Purchase Order', invoice: 'Invoice', grn: 'GRN' };
  const MONEY = new Set(['Total_Value', 'Total_Supply_Value', 'Discount', 'VAT_Amount', 'Less_Cess',
    'Net_Total', 'Unit_Price', 'Line_Total']);
  const csrf = document.querySelector('meta[name=csrf-token]').content;

  const state = {
    lists: {},
    selected: { po: '', invoice: [], grn: [] },
    active: { invoice: '', grn: '' },          // invoice / GRN whose header is shown in its panel
    details: { po: null, invoice: {}, grn: {} },
    highlights: null,                          // highlights of the last comparison, re-applied on tab switch
  };
  const $ = (s, r = document) => r.querySelector(s);

  const url = (tpl, doc, id) => tpl.replace('__DOC__', doc).replace('__ID__', encodeURIComponent(id));
  const splitIds = s => [...new Set(String(s || '').split(',').map(x => x.trim()).filter(Boolean))];
  const shownId = doc => doc === 'po' ? state.selected.po : state.active[doc];
  const plural = (n, doc) => `${n} ${LABEL[doc]}${n === 1 ? '' : 's'}`;

  function fmt(v, field) {
    if (v === null || v === undefined || v === '') return '';
    if (typeof v === 'number') {
      const money = MONEY.has(field);
      const dec = money || !Number.isInteger(v) ? 2 : 0;
      return v.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec });
    }
    return String(v);
  }

  async function getJSON(u, opts) {
    const res = await fetch(u, Object.assign({ headers: { 'X-CSRF-Token': csrf } }, opts || {}));
    if (res.status === 401) { window.location = '/login'; throw new Error('Session expired'); }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || ('Request failed: ' + res.status));
    return data;
  }

  // ------------------------------------------------------------------ pickers
  function linkedTo(doc, poNo) {
    return (state.lists[doc] || []).filter(x => x.po_no === poNo);
  }

  function renderPoOptions() {
    const s = $('#sel-po');
    const items = state.lists.po || [];
    s.innerHTML = '';
    const ph = document.createElement('option');
    ph.value = '';
    ph.textContent = `— Select ${LABEL.po} (${items.length}) —`;
    s.appendChild(ph);
    items.forEach(x => {
      const o = document.createElement('option');
      o.value = x.id;
      o.textContent = [x.id, x.date, x.supplier].filter(Boolean).join('  ·  ');
      s.appendChild(o);
    });
    s.value = state.selected.po || '';
  }

  function renderOptions(doc) {
    if (doc === 'po') { renderPoOptions(); return; }
    const box = $('#pick-' + doc);
    const all = state.lists[doc] || [];
    const po = state.selected.po;
    const linked = po ? linkedTo(doc, po) : [];
    const items = po ? linked.slice() : all.slice();
    // keep ticked documents visible even when they are outside the filtered list
    state.selected[doc].forEach(id => {
      if (!items.some(x => x.id === id)) items.push(all.find(x => x.id === id) || { id });
    });

    box.innerHTML = '';
    if (!items.length) {
      const e = document.createElement('div');
      e.className = 'pick-empty';
      e.textContent = po ? `No ${LABEL[doc]} linked to ${po}` : `No ${LABEL[doc]}s loaded`;
      box.appendChild(e);
    }
    items.forEach(x => {
      const unlinked = po && x.po_no !== po;
      const lbl = document.createElement('label');
      lbl.className = 'pick-item' + (unlinked ? ' unlinked' : '');
      const cb = document.createElement('input');
      cb.type = 'checkbox'; cb.value = x.id; cb.checked = state.selected[doc].includes(x.id);
      const id = document.createElement('span');
      id.className = 'mono'; id.textContent = x.id;
      const meta = document.createElement('span');
      meta.className = 'muted small';
      meta.textContent = [x.po_no && 'PO ' + x.po_no, x.date].filter(Boolean).join(' · ')
        + (unlinked ? '  (not linked to selected PO)' : '');
      lbl.append(cb, id, meta);
      box.appendChild(lbl);
    });

    const hint = $('#hint-' + doc);
    const n = linked.length;
    const k = state.selected[doc].filter(id => linked.some(x => x.id === id)).length;
    let msg = '', cls = '';
    if (po) {
      if (n === 0) { msg = `No ${LABEL[doc]} references ${po}.`; cls = 'hint-bad'; }
      else if (k === n) msg = n === 1 ? `1 ${LABEL[doc]} references ${po} — selected.`
        : `All ${plural(n, doc)} referencing ${po} selected — compared together.`;
      else { msg = `${k} of ${plural(n, doc)} referencing ${po} selected — the others will be flagged.`; cls = 'hint-warn'; }
    }
    hint.textContent = msg;
    hint.className = 'hint small ' + cls;
  }

  // ------------------------------------------------------------------ fields
  function clearDoc(doc) {
    if (doc === 'po') state.details.po = null;
    document.querySelectorAll(`[id^="f-${doc}-"]`).forEach(i => { i.value = ''; i.title = ''; });
    $(`#lines-${doc} tbody`).innerHTML = '<tr class="empty"><td colspan="12">Select a document</td></tr>';
    $('#count-' + doc).textContent = '';
    $(`[data-preview="${doc}"]`).disabled = true;
    const th = $(`#lines-${doc} thead th.col-src`);
    if (th) th.hidden = true;
    const tabs = $('#tabs-' + doc);
    if (tabs) { tabs.innerHTML = ''; tabs.hidden = true; }
  }

  function lineCols(doc) {
    return T.columns[doc].detail.filter(c => !['PO_No', 'Description', 'UOM'].includes(c));
  }

  function renderTabs(doc, docs) {
    const box = $('#tabs-' + doc);
    box.innerHTML = '';
    box.hidden = docs.length < 2;
    docs.forEach(d => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'doc-tab' + (d.id === state.active[doc] ? ' active' : '');
      b.dataset.id = d.id;
      b.textContent = d.id;
      b.title = `Show the header of ${LABEL[doc]} ${d.id}`;
      box.appendChild(b);
    });
  }

  // Header fields show the active document; the line table lists the lines of every selected document.
  function renderDoc(doc) {
    const get = id => doc === 'po' ? state.details.po : state.details[doc][id];
    const ids = doc === 'po' ? [state.selected.po].filter(Boolean) : state.selected[doc];
    const docs = ids.map(get).filter(Boolean);
    if (!docs.length) { clearDoc(doc); return; }
    const shown = get(shownId(doc)) || docs[0];
    if (doc !== 'po') state.active[doc] = shown.id;

    T.columns[doc].header.forEach(f => {
      const inp = $(`#f-${doc}-${CSS.escape(f)}`);
      if (inp) { inp.value = fmt(shown.header[f], f); inp.title = inp.value; }
    });
    if (doc !== 'po') renderTabs(doc, docs);

    const multi = docs.length > 1;
    const th = $(`#lines-${doc} thead th.col-src`);
    if (th) th.hidden = !multi;
    const cols = lineCols(doc);
    const tb = $(`#lines-${doc} tbody`);
    tb.innerHTML = '';
    let total = 0;
    docs.forEach(d => d.lines.forEach(ln => {
      total++;
      const tr = document.createElement('tr');
      const key = String(ln.PO_Line_No ?? ln.Line_No ?? '');
      tr.dataset.line = key.replace(/\.0$/, '');
      if (multi) {
        if (d.id === shown.id) tr.classList.add('is-shown');
        const td = document.createElement('td');
        td.className = 'col-src';
        td.textContent = d.id;
        tr.appendChild(td);
      }
      cols.forEach(c => {
        const td = document.createElement('td');
        td.textContent = fmt(ln[c], c);
        if (c !== 'Item_Code') td.className = 'num';
        if (c === 'Item_Code' && ln.Description) td.title = ln.Description;
        tr.appendChild(td);
      });
      tb.appendChild(tr);
    }));
    if (!total) tb.innerHTML = `<tr class="empty"><td colspan="${cols.length + (multi ? 1 : 0)}">No line items</td></tr>`;
    $('#count-' + doc).textContent = multi ? `(${total} from ${plural(docs.length, doc)})` : `(${total})`;
    $(`[data-preview="${doc}"]`).disabled = false;
  }

  async function loadPo() {
    forgetHighlights();
    const id = state.selected.po;
    if (!id) { clearDoc('po'); return; }
    try {
      state.details.po = await getJSON(url(T.urls.document, 'po', id));
      renderDoc('po');
    } catch (e) { clearDoc('po'); status(e.message, true); }
  }

  async function loadDocs(doc) {
    forgetHighlights();
    const cache = state.details[doc];
    const results = await Promise.allSettled(state.selected[doc].filter(id => !cache[id]).map(async id => {
      cache[id] = await getJSON(url(T.urls.document, doc, id));
    }));
    const failed = results.find(r => r.status === 'rejected');
    if (failed) status(failed.reason.message, true);
    renderDoc(doc);
  }

  // ------------------------------------------------------------------ selection flow
  // Choosing a PO selects every Invoice and GRN that references it.
  async function selectPo(id, opts = {}) {
    state.selected.po = id || '';
    MULTI.forEach(d => {
      const linked = id ? linkedTo(d, id).map(x => x.id) : [];
      const preset = (opts.preset && opts.preset[d]) || [];
      if (!id) state.selected[d] = [];
      else if (preset.length) state.selected[d] = [...new Set(opts.addLinked ? preset.concat(linked) : preset)];
      else state.selected[d] = linked;
      if (!state.selected[d].includes(state.active[d])) state.active[d] = state.selected[d][0] || '';
    });
    DOCS.forEach(renderOptions);
    await Promise.all([loadPo(), ...MULTI.map(loadDocs)]);
    updateButtons();
  }

  async function toggleDoc(doc, id, on) {
    const cur = state.selected[doc];
    state.selected[doc] = on ? (cur.includes(id) ? cur : cur.concat(id)) : cur.filter(x => x !== id);
    if (on) {
      // ticking an invoice/GRN with no PO selected -> adopt its PO and the PO's other linked documents
      const item = (state.lists[doc] || []).find(x => x.id === id);
      if (item && item.po_no && !state.selected.po && (state.lists.po || []).some(p => p.id === item.po_no)) {
        await selectPo(item.po_no, { preset: { invoice: state.selected.invoice, grn: state.selected.grn }, addLinked: true });
        return;
      }
      state.active[doc] = id;
    }
    renderOptions(doc);
    await loadDocs(doc);
    updateButtons();
  }

  function updateButtons() {
    const s = state.selected;
    $('#btn-compare').disabled = !(s.po && (s.invoice.length || s.grn.length));
    const missing = MULTI.filter(d => !s[d].length).map(d => LABEL[d]);
    if (!s.po) status('');
    else if (missing.length) status(`Missing: ${missing.join(', ')} — the comparison will be marked incomplete.`);
    else status(`${s.po} will be compared against ${plural(s.grn.length, 'grn')} and ${plural(s.invoice.length, 'invoice')}.`);
  }

  function status(msg, isErr) {
    const el = $('#compare-status');
    el.textContent = msg || '';
    el.classList.toggle('err', !!isErr);
  }

  // ------------------------------------------------------------------ compare
  function settings() {
    const out = {};
    ['qty_tolerance_pct', 'price_tolerance_pct', 'amount_tolerance', 'vat_rate_pct'].forEach(k => {
      out[k] = $('#s-' + k).value;
    });
    return out;
  }

  function clearHighlights() {
    document.querySelectorAll('.hl-FAIL, .hl-WARN, .hl-PASS').forEach(el =>
      el.classList.remove('hl-FAIL', 'hl-WARN', 'hl-PASS'));
    document.querySelectorAll('.doc-panel').forEach(p => p.classList.remove('panel-FAIL', 'panel-WARN', 'panel-PASS'));
  }

  function forgetHighlights() {
    state.highlights = null;
    clearHighlights();
  }

  // h = {po|invoice|grn: {doc_id: {field: status}}, lines: {po_line: status}}
  function applyHighlights(h) {
    clearHighlights();
    state.highlights = h;
    DOCS.forEach(doc => {
      if (doc === 'po' ? !state.selected.po : !state.selected[doc].length) return;
      const byId = h[doc] || {};
      let worst = 'PASS';
      const bump = st => { if (st === 'FAIL' || (st === 'WARN' && worst !== 'FAIL')) worst = st; };
      Object.entries(byId[shownId(doc)] || {}).forEach(([field, st]) => {
        const lbl = $(`.doc-panel[data-doc="${doc}"] label.field[data-field="${CSS.escape(field)}"]`);
        if (lbl) lbl.classList.add('hl-' + st);
      });
      Object.values(byId).forEach(fields => Object.values(fields).forEach(bump));
      document.querySelectorAll(`#tabs-${doc} .doc-tab`).forEach(b => {
        const sts = Object.values(byId[b.dataset.id] || {});
        const st = sts.includes('FAIL') ? 'FAIL' : sts.includes('WARN') ? 'WARN' : '';
        if (st) b.classList.add('hl-' + st);
      });
      document.querySelectorAll(`#lines-${doc} tbody tr[data-line]`).forEach(tr => {
        const st = (h.lines || {})[tr.dataset.line];
        if (st && st !== 'INFO') tr.classList.add('hl-' + st);
        bump(st);
      });
      $(`.doc-panel[data-doc="${doc}"]`).classList.add('panel-' + worst);
    });
  }

  async function compare() {
    const btn = $('#btn-compare');
    btn.disabled = true; btn.textContent = 'Comparing…';
    try {
      const data = await getJSON(T.urls.compare, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
        body: JSON.stringify({ po: state.selected.po, invoice: state.selected.invoice, grn: state.selected.grn,
          settings: settings() }),
      });
      // line comparison above the document cards, header & control checks below them
      const top = $('#result-card'), bottom = $('#result-checks');
      top.innerHTML = data.html_summary;
      bottom.innerHTML = data.html_checks;
      top.hidden = bottom.hidden = false;
      window.bindResultFilters($('main'));
      applyHighlights(data.highlights);
      updateButtons();
      status(`Saved as run #${data.run_id}.`);
      $('#result-card').scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (e) {
      updateButtons();
      status(e.message, true);
    } finally {
      btn.textContent = 'Compare documents';
    }
  }

  // ------------------------------------------------------------------ preview
  // Large modal for one document only: its details on the left, its printable sheet on the right.
  async function loadInto(sel, tpl, doc, id, what) {
    try {
      const res = await fetch(url(tpl, doc, id));
      if (!res.ok) throw new Error(`${what} not available`);
      $(sel).innerHTML = await res.text();
      $(sel).scrollTop = 0;
    } catch (e) {
      $(sel).innerHTML = `<p class="err">${e.message}</p>`;
    }
  }

  function preview(doc) {
    const id = shownId(doc);
    if (!id) return;
    $('#preview-title').textContent = `${LABEL[doc]} · ${id}`;
    $('#preview-open').href = url(T.urls.preview, doc, id) + '?full=1';
    $('#preview-related').innerHTML = '<p class="muted">Loading…</p>';
    $('#preview-body').innerHTML = '<p class="muted">Loading…</p>';
    $('#preview-dialog').showModal();
    loadInto('#preview-related', T.urls.details, doc, id, 'Details');
    loadInto('#preview-body', T.urls.preview, doc, id, 'Preview');
  }

  // ------------------------------------------------------------------ init
  async function init() {
    try {
      state.lists = await getJSON(T.urls.documents);
    } catch (e) { status(e.message, true); return; }
    DOCS.forEach(renderOptions);

    $('#sel-po').addEventListener('change', e => selectPo(e.target.value));
    MULTI.forEach(doc => {
      $('#pick-' + doc).addEventListener('change', e => {
        if (e.target.matches('input[type=checkbox]')) toggleDoc(doc, e.target.value, e.target.checked);
      });
      $('#tabs-' + doc).addEventListener('click', e => {
        const b = e.target.closest('.doc-tab');
        if (!b) return;
        state.active[doc] = b.dataset.id;
        renderDoc(doc);
        if (state.highlights) applyHighlights(state.highlights);
      });
    });
    document.querySelectorAll('[data-preview]').forEach(b =>
      b.addEventListener('click', () => preview(b.dataset.preview)));
    $('#preview-close').addEventListener('click', () => $('#preview-dialog').close());
    $('#preview-dialog').addEventListener('click', e => { if (e.target.id === 'preview-dialog') e.target.close(); });
    $('#btn-compare').addEventListener('click', compare);
    $('#btn-reset').addEventListener('click', async () => {
      ['#result-card', '#result-checks'].forEach(s => { $(s).innerHTML = ''; $(s).hidden = true; });
      await selectPo('');
    });

    // preselection from the URL (?po=&invoice=A,B&grn=C,D)
    const box = $('#docs').dataset;
    const pre = { invoice: splitIds(box.preselectInvoice), grn: splitIds(box.preselectGrn) };
    if (box.preselectPo) await selectPo(box.preselectPo, { preset: pre });
    else {
      for (const d of MULTI) for (const id of pre[d]) await toggleDoc(d, id, true);
      updateButtons();
    }
  }

  document.addEventListener('DOMContentLoaded', init);
})();
