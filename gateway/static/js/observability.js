document.addEventListener("DOMContentLoaded", () => {
    const tabs = document.querySelectorAll('.obs-tab');
    const contents = document.querySelectorAll('.obs-tab-content');
    const tableBody = document.querySelector('#obs-traces-table tbody');
    const refreshBtn = document.getElementById('obs-refresh-traces');
    const exportBtn = document.getElementById('obs-btn-export-audit');

    // Tab switching
    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            const target = tab.getAttribute('data-obs-tab');
            
            tabs.forEach(t => t.classList.remove('active'));
            contents.forEach(c => c.classList.remove('active'));
            
            tab.classList.add('active');
            document.getElementById(`obs-tab-${target}`).classList.add('active');
            
            if (target === 'traces') {
                loadTraces();
            } else if (target === 'overview') {
                loadOverview();
            } else if (target === 'logs') {
                initLiveLogs();
            }
        });
    });

    function formatTime(isoStr) {
        const d = new Date(isoStr);
        return d.toLocaleTimeString() + '.' + d.getMilliseconds().toString().padStart(3, '0');
    }

    function getStatusBadge(status) {
        if (status === 'success') return '<span class="obs-badge success">Success</span>';
        if (status === 'blocked') return '<span class="obs-badge blocked">Blocked</span>';
        return `<span class="obs-badge error">${status}</span>`;
    }

    async function loadTraces() {
        if (!tableBody) return;
        tableBody.innerHTML = '<tr><td colspan="8" class="text-center text-dim">Loading traces...</td></tr>';
        
        try {
            const res = await fetch('/api/observability/traces');
            if (!res.ok) throw new Error("Failed to fetch traces");
            const data = await res.json();
            
            if (!data.traces || data.traces.length === 0) {
                tableBody.innerHTML = '<tr><td colspan="8" class="text-center text-dim">No traces found in database.</td></tr>';
                return;
            }
            
            tableBody.innerHTML = '';
            data.traces.forEach(t => {
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td>${formatTime(t.timestamp)}</td>
                    <td class="mono" title="${t.correlation_id}">${t.correlation_id.substring(0, 8)}...</td>
                    <td>${t.route}</td>
                    <td>${t.backend} ${t.fallback_used ? '<span class="obs-badge blocked" style="font-size:0.6rem">Fallback</span>' : ''}</td>
                    <td>${t.model}</td>
                    <td>${t.duration_ms !== null ? t.duration_ms + 'ms' : '-'}</td>
                    <td>${t.ttft_ms !== null ? t.ttft_ms + 'ms' : '-'}</td>
                    <td>${getStatusBadge(t.status)}</td>
                `;
                
                tr.addEventListener('click', () => showTraceDetail(t.correlation_id));
                tableBody.appendChild(tr);
            });
        } catch (e) {
            tableBody.innerHTML = `<tr><td colspan="8" class="text-center text-danger">Error: ${e.message}</td></tr>`;
        }
    }

    async function showTraceDetail(id) {
        const drawer = document.getElementById('obs-trace-drawer');
        const content = document.getElementById('obs-drawer-content');
        if (!drawer || !content) return;
        
        content.innerHTML = '<div class="text-center text-dim" style="margin-top:2rem">Loading trace...</div>';
        drawer.classList.remove('hidden');
        
        try {
            const res = await fetch(`/api/observability/traces/${id}`);
            if (!res.ok) throw new Error("Failed to fetch trace details");
            const data = await res.json();
            
            const req = data.request;
            const events = data.events;
            
            let html = `
                <div class="obs-detail-grid">
                    <div class="obs-detail-item">
                        <div class="obs-detail-label">Correlation ID</div>
                        <div class="obs-detail-val mono">${req.correlation_id}</div>
                    </div>
                    <div class="obs-detail-item">
                        <div class="obs-detail-label">Status</div>
                        <div class="obs-detail-val">${getStatusBadge(req.status)}</div>
                    </div>
                    <div class="obs-detail-item">
                        <div class="obs-detail-label">Route & Backend</div>
                        <div class="obs-detail-val">${req.route} ➔ ${req.backend}</div>
                    </div>
                    <div class="obs-detail-item">
                        <div class="obs-detail-label">Model</div>
                        <div class="obs-detail-val">${req.model}</div>
                    </div>
                    <div class="obs-detail-item">
                        <div class="obs-detail-label">Duration</div>
                        <div class="obs-detail-val">${req.duration_ms ? req.duration_ms + 'ms' : '-'}</div>
                    </div>
                    <div class="obs-detail-item">
                        <div class="obs-detail-label">TTFT</div>
                        <div class="obs-detail-val">${req.time_to_first_token_ms ? req.time_to_first_token_ms + 'ms' : '-'}</div>
                    </div>
                </div>
                
                <h4 style="margin-bottom:1rem; border-bottom:1px solid var(--border); padding-bottom:0.5rem">Event Timeline</h4>
                <div class="obs-timeline">
            `;
            
            if (events && events.length > 0) {
                events.forEach(ev => {
                    const isErr = ev.event_type.toLowerCase().includes('error');
                    html += `
                        <div class="obs-timeline-item ${isErr ? 'error' : ''}">
                            <div class="obs-timeline-time">${formatTime(ev.timestamp)}</div>
                            <div class="obs-timeline-title">${ev.event_type}</div>
                            ${ev.message ? `<div class="obs-timeline-desc">${ev.message}</div>` : ''}
                        </div>
                    `;
                });
            } else {
                html += '<p class="text-dim">No timeline events recorded.</p>';
            }
            
            html += '</div>';
            
            html += `<h4 style="margin-top:2rem; margin-bottom:1rem; border-bottom:1px solid var(--border); padding-bottom:0.5rem">Backend Logs (Node Agent)</h4>
                     <div class="obs-provider-logs" id="obs-provider-logs-container">
                        <div class="text-dim">Fetching remote node logs...</div>
                     </div>`;
            
            content.innerHTML = html;
            
            // Now fetch the provider logs asynchronously
            try {
                const logsRes = await fetch(`/api/observability/traces/${id}/logs`);
                const logsContainer = document.getElementById('obs-provider-logs-container');
                if (logsRes.ok) {
                    const logsData = await logsRes.json();
                    if (logsData.logs && logsData.logs.length > 0) {
                        logsContainer.innerHTML = '<pre style="background: var(--bg-1); padding: 1rem; border-radius: var(--radius-sm); overflow-x: auto; font-size: 0.8rem; color: var(--text-dim); margin: 0; white-space: pre-wrap; word-break: break-all;">' + 
                            logsData.logs.map(l => l.replace(/</g, "&lt;").replace(/>/g, "&gt;").trim()).filter(l => l.length > 0).join('\\n') + 
                            '</pre>';
                    } else {
                        logsContainer.innerHTML = '<div class="text-dim">No logs retrieved.</div>';
                    }
                } else {
                    logsContainer.innerHTML = '<div class="text-danger">Failed to load backend logs.</div>';
                }
            } catch (err) {
                const logsContainer = document.getElementById('obs-provider-logs-container');
                if (logsContainer) {
                    logsContainer.innerHTML = `<div class="text-danger">Error: ${err.message}</div>`;
                }
            }
            
            
        } catch (e) {
            content.innerHTML = `<div class="text-center text-danger" style="margin-top:2rem">Error: ${e.message}</div>`;
        }
    }

    const drawerCloseBtn = document.getElementById('obs-drawer-close');
    if (drawerCloseBtn) {
        drawerCloseBtn.addEventListener('click', () => {
            document.getElementById('obs-trace-drawer').classList.add('hidden');
        });
    }

    if (refreshBtn) {
        refreshBtn.addEventListener('click', loadTraces);
    }

    if (exportBtn) {
        exportBtn.addEventListener('click', async () => {
            const res = await fetch('/api/observability/audit-export', { method: 'POST' });
            if (!res.ok) {
                alert('Failed to export audit bundle');
                return;
            }
            const blob = await res.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `audit-bundle-${new Date().toISOString()}.json`;
            document.body.appendChild(a);
            a.click();
            a.remove();
        });
    }

    // Initial load
    if (document.querySelector('#obs-tab-traces').classList.contains('active')) {
        loadTraces();
    } else if (document.querySelector('#obs-tab-overview').classList.contains('active')) {
        loadOverview();
    } else if (document.querySelector('#obs-tab-logs').classList.contains('active')) {
        initLiveLogs();
    }
    
    // --- Overview Logic ---
    async function loadOverview() {
        try {
            const res = await fetch('/api/observability/overview');
            if (res.ok) {
                const data = await res.json();
                document.getElementById('obs-stat-total').innerText = data.total_requests;
                document.getElementById('obs-stat-success').innerText = data.success_rate_pct + '%';
                document.getElementById('obs-stat-latency').innerText = data.avg_latency_ms + 'ms';
                document.getElementById('obs-stat-errors').innerText = data.error_count;
            }
        } catch (e) {
            console.error("Failed to load overview metrics", e);
        }
    }
    
    // --- Live Logs Logic ---
    let liveLogsEventSource = null;
    function initLiveLogs() {
        if (liveLogsEventSource) return; // already initialized
        
        const logsContainer = document.getElementById('obs-live-logs');
        if (!logsContainer) return;
        logsContainer.innerHTML = '';
        
        liveLogsEventSource = new EventSource('/api/observability/logs/live');
        
        liveLogsEventSource.onmessage = (event) => {
            const data = JSON.parse(event.data);
            const div = document.createElement('div');
            div.className = `obs-timeline-item ${data.event_type.toLowerCase().includes('error') ? 'error' : ''}`;
            div.innerHTML = `
                <div class="obs-timeline-time">${formatTime(data.timestamp)}</div>
                <div class="obs-timeline-title">[${data.correlation_id.split('-')[0]}] ${data.event_type}</div>
                <div class="obs-timeline-desc">${data.message}</div>
            `;
            logsContainer.prepend(div);
            
            // Limit to 200 items to prevent memory bloat
            while (logsContainer.children.length > 200) {
                logsContainer.removeChild(logsContainer.lastChild);
            }
        };
        
        liveLogsEventSource.onerror = (err) => {
            console.error("EventSource failed:", err);
            // Will auto-reconnect typically
        };
    }
});
