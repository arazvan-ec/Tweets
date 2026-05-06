// Talks to the Flask app at /api/snapshots and /api/tweets, which proxy
// to Supabase server-side. No JSON files on disk — Supabase is the only
// source of truth.

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
// SVG icons (Twitter-style)
// ---------------------------------------------------------------------------

const ICONS = {
  reply: '<svg viewBox="0 0 24 24"><path d="M1.751 10c0-4.42 3.584-8 8.005-8h4.366c4.49 0 8.129 3.64 8.129 8.13 0 2.96-1.607 5.68-4.196 7.11l-8.054 4.46v-3.69h-.067c-4.49.1-8.183-3.51-8.183-8.01z"/></svg>',
  retweet: '<svg viewBox="0 0 24 24"><path d="M4.5 3.88l4.432 4.14-1.364 1.46L5.5 7.55V16c0 1.1.896 2 2 2H13v2H7.5c-2.209 0-4-1.79-4-4V7.55L1.432 9.48.068 8.02 4.5 3.88zM16.5 6H11V4h5.5c2.209 0 4 1.79 4 4v8.45l2.068-1.93 1.364 1.46-4.432 4.14-4.432-4.14 1.364-1.46 2.068 1.93V8c0-1.1-.896-2-2-2z"/></svg>',
  like: '<svg viewBox="0 0 24 24"><path d="M16.697 5.5c-1.222-.06-2.679.51-3.89 2.16l-.805 1.09-.806-1.09C9.984 6.01 8.526 5.44 7.304 5.5c-1.243.07-2.349.78-2.91 1.91-.552 1.12-.633 2.78.479 4.82 1.074 1.97 3.257 4.27 7.129 6.61 3.87-2.34 6.052-4.64 7.126-6.61 1.111-2.04 1.03-3.7.477-4.82-.561-1.13-1.666-1.84-2.908-1.91zm4.187 7.69c-1.351 2.48-4.001 5.12-8.379 7.67l-.503.3-.504-.3c-4.379-2.55-7.029-5.19-8.382-7.67-1.36-2.5-1.41-4.86-.514-6.67.887-1.79 2.647-2.91 4.601-3.01 1.651-.09 3.368.56 4.798 2.01 1.429-1.45 3.146-2.1 4.796-2.01 1.954.1 3.714 1.22 4.601 3.01.896 1.81.846 4.17-.514 6.67z"/></svg>',
  views: '<svg viewBox="0 0 24 24"><path d="M8.75 21V3h2v18h-2zM18 21V8.5h2V21h-2zM4 21l.004-10h2L6 21H4zm9.248 0v-7h2v7h-2z"/></svg>',
  bookmark: '<svg viewBox="0 0 24 24"><path d="M4 4.5C4 3.12 5.119 2 6.5 2h11C18.881 2 20 3.12 20 4.5v18.44l-8-5.71-8 5.71V4.5zM6.5 4c-.276 0-.5.22-.5.5v14.56l6-4.29 6 4.29V4.5c0-.28-.224-.5-.5-.5h-11z"/></svg>',
  verified_blue: '<svg viewBox="0 0 22 22" class="verified-icon blue"><g><path d="M20.396 11c-.018-.646-.215-1.275-.57-1.816-.354-.54-.852-.972-1.438-1.246.223-.607.27-1.264.14-1.897-.131-.634-.437-1.218-.882-1.687-.47-.445-1.053-.75-1.687-.882-.633-.13-1.29-.083-1.897.14-.273-.587-.704-1.086-1.245-1.44S11.647 1.62 11 1.604c-.646.017-1.273.213-1.813.568s-.969.854-1.24 1.44c-.608-.223-1.267-.272-1.902-.14-.635.13-1.22.436-1.69.882-.445.47-.749 1.055-.878 1.688-.13.633-.08 1.29.144 1.896-.587.274-1.087.705-1.443 1.245-.356.54-.555 1.17-.574 1.817.02.647.218 1.276.574 1.817.356.54.856.972 1.443 1.245-.224.606-.274 1.263-.144 1.896.13.634.433 1.218.877 1.688.47.443 1.054.747 1.687.878.633.132 1.29.084 1.897-.136.274.586.705 1.084 1.246 1.439.54.354 1.17.551 1.816.569.647-.016 1.276-.213 1.817-.567s.972-.854 1.245-1.44c.604.239 1.266.296 1.903.164.636-.132 1.22-.447 1.68-.907.46-.46.776-1.044.908-1.681s.075-1.299-.165-1.903c.586-.274 1.084-.705 1.439-1.246.354-.54.551-1.17.569-1.816zM9.662 14.85l-3.429-3.428 1.293-1.302 2.072 2.072 4.4-4.794 1.347 1.246z"/></g></svg>',
  verified_gold: '<svg viewBox="0 0 22 22" class="verified-icon gold"><g><path d="M20.396 11c-.018-.646-.215-1.275-.57-1.816-.354-.54-.852-.972-1.438-1.246.223-.607.27-1.264.14-1.897-.131-.634-.437-1.218-.882-1.687-.47-.445-1.053-.75-1.687-.882-.633-.13-1.29-.083-1.897.14-.273-.587-.704-1.086-1.245-1.44S11.647 1.62 11 1.604c-.646.017-1.273.213-1.813.568s-.969.854-1.24 1.44c-.608-.223-1.267-.272-1.902-.14-.635.13-1.22.436-1.69.882-.445.47-.749 1.055-.878 1.688-.13.633-.08 1.29.144 1.896-.587.274-1.087.705-1.443 1.245-.356.54-.555 1.17-.574 1.817.02.647.218 1.276.574 1.817.356.54.856.972 1.443 1.245-.224.606-.274 1.263-.144 1.896.13.634.433 1.218.877 1.688.47.443 1.054.747 1.687.878.633.132 1.29.084 1.897-.136.274.586.705 1.084 1.246 1.439.54.354 1.17.551 1.816.569.647-.016 1.276-.213 1.817-.567s.972-.854 1.245-1.44c.604.239 1.266.296 1.903.164.636-.132 1.22-.447 1.68-.907.46-.46.776-1.044.908-1.681s.075-1.299-.165-1.903c.586-.274 1.084-.705 1.439-1.246.354-.54.551-1.17.569-1.816zM9.662 14.85l-3.429-3.428 1.293-1.302 2.072 2.072 4.4-4.794 1.347 1.246z"/></g></svg>',
  retweet_small: '<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M4.5 3.88l4.432 4.14-1.364 1.46L5.5 7.55V16c0 1.1.896 2 2 2H13v2H7.5c-2.209 0-4-1.79-4-4V7.55L1.432 9.48.068 8.02 4.5 3.88zM16.5 6H11V4h5.5c2.209 0 4 1.79 4 4v8.45l2.068-1.93 1.364 1.46-4.432 4.14-4.432-4.14 1.364-1.46 2.068 1.93V8c0-1.1-.896-2-2-2z"/></svg>',
  video: '<svg viewBox="0 0 24 24" width="14" height="14" fill="white"><path d="M7 4v16l13-8z"/></svg>',
};

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

