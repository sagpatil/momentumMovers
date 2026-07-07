// Renders the snapshot into a sortable/filterable table + detail drawer.
// No framework — small enough to read top to bottom.

const TIER_CLASS = {
  "Day-1 Breakout": "tier-breakout",
  Pullback: "tier-pullback",
  Continuation: "tier-continuation",
  "Reversal/Failed": "tier-reversal",
};

const state = { rows: [], filter: "all", sortKey: "mqs", sortDir: -1, q: "" };

const fmtNum = (v, d = 2) => (v == null || Number.isNaN(v) ? "—" : Number(v).toFixed(d));
const fmtM = (v) => (v == null ? "—" : `${(v / 1e6).toFixed(1)}M`);
const fmtVol = (v) => (v == null ? "—" : v >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : `${(v / 1e3).toFixed(0)}K`);

function mqsColor(m) {
  if (m >= 60) return "var(--green)";
  if (m >= 50) return "var(--accent)";
  if (m >= 40) return "var(--amber)";
  return "var(--faint)";
}

// Closing strength: a vertical range bar with a marker where the close landed.
// High close = green, mid = amber, low (faded/wicked) = red.
function closeCell(cp) {
  if (cp == null) return `<span class="num" style="color:var(--faint)">—</span>`;
  const pct = Math.round(cp * 100);
  const col = cp >= 0.75 ? "var(--green)" : cp >= 0.45 ? "var(--amber)" : "var(--red)";
  return `<span class="closebar" title="Closed at ${pct}% of the day's range">
    <span class="track"><i style="height:${pct}%;background:${col}"></i></span>
    <span class="num" style="color:${col}">${pct}%</span></span>`;
}

function badgeHtml(b) {
  const cls = b.includes("Very Extended") ? "danger" : b.includes("Extended") || b.includes("Climactic") ? "warn" : "";
  return `<span class="badge ${cls}">${b}</span>`;
}

function catClass(s) {
  return s === "strong" ? "cat-strong" : s === "weak" ? "cat-weak" : "cat-none";
}

function volProfileLabel(vp) {
  if (!vp || !vp.persistence) return "—";
  const map = { building: "Building ↑", balanced: "Balanced", front_loaded: "Front-loaded ↓" };
  const pm = vp.pm_share == null ? "" : ` (${Math.round(vp.pm_share * 100)}% PM)`;
  return (map[vp.persistence] || vp.persistence) + pm;
}

export function renderApp(app, snapshot) {
  state.rows = snapshot.rows || [];
  const barsDate = state.rows.find((r) => r.bars)?.bars?.date; // may be undefined; informational only

  app.innerHTML = `
    <div class="wrap">
      <header class="hero">
        <h1>Momentum Movers</h1>
        <div class="sub">Daily Finviz momentum screen, scored & triaged by Momentum Quality.</div>
        <div class="meta">Snapshot <b>${snapshot.run_date}</b> · ${snapshot.n} hits · generated ${new Date(
    snapshot.generated_at
  ).toLocaleString()} · <code>${snapshot.filters_url}</code></div>
      </header>

      <div class="stats" id="stats"></div>

      <div class="controls">
        ${["all", "Day-1 Breakout", "Pullback", "Continuation", "Reversal/Failed"]
          .map((t) => `<button class="chip ${t === "all" ? "active" : ""}" data-filter="${t}">${t === "all" ? "All" : t}</button>`)
          .join("")}
        <span class="spacer"></span>
        <input id="search" placeholder="Search ticker…" />
      </div>

      <div class="table-scroll">
        <table>
          <thead><tr>
            ${[
              ["ticker", "Ticker"],
              ["mqs", "MQS"],
              ["change_pct", "Chg %"],
              ["close_position", "Close"],
              ["tier", "Setup"],
              ["rel_volume", "RelVol"],
              ["shs_float", "Float"],
              ["short_float_pct", "Short %"],
              ["burst_age", "Burst"],
              ["catalyst", "Catalyst"],
            ]
              .map(([k, label]) => `<th data-sort="${k}">${label}</th>`)
              .join("")}
          </tr></thead>
          <tbody id="tbody"></tbody>
        </table>
      </div>

      <div class="disclaimer">
        <b>How to read this:</b> MQS is a 0–100 <i>triage</i> score (closing strength, volume, float, short interest,
        catalyst, persistence) — a ranking heuristic, not a validated trading edge. <b>Closing strength</b> is the
        real close-in-range from the latest closed daily bar; <b>volume profile</b> (Building / Front-loaded) is from
        hourly bars (afternoon vs morning volume). Setup tiers, <span class="badge warn">⚠️ Extended</span> and
        pullback-depth come from <b>daily bars through the last close</b> — a fresh intraday breakout can read as
        “Continuation” until the close is in. <b>Reversal/Failed</b> = gave back &gt;50% of the breakout→peak run.
        Do your own work before trading.
      </div>
    </div>

    <div class="drawer-bg" id="drawerBg"></div>
    <aside class="drawer" id="drawer"></aside>
  `;

  renderStats();
  wireControls();
  renderTable();
}

