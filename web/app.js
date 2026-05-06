const INDEX_URL = "../data/index.json";

const $ = (sel) => document.querySelector(sel);
const tweetsEl = $("#tweets");
const snapshotSelect = $("#snapshot-select");
const sourceFilter = $("#source-filter");
const typeFilter = $("#type-filter");
const sortBy = $("#sort-by");
const searchInput = $("#search");
const statsEl = $("#stats");

let allSnapshots = [];
let currentTweets = [];

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

async function init() {
  try {
    const resp = await fetch(INDEX_URL);
    if (!resp.ok) throw new Error(`No se pudo leer ${INDEX_URL}`);
    allSnapshots = await resp.json();
  } catch (e) {
    showError(`Error cargando index.json: ${e.message}. Recuerda servir el repo con un servidor HTTP (p.ej. <code>python -m http.server</code>).`);
    return;
  }

  if (allSnapshots.length === 0) {
    showError("No hay snapshots todavía. Ejecuta el script de captura primero.");
    return;
  }

  // Most recent first
  allSnapshots.sort((a, b) => b.fetched_at.localeCompare(a.fetched_at));

  populateSnapshots();

  snapshotSelect.addEventListener("change", loadCurrentSelection);
  sourceFilter.addEventListener("change", loadCurrentSelection);
  typeFilter.addEventListener("change", render);
  sortBy.addEventListener("change", render);
  searchInput.addEventListener("input", render);

  await loadCurrentSelection();
}

function populateSnapshots() {
  // Two synthetic options first: latest of each source + all
  const opts = [
    { value: "all_latest", label: "Último de cada fuente (combinado)" },
    { value: "all", label: "Todos los snapshots (combinado)" },
  ];
  for (const s of allSnapshots) {
    const dateStr = new Date(s.fetched_at).toLocaleString();
    opts.push({
      value: s.file,
      label: `${dateStr} — ${s.source} (@${s.username}) — ${s.count} tweets`,
    });
  }
  snapshotSelect.innerHTML = opts
    .map((o) => `<option value="${o.value}">${o.label}</option>`)
    .join("");
}

// ---------------------------------------------------------------------------
// Loading
// ---------------------------------------------------------------------------

async function loadCurrentSelection() {
  const value = snapshotSelect.value;
  const sourceWanted = sourceFilter.value;

  let snapshotsToLoad = [];
  if (value === "all") {
    snapshotsToLoad = allSnapshots;
  } else if (value === "all_latest") {
    const seen = new Set();
    for (const s of allSnapshots) {
      if (!seen.has(s.source)) {
        seen.add(s.source);
        snapshotsToLoad.push(s);
      }
    }
  } else {
    snapshotsToLoad = allSnapshots.filter((s) => s.file === value);
  }

  if (sourceWanted !== "all") {
    snapshotsToLoad = snapshotsToLoad.filter((s) => s.source === sourceWanted);
  }

  if (snapshotsToLoad.length === 0) {
    currentTweets = [];
    render();
    return;
  }

  tweetsEl.innerHTML = '<div class="empty">Cargando...</div>';

  const all = [];
  for (const s of snapshotsToLoad) {
    try {
      const resp = await fetch(`../${s.file}`);
      const data = await resp.json();
      for (const t of data.tweets) {
        all.push({ ...t, _source: data.source, _fetched_at: data.fetched_at });
      }
    } catch (e) {
      console.error(`Failed to load ${s.file}:`, e);
    }
  }

  // Deduplicate by tweet id, keep first occurrence (most recent snapshot first)
  const seen = new Set();
  currentTweets = all.filter((t) => {
    if (seen.has(t.id)) return false;
    seen.add(t.id);
    return true;
  });

  render();
}

// ---------------------------------------------------------------------------
// Filtering + rendering
// ---------------------------------------------------------------------------

