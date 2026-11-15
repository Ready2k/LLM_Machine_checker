// Auto-Router tab: how model:"auto" traffic splits, per-route latency/volume,
// recent decisions with their reason strings, and a "should have been…" action
// that files a misroute report (POST /api/router/feedback — no prompt text is
// sent from here; the gateway never has it).
document.addEventListener("DOMContentLoaded", () => {
    const tab = document.querySelector('.obs-tab[data-obs-tab="router"]');
    const pane = document.getElementById('obs-tab-router');
    if (!tab || !pane) return;

    const chips = document.getElementById('rt-chips');
    const routesBody = document.querySelector('#rt-routes-table tbody');
    const decisionsBody = document.querySelector('#rt-decisions-table tbody');
    const cardsBox = document.getElementById('rt-cards');
    const pickBox = document.getElementById('rt-routepick');
    const candidatesBox = document.getElementById('rt-candidates');
    const CHOICES = [['fast', 'local-fast'], ['quality', 'local-quality'], ['private', 'private-docs']];
    let win = '24h';
    let stats = null;
    let selRoute = null;

    const esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    const ms = (v) => (v === null || v === undefined) ? '–' : (v >= 10000 ? (v / 1000).toFixed(1) + 's' : v + 'ms');
    const num = (v) => (v || 0).toLocaleString();
    const empty = (cols, text, cls = 'text-dim') => `<tr><td colspan="${cols}" class="text-center ${cls}">${esc(text)}</td></tr>`;

    function renderStats() {
        const w = stats && stats.windows[win];
        if (!w) return;
        const fb = stats.feedback || {};
        chips.innerHTML = `
            <span class="obs-stat-chip">auto requests: ${num(w.total)}</span>
            <span class="obs-stat-chip">low confidence (≤${stats.low_confidence_threshold}): ${num(w.low_confidence)}</span>
            <span class="obs-stat-chip">misroute reports pending: ${num(fb.new)}</span>
            <span class="obs-stat-chip">promoted to tests: ${num(fb.promoted)}</span>`;
        const names = Object.keys(w.routes).sort((a, b) => w.routes[b].requests - w.routes[a].requests);
        if (!names.length) {
            pickBox.innerHTML = '';
            cardsBox.innerHTML = '';
            routesBody.innerHTML = empty(8, `No model:"auto" requests in the last ${win}.`);
            return;
        }
        // Cards come from ONE route's stats — percentiles are never averaged across routes.
        if (!selRoute || !w.routes[selRoute]) selRoute = names[0];
        pickBox.innerHTML = names.map((n) => `<button type="button" class="rt-pick${n === selRoute ? ' active' : ''}" data-route="${esc(n)}" aria-pressed="${n === selRoute}">${esc(n)}</button>`).join('');
        const sr = w.routes[selRoute];
        cardsBox.innerHTML = `
            <div class="rt-card"><div class="rt-card-k">Route share</div>
                <div class="rt-card-v">${sr.share_pct}<small>%</small></div>
                <span class="rt-bar rt-bar-lg"><i style="width:${Math.min(100, sr.share_pct)}%"></i></span>
                <div class="rt-card-s">of auto traffic</div></div>
            <div class="rt-card"><div class="rt-card-k">Latency · p50</div>
                <div class="rt-card-v">${ms(sr.p50_ms)}</div>
                <div class="rt-card-s">p95 · ${ms(sr.p95_ms)}</div></div>
            <div class="rt-card"><div class="rt-card-k">Volume</div>
                <div class="rt-card-v">${num(sr.requests)}</div>
                <div class="rt-card-s">of ${num(w.total)} auto requests</div></div>`;
        routesBody.innerHTML = names.map((name) => {
            const r = w.routes[name];
            return `<tr${name === selRoute ? ' class="rt-sel"' : ''}>
                <td>${esc(name)}</td>
                <td><div class="rt-share"><span class="rt-bar"><i style="width:${r.share_pct}%"></i></span>${r.share_pct}%</div></td>
                <td>${num(r.requests)}</td>
                <td>${ms(r.p50_ms)}</td>
                <td>${ms(r.p95_ms)}</td>
                <td>${num(r.tokens_in)} / ${num(r.tokens_out)}</td>
                <td class="${r.errors ? 'text-danger' : ''}">${num(r.errors)}</td>
                <td>${r.avg_confidence ?? '–'}</td>
            </tr>`;
        }).join('');
    }

    async function loadStats() {
        try {
            const res = await fetch('/api/router/stats');
            if (!res.ok) throw new Error('HTTP ' + res.status);
            stats = await res.json();
            renderStats();
        } catch (e) {
            routesBody.innerHTML = empty(8, 'Error: ' + e.message, 'text-danger');
        }
    }

    function feedbackCell(d) {
        if (d.feedback) return `<span class="rt-receipt">Reported → ${esc(d.feedback.desired_route)} · ${esc(d.feedback.status)}</span>`;
        const opts = CHOICES.filter(([, name]) => name !== d.route)
            .map(([key]) => `<option value="${key}">${key}</option>`).join('');
        return `<select class="rt-fb" data-cid="${esc(d.correlation_id)}" aria-label="Report misroute">
                    <option value="">should be…</option>${opts}</select>`;
    }

    async function loadDecisions() {
        decisionsBody.innerHTML = empty(2, 'Loading…');
        try {
            const res = await fetch('/api/router/decisions?limit=50' + (candidatesBox.checked ? '&candidates=true' : ''));
            if (!res.ok) throw new Error('HTTP ' + res.status);
            const data = await res.json();
            if (!data.decisions.length) {
                decisionsBody.innerHTML = empty(2, candidatesBox.checked ? 'No low-confidence decisions.' : 'No auto-routing decisions recorded yet.');
                return;
            }
            decisionsBody.innerHTML = data.decisions.map((d) => {
                const scored = d.stage === 'complexity' || d.stage === 'model';
                const meta = [esc(new Date(d.timestamp).toLocaleString()), ms(d.latency_ms),
                    scored ? `conf ${esc(d.confidence)}` : null,
                    scored ? `score ${esc(d.score)}/${esc(d.threshold)}` : null].filter(Boolean).join(' · ');
                return `<tr class="${d.low_confidence ? 'rt-low' : ''}">
                <td class="rt-dec">
                    <div class="rt-dec-head"><span class="rt-route">${esc(d.route)}</span>${d.low_confidence ? '<span class="rt-lowlabel">Low confidence</span>' : ''}${d.status && d.status !== 'success' ? ` <span class="obs-badge error">${esc(d.status)}</span>` : ''}</div>
                    <div class="rt-reason">${esc(d.reason)}</div>
                    <div class="rt-meta">${meta}</div>
                </td>
                <td class="rt-act">${feedbackCell(d)}</td>
            </tr>`;
            }).join('');
        } catch (e) {
            decisionsBody.innerHTML = empty(2, 'Error: ' + e.message, 'text-danger');
        }
    }

    decisionsBody.addEventListener('change', async (ev) => {
        const sel = ev.target.closest('.rt-fb');
        if (!sel || !sel.value) return;
        sel.disabled = true;
        try {
            const res = await fetch('/api/router/feedback', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ correlation_id: sel.dataset.cid, desired: sel.value }),
            });
            const d = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(typeof d.detail === 'string' ? d.detail : 'HTTP ' + res.status);
            sel.outerHTML = `<span class="rt-receipt">Reported → ${esc(d.desired_route)} · new</span>`;
            loadStats();
        } catch (e) {
            sel.disabled = false;
            sel.value = '';
            sel.title = 'Failed: ' + e.message;
            sel.classList.add('rt-fb-error');
        }
    });

    pickBox.addEventListener('click', (ev) => {
        const b = ev.target.closest('.rt-pick');
        if (!b) return;
        selRoute = b.dataset.route;
        renderStats();
    });

    pane.querySelectorAll('.rt-win').forEach((b) => b.addEventListener('click', () => {
        win = b.dataset.win;
        pane.querySelectorAll('.rt-win').forEach((x) => x.classList.toggle('active', x === b));
        renderStats();
    }));
    const loadAll = () => { loadStats(); loadDecisions(); };
    tab.addEventListener('click', loadAll);
    document.getElementById('rt-refresh').addEventListener('click', loadAll);
    candidatesBox.addEventListener('change', loadDecisions);
});