function renderStats() {
  const rows = state.rows;
  const strong = rows.filter((r) => r.catalyst?.strength === "strong").length;
  const breakouts = rows.filter((r) => r.tier === "Day-1 Breakout").length;
  const repeat = rows.filter((r) => (r.burst_age ?? r.streak ?? 1) >= 2).length;
  const avgMqs = rows.length ? rows.reduce((a, r) => a + (r.mqs || 0), 0) / rows.length : 0;
  const cards = [
    ["Hits", rows.length],
    ["Avg MQS", avgMqs.toFixed(1)],
    ["Strong catalysts", strong],
    ["Fresh breakouts", breakouts],
    ["Repeat (≥2d)", repeat],
  ];
  document.getElementById("stats").innerHTML = cards
    .map(([l, v]) => `<div class="stat"><div class="v">${v}</div><div class="l">${l}</div></div>`)
    .join("");
}

function wireControls() {
  document.querySelectorAll(".chip").forEach((c) =>
    c.addEventListener("click", () => {
      document.querySelectorAll(".chip").forEach((x) => x.classList.remove("active"));
      c.classList.add("active");
      state.filter = c.dataset.filter;
      renderTable();
    })
  );
  document.querySelectorAll("th[data-sort]").forEach((th) =>
    th.addEventListener("click", () => {
      const k = th.dataset.sort;
      if (state.sortKey === k) state.sortDir *= -1;
      else { state.sortKey = k; state.sortDir = k === "ticker" ? 1 : -1; }
      renderTable();
    })
  );
  document.getElementById("search").addEventListener("input", (e) => {
    state.q = e.target.value.trim().toUpperCase();
    renderTable();
  });
  document.getElementById("drawerBg").addEventListener("click", closeDrawer);
}

function sortVal(r, k) {
  if (k === "catalyst") return { strong: 2, weak: 1, none: 0, unknown: 0.5 }[r.catalyst?.strength] ?? 0;
  if (k === "ticker") return r.ticker;
  return r[k] ?? -Infinity;
}

function renderTable() {
  let rows = state.rows.slice();
  if (state.filter !== "all") rows = rows.filter((r) => r.tier === state.filter);
  if (state.q) rows = rows.filter((r) => r.ticker.includes(state.q));
  rows.sort((a, b) => {
    const av = sortVal(a, state.sortKey), bv = sortVal(b, state.sortKey);
    if (av < bv) return -1 * state.sortDir;
    if (av > bv) return 1 * state.sortDir;
    return 0;
  });

  const tb = document.getElementById("tbody");
  if (!rows.length) { tb.innerHTML = `<tr><td colspan="9" class="empty">No matches.</td></tr>`; return; }

  tb.innerHTML = rows
    .map((r, i) => {
      const chgCls = r.change_pct >= 0 ? "pos" : "neg";
      const tierCls = TIER_CLASS[r.tier] || "";
      const cat = r.catalyst || {};
      const burst = r.burst_age ?? r.streak ?? 1;
      return `<tr data-idx="${state.rows.indexOf(r)}">
        <td class="tick">${r.ticker}<span class="co">${r.company || ""}</span></td>
        <td><span class="mqs"><span class="bar"><i style="width:${Math.min(100, r.mqs)}%;background:${mqsColor(r.mqs)}"></i></span>${fmtNum(r.mqs, 1)}</span></td>
        <td class="num ${chgCls}">${r.change_pct >= 0 ? "+" : ""}${fmtNum(r.change_pct, 1)}%</td>
        <td>${closeCell(r.close_position)}</td>
        <td>
          <span class="tier ${tierCls}">${r.tier}</span>
          ${r.badges?.length ? `<div class="badges">${r.badges.map(badgeHtml).join("")}</div>` : ""}
        </td>
        <td class="num">${fmtNum(r.rel_volume, 1)}×</td>
        <td class="num">${fmtM(r.shs_float)}</td>
        <td class="num">${r.short_float_pct == null ? "—" : fmtNum(r.short_float_pct, 1) + "%"}</td>
        <td class="streak ${burst >= 3 ? "hot" : ""}" title="${r.burst_thrust_days ?? "?"} thrust day(s) in burst">${burst}d</td>
        <td><span class="cat ${catClass(cat.strength)}">${cat.label || "—"}</span></td>
      </tr>`;
    })
    .join("");

  tb.querySelectorAll("tr[data-idx]").forEach((tr) =>
    tr.addEventListener("click", () => openDrawer(state.rows[+tr.dataset.idx]))
  );
}