function applyFilters(tweets) {
  let filtered = tweets;

  const type = typeFilter.value;
  if (type === "original") {
    filtered = filtered.filter(
      (t) => !t.is_retweet && !t.is_reply && !t.is_quote
    );
  } else if (type === "retweet") {
    filtered = filtered.filter((t) => t.is_retweet);
  } else if (type === "reply") {
    filtered = filtered.filter((t) => t.is_reply);
  } else if (type === "quote") {
    filtered = filtered.filter((t) => t.is_quote);
  }

  const q = searchInput.value.trim().toLowerCase();
  if (q) {
    filtered = filtered.filter((t) => {
      const haystack = [
        t.text,
        t.author?.name,
        t.author?.username,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return haystack.includes(q);
    });
  }

  return filtered;
}

function applySort(tweets) {
  const mode = sortBy.value;
  const copy = [...tweets];
  const numeric = (v) => {
    if (v == null) return 0;
    const n = parseInt(String(v).replace(/,/g, ""), 10);
    return isNaN(n) ? 0 : n;
  };

  if (mode === "date_desc") {
    copy.sort((a, b) => parseTwitterDate(b.created_at) - parseTwitterDate(a.created_at));
  } else if (mode === "date_asc") {
    copy.sort((a, b) => parseTwitterDate(a.created_at) - parseTwitterDate(b.created_at));
  } else if (mode === "likes") {
    copy.sort((a, b) => numeric(b.metrics?.likes) - numeric(a.metrics?.likes));
  } else if (mode === "retweets") {
    copy.sort((a, b) => numeric(b.metrics?.retweets) - numeric(a.metrics?.retweets));
  } else if (mode === "replies") {
    copy.sort((a, b) => numeric(b.metrics?.replies) - numeric(a.metrics?.replies));
  } else if (mode === "views") {
    copy.sort((a, b) => numeric(b.metrics?.views) - numeric(a.metrics?.views));
  }
  return copy;
}

function render() {
  const filtered = applySort(applyFilters(currentTweets));

  statsEl.textContent = `${filtered.length} de ${currentTweets.length} tweets`;

  if (filtered.length === 0) {
    tweetsEl.innerHTML = '<div class="empty">No hay tweets con estos filtros.</div>';
    return;
  }

  tweetsEl.innerHTML = filtered.map(renderTweet).join("");
}

function renderTweet(t) {
  const author = t.author || {};
  const m = t.metrics || {};
  const dateLocal = formatDate(t.created_at);
  const text = linkify(escapeHtml(t.text || ""));

  let context = "";
  if (t.is_retweet) {
    context = '<div class="tweet-context retweet">↻ Retweet</div>';
  } else if (t.is_reply) {
    context = '<div class="tweet-context reply">↩ Respuesta</div>';
  } else if (t.is_quote) {
    context = '<div class="tweet-context quote">❝ Cita</div>';
  }

  const langBadge = t.lang ? `<span class="lang-badge">${escapeHtml(t.lang)}</span>` : "";

  return `
    <article class="tweet">
      ${context}
      <div class="tweet-header">
        <span class="tweet-author">${escapeHtml(author.name || "?")}</span>
        <span class="tweet-handle">@${escapeHtml(author.username || "?")}</span>
        ${langBadge}
        <span class="tweet-date">${dateLocal}</span>
      </div>
      <div class="tweet-text">${text}</div>
      <div class="tweet-metrics">
        <span class="metric">💬 <strong>${formatNum(m.replies)}</strong></span>
        <span class="metric">🔁 <strong>${formatNum(m.retweets)}</strong></span>
        <span class="metric">❤️ <strong>${formatNum(m.likes)}</strong></span>
        <span class="metric">🔖 <strong>${formatNum(m.bookmarks)}</strong></span>
        <span class="metric">👁 <strong>${formatNum(m.views)}</strong></span>
      </div>
      ${t.url ? `<a class="tweet-link" href="${t.url}" target="_blank" rel="noopener">${escapeHtml(t.url)}</a>` : ""}
    </article>
  `;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function linkify(html) {
  return html.replace(
    /(https?:\/\/[^\s<]+)/g,
    '<a href="$1" target="_blank" rel="noopener">$1</a>'
  );
}

function parseTwitterDate(s) {
  if (!s) return 0;
  const d = new Date(s);
  return isNaN(d.getTime()) ? 0 : d.getTime();
}

function formatDate(s) {
  const t = parseTwitterDate(s);
  if (!t) return "";
  const d = new Date(t);
  return d.toLocaleString();
}

function formatNum(v) {
  if (v == null || v === "") return "0";
  const n = parseInt(String(v).replace(/,/g, ""), 10);
  if (isNaN(n)) return "0";
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "K";
  return n.toString();
}

function showError(msg) {
  tweetsEl.innerHTML = `<div class="error">${msg}</div>`;
}

init();
