/* ============================================================
   Local AI Fabric — Dashboard Logic
   ============================================================
   Fetches fleet, routing, and profile data from the gateway API.
   Renders all dashboard sections dynamically.
   No external dependencies.
   ============================================================ */

(function () {
  "use strict";

  // ── API endpoints ──
  const API = {
    health: "/api/health",
    machines: "/api/machines",
    routes: "/api/config/routes",
    profile: "/api/profile/current",
    profileRun: "/api/profile/run",
    backendsHealth: "/api/backends/health",
    events: "/api/events",
    stats: "/api/stats",
  };

  // ── State ──
  let state = {
    machines: null,
    routes: null,
    profile: null,
    health: null,
    backendsHealth: null,
    lastRefresh: null,
    autoRefreshId: null,
    profiling: false,
  };

  // ── DOM refs ──
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => document.querySelectorAll(sel);

  const dom = {
    appContainer: $("#app-container"),
    statusDot: $("#system-status-dot"),
    lastRefresh: $("#last-refresh"),
    autoRefreshToggle: $("#auto-refresh-toggle"),
    refreshBtn: $("#refresh-btn"),
    btnDockLayout: $("#btn-dock-layout"),
    btnAddWidget: $("#btn-add-widget"),
    widgetModal: $("#widget-modal"),
    btnCloseModal: $("#btn-close-modal"),
    widgetGallery: $("#widget-gallery"),
    fleetGrid: $("#fleet-grid"),
    fleetLoading: $("#fleet-loading"),
    profilePanel: $("#profile-panel"),
    profileLoading: $("#profile-loading"),
    routingGrid: $("#routing-grid"),
    routingLoading: $("#routing-loading"),
    archPanel: $("#arch-panel"),
    observabilityPanel: $("#observability-panel"),
    spiderPanel: $("#spider-panel"),
  };

  // ── Helpers ──

  /** Human-readable machine name from id */
  function humanName(id) {
    const names = {
      "macbook-m4pro": "MacBook M4 Pro",
      "pc-rtx2070": "RTX 2070 PC",
      "old-macbook": "Old MacBook",
    };
    return names[id] || id;
  }

  /** Role badge class */
  function roleBadgeClass(role) {
    if (role === "local_quality") return "badge--quality";
    if (role === "local_fast") return "badge--fast";
    return "badge--utility";
  }

  /** Role display text */
  function roleLabel(role) {
    if (role === "local_quality") return "⚡ Quality";
    if (role === "local_fast") return "🚀 Fast";
    return "🔧 Utility";
  }

  /** Format risk text */
  function formatRisk(risk) {
    if (!risk || risk === "none") return null;
    return risk.replace(/_/g, " ");
  }

  /** Format timestamp */
  function formatTime(date) {
    return date.toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  }

  /** Safe fetch with error handling */
  async function apiFetch(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  }

  /** Create element helper */
  function el(tag, attrs, ...children) {
    const e = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (k === "className") e.className = v;
        else if (k === "innerHTML") e.innerHTML = v;
        else if (k.startsWith("on")) e.addEventListener(k.slice(2).toLowerCase(), v);
        else if (k === "style" && typeof v === "object") Object.assign(e.style, v);
        else e.setAttribute(k, v);
      }
    }
    for (const child of children) {
      if (typeof child === "string") e.appendChild(document.createTextNode(child));
      else if (child) e.appendChild(child);
    }
    return e;
  }

  // ── Gauge Animation ──

  /** Animate a gauge from 0 to target percent */
  function animateGauge(gaugeEl, targetPercent, durationMs = 800) {
    const circle = gaugeEl.querySelector(".gauge__circle");
    const valueEl = gaugeEl.querySelector(".gauge__value");
    if (!circle) return;

    const start = performance.now();
    const isUnified = gaugeEl.dataset.unified === "true";
    const maxVal = parseFloat(gaugeEl.dataset.max) || 100;

    function tick(now) {
      const elapsed = now - start;
      const progress = Math.min(elapsed / durationMs, 1);
      // ease-out cubic
      const eased = 1 - Math.pow(1 - progress, 3);
      const currentPercent = eased * targetPercent;

      circle.style.background = `conic-gradient(
        var(--gauge-color, var(--primary)) 0deg,
        var(--gauge-color, var(--primary)) ${currentPercent * 3.6}deg,
        var(--bg-2) ${currentPercent * 3.6}deg,
        var(--bg-2) 360deg
      )`;

      if (valueEl && !isUnified) {
        const currentVal = (eased * maxVal * targetPercent / 100).toFixed(0);
        valueEl.textContent = currentVal + " GB";
      }

      if (progress < 1) {
        requestAnimationFrame(tick);
      }
    }

    requestAnimationFrame(tick);
  }

  // ── Render: Machine Card ──

  function renderMachineCard(machine, backendInfo) {
    const card = el("div", { className: "glass-card machine-card" });

    // Header row: name + role badge
    const header = el("div", { className: "machine-card__header" });
    const nameBlock = el("div");
    nameBlock.appendChild(el("div", { className: "machine-card__name" }, humanName(machine.id)));
    nameBlock.appendChild(el("div", { className: "machine-card__hostname mono" }, machine.hostname));
    header.appendChild(nameBlock);

    const badges = el("div", { style: { display: "flex", gap: "0.35rem", flexWrap: "wrap" } });
    badges.appendChild(
      el("span", { className: `badge ${roleBadgeClass(machine.recommended_role)}` }, roleLabel(machine.recommended_role))
    );
    badges.appendChild(
      el(
        "span",
        { className: `badge ${machine.compatible ? "badge--compatible" : "badge--incompatible"}` },
        machine.compatible ? "✓ Compatible" : "✗ Incompatible"
      )
    );
    header.appendChild(badges);
    card.appendChild(header);

    // OS / Arch / CPU line
    const meta = el("div", { className: "machine-card__meta" });
    meta.appendChild(el("span", { className: "machine-card__os" }, `${machine.os} · ${machine.arch}`));
    if (machine.cpu) {
      meta.appendChild(el("span", { className: "machine-card__os" }, `· ${machine.cpu}`));
    }
    card.appendChild(meta);

    // Gauges + stats row
    const gaugesRow = el("div", { className: "gauges-row" });

    // RAM gauge
    if (machine.ram_gb) {
      const ramPercent = Math.min((machine.ram_gb / 64) * 100, 100); // scale to 64GB max
      const ramGauge = el("div", { className: "gauge", "data-max": machine.ram_gb, "data-target": ramPercent });
      ramGauge.innerHTML = `
        <div class="gauge__circle" style="--gauge-color: var(--primary)"></div>
        <div class="gauge__label">
          <div class="gauge__value mono">${machine.ram_gb} GB</div>
          <div class="gauge__caption">RAM</div>
        </div>`;
      gaugesRow.appendChild(ramGauge);
      // Schedule animation
      requestAnimationFrame(() => animateGauge(ramGauge, ramPercent));
    }

    // VRAM gauge
    if (machine.vram_gb !== null && machine.vram_gb !== undefined) {
      const vramPercent = Math.min((machine.vram_gb / 24) * 100, 100); // scale to 24GB max
      const vramGauge = el("div", { className: "gauge", "data-max": machine.vram_gb, "data-target": vramPercent });
      vramGauge.innerHTML = `
        <div class="gauge__circle" style="--gauge-color: var(--secondary)"></div>
        <div class="gauge__label">
          <div class="gauge__value mono">${machine.vram_gb} GB</div>
          <div class="gauge__caption">VRAM</div>
        </div>`;
      gaugesRow.appendChild(vramGauge);
      requestAnimationFrame(() => animateGauge(vramGauge, vramPercent));
    } else if (machine.gpu_name && machine.gpu_name.includes("Apple Silicon")) {
      // Unified memory indicator
      const unifiedGauge = el("div", { className: "gauge", "data-unified": "true" });
      const unifiedPercent = machine.ram_gb ? Math.min((machine.ram_gb / 64) * 100, 100) : 50;
      unifiedGauge.innerHTML = `
        <div class="gauge__circle" style="--gauge-color: var(--secondary)"></div>
        <div class="gauge__label">
          <div class="gauge__value mono" style="font-size:0.7rem">Unified</div>
          <div class="gauge__caption">GPU</div>
        </div>`;
      gaugesRow.appendChild(unifiedGauge);
      requestAnimationFrame(() => animateGauge(unifiedGauge, unifiedPercent));
    }

    // Stats chips next to gauges
    const statsCol = el("div", { style: { display: "flex", flexDirection: "column", gap: "0.4rem" } });

    if (machine.measured_tokens_per_second) {
      statsCol.appendChild(
        el("div", { className: "stat-chip" },
          el("span", { className: "stat-icon" }, "⚡"),
          el("span", { className: "stat-value mono" }, String(machine.measured_tokens_per_second)),
          el("span", { className: "stat-unit" }, "tok/s")
        )
      );
    }

    if (machine.recommended_model) {
      statsCol.appendChild(
        el("div", { className: "stat-chip" },
          el("span", { className: "stat-icon" }, "🤖"),
          el("span", { className: "mono", style: { fontSize: "0.75rem", color: "var(--text-muted)" } }, machine.recommended_model)
        )
      );
    }

    if (machine.gpu_name && machine.gpu_name !== "Unknown") {
      statsCol.appendChild(
        el("div", { className: "stat-chip" },
          el("span", { className: "stat-icon" }, "🎮"),
          el("span", { className: "mono", style: { fontSize: "0.72rem", color: "var(--text-dim)" } }, machine.gpu_name)
        )
      );
    }

    gaugesRow.appendChild(statsCol);
    card.appendChild(gaugesRow);

    // Risk indicator
    const riskText = formatRisk(machine.risk);
    if (riskText) {
      card.appendChild(
        el("div", { className: "risk-indicator risk-indicator--warning" },
          el("span", {}, "⚠"),
          el("span", {}, riskText)
        )
      );
    } else if (machine.compatible) {
      card.appendChild(
        el("div", { className: "risk-indicator risk-indicator--none" },
          el("span", {}, "✓"),
          el("span", {}, "No risk")
        )
      );
    }

    // Routing info
    if (machine.routing) {
      const routingRow = el("div", { style: { display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" } });
      routingRow.appendChild(
        el("span", { className: `badge ${machine.routing.enabled ? "badge--enabled" : "badge--disabled"}` },
          machine.routing.enabled ? "Routing ✓" : "Routing ✗"
        )
      );

      if (machine.routing.preferred_routes && machine.routing.preferred_routes.length > 0) {
        const pills = el("div", { className: "route-pills" });
        for (const r of machine.routing.preferred_routes) {
          pills.appendChild(el("span", { className: "route-pill route-pill--preferred" }, r));
        }
        routingRow.appendChild(pills);
      }

      if (machine.routing.fallback_routes && machine.routing.fallback_routes.length > 0) {
        const pills = el("div", { className: "route-pills" });
        for (const r of machine.routing.fallback_routes) {
          pills.appendChild(el("span", { className: "route-pill route-pill--fallback" }, r));
        }
        routingRow.appendChild(pills);
      }

      card.appendChild(routingRow);
    }

    if (backendInfo) {
      const rtRow = el("div", { style: { marginTop: "0.5rem", display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" } });
      
      const configCls = backendInfo.configured ? "badge--enabled" : "badge--disabled";
      rtRow.appendChild(el("span", { className: `badge ${configCls}` }, `Config`));

      const reachCls = backendInfo.reachable ? "badge--enabled" : "badge--disabled";
      const reachIcon = backendInfo.reachable ? "🟢" : "🔴";
      rtRow.appendChild(el("span", { className: `badge ${reachCls}` }, `${reachIcon} Reachable`));

      const eligCls = backendInfo.eligible ? "badge--enabled" : "badge--disabled";
      rtRow.appendChild(el("span", { className: `badge ${eligCls}` }, `Eligible`));

      if (backendInfo.latency_ms != null) {
        rtRow.appendChild(el("span", { className: "mono text-dim", style: { fontSize: "0.75rem" } }, `${backendInfo.latency_ms}ms`));
      }
      if (backendInfo.error) {
        rtRow.appendChild(el("span", { className: "mono text-dim", style: { fontSize: "0.75rem", color: "var(--danger)" } }, backendInfo.error));
      }
      card.appendChild(rtRow);

      if (backendInfo.models && backendInfo.models.length > 0) {
        const modRow = el("div", { style: { marginTop: "0.5rem", fontSize: "0.75rem", color: "var(--text-dim)", display: "flex", flexWrap: "wrap", gap: "0.25rem", alignItems: "center" } }, "Models: ");
        const modPills = el("div", { className: "route-pills", style: { display: "inline-flex", flexWrap: "wrap", gap: "0.25rem" } });
        backendInfo.models.forEach(m => modPills.appendChild(el("span", { className: "route-pill route-pill--fallback" }, m)));
        modRow.appendChild(modPills);
        card.appendChild(modRow);
      }
    }

    // Notes (collapsible)
    if (machine.notes && machine.notes.length > 0) {
      const notesWrap = el("div");
      const toggleBtn = el("button", {
        className: "notes-toggle",
        "aria-expanded": "false",
        onClick: () => {
          const content = notesWrap.querySelector(".notes-content");
          const isOpen = content.classList.toggle("open");
          toggleBtn.setAttribute("aria-expanded", String(isOpen));
        },
      });
      toggleBtn.innerHTML = '<span class="chevron">▶</span> Notes';
      notesWrap.appendChild(toggleBtn);

      const notesList = el("div", { className: "notes-content" });
      const ul = el("ul");
      for (const note of machine.notes) {
        ul.appendChild(el("li", {}, note));
      }
      notesList.appendChild(ul);
      notesWrap.appendChild(notesList);
      card.appendChild(notesWrap);
    }

    return card;
  }

  // ── Render: Fleet ──

  function renderFleet(data, backendsData) {
    const grid = dom.fleetGrid;
    // Remove loading skeleton
    if (dom.fleetLoading) dom.fleetLoading.remove();
    // Clear existing cards
    grid.querySelectorAll(".glass-card").forEach((c) => c.remove());

    if (!data || !data.machines || data.machines.length === 0) {
      grid.appendChild(
        el("div", { className: "glass-card-static empty-state" },
          el("p", {}, "No machines found in inventory.")
        )
      );
      return;
    }

    const backends = backendsData && backendsData.backends ? backendsData.backends : [];

    for (const machine of data.machines) {
      const backendInfo = backends.find(b => b.id === machine.id);
      grid.appendChild(renderMachineCard(machine, backendInfo));
    }
  }

  // ── Render: Profile (This Machine) ──

  function renderProfile(profile) {
    const panel = dom.profilePanel;
    panel.innerHTML = "";

    // Profile unavailable
    if (!profile || profile.status === "unavailable" || profile.error) {
      panel.innerHTML = `
        <div class="error-state">
          <div class="error-state__icon">📍</div>
          <p class="text-muted">Profile data unavailable for this host.</p>
          ${profile && profile.error ? `<p class="error-state__message">${escapeHtml(profile.error)}</p>` : ""}
        </div>
        <div style="text-align:center;margin-top:0.75rem">
          <button class="btn btn--primary" id="reprofile-btn">🔄 Run Profile</button>
        </div>`;
      attachReprofileHandler();
      return;
    }

    // Build profile content
    const top = el("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: "0.75rem", marginBottom: "1rem" } });
    const info = el("div");
    info.appendChild(el("h3", {}, `${humanName(profile.id || profile.machine_id || "")} — Profile`));
    if (profile.hostname) {
      info.appendChild(el("div", { className: "mono text-dim", style: { fontSize: "0.8rem" } }, profile.hostname));
    }
    top.appendChild(info);
    top.appendChild(el("button", { className: "btn btn--primary", id: "reprofile-btn" }, "🔄 Re-Profile"));
    panel.appendChild(top);

    // Benchmark stats grid
    const benchmarks = [];
    if (profile.measured_tokens_per_second != null) {
      benchmarks.push({ label: "Tokens/sec", value: profile.measured_tokens_per_second, unit: "tok/s" });
    }
    if (profile.bandwidth_gb_s != null) {
      benchmarks.push({ label: "Bandwidth", value: profile.bandwidth_gb_s, unit: "GB/s" });
    }
    if (profile.tflops != null) {
      benchmarks.push({ label: "TFLOPS", value: profile.tflops, unit: "TF" });
    }
    if (profile.ttft_ms != null) {
      benchmarks.push({ label: "TTFT", value: profile.ttft_ms, unit: "ms" });
    }
    if (profile.ram_gb != null) {
      benchmarks.push({ label: "RAM", value: profile.ram_gb, unit: "GB" });
    }
    if (profile.vram_gb != null) {
      benchmarks.push({ label: "VRAM", value: profile.vram_gb, unit: "GB" });
    }

    if (benchmarks.length > 0) {
      const grid = el("div", { className: "profile-grid" });
      for (const b of benchmarks) {
        const stat = el("div", { className: "profile-stat" });
        stat.appendChild(el("div", { className: "profile-stat__label" }, b.label));
        stat.appendChild(
          el("div", {},
            el("span", { className: "profile-stat__value" }, String(b.value)),
            el("span", { className: "profile-stat__unit" }, ` ${b.unit}`)
          )
        );
        grid.appendChild(stat);
      }
      panel.appendChild(grid);
    }

    // Runtime readiness
    const runtimes = buildRuntimeChecks(profile);
    if (runtimes.length > 0) {
      const rtSection = el("div", { style: { marginTop: "1rem" } });
      rtSection.appendChild(el("h4", { className: "text-muted", style: { marginBottom: "0.5rem" } }, "Runtime Readiness"));
      const rtGrid = el("div", { className: "runtime-grid" });
      for (const rt of runtimes) {
        const cls = rt.ok ? "runtime-check runtime-check--ok" : "runtime-check runtime-check--missing";
        rtGrid.appendChild(
          el("div", { className: cls },
            el("span", {}, rt.ok ? "✓" : "✗"),
            el("span", {}, rt.name)
          )
        );
      }
      rtSection.appendChild(rtGrid);
      panel.appendChild(rtSection);
    }

    attachReprofileHandler();
  }

  /** Build runtime check list from profile data */
  function buildRuntimeChecks(profile) {
    const checks = [];

    // Try to detect from various profile shapes
    const rv = profile.runtime_validation || profile.runtimes || {};

    checks.push({ name: "Ollama", ok: !!(rv.ollama || rv.ollama_version) });
    checks.push({ name: "LM Studio", ok: !!(rv.lm_studio || rv.lmstudio) });
    checks.push({ name: "Docker", ok: !!(rv.docker || rv.docker_version) });

    // CUDA / Metal
    if (rv.cuda_available != null) {
      checks.push({ name: "CUDA", ok: !!rv.cuda_available });
    }
    if (rv.metal_available != null || (profile.gpu_name && profile.gpu_name.includes("Metal"))) {
      checks.push({ name: "Metal", ok: rv.metal_available !== false });
    }
    if (!rv.cuda_available && !rv.metal_available && !profile.gpu_name) {
      checks.push({ name: "CUDA", ok: false });
      checks.push({ name: "Metal", ok: false });
    }

    // If we found CUDA device info
    if (rv.cuda_device) {
      checks.push({ name: rv.cuda_device, ok: true });
    }

    return checks;
  }

  /** Attach the re-profile button handler */
  function attachReprofileHandler() {
    const btn = document.getElementById("reprofile-btn");
    if (!btn) return;
    btn.addEventListener("click", runProfile);
  }

  /** Run profile action */
  async function runProfile() {
    if (state.profiling) return;
    state.profiling = true;

    const btn = document.getElementById("reprofile-btn");
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner"></span> Profiling…';
    }

    try {
      const result = await fetch(API.profileRun, { method: "POST" });
      const data = await result.json();
      state.profile = data;
      renderProfile(data);
    } catch (err) {
      renderProfile({ status: "unavailable", error: `Profile failed: ${err.message}` });
    } finally {
      state.profiling = false;
    }
  }

  // ── Render: Routing Map ──

  function renderRouting(routeData) {
    const grid = dom.routingGrid;
    if (dom.routingLoading) dom.routingLoading.remove();
    grid.querySelectorAll(".route-card").forEach((c) => c.remove());

    if (!routeData || !routeData.routes) {
      grid.appendChild(
        el("div", { className: "glass-card-static empty-state" },
          el("p", {}, "No routing data available.")
        )
      );
      return;
    }

    const routes = routeData.routes;

    for (const [name, route] of Object.entries(routes)) {
      const card = el("div", { className: "route-card" });

      const isCloud = route.cloud_fallback && !route.primary;
      card.appendChild(
        el("div", { className: `route-card__name ${isCloud ? "route-card__name--cloud" : ""}` },
          `${isCloud ? "☁️" : "🔗"} ${name}`
        )
      );

      // Primary row
      if (route.primary) {
        const row = el("div", { className: "route-card__row" });
        row.appendChild(el("span", { className: "route-card__label" }, "Primary"));
        row.appendChild(el("span", { className: "route-card__arrow" }, "→"));
        row.appendChild(el("span", { className: "route-card__machine" }, humanName(route.primary)));
        card.appendChild(row);
      }

      // Fallback row
      if (route.fallback) {
        const row = el("div", { className: "route-card__row" });
        row.appendChild(el("span", { className: "route-card__label" }, "Fallback"));
        row.appendChild(el("span", { className: "route-card__arrow route-card__arrow--fallback" }, "⤑"));
        row.appendChild(el("span", { className: "route-card__machine" }, humanName(route.fallback)));
        card.appendChild(row);
      }

      // Cloud fallback
      if (route.cloud_fallback) {
        const row = el("div", { className: "route-card__row" });
        row.appendChild(el("span", { className: "route-card__label" }, "Cloud"));
        row.appendChild(el("span", { className: "badge badge--cloud" }, "☁ Enabled"));
        card.appendChild(row);
      } else {
        const row = el("div", { className: "route-card__row" });
        row.appendChild(el("span", { className: "route-card__label" }, "Cloud"));
        row.appendChild(el("span", { className: "badge badge--disabled" }, "Disabled"));
        card.appendChild(row);
      }

      // Route test button
      const testRow = el("div", { className: "route-card__row", style: { marginTop: "0.5rem" } });
      const testBtn = el("button", { className: "btn btn--sm", style: { padding: "0.25rem 0.5rem", fontSize: "0.75rem" } }, "Test Route");
      testBtn.addEventListener("click", () => runRouteTest(name, card, testBtn));
      testRow.appendChild(testBtn);
      card.appendChild(testRow);

      const resultDiv = el("div", { className: "route-test-result", style: { display: "none", marginTop: "0.5rem", fontSize: "0.75rem", lineHeight: "1.4" } });
      card.appendChild(resultDiv);

      // Source indicator
      if (routeData.source) {
        card.appendChild(
          el("div", { className: "text-dim", style: { fontSize: "0.65rem", fontFamily: "var(--font-mono)", marginTop: "0.25rem" } },
            `source: ${routeData.source}`
          )
        );
      }

      grid.appendChild(card);
    }
  }

  async function runRouteTest(routeName, cardNode, btnNode) {
    const resDiv = cardNode.querySelector(".route-test-result");
    if (btnNode) {
        btnNode.disabled = true;
        btnNode.innerHTML = '<span class="spinner"></span> Testing...';
    }
    resDiv.style.display = "block";
    resDiv.innerHTML = `<span class="text-dim">Testing...</span>`;
    
    try {
        const resp = await fetch("/api/routes/test", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ route: routeName })
        });
        const data = await resp.json();
        if (!resp.ok) {
            throw new Error(data.detail?.message || "Error");
        }
        resDiv.innerHTML = `<span style="color:var(--success)">✓ Success</span> • ${data.latency_ms}ms • ${data.backend}<br/><span class="mono text-dim">"${escapeHtml(data.response_preview)}"</span>`;
    } catch (err) {
        resDiv.innerHTML = `<span style="color:var(--danger)">✗ Failed</span><br/><span class="text-dim">${escapeHtml(err.message)}</span>`;
    } finally {
        if (btnNode) {
            btnNode.disabled = false;
            btnNode.textContent = "Test Route";
        }
    }
  }

  // ── Render: Architecture Diagram ──

  function renderArchitecture(machinesData) {
    const panel = dom.archPanel;
    panel.innerHTML = "";

    const diagram = el("div", { className: "arch-diagram" });

    // Layer 1: Users
    const usersNode = el("div", { className: "arch-node" });
    usersNode.innerHTML = `
      <div class="arch-node__title">Users / Tools / IDEs / Apps</div>
      <div class="arch-node__subtitle">Cursor · VS Code · Open WebUI · CLI</div>`;
    diagram.appendChild(el("div", { className: "arch-layer" }, usersNode));

    // Connector
    diagram.appendChild(el("div", { className: "arch-connector" }));

    // Layer 2: Gateway
    const gatewayNode = el("div", { className: "arch-node arch-node--gateway" });
    gatewayNode.innerHTML = `
      <div class="arch-node__title">⚡ AI Gateway / Router</div>
      <div class="arch-node__subtitle">OpenAI-compatible · Model routing · Load balancing</div>`;
    diagram.appendChild(el("div", { className: "arch-layer" }, gatewayNode));

    // Connector
    diagram.appendChild(el("div", { className: "arch-connector" }));

    // Layer 3: Machine nodes spread
    const spread = el("div", { className: "arch-spread" });

    // Determine machines to show
    const machines = machinesData && machinesData.machines ? machinesData.machines : [];

    const nodeConfigs = [
      {
        id: "macbook-m4pro",
        title: "Mac M4 Pro",
        subtitle: "Quality · 14B",
        cls: "arch-node--quality",
        icon: "💻",
        fallbackRole: "local_quality",
      },
      {
        id: "pc-rtx2070",
        title: "RTX 2070 PC",
        subtitle: "Fast · 7B",
        cls: "arch-node--fast",
        icon: "🖥",
        fallbackRole: "local_fast",
      },
      {
        id: "old-macbook",
        title: "Old MacBook",
        subtitle: "Utility · Not inference",
        cls: "arch-node--utility",
        icon: "📟",
        fallbackRole: "utility_node",
      },
      {
        id: "cloud",
        title: "Cloud",
        subtitle: "Explicit fallback only",
        cls: "arch-node--cloud",
        icon: "☁️",
      },
    ];

    for (const cfg of nodeConfigs) {
      const branch = el("div", { className: "arch-branch" });
      branch.appendChild(el("div", { className: "arch-connector--branch" }));

      const node = el("div", { className: `arch-node ${cfg.cls}` });

      // Try to pull real data
      const machineData = machines.find((m) => m.id === cfg.id);
      let subtitle = cfg.subtitle;
      if (machineData) {
        const tps = machineData.measured_tokens_per_second
          ? `${machineData.measured_tokens_per_second} tok/s`
          : "";
        const model = machineData.recommended_model || "";
        subtitle = [roleLabel(machineData.recommended_role), tps, model].filter(Boolean).join(" · ");
      }

      node.innerHTML = `
        <div class="arch-node__title">${cfg.icon} ${cfg.title}</div>
        <div class="arch-node__subtitle">${escapeHtml(subtitle)}</div>`;

      branch.appendChild(node);
      spread.appendChild(branch);
    }

    diagram.appendChild(spread);
    panel.appendChild(diagram);
  }

  // ── System Status ──

  function updateSystemStatus(machinesData, healthData) {
    const dot = dom.statusDot;
    // Remove all status classes
    dot.classList.remove("status-dot--healthy", "status-dot--warning", "status-dot--error");

    if (!machinesData || !machinesData.machines) {
      dot.classList.add("status-dot--error");
      dot.title = "API error — cannot load inventory";
      return;
    }

    const enabledNodes = machinesData.machines.filter(
      (m) => m.routing && m.routing.enabled
    );
    const hasWarnings = machinesData.machines.some(
      (m) => m.risk && m.risk !== "none"
    );

    if (enabledNodes.length === 0) {
      dot.classList.add("status-dot--error");
      dot.title = "No inference nodes enabled";
    } else if (hasWarnings) {
      dot.classList.add("status-dot--warning");
      dot.title = `${enabledNodes.length} node(s) enabled, warnings present`;
    } else {
      dot.classList.add("status-dot--healthy");
      dot.title = `${enabledNodes.length} node(s) healthy`;
    }
  }

  function updateTimestamp() {
    const now = new Date();
    state.lastRefresh = now;
    const ts = formatTime(now);
    dom.lastRefresh.textContent = `Last: ${ts}`;
    dom.footerRefresh.textContent = `Last refresh: ${ts}`;
  }

  // ── Fetch All ──

  async function fetchAll() {
    const results = await Promise.allSettled([
      apiFetch(API.machines),
      apiFetch(API.routes),
      apiFetch(API.profile),
      apiFetch(API.health),
      apiFetch(API.backendsHealth),
    ]);

    const [machinesRes, routesRes, profileRes, healthRes, backendsRes] = results;

    if (backendsRes && backendsRes.status === "fulfilled") {
      state.backendsHealth = backendsRes.value;
    }

    // Machines
    if (machinesRes.status === "fulfilled") {
      state.machines = machinesRes.value;
      renderFleet(state.machines, state.backendsHealth);
    } else {
      renderFleetError(machinesRes.reason);
    }

    // Routes
    if (routesRes.status === "fulfilled") {
      state.routes = routesRes.value;
      renderRouting(state.routes);
    } else {
      renderRoutingError(routesRes.reason);
    }

    // Profile
    if (profileRes.status === "fulfilled") {
      state.profile = profileRes.value;
      renderProfile(state.profile);
    } else {
      renderProfile({ status: "unavailable", error: profileRes.reason.message });
    }

    // Health
    if (healthRes.status === "fulfilled") {
      state.health = healthRes.value;
    }

    // Update status & timestamp
    updateSystemStatus(state.machines, state.health);
    updateTimestamp();

    // Architecture (uses machine data)
    renderArchitecture(state.machines);
    drawRadarChart(state.machines);

    // Observability
    fetchObservability();
  }

  // ── Error Renders ──

  function renderFleetError(err) {
    const grid = dom.fleetGrid;
    if (dom.fleetLoading) dom.fleetLoading.remove();
    grid.querySelectorAll(".glass-card, .glass-card-static").forEach((c) => c.remove());
    grid.appendChild(
      el("div", { className: "glass-card-static error-state" },
        el("div", { className: "error-state__icon" }, "⚠️"),
        el("p", { className: "text-muted" }, "Failed to load fleet data"),
        el("p", { className: "error-state__message" }, err.message || String(err))
      )
    );
  }

  function renderRoutingError(err) {
    const grid = dom.routingGrid;
    if (dom.routingLoading) dom.routingLoading.remove();
    grid.querySelectorAll(".route-card, .glass-card-static").forEach((c) => c.remove());
    grid.appendChild(
      el("div", { className: "glass-card-static error-state" },
        el("div", { className: "error-state__icon" }, "⚠️"),
        el("p", { className: "text-muted" }, "Failed to load routing data"),
        el("p", { className: "error-state__message" }, err.message || String(err))
      )
    );
  }

  // ── Auto-Refresh ──

  function startAutoRefresh() {
    stopAutoRefresh();
    state.autoRefreshId = setInterval(fetchAll, 30000);
  }

  function stopAutoRefresh() {
    if (state.autoRefreshId) {
      clearInterval(state.autoRefreshId);
      state.autoRefreshId = null;
    }
  }

  // ── Escape HTML ──

  function escapeHtml(str) {
    if (!str) return "";
    const div = document.createElement("div");
    div.appendChild(document.createTextNode(str));
    return div.innerHTML;
  }

  // ── Observability ──

  async function fetchObservability() {
    try {
      const [statsRes, eventsRes] = await Promise.all([
        fetch(API.stats).then(r => r.json()),
        fetch(API.events + "?limit=50").then(r => r.json())
      ]);
      renderObservability(statsRes, eventsRes);
    } catch (err) {
      console.error("Failed to fetch observability data", err);
      if (dom.observabilityPanel) {
        dom.observabilityPanel.innerHTML = '<div class="glass-card-static error-state"><p class="text-muted">Failed to load observability data.</p></div>';
      }
    }
  }

  function renderObservability(stats, eventsData) {
    const panel = dom.observabilityPanel;
    if (!panel) return;
    panel.innerHTML = "";

    // 1. Stats Bar
    if (stats && stats.total_requests > 0) {
      const statsBar = el("div", { className: "obs-stats-bar glass-card-static", style: { marginBottom: "1rem" } });
      
      const topChips = el("div", { style: { display: "flex", gap: "0.5rem", flexWrap: "wrap", marginBottom: "0.5rem" } });
      topChips.appendChild(el("div", { className: "obs-stat-chip badge" }, `Total: ${stats.total_requests}`));
      topChips.appendChild(el("div", { className: "obs-stat-chip badge" }, `Success: ${stats.success_rate_pct}%`));
      topChips.appendChild(el("div", { className: "obs-stat-chip badge" }, `Errors: ${stats.error_count}`));
      topChips.appendChild(el("div", { className: "obs-stat-chip badge" }, `Fallbacks: ${stats.fallback_count}`));
      topChips.appendChild(el("div", { className: "obs-stat-chip badge" }, `Streams: ${stats.stream_count}`));
      statsBar.appendChild(topChips);

      const routeChips = el("div", { style: { display: "flex", gap: "0.5rem", flexWrap: "wrap", marginBottom: "0.5rem" } });
      routeChips.appendChild(el("span", { className: "text-dim", style: { fontSize: "0.8rem", marginRight: "0.5rem" } }, "Routes:"));
      for (const [r, c] of Object.entries(stats.requests_by_route || {})) {
        routeChips.appendChild(el("div", { className: "obs-stat-chip badge" }, `${r} ${c}`));
      }
      statsBar.appendChild(routeChips);

      const latChips = el("div", { style: { display: "flex", gap: "0.5rem", flexWrap: "wrap" } });
      latChips.appendChild(el("span", { className: "text-dim", style: { fontSize: "0.8rem", marginRight: "0.5rem" } }, "Avg latency:"));
      for (const [b, l] of Object.entries(stats.avg_latency_ms_by_backend || {})) {
        latChips.appendChild(el("div", { className: "obs-stat-chip badge" }, `${b} ${l}ms`));
      }
      statsBar.appendChild(latChips);

      panel.appendChild(statsBar);
    }

    // 2. Sparklines
    if (stats && stats.avg_latency_ms_by_backend) {
      const sparkContainer = el("div", { className: "glass-card-static", style: { marginBottom: "1rem" } });
      let hasData = false;
      
      // Extract latencies by backend from recent events
      const recentLats = {};
      if (eventsData && eventsData.events) {
          const revEvents = [...eventsData.events].reverse();
          for (const ev of revEvents) {
              if (ev.latency_ms != null && ev.backend) {
                  if (!recentLats[ev.backend]) recentLats[ev.backend] = [];
                  recentLats[ev.backend].push(ev.latency_ms);
              }
          }
      }

      for (const [b, avgLat] of Object.entries(stats.avg_latency_ms_by_backend)) {
        const lats = recentLats[b] || [];
        if (lats.length === 0) continue;
        hasData = true;

        const row = el("div", { className: "obs-sparkline-row" });
        const label = el("div", { style: { width: "150px", fontSize: "0.8rem" } },
            el("div", { className: "mono text-dim" }, b),
            el("div", { style: { fontWeight: "bold" } }, `Avg: ${avgLat}ms`)
        );
        row.appendChild(label);

        // Draw SVG
        const w = 300;
        const h = 40;
        const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
        svg.setAttribute("width", "100%");
        svg.setAttribute("height", h);
        svg.style.flex = "1";
        
        const minL = Math.min(...lats);
        const maxL = Math.max(...lats);
        const range = maxL - minL || 1;
        
        const points = lats.map((val, i) => {
            const x = (i / Math.max(1, lats.length - 1)) * w;
            const y = h - ((val - minL) / range) * (h - 10) - 5;
            return `${x},${y}`;
        }).join(" ");
        
        const polyline = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
        polyline.setAttribute("points", points);
        polyline.setAttribute("fill", "none");
        polyline.setAttribute("stroke", "var(--primary)");
        polyline.setAttribute("stroke-width", "2");
        polyline.setAttribute("stroke-linecap", "round");
        polyline.setAttribute("stroke-linejoin", "round");
        
        svg.appendChild(polyline);
        row.appendChild(svg);
        sparkContainer.appendChild(row);
      }
      if (hasData) panel.appendChild(sparkContainer);
    }

    // 3. Event log
    if (eventsData && eventsData.events && eventsData.events.length > 0) {
        const tableWrap = el("div", { className: "glass-card-static", style: { overflowX: "auto", maxHeight: "400px" } });
        const table = el("table", { className: "obs-event-table" });
        table.innerHTML = `
            <thead>
                <tr>
                    <th>Time</th>
                    <th>Route</th>
                    <th>Backend</th>
                    <th>Model</th>
                    <th>Privacy</th>
                    <th>Latency</th>
                    <th>Status</th>
                </tr>
            </thead>
            <tbody></tbody>
        `;
        const tbody = table.querySelector("tbody");
        for (const ev of eventsData.events) {
            const tr = el("tr");
            if (ev.status === "error") tr.classList.add("status-error");
            else if (ev.fallback_used) tr.classList.add("fallback-used");

            let timeStr = "";
            try {
                const d = new Date(ev.timestamp);
                timeStr = formatTime(d);
            } catch (e) {
                timeStr = ev.timestamp;
            }

            const streamBadge = ev.stream || ev.error_code === "stream" ? `<span class="badge badge--stream">[stream]</span>` : "";
            const statusIcon = ev.status === "success" ? "✓" : "✗";
            
            tr.innerHTML = `
                <td class="mono">\${timeStr}</td>
                <td>\${escapeHtml(ev.route)}</td>
                <td>\${escapeHtml(ev.backend)}</td>
                <td>\${escapeHtml(ev.model)}</td>
                <td>\${escapeHtml(ev.privacy)}</td>
                <td class="mono">\${ev.latency_ms != null ? ev.latency_ms + "ms" : "—"}</td>
                <td>\${statusIcon} \${streamBadge}</td>
            `;
            tbody.appendChild(tr);
        }
        tableWrap.appendChild(table);
        panel.appendChild(tableWrap);
    }
  }

  // ── Layout Manager ──
  const ALL_WIDGETS = [
    { id: "widget-fleet", title: "Fleet Overview", icon: "🖥" },
    { id: "widget-spider", title: "Capability Radar", icon: "🕸" },
    { id: "widget-routing", title: "Routing Map", icon: "🔀" },
    { id: "widget-observability", title: "Observability Logs", icon: "📊" },
    { id: "widget-architecture", title: "Architecture Diagram", icon: "🏗" },
    { id: "widget-diagnostics", title: "Gateway Diagnostics", icon: "📍" }
  ];

  function initLayoutManager() {
    let pinned = [];
    try {
      const stored = localStorage.getItem("dashboard_widgets");
      if (stored) pinned = JSON.parse(stored);
      else pinned = ["widget-fleet", "widget-spider", "widget-observability", "widget-routing"];
    } catch (e) {
      pinned = ["widget-fleet", "widget-spider", "widget-observability", "widget-routing"];
    }

    let navPos = localStorage.getItem("nav_position") || "nav-left";

    function applyDocking() {
      dom.appContainer.className = navPos;
      dom.btnDockLayout.innerHTML = `<span class="icon">🔄</span> Dock: ${navPos.replace('nav-', '').toUpperCase()}`;
    }

    function applyWidgets() {
      const allWidgets = Array.from(document.querySelectorAll(".widget"));
      allWidgets.forEach(w => w.classList.add("hidden"));
      pinned.forEach((id, index) => {
        const w = document.getElementById(id);
        if (w) {
          w.classList.remove("hidden");
          w.style.order = index;
        }
      });
      localStorage.setItem("dashboard_widgets", JSON.stringify(pinned));
    }

    applyDocking();
    applyWidgets();

    dom.btnDockLayout.addEventListener("click", () => {
      if (navPos === "nav-left") navPos = "nav-top";
      else if (navPos === "nav-top") navPos = "nav-right";
      else navPos = "nav-left";
      localStorage.setItem("nav_position", navPos);
      applyDocking();
    });

    dom.btnAddWidget.addEventListener("click", () => {
      dom.widgetGallery.innerHTML = "";
      ALL_WIDGETS.forEach(w => {
        const isPinned = pinned.includes(w.id);
        const item = el("div", { className: `widget-gallery-item ${isPinned ? "pinned" : ""}` },
          el("span", { className: "icon" }, w.icon),
          el("div", { className: "title" }, w.title)
        );
        item.addEventListener("click", () => {
          if (pinned.includes(w.id)) {
            pinned = pinned.filter(id => id !== w.id);
            item.classList.remove("pinned");
          } else {
            pinned.push(w.id);
            item.classList.add("pinned");
          }
          applyWidgets();
        });
        dom.widgetGallery.appendChild(item);
      });
      dom.widgetModal.classList.remove("hidden");
    });

    dom.btnCloseModal.addEventListener("click", () => dom.widgetModal.classList.add("hidden"));

    document.querySelectorAll(".btn-remove-widget").forEach(btn => {
      btn.addEventListener("click", () => {
        const id = btn.getAttribute("data-target");
        pinned = pinned.filter(p => p !== id);
        applyWidgets();
      });
    });
  }

  // ── Radar Chart ──
  function drawRadarChart(machinesObj) {
    if (!dom.spiderPanel) return;
    if (!machinesObj || Object.keys(machinesObj).length === 0) {
      dom.spiderPanel.innerHTML = '<div class="empty-state text-dim">Waiting for node data...</div>';
      return;
    }

    const mKeys = Object.keys(machinesObj);
    const numAxes = 5;
    const axes = ["Compute", "Memory", "Speed", "IOPS", "Network"];
    const w = 300, h = 300;
    const cx = w/2, cy = h/2;
    const radius = 90;

    let svgStr = `<svg viewBox="0 0 ${w} ${h}" width="100%" height="100%" style="overflow:visible">`;
    
    // Grid polygons
    for (let level=1; level<=4; level++) {
        const r = radius * (level/4);
        let pts = [];
        for (let i=0; i<numAxes; i++) {
            const angle = (Math.PI*2 * i)/numAxes - Math.PI/2;
            pts.push(`${cx + r*Math.cos(angle)},${cy + r*Math.sin(angle)}`);
        }
        svgStr += `<polygon points="${pts.join(" ")}" fill="none" stroke="rgba(255,255,255,0.1)" />`;
    }
    
    // Axes and Labels
    for (let i=0; i<numAxes; i++) {
        const angle = (Math.PI*2 * i)/numAxes - Math.PI/2;
        const lx = cx + radius*Math.cos(angle);
        const ly = cy + radius*Math.sin(angle);
        svgStr += `<line x1="${cx}" y1="${cy}" x2="${lx}" y2="${ly}" stroke="rgba(255,255,255,0.2)" />`;
        
        const tx = cx + (radius+25)*Math.cos(angle);
        const ty = cy + (radius+25)*Math.sin(angle);
        svgStr += `<text x="${tx}" y="${ty}" fill="var(--text-dim)" font-size="11" font-family="var(--font-mono)" text-anchor="middle" dominant-baseline="middle">${axes[i]}</text>`;
    }

    // Machine Polygons
    const colors = ["#00d4ff", "#7c3aed", "#22c55e", "#f59e0b"];
    let colorIdx = 0;
    let legendStr = `<div style="display:flex; justify-content:center; gap:1rem; margin-top:1rem; flex-wrap:wrap">`;

    for (const key of mKeys) {
        const m = machinesObj[key];
        const sys = m.sys_info;
        if (!sys) continue;
        
        const computeScore = sys.gpu_count > 0 ? 0.9 : (sys.cpu_cores / 16);
        const memoryScore = sys.total_ram_gb / 64;
        const speedScore = m.status === 'online' ? 0.8 : 0.2;
        const iopsScore = sys.os.includes("macOS") ? 0.8 : 0.6;
        const netScore = 0.7;

        const scores = [
            Math.min(1, Math.max(0.1, computeScore)),
            Math.min(1, Math.max(0.1, memoryScore)),
            Math.min(1, Math.max(0.1, speedScore)),
            Math.min(1, Math.max(0.1, iopsScore)),
            Math.min(1, Math.max(0.1, netScore))
        ];

        let pts = [];
        for (let i=0; i<numAxes; i++) {
            const angle = (Math.PI*2 * i)/numAxes - Math.PI/2;
            const r = radius * scores[i];
            pts.push(`${cx + r*Math.cos(angle)},${cy + r*Math.sin(angle)}`);
        }
        
        const color = colors[colorIdx % colors.length];
        svgStr += `<polygon points="${pts.join(" ")}" fill="${color}33" stroke="${color}" stroke-width="2" style="filter: drop-shadow(0 0 8px ${color}80);" />`;
        
        legendStr += `<div style="display:flex; align-items:center; gap:6px; font-size:0.85rem; color:var(--text)"><span style="display:inline-block;width:10px;height:10px;background:${color};border-radius:50%;box-shadow:0 0 5px ${color}"></span>${humanName(key)}</div>`;
        colorIdx++;
    }
    
    svgStr += `</svg>`;
    dom.spiderPanel.innerHTML = `<div style="display:flex; flex-direction:column; align-items:center; width:100%; height:100%">
        <div style="flex:1; width:100%; display:flex; align-items:center; justify-content:center; min-height: 250px">${svgStr}</div>
        ${legendStr}</div>
    </div>`;
  }

  // ── Init ──

  function init() {
    initLayoutManager();

    // Auto-refresh toggle
    if (dom.autoRefreshToggle) {
      dom.autoRefreshToggle.addEventListener("change", (e) => {
        if (e.target.checked) startAutoRefresh();
        else stopAutoRefresh();
      });
    }

    // Manual refresh
    if (dom.refreshBtn) {
      dom.refreshBtn.addEventListener("click", fetchAll);
    }

    // Initial fetch
    fetchAll();
  }

  // Boot when DOM is ready
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