async function init() {
  try {
    const resp = await fetch("/api/snapshots", { cache: "no-store" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    allSnapshots = await resp.json();
  } catch (e) {
    showError(`Error contactando con la API: ${e.message}`);
    return;
  }

  if (!Array.isArray(allSnapshots) || allSnapshots.length === 0) {
    showError("No hay snapshots todavía. Lanza la rutina de captura primero.");
    return;
  }

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
  const opts = [
    { value: "all_latest", label: "Último de cada fuente (combinado)" },
    { value: "all", label: "Todos los snapshots (combinado)" },
  ];
  for (const s of allSnapshots) {
    const dateStr = new Date(s.fetched_at).toLocaleString();
    opts.push({
      value: `id:${s.id}`,
      label: `${dateStr} — ${s.source} (@${s.username}) — ${s.count} tweets`,
    });
  }
  snapshotSelect.innerHTML = opts
    .map((o) => `<option value="${escapeAttr(o.value)}">${escapeHtml(o.label)}</option>`)
    .join("");
}

// ---------------------------------------------------------------------------
// Loading
// ---------------------------------------------------------------------------

async function loadCurrentSelection() {
  const value = snapshotSelect.value;
  const sourceWanted = sourceFilter.value;

  tweetsEl.innerHTML = '<div class="spinner">Cargando…</div>';

  const params = new URLSearchParams();
  if (value === "all" || value === "all_latest") {
    params.set("selection", value);
  } else if (value.startsWith("id:")) {
    params.set("selection", "snapshot");
    params.set("id", value.slice(3));
  }
  if (sourceWanted !== "all") params.set("source", sourceWanted);

  try {
    const resp = await fetch(`/api/tweets?${params.toString()}`, { cache: "no-store" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    currentTweets = await resp.json();
  } catch (e) {
    showError(`Error cargando tweets: ${e.message}`);
    return;
  }
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
  } else if (type === "media") {
    filtered = filtered.filter((t) => Array.isArray(t.media) && t.media.length > 0);
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

// ---------------------------------------------------------------------------
// Tweet rendering
// ---------------------------------------------------------------------------

function renderTweet(t) {
  // If retweet, render the underlying tweet but keep the retweet header
  const isRT = t.is_retweet && t.retweeted_tweet;
  const display = isRT ? t.retweeted_tweet : t;
  const retweeter = isRT ? t.author : null;

  const author = display.author || {};
  const m = display.metrics || {};

  let header = "";
  if (retweeter) {
    header = `<div class="tweet-context">${ICONS.retweet_small} ${escapeHtml(retweeter.name || retweeter.username || "")} retwitteó</div>`;
  }

  let replyingTo = "";
  if (display.is_reply && Array.isArray(display.user_mentions) && display.user_mentions.length > 0) {
    const m0 = display.user_mentions[0];
    replyingTo = `<div class="replying-to">Respondiendo a <a href="https://x.com/${escapeAttr(m0.username || "")}" target="_blank" rel="noopener">@${escapeHtml(m0.username || "")}</a></div>`;
  }

  const verifiedBadge = badgeFor(author);
  const text = renderText(display);
  const media = renderMedia(display.media);
  const quote = display.quoted_tweet ? renderQuote(display.quoted_tweet) : "";
  const card = renderUrlCard(display);
  const langBadge = display.lang ? `<span class="lang-badge">${escapeHtml(display.lang)}</span>` : "";

  const avatar = author.avatar
    ? `<img src="${escapeAttr(upgradeAvatar(author.avatar))}" alt="" loading="lazy">`
    : `<div style="width:100%;height:100%;background:#2f3336;"></div>`;

  return `
    <article class="tweet">
      <div class="tweet-avatar">${avatar}</div>
      <div class="tweet-body">
        ${header}
        <div class="tweet-header">
          <a class="tweet-name" href="https://x.com/${escapeAttr(author.username || "")}" target="_blank" rel="noopener">${escapeHtml(author.name || "?")}${verifiedBadge}</a>
          <span class="tweet-handle">@${escapeHtml(author.username || "?")}</span>
          <span class="tweet-sep">·</span>
          <a class="tweet-date" href="${escapeAttr(display.url || "#")}" target="_blank" rel="noopener" title="${escapeAttr(formatFullDate(display.created_at))}">${escapeHtml(formatRelative(display.created_at))}</a>
          ${langBadge}
        </div>
        ${replyingTo}
        <div class="tweet-text">${text}</div>
        ${media}
        ${quote}
        ${card}
        <div class="tweet-actions">
          <span class="tweet-action action-reply">${ICONS.reply}<span>${formatNum(m.replies)}</span></span>
          <span class="tweet-action action-retweet">${ICONS.retweet}<span>${formatNum(m.retweets)}</span></span>
          <span class="tweet-action action-like">${ICONS.like}<span>${formatNum(m.likes)}</span></span>
          <span class="tweet-action action-bookmark">${ICONS.bookmark}<span>${formatNum(m.bookmarks)}</span></span>
          <span class="tweet-action action-views">${ICONS.views}<span>${formatNum(m.views)}</span></span>
        </div>
      </div>
    </article>
  `;
}

function renderQuote(q) {
  const author = q.author || {};
  const verifiedBadge = badgeFor(author);
  const avatar = author.avatar
    ? `<img src="${escapeAttr(upgradeAvatar(author.avatar))}" alt="" loading="lazy">`
    : "";

  return `
    <a class="quote-tweet" href="${escapeAttr(q.url || "#")}" target="_blank" rel="noopener" style="text-decoration:none;display:block;">
      <div class="quote-header">
        <div class="quote-avatar">${avatar}</div>
        <span class="quote-name">${escapeHtml(author.name || "?")}${verifiedBadge}</span>
        <span class="quote-handle">@${escapeHtml(author.username || "?")}</span>
        <span class="quote-sep">·</span>
        <span class="quote-date">${escapeHtml(formatRelative(q.created_at))}</span>
      </div>
      <div class="quote-text">${renderText(q)}</div>
      ${renderMedia(q.media)}
    </a>
  `;
}

function renderText(t) {
  let text = t.text || "";

  // Remove t.co media-shortener URLs at the end (they appear when there's media)
  if (Array.isArray(t.media) && t.media.length > 0) {
    for (const m of t.media) {
      if (m.url) {
        text = text.split(m.url).join("");
      }
    }
  }
  text = text.trim();

  let html = escapeHtml(text);

  // Linkify URLs (replace t.co with display URLs)
  if (Array.isArray(t.urls)) {
    for (const u of t.urls) {
      if (!u.url) continue;
      const escUrl = escapeHtml(u.url);
      const display = u.display_url || u.expanded_url || u.url;
      const expanded = u.expanded_url || u.url;
      html = html.split(escUrl).join(
        `<a href="${escapeAttr(expanded)}" target="_blank" rel="noopener">${escapeHtml(display)}</a>`
      );
    }
  }

  // Linkify any remaining bare URLs
  html = html.replace(/(https?:\/\/[^\s<]+)/g, (url) => {
    if (url.includes(">") || url.includes("</a")) return url;
    return `<a href="${escapeAttr(url)}" target="_blank" rel="noopener">${escapeHtml(url)}</a>`;
  });

  // Linkify @mentions
  html = html.replace(/(^|\s)(@\w+)/g, (full, pre, mention) => {
    const username = mention.slice(1);
    return `${pre}<a href="https://x.com/${escapeAttr(username)}" target="_blank" rel="noopener">${escapeHtml(mention)}</a>`;
  });

  // Linkify #hashtags
  html = html.replace(/(^|\s)(#[\wÀ-ÿĀ-ɏ一-鿿_]+)/g, (full, pre, tag) => {
    const t = tag.slice(1);
    return `${pre}<a href="https://x.com/hashtag/${escapeAttr(t)}" target="_blank" rel="noopener">${escapeHtml(tag)}</a>`;
  });

  return html;
}

function renderMedia(media) {
  if (!Array.isArray(media) || media.length === 0) return "";
  const items = media.slice(0, 4);
  const count = items.length;
  const html = items.map((m) => {
    const src = m.media_url || "";
    const isVideo = m.type === "video" || m.type === "animated_gif";
    return `
      <div class="media-item">
        <img src="${escapeAttr(src)}" alt="" loading="lazy">
        ${isVideo ? `<span class="video-badge">${ICONS.video} ${m.type === "animated_gif" ? "GIF" : "VIDEO"}</span>` : ""}
      </div>
    `;
  }).join("");
  return `<div class="media-grid count-${count}">${html}</div>`;
}

function renderUrlCard(t) {
  // Only show a card when there's a non-Twitter URL and no media or quote
  if (Array.isArray(t.media) && t.media.length > 0) return "";
  if (t.quoted_tweet) return "";
  if (!Array.isArray(t.urls) || t.urls.length === 0) return "";

  // Take the first non-x.com link
  const u = t.urls.find((u) => {
    const e = u.expanded_url || "";
    return e && !/^https?:\/\/(x\.com|twitter\.com)/i.test(e);
  });
  if (!u) return "";

  const display = u.display_url || u.expanded_url || u.url;
  const href = u.expanded_url || u.url;
  return `
    <a class="url-card" href="${escapeAttr(href)}" target="_blank" rel="noopener">
      <div class="url-card-link">${escapeHtml(display)}</div>
    </a>
  `;
}

function badgeFor(author) {
  if (!author) return "";
  if (author.verified) return ICONS.verified_gold;
  if (author.is_blue_verified) return ICONS.verified_blue;
  return "";
}

function upgradeAvatar(url) {
  // Twitter serves _normal (48px) by default; bigger looks better on retina
  return url.replace("_normal.", "_bigger.");
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function escapeHtml(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function escapeAttr(str) {
  return escapeHtml(str);
}

function parseTwitterDate(s) {
  if (!s) return 0;
  const d = new Date(s);
  return isNaN(d.getTime()) ? 0 : d.getTime();
}

function formatRelative(s) {
  const t = parseTwitterDate(s);
  if (!t) return "";
  const diffMs = Date.now() - t;
  const sec = Math.round(diffMs / 1000);
  if (sec < 60) return `${Math.max(sec, 1)}s`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m`;
  const hrs = Math.round(min / 60);
  if (hrs < 24) return `${hrs}h`;
  const days = Math.round(hrs / 24);
  if (days < 7) return `${days}d`;
  const d = new Date(t);
  const now = new Date();
  const sameYear = d.getFullYear() === now.getFullYear();
  const opts = sameYear ? { day: "numeric", month: "short" } : { day: "numeric", month: "short", year: "numeric" };
  return d.toLocaleDateString(undefined, opts);
}

function formatFullDate(s) {
  const t = parseTwitterDate(s);
  if (!t) return "";
  return new Date(t).toLocaleString();
}

function formatNum(v) {
  if (v == null || v === "") return "";
  const n = parseInt(String(v).replace(/,/g, ""), 10);
  if (isNaN(n) || n === 0) return "";
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1).replace(/\.0$/, "") + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1).replace(/\.0$/, "") + "K";
  return n.toString();
}

function showError(msg) {
  tweetsEl.innerHTML = `<div class="error">${msg}</div>`;
}

init();