function compRow(name, val, weight) {
  const pct = Math.round(val * 100);
  return `<div class="comp-row"><span>${name} <small style="color:var(--faint)">·${Math.round(weight * 100)}%</small></span>
    <span class="cbar"><i style="width:${pct}%"></i></span><span class="num">${pct}</span></div>`;
}

function openDrawer(r) {
  const d = document.getElementById("drawer");
  const cat = r.catalyst || {};
  const c = r.components || {};
  const w = r.weights || {};
  const kv = (k, v) => `<span class="k">${k}</span><span class="v">${v}</span>`;
  d.innerHTML = `
    <button class="close" id="closeDrawer">✕</button>
    <h2>${r.ticker} <span style="color:var(--faint);font-size:0.9rem;font-weight:400">${fmtNum(r.price, 2)}</span></h2>
    <div style="color:var(--muted);font-size:0.85rem">${r.company || ""} · ${r.sector || ""}</div>
    <div style="margin-top:14px"><span class="tier ${TIER_CLASS[r.tier] || ""}">${r.tier}</span>
      ${(r.badges || []).map(badgeHtml).join(" ")}</div>

    <div class="kv">
      ${kv("MQS", `<b style="color:${mqsColor(r.mqs)}">${fmtNum(r.mqs, 1)}</b>`)}
      ${kv("Change today", `${r.change_pct >= 0 ? "+" : ""}${fmtNum(r.change_pct, 1)}%`)}
      ${kv("Closing strength", r.close_position == null ? "—" : Math.round(r.close_position * 100) + "% of range")}
      ${kv("Volume profile", volProfileLabel(r.vol_profile))}
      ${kv("Rel volume", `${fmtNum(r.rel_volume, 1)}×`)}
      ${kv("Volume", fmtVol(r.volume))}
      ${kv("Float", fmtM(r.shs_float))}
      ${kv("Short float", r.short_float_pct == null ? "—" : fmtNum(r.short_float_pct, 1) + "%")}
      ${kv("Short ratio", fmtNum(r.short_ratio, 2))}
      ${kv("ATR(14)", fmtNum(r.atr14, 2))}
      ${kv("Dist > EMA10", r.dist_above_ema10_atr == null ? "—" : fmtNum(r.dist_above_ema10_atr, 1) + " ATR")}
      ${kv("Run (low → peak)", r.run_low == null ? "—" : `${fmtNum(r.run_low, 2)} → ${fmtNum(r.run_high, 2)}`)}
      ${kv("Retrace from peak", r.retrace_pct == null ? "—" : fmtNum(r.retrace_pct, 1) + "% given back")}
      ${kv("Burst", r.burst_age == null ? "—" : `day ${r.burst_age} · ${r.burst_thrust_days ?? 0} thrust`)}
      ${kv("Up-day streak", `${r.up_streak ?? 0}d`)}
      ${kv("Screener streak", `${r.streak || 1}d`)}
      ${kv("vs SMA50", r.sma50_dist_pct == null ? "—" : "+" + fmtNum(r.sma50_dist_pct, 0) + "%")}
      ${kv("vs SMA200", r.sma200_dist_pct == null ? "—" : "+" + fmtNum(r.sma200_dist_pct, 0) + "%")}
      ${kv("Earnings", r.earnings || "—")}
    </div>

    <div class="section-title">MQS breakdown</div>
    ${compRow("Closing str.", c.closing_strength || 0, w.closing_strength || 0)}
    ${compRow("Volume", c.volume || 0, w.volume || 0)}
    ${compRow("Float", c.float || 0, w.float || 0)}
    ${compRow("Short float", c.short_float || 0, w.short_float || 0)}
    ${compRow("Catalyst", c.catalyst || 0, w.catalyst || 0)}
    ${compRow("Persistence", c.persistence || 0, w.persistence || 0)}

    <div class="section-title">Catalyst</div>
    <div><span class="cat ${catClass(cat.strength)}">${cat.label || "—"}</span>
      <span class="cat src">${cat.source || ""}</span></div>
    <div style="color:var(--muted);font-size:0.82rem;margin-top:6px">${cat.reason || ""}</div>

    <div class="section-title">Recent headlines</div>
    ${
      (r.news || []).length
        ? r.news
            .map(
              (n) => `<div class="news-item"><a href="${n.link}" target="_blank" rel="noopener">${n.title}</a>
        <div class="meta">${n.source || ""} · ${n.date || ""}</div></div>`
            )
            .join("")
        : `<div style="color:var(--faint);font-size:0.82rem">No recent headlines.</div>`
    }
  `;
  document.getElementById("closeDrawer").addEventListener("click", closeDrawer);
  d.classList.add("open");
  document.getElementById("drawerBg").classList.add("open");
}

function closeDrawer() {
  document.getElementById("drawer").classList.remove("open");
  document.getElementById("drawerBg").classList.remove("open");
}
