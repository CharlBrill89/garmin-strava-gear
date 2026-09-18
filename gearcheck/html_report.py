"""Render the comparison as a self-contained interactive HTML page.

One file, no network, no build step: the data is embedded as JSON and a small
script turns it into a filterable worklist. Ticking an activity off is kept in
the browser's localStorage so the page can be regenerated without losing
progress.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from .matching import PROBLEM_VERDICTS, Pair
from .report import (
    GARMIN_ACTIVITY_URL,
    STRAVA_ACTIVITY_URL,
    VERDICT_LABELS,
    action_batches,
    priority_needed,
    summarise,
)


def _row(index: int, pair: Pair) -> dict[str, object]:
    anchor = pair.anchor
    return {
        "id": index,
        "date": anchor.start_utc.date().isoformat(),
        "start": anchor.start_utc.strftime("%Y-%m-%d %H:%M"),
        "name": (pair.garmin or pair.strava).name,  # type: ignore[union-attr]
        "km": round(anchor.distance_km, 2),
        "min": round(anchor.duration_s / 60, 1),
        "garmin_gear": pair.garmin.gear_label if pair.garmin else "",
        "strava_gear": pair.strava.gear_label if pair.strava else "",
        "verdict": pair.verdict,
        "detail": pair.detail,
        "method": pair.match_method,
        "garmin_id": pair.garmin.activity_id if pair.garmin else "",
        "strava_id": pair.strava.activity_id if pair.strava else "",
        "garmin_url": GARMIN_ACTIVITY_URL.format(pair.garmin.activity_id) if pair.garmin else "",
        "strava_url": STRAVA_ACTIVITY_URL.format(pair.strava.activity_id) if pair.strava else "",
        "problem": pair.verdict in PROBLEM_VERDICTS,
    }


def build_payload(pairs: list[Pair], start: date, end: date) -> dict[str, object]:
    ordered = sorted(pairs, key=lambda p: p.anchor.start_utc, reverse=True)
    rows = [_row(i, p) for i, p in enumerate(ordered)]
    index_of = {id(p): r["id"] for p, r in zip(ordered, rows)}

    batches = []
    for batch in action_batches(pairs):
        batches.append(
            {
                "verdict": batch.verdict,
                "garmin_gear": batch.garmin_gear,
                "strava_gear": batch.strava_gear,
                "span": batch.span,
                "needs_decision": priority_needed(batch.verdict),
                "rows": [index_of[id(p)] for p in batch.pairs],
            }
        )

    stats = summarise(pairs)
    return {
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "generated": date.today().isoformat(),
        "labels": VERDICT_LABELS,
        "problem_verdicts": sorted(PROBLEM_VERDICTS),
        "verdict_counts": dict(stats["verdicts"]),  # type: ignore[arg-type]
        "culprits": stats["culprits"].most_common(),  # type: ignore[union-attr]
        "rows": rows,
        "batches": batches,
    }


def write_html(pairs: list[Pair], path: Path, start: date, end: date) -> None:
    payload = json.dumps(build_payload(pairs, start, end), ensure_ascii=False)
    # A literal "</script>" inside a name would end the data block early.
    payload = payload.replace("</", "<\\/")
    html = _TEMPLATE.replace("__DATA__", payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")


_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Gear check</title>
<style>
  :root {
    --bg: #f6f7f9; --panel: #ffffff; --ink: #1c1e21; --muted: #6b7280;
    --line: #e3e6ea; --accent: #2563eb; --accent-ink: #ffffff;
    --ok: #15803d; --ok-bg: #e7f6ec; --warn: #b45309; --warn-bg: #fdf1e0;
    --bad: #b91c1c; --bad-bg: #fbe9e9; --info: #4b5563; --info-bg: #eceff3;
    --garmin: #0b7abf; --strava: #fc4c02; --done-bg: #f1f3f5;
    --radius: 10px; --shadow: 0 1px 2px rgba(0,0,0,.05);
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #101215; --panel: #181b1f; --ink: #e6e8eb; --muted: #9aa3ad;
      --line: #2a2f36; --accent: #60a5fa; --accent-ink: #0b1220;
      --ok: #4ade80; --ok-bg: #12301c; --warn: #fbbf24; --warn-bg: #3a2a0d;
      --bad: #f87171; --bad-bg: #3c1717; --info: #c3c9d1; --info-bg: #242930;
      --garmin: #5bb8f0; --strava: #ff7a3d; --done-bg: #14171a;
      --shadow: none;
    }
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; background: var(--bg); color: var(--ink); }
  body {
    font: 15px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    padding: 24px 16px 80px;
  }
  main { max-width: 1240px; margin: 0 auto; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  h2 { font-size: 17px; margin: 32px 0 12px; }
  .sub { color: var(--muted); margin: 0 0 20px; }
  a { color: var(--accent); }
  code { font-family: ui-monospace, Consolas, monospace; font-size: 13px; }

  .tiles { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 8px; }
  .tile {
    flex: 1 1 140px; min-width: 140px; background: var(--panel); border: 1px solid var(--line);
    border-radius: var(--radius); padding: 12px 14px; cursor: pointer; box-shadow: var(--shadow);
    text-align: left; color: var(--ink); font: inherit;
  }
  .tile:hover { border-color: var(--accent); }
  .tile.active { outline: 2px solid var(--accent); outline-offset: -1px; }
  .tile .n { font-size: 26px; font-weight: 600; line-height: 1.1; }
  .tile .l { font-size: 13px; color: var(--muted); margin-top: 2px; }
  .tile .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; vertical-align: 1px; }

  .badge {
    display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 12px;
    font-weight: 600; white-space: nowrap; line-height: 1.5;
  }
  .v-match { color: var(--ok); background: var(--ok-bg); }
  .v-name_variant { color: var(--ok); background: var(--ok-bg); }
  .v-different_shoe { color: var(--bad); background: var(--bad-bg); }
  .v-missing_on_strava, .v-missing_on_garmin { color: var(--warn); background: var(--warn-bg); }
  .v-missing_on_both, .v-no_strava_activity, .v-no_garmin_activity { color: var(--info); background: var(--info-bg); }
  .dot-match, .dot-name_variant { background: var(--ok); }
  .dot-different_shoe { background: var(--bad); }
  .dot-missing_on_strava, .dot-missing_on_garmin { background: var(--warn); }
  .dot-missing_on_both, .dot-no_strava_activity, .dot-no_garmin_activity { background: var(--muted); }

  .batch {
    background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius);
    margin-bottom: 12px; box-shadow: var(--shadow);
  }
  .batch summary {
    list-style: none; cursor: pointer; padding: 12px 16px; display: flex; gap: 12px;
    align-items: center; flex-wrap: wrap;
  }
  .batch summary::-webkit-details-marker { display: none; }
  .batch summary::before { content: "▸"; color: var(--muted); width: 12px; }
  .batch[open] summary::before { content: "▾"; }
  .batch .title { font-weight: 600; flex: 1 1 200px; }
  .batch .meta { color: var(--muted); font-size: 13px; }
  .batch .progress { font-size: 13px; color: var(--muted); min-width: 80px; text-align: right; }
  .batch .progress.done { color: var(--ok); font-weight: 600; }
  .batch .body { padding: 0 16px 16px; }
  .advice { margin: 0 0 12px; color: var(--ink); }
  .advice ul { margin: 6px 0 0 18px; padding: 0; }
  .advice b { font-weight: 600; }
  .advice .g { color: var(--garmin); } .advice .s { color: var(--strava); }
  .batch-actions { margin: 0 0 10px; display: flex; gap: 8px; flex-wrap: wrap; }

  .toolbar { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin: 0 0 12px; }
  .toolbar input[type=search] {
    flex: 1 1 220px; padding: 8px 12px; border: 1px solid var(--line); border-radius: 8px;
    background: var(--panel); color: var(--ink); font: inherit;
  }
  .toolbar label { color: var(--muted); font-size: 14px; display: flex; gap: 6px; align-items: center; }
  button.btn {
    padding: 7px 12px; border: 1px solid var(--line); border-radius: 8px; background: var(--panel);
    color: var(--ink); font: inherit; font-size: 13px; cursor: pointer;
  }
  button.btn:hover { border-color: var(--accent); }
  button.btn.primary { background: var(--accent); color: var(--accent-ink); border-color: var(--accent); }
  .count { color: var(--muted); font-size: 14px; }

  .tablewrap { overflow-x: auto; background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); box-shadow: var(--shadow); }
  table { border-collapse: collapse; width: 100%; font-size: 14px; }
  th, td { padding: 8px 10px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
  th { font-size: 12px; text-transform: uppercase; letter-spacing: .03em; color: var(--muted); white-space: nowrap; user-select: none; }
  th.sortable { cursor: pointer; }
  th.sortable:hover { color: var(--ink); }
  th .arrow { opacity: .5; margin-left: 3px; }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
  td.nowrap { white-space: nowrap; }
  td.detailcol { min-width: 280px; }
  tr:last-child td { border-bottom: 0; }
  tr.done td { color: var(--muted); background: var(--done-bg); }
  tr.done td .badge { opacity: .55; }
  td.links a { margin-right: 8px; font-size: 13px; white-space: nowrap; text-decoration: none; }
  td.links a.g { color: var(--garmin); } td.links a.s { color: var(--strava); }
  td.links a:hover { text-decoration: underline; }
  td .detail { color: var(--muted); font-size: 13px; }
  td .dash { color: var(--muted); }
  input[type=checkbox] { width: 16px; height: 16px; cursor: pointer; }
  .empty { padding: 24px; color: var(--muted); text-align: center; }
  .footnote { color: var(--muted); font-size: 13px; margin-top: 10px; }
  .culprits { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: 6px 16px; }
  .culprits div { display: flex; justify-content: space-between; gap: 16px; padding: 6px 0; border-bottom: 1px solid var(--line); }
  .culprits div:last-child { border-bottom: 0; }
  .culprits .n { font-variant-numeric: tabular-nums; color: var(--muted); }
</style>
</head>
<body>
<main>
  <h1>Garmin vs Strava gear check</h1>
  <p class="sub" id="sub"></p>

  <div class="tiles" id="tiles"></div>
  <p class="footnote">Click a tile to filter the table. Ticking a row marks it done — that's saved in this browser only, and survives regenerating the report.</p>

  <section id="plan-section">
    <h2>Action plan</h2>
    <p class="sub" id="plan-sub"></p>
    <div id="batches"></div>
  </section>

  <section id="culprit-section">
    <h2>Most common offenders</h2>
    <div class="culprits" id="culprits"></div>
  </section>

  <section>
    <h2>All activities</h2>
    <div class="toolbar">
      <input type="search" id="q" placeholder="Search activity, shoe, detail…">
      <label><input type="checkbox" id="hide-done"> hide done</label>
      <label><input type="checkbox" id="problems-only"> problems only</label>
      <span class="count" id="count"></span>
      <button class="btn" id="clear-filter" hidden>Clear filter</button>
    </div>
    <div class="tablewrap">
      <table id="table">
        <thead><tr id="head"></tr></thead>
        <tbody id="body"></tbody>
      </table>
      <div class="empty" id="empty" hidden>Nothing matches.</div>
    </div>
  </section>
</main>

<script id="data" type="application/json">__DATA__</script>
<script>
(function () {
  const DATA = JSON.parse(document.getElementById('data').textContent);
  const LABELS = DATA.labels;
  const PROBLEM = new Set(DATA.problem_verdicts);
  const STORE_KEY = 'gearcheck-done';
  const ORDER = ['different_shoe', 'missing_on_strava', 'missing_on_garmin',
                 'missing_on_both', 'no_strava_activity', 'no_garmin_activity',
                 'name_variant', 'match'];

  // ---- done state -------------------------------------------------------
  function rowKey(r) { return (r.garmin_id ? 'g:' + r.garmin_id : '') + '|' + (r.strava_id ? 's:' + r.strava_id : ''); }
  let done = new Set();
  try { done = new Set(JSON.parse(localStorage.getItem(STORE_KEY) || '[]')); } catch (e) {}
  function saveDone() { try { localStorage.setItem(STORE_KEY, JSON.stringify([...done])); } catch (e) {} }
  function isDone(r) { return done.has(rowKey(r)); }
  function setDone(r, v) { v ? done.add(rowKey(r)) : done.delete(rowKey(r)); saveDone(); render(); }

  // ---- helpers ----------------------------------------------------------
  const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const badge = v => `<span class="badge v-${v}">${esc(LABELS[v] || v)}</span>`;
  const activities = n => `${n} ${n === 1 ? 'activity' : 'activities'}`;
  function links(r) {
    const g = r.garmin_url ? `<a class="g" href="${esc(r.garmin_url)}" target="_blank" rel="noopener">Garmin ↗</a>` : '';
    const s = r.strava_url ? `<a class="s" href="${esc(r.strava_url)}" target="_blank" rel="noopener">Strava ↗</a>` : '';
    return g + s;
  }
  const gear = g => g ? esc(g) : '<span class="dash">—</span>';

  // ---- header -----------------------------------------------------------
  const total = DATA.rows.length;
  const problems = DATA.rows.filter(r => r.problem).length;
  document.getElementById('sub').innerHTML =
    `Window <b>${DATA.window.start}</b> to <b>${DATA.window.end}</b> · ` +
    `${activities(total)} compared · <b>${problems}</b> mismatched or missing` +
    (total ? ` (${Math.round(100 * problems / total)}%)` : '') +
    ` · generated ${DATA.generated}`;

  // ---- state ------------------------------------------------------------
  let filterVerdict = null;
  let sortKey = 'start', sortDir = -1;
  const q = document.getElementById('q');
  const hideDone = document.getElementById('hide-done');
  const problemsOnly = document.getElementById('problems-only');

  // ---- tiles ------------------------------------------------------------
  function renderTiles() {
    const el = document.getElementById('tiles');
    el.innerHTML = '';
    const all = document.createElement('button');
    all.className = 'tile' + (filterVerdict === null ? ' active' : '');
    all.innerHTML = `<div class="n">${total}</div><div class="l">All activities</div>`;
    all.onclick = () => { filterVerdict = null; render(); };
    el.appendChild(all);
    for (const v of ORDER) {
      const n = DATA.verdict_counts[v];
      if (!n) continue;
      const rows = DATA.rows.filter(r => r.verdict === v);
      const d = rows.filter(isDone).length;
      const b = document.createElement('button');
      b.className = 'tile' + (filterVerdict === v ? ' active' : '');
      b.innerHTML = `<div class="n">${n}</div><div class="l"><span class="dot dot-${v}"></span>${esc(LABELS[v])}` +
                    (d && PROBLEM.has(v) ? ` · ${d} done` : '') + `</div>`;
      b.onclick = () => { filterVerdict = filterVerdict === v ? null : v; render(); };
      el.appendChild(b);
    }
  }

  // ---- action plan ------------------------------------------------------
  function advice(b) {
    const g = `<b class="g">${esc(b.garmin_gear)}</b>`, s = `<b class="s">${esc(b.strava_gear)}</b>`;
    switch (b.verdict) {
      case 'different_shoe':
        return `Garmin says ${g}, Strava says ${s}.<ul>` +
          `<li>If Garmin is right → set Strava to <i>${esc(b.garmin_gear)}</i> (Strava supports bulk editing: My Activities → select → Edit).</li>` +
          `<li>If Strava is right → set Garmin to <i>${esc(b.strava_gear)}</i> (Garmin is one activity at a time).</li></ul>`;
      case 'missing_on_strava':
        return `Garmin has ${g}; Strava has nothing. Bulk-edit these on Strava in one pass.`;
      case 'missing_on_garmin':
        return `Strava has ${s}; Garmin has nothing. Set gear on each Garmin activity.`;
      case 'missing_on_both':
        return `No gear on either side — nothing to reconcile unless you want to backfill what you wore.`;
      default:
        return `These didn't pair up, so check whether they're the same run recorded twice or genuinely one-sided.`;
    }
  }

  function batchTitle(b) {
    if (b.garmin_gear && b.strava_gear) return `${esc(b.garmin_gear)} <span class="meta">vs</span> ${esc(b.strava_gear)}`;
    if (b.garmin_gear) return `${esc(b.garmin_gear)} <span class="meta">on Garmin</span>`;
    if (b.strava_gear) return `${esc(b.strava_gear)} <span class="meta">on Strava</span>`;
    return '';
  }

  const openBatches = new Set();
  function renderBatches() {
    const el = document.getElementById('batches');
    const section = document.getElementById('plan-section');
    if (!DATA.batches.length) {
      document.getElementById('plan-sub').textContent = 'Nothing mismatched — every activity agrees on gear.';
      el.innerHTML = '';
      return;
    }
    const decisions = DATA.batches.filter(b => b.needs_decision).length;
    const actionable = DATA.batches.filter(b => b.needs_decision).reduce((n, b) => n + b.rows.length, 0);
    const actionableDone = DATA.batches.filter(b => b.needs_decision)
      .reduce((n, b) => n + b.rows.filter(i => isDone(DATA.rows[i])).length, 0);
    document.getElementById('plan-sub').innerHTML =
      `<b>${actionable} activities to fix, across ${decisions} decision${decisions === 1 ? '' : 's'}.</b> ` +
      (actionableDone ? `<b>${actionableDone}</b> ticked off so far. ` : '') +
      `Each batch needs one call on which platform is right, then it's just clicking through the links. ` +
      `Batches needing no decision are last.`;

    el.innerHTML = '';
    DATA.batches.forEach((b, i) => {
      const rows = b.rows.map(j => DATA.rows[j]);
      const nDone = rows.filter(isDone).length;
      const det = document.createElement('details');
      det.className = 'batch';
      det.open = openBatches.has(i) || (openBatches.size === 0 && i === 0 && b.needs_decision);
      det.addEventListener('toggle', () => det.open ? openBatches.add(i) : openBatches.delete(i));
      const allDone = nDone === rows.length;
      det.innerHTML =
        `<summary>
           <span class="title">${i + 1}. ${badge(b.verdict)} &nbsp;${batchTitle(b)}</span>
           <span class="meta">${activities(rows.length)} · ${esc(b.span)}</span>
           <span class="progress${allDone ? ' done' : ''}">${allDone ? '✓ done' : nDone + ' / ' + rows.length + ' done'}</span>
         </summary>
         <div class="body">
           <p class="advice">${advice(b)}</p>
           <div class="batch-actions">
             <button class="btn" data-act="all">Mark all done</button>
             <button class="btn" data-act="none">Clear</button>
             ${rows.some(r => r.strava_url) ? '<button class="btn" data-act="open-s">Open all on Strava</button>' : ''}
             ${rows.some(r => r.garmin_url) ? '<button class="btn" data-act="open-g">Open all on Garmin</button>' : ''}
           </div>
           <div class="tablewrap"><table><thead><tr>
             <th></th><th>Date</th><th>Activity</th><th class="num">km</th><th>Garmin</th><th>Strava</th><th>Links</th>
           </tr></thead><tbody>` +
           rows.map(r => `<tr class="${isDone(r) ? 'done' : ''}">
             <td><input type="checkbox" data-id="${r.id}" ${isDone(r) ? 'checked' : ''}></td>
             <td class="nowrap">${r.date}</td><td class="nowrap">${esc(r.name)}</td><td class="num">${r.km.toFixed(2)}</td>
             <td class="nowrap">${gear(r.garmin_gear)}</td><td class="nowrap">${gear(r.strava_gear)}</td><td class="links">${links(r)}</td></tr>`).join('') +
           `</tbody></table></div>
         </div>`;
      det.querySelectorAll('input[type=checkbox]').forEach(cb =>
        cb.addEventListener('change', () => setDone(DATA.rows[+cb.dataset.id], cb.checked)));
      det.querySelectorAll('button[data-act]').forEach(btn => btn.addEventListener('click', ev => {
        ev.preventDefault();
        const act = btn.dataset.act;
        if (act === 'all' || act === 'none') {
          rows.forEach(r => act === 'all' ? done.add(rowKey(r)) : done.delete(rowKey(r)));
          saveDone(); openBatches.add(i); render();
        } else {
          const urls = rows.filter(r => !isDone(r)).map(r => act === 'open-s' ? r.strava_url : r.garmin_url).filter(Boolean);
          if (urls.length > 10 && !confirm(`Open ${urls.length} tabs?`)) return;
          urls.forEach(u => window.open(u, '_blank', 'noopener'));
        }
      }));
      el.appendChild(det);
    });
  }

  // ---- culprits ---------------------------------------------------------
  function renderCulprits() {
    const el = document.getElementById('culprits');
    if (!DATA.culprits.length) { document.getElementById('culprit-section').hidden = true; return; }
    el.innerHTML = DATA.culprits.map(([name, n]) => `<div><span>${esc(name)}</span><span class="n">${activities(n)}</span></div>`).join('');
  }

  // ---- table ------------------------------------------------------------
  const COLS = [
    { key: 'done', label: '', sortable: false },
    { key: 'start', label: 'Date', cls: 'nowrap' },
    { key: 'name', label: 'Activity' },
    { key: 'km', label: 'km', cls: 'num' },
    { key: 'min', label: 'min', cls: 'num' },
    { key: 'garmin_gear', label: 'Garmin gear' },
    { key: 'strava_gear', label: 'Strava gear' },
    { key: 'verdict', label: 'Verdict' },
    { key: 'detail', label: 'Detail' },
    { key: 'links', label: 'Links', sortable: false },
  ];

  function visibleRows() {
    const needle = q.value.trim().toLowerCase();
    let rows = DATA.rows.filter(r =>
      (filterVerdict === null || r.verdict === filterVerdict) &&
      (!problemsOnly.checked || r.problem) &&
      (!hideDone.checked || !isDone(r)) &&
      (!needle || [r.name, r.garmin_gear, r.strava_gear, r.detail, LABELS[r.verdict], r.date]
        .join(' ').toLowerCase().includes(needle)));
    const key = sortKey;
    rows.sort((a, b) => {
      let x = a[key], y = b[key];
      if (key === 'verdict') { x = ORDER.indexOf(x); y = ORDER.indexOf(y); }
      if (typeof x === 'string') { x = x.toLowerCase(); y = String(y).toLowerCase(); }
      return (x < y ? -1 : x > y ? 1 : 0) * sortDir;
    });
    return rows;
  }

  function renderTable() {
    const head = document.getElementById('head');
    head.innerHTML = COLS.map(c => {
      const sortable = c.sortable !== false;
      const arrow = sortable && sortKey === c.key ? `<span class="arrow">${sortDir > 0 ? '▲' : '▼'}</span>` : '';
      return `<th class="${c.cls || ''}${sortable ? ' sortable' : ''}" data-key="${c.key}">${esc(c.label)}${arrow}</th>`;
    }).join('');
    head.querySelectorAll('th.sortable').forEach(th => th.onclick = () => {
      const k = th.dataset.key;
      if (sortKey === k) sortDir = -sortDir; else { sortKey = k; sortDir = k === 'start' ? -1 : 1; }
      render();
    });

    const rows = visibleRows();
    const body = document.getElementById('body');
    body.innerHTML = rows.map(r => `<tr class="${isDone(r) ? 'done' : ''}">
      <td>${r.problem ? `<input type="checkbox" data-id="${r.id}" ${isDone(r) ? 'checked' : ''}>` : ''}</td>
      <td class="nowrap">${r.date}</td>
      <td class="nowrap">${esc(r.name)}</td>
      <td class="num">${r.km.toFixed(2)}</td>
      <td class="num">${r.min.toFixed(0)}</td>
      <td class="nowrap">${gear(r.garmin_gear)}</td>
      <td class="nowrap">${gear(r.strava_gear)}</td>
      <td>${badge(r.verdict)}</td>
      <td class="detailcol"><span class="detail">${esc(r.detail || '')}</span></td>
      <td class="links">${links(r)}</td>
    </tr>`).join('');
    body.querySelectorAll('input[type=checkbox]').forEach(cb =>
      cb.addEventListener('change', () => setDone(DATA.rows[+cb.dataset.id], cb.checked)));
    document.getElementById('empty').hidden = rows.length > 0;
    document.getElementById('count').textContent =
      rows.length === total ? `${activities(total)}` : `${rows.length} of ${activities(total)}`;
    const clear = document.getElementById('clear-filter');
    clear.hidden = filterVerdict === null && !q.value && !hideDone.checked && !problemsOnly.checked;
  }

  document.getElementById('clear-filter').onclick = () => {
    filterVerdict = null; q.value = ''; hideDone.checked = false; problemsOnly.checked = false; render();
  };
  q.addEventListener('input', renderTable);
  hideDone.addEventListener('change', renderTable);
  problemsOnly.addEventListener('change', renderTable);

  function render() { renderTiles(); renderBatches(); renderTable(); }
  renderCulprits();
  render();
})();
</script>
</body>
</html>
"""
