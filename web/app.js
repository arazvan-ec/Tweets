// Tweets — single-page Twitter clone backed by /api/* (Supabase + twikit).
//
// Major features wired here:
//   * Tabs: For You / Following / My tweets
//   * Auto-refresh on load + freshness indicator
//   * Infinite scroll (IntersectionObserver)
//   * Pull-to-refresh (touch gestures)
//   * Like / Retweet / Bookmark / Reply with optimistic UI
//   * In-app profile pages at #u/<handle>
//   * New-tweet highlight via localStorage marker

// ---------------------------------------------------------------------------
// DOM refs
// ---------------------------------------------------------------------------

const $ = (sel) => document.querySelector(sel);
const contentEl = $("#content");
const tabsEl = $("#tabs");
const controlsEl = $("#controls");
const typeFilter = $("#type-filter");
const sortBy = $("#sort-by");
const searchInput = $("#search");
const refreshBtn = $("#refresh-btn");
const refreshLabel = $("#refresh-label");
const freshnessEl = $("#freshness");
const ptrEl = $("#ptr-indicator");
const sentinelEl = $("#scroll-sentinel");

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const LAST_SEEN_KEY = "tweets:last_seen_at";
const READ_IDS_KEY = "tweets:read_ids";
const TAB_LAST_SEEN_KEY = "tweets:last_seen_at:by_tab";
const READ_IDS_CAP = 2000;
const REPLIES_OPEN = new Set();
const REPLIES_THREAD_ONLY = new Set();   // tweet ids whose replies pane is in "author only" mode
const REPLIES_CACHE = new Map();
const PAGE_SIZE = 25;

const READ_IDS = loadReadIds();
let _readIdsSaveTimer = null;

function loadReadIds() {
  try {
    const raw = localStorage.getItem(READ_IDS_KEY);
    if (!raw) return new Set();
    return new Set(JSON.parse(raw));
  } catch (_) {
    return new Set();
  }
}

function persistReadIds() {
  clearTimeout(_readIdsSaveTimer);
  _readIdsSaveTimer = setTimeout(() => {
    try {
      // Cap size — drop oldest insertions (Set preserves insertion order).
      let arr = Array.from(READ_IDS);
      if (arr.length > READ_IDS_CAP) {
        arr = arr.slice(arr.length - READ_IDS_CAP);
        READ_IDS.clear();
        for (const id of arr) READ_IDS.add(id);
      }
      localStorage.setItem(READ_IDS_KEY, JSON.stringify(arr));
    } catch (_) {}
  }, 250);
}

// Cache of unread counts per tab for badges. Refreshed on tab change + on
// first mount via fetchOtherTabBadges.
const TAB_UNREAD = { for_you: 0, following: 0, mine: 0 };

function setTabBadge(tab, count) {
  TAB_UNREAD[tab] = count;
  const link = tabsEl?.querySelector?.(`[data-tab="${tab}"]`);
  if (!link) return;
  let badge = link.querySelector(".tab-badge");
  if (count > 0) {
    if (!badge) {
      badge = document.createElement("span");
      badge.className = "tab-badge";
      link.appendChild(badge);
    }
    badge.textContent = count > 99 ? "99+" : String(count);
  } else if (badge) {
    badge.remove();
  }
}

function countUnread(tweets, tab) {
  const tabSeen = loadTabLastSeen();
  const lastSeen = tabSeen[tab] || "1970-01-01T00:00:00Z";
  let unread = 0;
  for (const t of tweets) {
    const fs = t._first_seen_at;
    if (fs && fs > lastSeen) unread++;
  }
  return unread;
}

function updateTabBadges(currentList) {
  if (route.kind !== "home") return;
  // Update active tab from current rendered list
  setTabBadge(currentTab, countUnread(currentList || currentTweets, currentTab));
  // And mark active tab as seen so next visit doesn't keep showing the count
  const tabSeen = loadTabLastSeen();
  let latest = tabSeen[currentTab] || "";
  for (const t of (currentList || currentTweets)) {
    if (t._first_seen_at && t._first_seen_at > latest) latest = t._first_seen_at;
  }
  if (latest && latest !== tabSeen[currentTab]) {
    tabSeen[currentTab] = latest;
    saveTabLastSeen(tabSeen);
    // Clear badge after a beat — user has seen them now.
    setTimeout(() => setTabBadge(currentTab, 0), 1500);
  }
}

async function fetchOtherTabBadges() {
  const others = ["for_you", "following", "mine"].filter((t) => t !== currentTab);
  for (const tab of others) {
    try {
      const r = await fetch(`/api/tweets?selection=all_latest&source=${tab}&limit=25&offset=0`, { cache: "no-store" });
      if (!r.ok) continue;
      const data = await r.json();
      const tweets = data.tweets || [];
      setTabBadge(tab, countUnread(tweets, tab));
    } catch (_) { /* ignore */ }
  }
}

function loadTabLastSeen() {
  try {
    return JSON.parse(localStorage.getItem(TAB_LAST_SEEN_KEY) || "{}") || {};
  } catch (_) {
    return {};
  }
}

function saveTabLastSeen(map) {
  try { localStorage.setItem(TAB_LAST_SEEN_KEY, JSON.stringify(map)); } catch (_) {}
}

let snapshots = [];
let currentTab = "for_you";       // for_you | following | mine | profile
let currentTweets = [];           // hydrated rows currently rendered
let pageOffset = 0;
let pageHasMore = false;
let pageLoading = false;
let lastFetchedAt = null;         // ISO of newest snapshot, for freshness label
let route = { kind: "home" };     // {kind:'home'} | {kind:'profile', handle: '...'}

// ---------------------------------------------------------------------------
// SVG icons
// ---------------------------------------------------------------------------

const ICONS = {
  reply: '<svg viewBox="0 0 24 24"><path d="M1.751 10c0-4.42 3.584-8 8.005-8h4.366c4.49 0 8.129 3.64 8.129 8.13 0 2.96-1.607 5.68-4.196 7.11l-8.054 4.46v-3.69h-.067c-4.49.1-8.183-3.51-8.183-8.01z"/></svg>',
  article: '<svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor"><path d="M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm0 16H5V5h14v14zM7 7h10v2H7V7zm0 4h10v2H7v-2zm0 4h7v2H7v-2z"/></svg>',
  chevron_down: '<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6z"/></svg>',
  chevron_up: '<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M7.41 15.41L12 10.83l4.59 4.58L18 14l-6-6-6 6z"/></svg>',
  external: '<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M14 3v2h3.59l-9.83 9.83 1.41 1.41L19 6.41V10h2V3h-7zM19 19H5V5h7V3H5c-1.11 0-2 .9-2 2v14c0 1.1.89 2 2 2h14c1.1 0 2-.9 2-2v-7h-2v7z"/></svg>',
  retweet: '<svg viewBox="0 0 24 24"><path d="M4.5 3.88l4.432 4.14-1.364 1.46L5.5 7.55V16c0 1.1.896 2 2 2H13v2H7.5c-2.209 0-4-1.79-4-4V7.55L1.432 9.48.068 8.02 4.5 3.88zM16.5 6H11V4h5.5c2.209 0 4 1.79 4 4v8.45l2.068-1.93 1.364 1.46-4.432 4.14-4.432-4.14 1.364-1.46 2.068 1.93V8c0-1.1-.896-2-2-2z"/></svg>',
  like: '<svg viewBox="0 0 24 24"><path d="M16.697 5.5c-1.222-.06-2.679.51-3.89 2.16l-.805 1.09-.806-1.09C9.984 6.01 8.526 5.44 7.304 5.5c-1.243.07-2.349.78-2.91 1.91-.552 1.12-.633 2.78.479 4.82 1.074 1.97 3.257 4.27 7.129 6.61 3.87-2.34 6.052-4.64 7.126-6.61 1.111-2.04 1.03-3.7.477-4.82-.561-1.13-1.666-1.84-2.908-1.91zm4.187 7.69c-1.351 2.48-4.001 5.12-8.379 7.67l-.503.3-.504-.3c-4.379-2.55-7.029-5.19-8.382-7.67-1.36-2.5-1.41-4.86-.514-6.67.887-1.79 2.647-2.91 4.601-3.01 1.651-.09 3.368.56 4.798 2.01 1.429-1.45 3.146-2.1 4.796-2.01 1.954.1 3.714 1.22 4.601 3.01.896 1.81.846 4.17-.514 6.67z"/></svg>',
  like_filled: '<svg viewBox="0 0 24 24"><path d="M20.884 13.19c-1.351 2.48-4.001 5.12-8.379 7.67l-.503.3-.504-.3C7.119 18.31 4.469 15.67 3.116 13.19c-1.36-2.5-1.41-4.86-.514-6.67.887-1.79 2.647-2.91 4.601-3.01 1.651-.09 3.368.56 4.798 2.01 1.429-1.45 3.146-2.1 4.796-2.01 1.954.1 3.714 1.22 4.601 3.01.896 1.81.846 4.17-.514 6.67z"/></svg>',
  views: '<svg viewBox="0 0 24 24"><path d="M8.75 21V3h2v18h-2zM18 21V8.5h2V21h-2zM4 21l.004-10h2L6 21H4zm9.248 0v-7h2v7h-2z"/></svg>',
  filter: '<svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor"><path d="M3 5h18v2l-7 8v5l-4-2v-3L3 7V5z"/></svg>',
  bookmark: '<svg viewBox="0 0 24 24"><path d="M4 4.5C4 3.12 5.119 2 6.5 2h11C18.881 2 20 3.12 20 4.5v18.44l-8-5.71-8 5.71V4.5zM6.5 4c-.276 0-.5.22-.5.5v14.56l6-4.29 6 4.29V4.5c0-.28-.224-.5-.5-.5h-11z"/></svg>',
  bookmark_filled: '<svg viewBox="0 0 24 24"><path d="M4 4.5C4 3.12 5.119 2 6.5 2h11C18.881 2 20 3.12 20 4.5v18.44l-8-5.71-8 5.71V4.5z"/></svg>',
  verified_blue: '<svg viewBox="0 0 22 22" class="verified-icon blue"><g><path d="M20.396 11c-.018-.646-.215-1.275-.57-1.816-.354-.54-.852-.972-1.438-1.246.223-.607.27-1.264.14-1.897-.131-.634-.437-1.218-.882-1.687-.47-.445-1.053-.75-1.687-.882-.633-.13-1.29-.083-1.897.14-.273-.587-.704-1.086-1.245-1.44S11.647 1.62 11 1.604c-.646.017-1.273.213-1.813.568s-.969.854-1.24 1.44c-.608-.223-1.267-.272-1.902-.14-.635.13-1.22.436-1.69.882-.445.47-.749 1.055-.878 1.688-.13.633-.08 1.29.144 1.896-.587.274-1.087.705-1.443 1.245-.356.54-.555 1.17-.574 1.817.02.647.218 1.276.574 1.817.356.54.856.972 1.443 1.245-.224.606-.274 1.263-.144 1.896.13.634.433 1.218.877 1.688.47.443 1.054.747 1.687.878.633.132 1.29.084 1.897-.136.274.586.705 1.084 1.246 1.439.54.354 1.17.551 1.816.569.647-.016 1.276-.213 1.817-.567s.972-.854 1.245-1.44c.604.239 1.266.296 1.903.164.636-.132 1.22-.447 1.68-.907.46-.46.776-1.044.908-1.681s.075-1.299-.165-1.903c.586-.274 1.084-.705 1.439-1.246.354-.54.551-1.17.569-1.816zM9.662 14.85l-3.429-3.428 1.293-1.302 2.072 2.072 4.4-4.794 1.347 1.246z"/></g></svg>',
  verified_gold: '<svg viewBox="0 0 22 22" class="verified-icon gold"><g><path d="M20.396 11c-.018-.646-.215-1.275-.57-1.816-.354-.54-.852-.972-1.438-1.246.223-.607.27-1.264.14-1.897-.131-.634-.437-1.218-.882-1.687-.47-.445-1.053-.75-1.687-.882-.633-.13-1.29-.083-1.897.14-.273-.587-.704-1.086-1.245-1.44S11.647 1.62 11 1.604c-.646.017-1.273.213-1.813.568s-.969.854-1.24 1.44c-.608-.223-1.267-.272-1.902-.14-.635.13-1.22.436-1.69.882-.445.47-.749 1.055-.878 1.688-.13.633-.08 1.29.144 1.896-.587.274-1.087.705-1.443 1.245-.356.54-.555 1.17-.574 1.817.02.647.218 1.276.574 1.817.356.54.856.972 1.443 1.245-.224.606-.274 1.263-.144 1.896.13.634.433 1.218.877 1.688.47.443 1.054.747 1.687.878.633.132 1.29.084 1.897-.136.274.586.705 1.084 1.246 1.439.54.354 1.17.551 1.816.569.647-.016 1.276-.213 1.817-.567s.972-.854 1.245-1.44c.604.239 1.266.296 1.903.164.636-.132 1.22-.447 1.68-.907.46-.46.776-1.044.908-1.681s.075-1.299-.165-1.903c.586-.274 1.084-.705 1.439-1.246.354-.54.551-1.17.569-1.816zM9.662 14.85l-3.429-3.428 1.293-1.302 2.072 2.072 4.4-4.794 1.347 1.246z"/></g></svg>',
  retweet_small: '<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M4.5 3.88l4.432 4.14-1.364 1.46L5.5 7.55V16c0 1.1.896 2 2 2H13v2H7.5c-2.209 0-4-1.79-4-4V7.55L1.432 9.48.068 8.02 4.5 3.88zM16.5 6H11V4h5.5c2.209 0 4 1.79 4 4v8.45l2.068-1.93 1.364 1.46-4.432 4.14-4.432-4.14 1.364-1.46 2.068 1.93V8c0-1.1-.896-2-2-2z"/></svg>',
  video: '<svg viewBox="0 0 24 24" width="14" height="14" fill="white"><path d="M7 4v16l13-8z"/></svg>',
  back: '<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M7.414 13l5.043 5.04-1.414 1.42L3.586 12l7.457-7.46 1.414 1.42L7.414 11H21v2H7.414z"/></svg>',
};

const SOURCE_LABEL = {
  for_you: "Para ti",
  following: "Siguiendo",
  mine: "Mis tweets",
};

// ---------------------------------------------------------------------------
// Lightbox
// ---------------------------------------------------------------------------

let lightboxItems = [];
let lightboxIndex = 0;

function openLightbox(items, index) {
  if (!Array.isArray(items) || items.length === 0) return;
  lightboxItems = items;
  lightboxIndex = Math.max(0, Math.min(index, items.length - 1));
  renderLightbox();
  document.getElementById("lightbox").classList.add("open");
  document.body.style.overflow = "hidden";
}

function closeLightbox() {
  const lb = document.getElementById("lightbox");
  lb.classList.remove("open");
  document.getElementById("lightbox-stage").innerHTML = "";
  document.body.style.overflow = "";
  lightboxItems = [];
}

function navLightbox(delta) {
  if (lightboxItems.length === 0) return;
  lightboxIndex = (lightboxIndex + delta + lightboxItems.length) % lightboxItems.length;
  renderLightbox();
}

function renderLightbox() {
  const m = lightboxItems[lightboxIndex];
  if (!m) return;
  const stage = document.getElementById("lightbox-stage");
  const counter = document.getElementById("lightbox-counter");
  const isVideo = m.type === "video" || m.type === "animated_gif";

  if (isVideo) {
    const variant = bestVideoVariant(m.video_variants);
    if (variant && variant.url) {
      stage.innerHTML = `
        <video src="${escapeAttr(variant.url)}" poster="${escapeAttr(m.media_url || "")}"
               controls autoplay ${m.type === "animated_gif" ? "loop muted" : ""} playsinline></video>`;
    } else {
      // No playable variant captured — fall back to the poster.
      stage.innerHTML = `<img src="${escapeAttr(fullResImageUrl(m.media_url || ""))}" alt="">`;
    }
  } else {
    stage.innerHTML = `<img src="${escapeAttr(fullResImageUrl(m.media_url || ""))}" alt="">`;
  }

  const showNav = lightboxItems.length > 1;
  document.querySelector(".lightbox-prev").hidden = !showNav;
  document.querySelector(".lightbox-next").hidden = !showNav;
  counter.textContent = showNav ? `${lightboxIndex + 1} / ${lightboxItems.length}` : "";
}

function setupLightbox() {
  const lb = document.getElementById("lightbox");

  lb.addEventListener("click", (ev) => {
    // Stop propagation from inner image/video so click on them doesn't close
    if (ev.target.closest("[data-stop-bubble]") && !ev.target.closest("[data-action]")) {
      return;
    }
    const action = ev.target.closest("[data-action]");
    if (!action) return;
    if (action.dataset.action === "lb-close") closeLightbox();
    else if (action.dataset.action === "lb-prev") { ev.stopPropagation(); navLightbox(-1); }
    else if (action.dataset.action === "lb-next") { ev.stopPropagation(); navLightbox(1); }
  });

  document.addEventListener("keydown", (ev) => {
    if (!lb.classList.contains("open")) return;
    if (ev.key === "Escape") closeLightbox();
    else if (ev.key === "ArrowLeft") navLightbox(-1);
    else if (ev.key === "ArrowRight") navLightbox(1);
  });

  // Touch swipe inside the stage to navigate
  let startX = null;
  lb.addEventListener("touchstart", (ev) => {
    if (ev.touches.length !== 1) return;
    startX = ev.touches[0].clientX;
  }, { passive: true });
  lb.addEventListener("touchend", (ev) => {
    if (startX == null) return;
    const dx = (ev.changedTouches[0]?.clientX || startX) - startX;
    if (Math.abs(dx) > 60) navLightbox(dx < 0 ? 1 : -1);
    startX = null;
  });
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

async function init() {
  // Tabs
  tabsEl.addEventListener("click", (ev) => {
    const link = ev.target.closest("[data-tab]");
    if (!link) return;
    ev.preventDefault();
    const tab = link.dataset.tab;
    if (tab === currentTab && route.kind === "home") return;
    location.hash = `#${tab}`;
  });

  // Brand → home
  document.querySelector(".brand").addEventListener("click", (ev) => {
    ev.preventDefault();
    location.hash = `#${currentTab}`;
  });

  // Filters
  typeFilter.addEventListener("change", () => render());
  sortBy.addEventListener("change", () => render());
  searchInput.addEventListener("input", () => render());

  // Refresh
  refreshBtn.addEventListener("click", () => triggerRefresh(false));

  // Tweets click delegation (replies, compose, like/RT/bookmark, profile)
  contentEl.addEventListener("click", onContentClick);

  // Hash routing
  window.addEventListener("hashchange", handleRoute);

  // Infinite scroll
  setupInfiniteScroll();

  // Pull to refresh
  setupPullToRefresh();

  // Periodic freshness label update
  setInterval(updateFreshness, 15000);

  // Lightbox / media viewer
  setupLightbox();

  // Long-press on tweet date / metrics to show absolute / exact info on mobile
  setupLongPressTooltips();

  await refreshSnapshotList();
  handleRoute();

  // Auto-refresh once at load if data is older than 30s — same call as
  // clicking Actualizar manually, just silent.
  if (snapshots.length === 0 || isStale(30)) {
    triggerRefresh(/*silent=*/true);
  }
}

// ---------------------------------------------------------------------------
// Routing
// ---------------------------------------------------------------------------

function handleRoute() {
  const h = (location.hash || "").replace(/^#/, "");
  if (h.startsWith("u/")) {
    route = { kind: "profile", handle: h.slice(2) };
    renderProfile();
    return;
  }
  if (["for_you", "following", "mine"].includes(h)) {
    currentTab = h;
  }
  route = { kind: "home" };
  // Mark active tab in DOM
  for (const a of tabsEl.querySelectorAll("[data-tab]")) {
    a.classList.toggle("active", a.dataset.tab === currentTab);
  }
  controlsEl.style.display = "";
  tabsEl.style.display = "";
  loadFirstPage();
}

function navigateProfile(handle) {
  if (!handle) return;
  location.hash = `#u/${handle}`;
}

function applyAuthorFilter(handle) {
  if (!handle) return;
  searchInput.value = `@${handle}`;
  if (route.kind !== "home") {
    location.hash = `#${currentTab}`;
    // render() runs after route handler picks up the change
    setTimeout(() => render(), 0);
  } else {
    render();
  }
  showToast(`Filtrando por @${handle}`, "info");
  searchInput.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// ---------------------------------------------------------------------------
// Snapshots / freshness
// ---------------------------------------------------------------------------

async function refreshSnapshotList() {
  try {
    const resp = await fetch("/api/snapshots", { cache: "no-store" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    snapshots = await resp.json();
    snapshots.sort((a, b) => b.fetched_at.localeCompare(a.fetched_at));
    lastFetchedAt = snapshots[0]?.fetched_at || null;
    updateFreshness();
  } catch (e) {
    console.error("snapshots error", e);
  }
}

function isStale(seconds) {
  if (!lastFetchedAt) return true;
  return (Date.now() - new Date(lastFetchedAt).getTime()) / 1000 > seconds;
}

function updateFreshness() {
  if (!lastFetchedAt) {
    freshnessEl.textContent = "";
    return;
  }
  const ageSec = Math.max(0, Math.round((Date.now() - new Date(lastFetchedAt).getTime()) / 1000));
  freshnessEl.textContent = ageSec < 60
    ? `actualizado hace ${ageSec}s`
    : `actualizado hace ${Math.round(ageSec / 60)} min`;
}

// ---------------------------------------------------------------------------
// Loading tweets
// ---------------------------------------------------------------------------

async function loadFirstPage() {
  pageOffset = 0;
  pageHasMore = false;
  currentTweets = [];
  contentEl.innerHTML = '<div class="spinner">Cargando…</div>';
  await loadMore();
  // Don't await — runs in background, just refreshes the other tabs' badges.
  fetchOtherTabBadges();
}

async function loadMore() {
  if (pageLoading || (!pageHasMore && pageOffset > 0)) return;
  pageLoading = true;

  if (pageOffset > 0) {
    const loader = document.createElement("div");
    loader.className = "bottom-loader";
    loader.id = "bottom-loader";
    loader.textContent = "Cargando más…";
    contentEl.appendChild(loader);
  }

  // Merge a wider window of recent snapshots for the algorithmic feeds —
  // the 'For You' algorithm tends to recycle content, so showing only the
  // latest snapshot keeps the same posts pinned. With window=60 we union
  // ~the last hour of captures and the new-tweet sort surfaces what just
  // appeared.
  const params = new URLSearchParams({
    selection: "all_latest",
    source: currentTab,
    limit: String(PAGE_SIZE),
    offset: String(pageOffset),
    window: currentTab === "mine" ? "1" : "60",
  });

  try {
    const resp = await fetch(`/api/tweets?${params.toString()}`, { cache: "no-store" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    currentTweets = currentTweets.concat(data.tweets || []);
    pageOffset += data.tweets?.length || 0;
    pageHasMore = !!data.has_more;
    document.getElementById("bottom-loader")?.remove();
    render();
    if (!pageHasMore && currentTweets.length > 0) {
      const end = document.createElement("div");
      end.className = "bottom-loader";
      end.textContent = "·";
      contentEl.appendChild(end);
    }
  } catch (e) {
    document.getElementById("bottom-loader")?.remove();
    if (currentTweets.length === 0) {
      showError(`Error cargando tweets: ${e.message}`);
    } else {
      showToast(`Error: ${e.message}`, "error");
    }
  } finally {
    pageLoading = false;
  }
}

function setupInfiniteScroll() {
  const io = new IntersectionObserver((entries) => {
    if (route.kind !== "home") return;
    if (entries.some((e) => e.isIntersecting) && pageHasMore) {
      loadMore();
    }
  }, { rootMargin: "200px" });
  io.observe(sentinelEl);
}

// ---------------------------------------------------------------------------
// Refresh trigger
// ---------------------------------------------------------------------------

async function triggerRefresh(silent = false) {
  if (refreshBtn.disabled) return;
  refreshBtn.disabled = true;
  refreshBtn.classList.add("loading");
  if (!silent) refreshLabel.textContent = "Capturando…";

  // Snapshot the user's "last seen" cutoff BEFORE the refresh so we can
  // identify which tweets the refresh actually surfaces.
  const cutoff = localStorage.getItem(LAST_SEEN_KEY) || "1970-01-01T00:00:00Z";

  try {
    // Manual refreshes go deep (max=200) so we paginate further into the
    // algorithmic feed and surface tweets X wouldn't otherwise serve us
    // in the top 50. The cron keeps a lighter --max for steady coverage.
    const max = silent ? 100 : 200;
    const resp = await fetch(`/api/refresh?source=all_feeds&max=${max}`, { method: "POST" });
    if (!resp.ok) throw new Error(await resp.text() || `HTTP ${resp.status}`);
    const data = await resp.json();
    await refreshSnapshotList();
    if (route.kind === "home") await loadFirstPage();
    if (!silent) {
      const fresh = currentTweets.filter(
        (t) => t._first_seen_at && t._first_seen_at > cutoff
      ).length;
      const counts = (data.latest_snapshots || [])
        .map((s) => `${SOURCE_LABEL[s.source] || s.source}: ${s.count}`)
        .join(" · ");
      const msg = fresh > 0
        ? `✓ ${fresh} ${fresh === 1 ? "tweet nuevo" : "tweets nuevos"} (${counts})`
        : `✓ Sin novedades — ${counts}`;
      showToast(msg, fresh > 0 ? "success" : "");
      // Bump the marker so the next refresh highlights only what's truly
      // new since this click. NUEVO pills now reset on every manual refresh.
      let latest = cutoff;
      for (const t of currentTweets) {
        if (t._first_seen_at && t._first_seen_at > latest) latest = t._first_seen_at;
      }
      localStorage.setItem(LAST_SEEN_KEY, latest);
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  } catch (e) {
    console.error(e);
    if (!silent) showToast(`Error: ${e.message}`, "error");
  } finally {
    refreshBtn.disabled = false;
    refreshBtn.classList.remove("loading");
    refreshLabel.textContent = "Actualizar";
  }
}

// ---------------------------------------------------------------------------
// Pull to refresh
// ---------------------------------------------------------------------------

// Long-press anywhere on `[title]` elements inside the feed to show the title
// as a transient toast — gives mobile users access to absolute dates / exact
// counts that desktop gets via hover.
function setupLongPressTooltips() {
  let timer = null;
  let target = null;
  const start = (ev) => {
    const el = ev.target.closest("[title]");
    if (!el) return;
    const text = el.getAttribute("title");
    if (!text) return;
    target = el;
    clearTimeout(timer);
    timer = setTimeout(() => {
      ev.preventDefault?.();
      showToast(text, "info");
      // Suppress the upcoming click so we don't open the link.
      const block = (e) => { e.preventDefault(); e.stopPropagation(); el.removeEventListener("click", block, true); };
      el.addEventListener("click", block, true);
      setTimeout(() => el.removeEventListener("click", block, true), 800);
    }, 500);
  };
  const cancel = () => {
    clearTimeout(timer);
    timer = null;
    target = null;
  };
  document.addEventListener("touchstart", start, { passive: true });
  document.addEventListener("touchend", cancel, { passive: true });
  document.addEventListener("touchmove", cancel, { passive: true });
  document.addEventListener("touchcancel", cancel, { passive: true });
}

function setupPullToRefresh() {
  let startY = null;
  let pulling = false;

  document.addEventListener("touchstart", (ev) => {
    if (window.scrollY > 0) return;
    startY = ev.touches[0].clientY;
    pulling = true;
  }, { passive: true });

  document.addEventListener("touchmove", (ev) => {
    if (!pulling || startY == null) return;
    const dy = ev.touches[0].clientY - startY;
    if (dy < 0) {
      pulling = false;
      ptrEl.classList.remove("visible");
      return;
    }
    if (dy > 60) {
      ptrEl.classList.add("visible");
    } else {
      ptrEl.classList.remove("visible");
    }
  }, { passive: true });

  document.addEventListener("touchend", async () => {
    if (!pulling) return;
    pulling = false;
    if (ptrEl.classList.contains("visible")) {
      ptrEl.classList.add("spinning");
      await triggerRefresh(currentTab, /*silent=*/true);
      ptrEl.classList.remove("visible", "spinning");
    }
    startY = null;
  });
}

// ---------------------------------------------------------------------------
// Filtering + rendering (home tab)
// ---------------------------------------------------------------------------

function applyFilters(tweets) {
  let filtered = tweets;
  const type = typeFilter.value;
  if (type === "original") filtered = filtered.filter((t) => !t.is_retweet && !t.is_reply && !t.is_quote);
  else if (type === "retweet") filtered = filtered.filter((t) => t.is_retweet);
  else if (type === "reply") filtered = filtered.filter((t) => t.is_reply);
  else if (type === "quote") filtered = filtered.filter((t) => t.is_quote);
  else if (type === "media") filtered = filtered.filter((t) => Array.isArray(t.media) && t.media.length > 0);

  const q = searchInput.value.trim().toLowerCase();
  if (q) {
    filtered = filtered.filter((t) => {
      const haystack = [t.text, t.author?.name, t.author?.username].filter(Boolean).join(" ").toLowerCase();
      return haystack.includes(q);
    });
  }
  return filtered;
}

function applySort(tweets) {
  const mode = sortBy.value;
  const copy = [...tweets];
  const num = (v) => {
    if (v == null) return 0;
    const n = parseInt(String(v).replace(/,/g, ""), 10);
    return isNaN(n) ? 0 : n;
  };
  if (mode === "first_seen_desc") {
    // Default: matches server-side ordering, no client re-sort needed.
    return copy;
  }
  if (mode === "date_desc") copy.sort((a, b) => parseTwitterDate(b.created_at) - parseTwitterDate(a.created_at));
  else if (mode === "date_asc") copy.sort((a, b) => parseTwitterDate(a.created_at) - parseTwitterDate(b.created_at));
  else if (mode === "likes") copy.sort((a, b) => num(b.metrics?.likes) - num(a.metrics?.likes));
  else if (mode === "retweets") copy.sort((a, b) => num(b.metrics?.retweets) - num(a.metrics?.retweets));
  else if (mode === "replies") copy.sort((a, b) => num(b.metrics?.replies) - num(a.metrics?.replies));
  else if (mode === "views") copy.sort((a, b) => num(b.metrics?.views) - num(a.metrics?.views));
  return copy;
}

function render() {
  if (route.kind !== "home") return;
  const filtered = applySort(applyFilters(currentTweets));

  if (filtered.length === 0) {
    contentEl.innerHTML = '<div class="empty">No hay tweets en este feed todavía. Pulsa Actualizar para capturar.</div>';
    return;
  }

  contentEl.innerHTML = filtered.map(renderTweet).join("");
  observeOgCards();
  observeReadTweets();
  updateTabBadges(filtered);
}

// ---------------------------------------------------------------------------
// Tweet card rendering
// ---------------------------------------------------------------------------

function renderTweet(t) {
  const isRT = t.is_retweet && t.retweeted_tweet;
  const display = isRT ? t.retweeted_tweet : t;
  const retweeter = isRT ? t.author : null;

  const author = display.author || {};
  const m = display.metrics || {};
  const vs = display.viewer_state || {};

  const lastSeen = localStorage.getItem(LAST_SEEN_KEY) || "1970-01-01T00:00:00Z";
  const isNew = !!(t._first_seen_at && t._first_seen_at > lastSeen);

  let header = "";
  if (retweeter) {
    header = `<div class="tweet-context">${ICONS.retweet_small} ${escapeHtml(retweeter.name || retweeter.username || "")} retwitteó</div>`;
  }

  let replyingTo = "";
  if (display.is_reply && Array.isArray(display.user_mentions) && display.user_mentions.length > 0) {
    const m0 = display.user_mentions[0];
    replyingTo = `<div class="replying-to">Respondiendo a <a href="#u/${escapeAttr(m0.username || "")}" data-handle="${escapeAttr(m0.username || "")}">@${escapeHtml(m0.username || "")}</a></div>`;
  }

  const verifiedBadge = badgeFor(author);
  const text = renderText(display);
  const media = renderMedia(display.media);
  const quote = display.quoted_tweet ? renderQuote(display.quoted_tweet) : "";
  const article = renderArticleCard(display);
  const card = article ? "" : renderUrlCard(display);
  const langBadge = display.lang ? `<span class="lang-badge">${escapeHtml(display.lang)}</span>` : "";
  const newPill = isNew ? `<span class="new-pill">nuevo</span>` : "";
  const sourceBadge = renderSourceBadge(t._sources);

  const avatar = author.avatar
    ? `<img src="${escapeAttr(upgradeAvatar(author.avatar))}" alt="" loading="lazy">`
    : `<div style="width:100%;height:100%;background:#2f3336;"></div>`;

  const isRead = READ_IDS.has(display.id);

  return `
    <article class="tweet ${isNew ? "is-new" : ""} ${isRead ? "is-read" : ""}" data-tweet-id="${escapeAttr(display.id)}">
      <div class="tweet-avatar" data-action="profile" data-handle="${escapeAttr(author.username || "")}">${avatar}</div>
      <div class="tweet-body">
        ${header}
        <div class="tweet-header">
          <span class="tweet-name" data-action="profile" data-handle="${escapeAttr(author.username || "")}">${escapeHtml(author.name || "?")}${verifiedBadge}</span>
          <span class="tweet-handle" data-action="profile" data-handle="${escapeAttr(author.username || "")}">@${escapeHtml(author.username || "?")}</span>
          <span class="tweet-sep">·</span>
          <a class="tweet-date" href="${escapeAttr(display.url || "#")}" target="_blank" rel="noopener" title="${escapeAttr(formatFullDate(display.created_at))}">${escapeHtml(formatRelative(display.created_at))}</a>
          ${langBadge}
          ${newPill}
          ${sourceBadge}
          <button class="tweet-author-filter" data-action="filter-author" data-handle="${escapeAttr(author.username || "")}" title="Solo tweets de @${escapeAttr(author.username || "")}" aria-label="Solo tweets de @${escapeAttr(author.username || "")}">${ICONS.filter}</button>
        </div>
        ${replyingTo}
        <div class="tweet-text">${text}</div>
        ${media}
        ${quote}
        ${article}
        ${card}
        <div class="tweet-actions">
          <button class="tweet-action action-reply" data-action="toggle-replies" title="Comentarios · ${escapeAttr(formatExact(m.replies))}">
            ${ICONS.reply}<span>${formatNum(m.replies)}</span>
          </button>
          <button class="tweet-action action-retweet ${vs.retweeted ? "is-active" : ""}" data-action="toggle-retweet" title="Retweet · ${escapeAttr(formatExact(m.retweets))}">
            ${ICONS.retweet}<span>${formatNum(m.retweets)}</span>
          </button>
          <button class="tweet-action action-like ${vs.liked ? "is-active" : ""}" data-action="toggle-like" title="Like · ${escapeAttr(formatExact(m.likes))}">
            ${vs.liked ? ICONS.like_filled : ICONS.like}<span>${formatNum(m.likes)}</span>
          </button>
          <button class="tweet-action action-bookmark ${vs.bookmarked ? "is-active" : ""}" data-action="toggle-bookmark" title="Bookmark · ${escapeAttr(formatExact(m.bookmarks))}">
            ${vs.bookmarked ? ICONS.bookmark_filled : ICONS.bookmark}<span>${formatNum(m.bookmarks)}</span>
          </button>
          <button class="tweet-action action-views" title="Vistas · ${escapeAttr(formatExact(m.views))}">
            ${ICONS.views}<span>${formatNum(m.views)}</span>
          </button>
          <button class="tweet-action action-thread" data-action="toggle-thread" title="Ver hilo del autor">
            🧵
          </button>
        </div>
        ${renderEngagement(m)}
      </div>
    </article>
  `;
}

function renderSourceBadge(sources) {
  if (!Array.isArray(sources) || sources.length < 2) return "";
  // Tweet appears in more than one feed — informative for "viralidad cross-feed"
  const labels = sources.map((s) => SOURCE_LABEL[s] || s).join(" + ");
  return `<span class="source-badge" title="${escapeAttr(labels)}">${escapeHtml(labels)}</span>`;
}

function renderQuote(q) {
  const author = q.author || {};
  const verifiedBadge = badgeFor(author);
  const avatar = author.avatar ? `<img src="${escapeAttr(upgradeAvatar(author.avatar))}" loading="lazy">` : "";
  return `
    <a class="quote-tweet" href="${escapeAttr(q.url || "#")}" target="_blank" rel="noopener">
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
  if (Array.isArray(t.media) && t.media.length > 0) {
    for (const m of t.media) {
      if (m.url) text = text.split(m.url).join("");
    }
  }
  const article = extractArticle(t);
  if (article) {
    if (article.short) text = text.split(article.short).join("");
    if (article.url) text = text.split(article.url).join("");
  }
  text = text.trim();

  const urlMap = new Map();
  for (const u of (t.urls || [])) {
    if (u && u.url) {
      urlMap.set(u.url, {
        display: u.display_url || u.expanded_url || u.url,
        expanded: u.expanded_url || u.url,
      });
    }
  }

  const URL_RE = /https?:\/\/\S+/g;
  const pieces = [];
  let last = 0;
  let m;
  while ((m = URL_RE.exec(text)) !== null) {
    if (m.index > last) pieces.push({ kind: "text", value: text.slice(last, m.index) });
    pieces.push({ kind: "url", value: m[0] });
    last = m.index + m[0].length;
  }
  if (last < text.length) pieces.push({ kind: "text", value: text.slice(last) });

  return pieces.map((p) => {
    if (p.kind === "url") {
      const map = urlMap.get(p.value);
      const display = map ? map.display : p.value;
      const expanded = map ? map.expanded : p.value;
      return `<a href="${escapeAttr(expanded)}" target="_blank" rel="noopener">${escapeHtml(display)}</a>`;
    }
    let h = escapeHtml(p.value);
    h = h.replace(/(^|\s)(@\w+)/g, (full, pre, mention) => {
      const username = mention.slice(1);
      return `${pre}<a href="#u/${escapeAttr(username)}" data-handle="${escapeAttr(username)}">${escapeHtml(mention)}</a>`;
    });
    h = h.replace(/(^|\s)(#[\wÀ-ÿĀ-ɏ一-鿿_]+)/g, (full, pre, tag) => {
      const slug = tag.slice(1);
      return `${pre}<a href="https://x.com/hashtag/${escapeAttr(slug)}" target="_blank" rel="noopener">${escapeHtml(tag)}</a>`;
    });
    return h;
  }).join("");
}

function renderMedia(media) {
  if (!Array.isArray(media) || media.length === 0) return "";
  const items = media.slice(0, 4);
  const count = items.length;
  // Serialize the items list once per tweet so the lightbox can pull it
  // straight from the DOM without a side-channel registry.
  const payload = encodeURIComponent(JSON.stringify(items));
  return `<div class="media-grid count-${count}" data-media="${payload}">${items.map((m, i) => {
    const src = m.media_url || "";
    const isVideo = m.type === "video" || m.type === "animated_gif";
    return `
      <div class="media-item ${isVideo ? "is-video" : ""}" data-action="open-media" data-index="${i}">
        <img src="${escapeAttr(src)}" alt="" loading="lazy">
        ${isVideo ? `
          <span class="video-badge">${ICONS.video} ${m.type === "animated_gif" ? "GIF" : "VIDEO"}</span>
          <span class="play-btn"><svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg></span>
        ` : ""}
      </div>
    `;
  }).join("")}</div>`;
}

function bestVideoVariant(variants) {
  if (!Array.isArray(variants) || variants.length === 0) return null;
  // Prefer mp4, then highest bitrate.
  const mp4s = variants.filter((v) => v.content_type === "video/mp4" && v.url);
  if (mp4s.length) {
    return mp4s.reduce((a, b) => ((b.bitrate || 0) > (a.bitrate || 0) ? b : a));
  }
  return variants.find((v) => v.url) || null;
}

function fullResImageUrl(url) {
  // pbs.twimg.com images accept ?name=large / orig / 4096x4096 for higher
  // resolution than the default. Default is usually 'small' / no suffix.
  if (!url) return url;
  try {
    const u = new URL(url);
    if (u.hostname.includes("twimg.com") && u.pathname.startsWith("/media/")) {
      u.searchParams.set("name", "large");
      // If the path doesn't have an extension, default to jpg; X serves
      // either, but explicit format keeps the URL clean.
      if (!u.searchParams.has("format") && /\.(jpg|jpeg|png|webp)$/i.test(u.pathname)) {
        // already has extension via path — leave as-is
      }
      return u.toString();
    }
  } catch (_) {}
  return url;
}

const ARTICLE_RE = /https?:\/\/(?:x\.com|twitter\.com|mobile\.twitter\.com)\/i\/article\/(\d+)/i;

function extractArticle(t) {
  if (!t) return null;
  if (Array.isArray(t.urls)) {
    for (const u of t.urls) {
      const expanded = u.expanded_url || u.url || "";
      const m = expanded.match(ARTICLE_RE);
      if (m) {
        return {
          id: m[1],
          url: expanded,
          short: u.url || "",
          display: u.display_url || expanded,
        };
      }
    }
  }
  const m = (t.text || "").match(ARTICLE_RE);
  if (m) return { id: m[1], url: m[0], short: "", display: m[0] };
  return null;
}

function renderArticleCard(t) {
  const article = extractArticle(t);
  if (!article) return "";
  const canonical = `https://x.com/i/article/${article.id}`;
  return `
    <div class="article-card" data-article-id="${escapeAttr(article.id)}" data-article-url="${escapeAttr(canonical)}">
      <button type="button" class="article-card-head" data-action="toggle-article" aria-expanded="false">
        <span class="article-card-icon">${ICONS.article}</span>
        <span class="article-card-info">
          <span class="article-card-label">Artículo</span>
          <span class="article-card-url">${escapeHtml(article.display || canonical)}</span>
        </span>
        <span class="article-card-chevron">${ICONS.chevron_down}</span>
      </button>
    </div>
  `;
}

function toggleArticleCard(card) {
  const isOpen = card.classList.contains("is-open");
  const head = card.querySelector(".article-card-head");
  const chevron = card.querySelector(".article-card-chevron");
  const url = card.getAttribute("data-article-url") || "";
  if (isOpen) {
    card.classList.remove("is-open");
    card.querySelector(".article-card-body")?.remove();
    if (head) head.setAttribute("aria-expanded", "false");
    if (chevron) chevron.innerHTML = ICONS.chevron_down;
    return;
  }
  card.classList.add("is-open");
  if (head) head.setAttribute("aria-expanded", "true");
  if (chevron) chevron.innerHTML = ICONS.chevron_up;
  const body = document.createElement("div");
  body.className = "article-card-body";
  body.innerHTML = `
    <div class="article-card-frame-wrap">
      <iframe class="article-card-frame" src="${escapeAttr(url)}"
              loading="lazy" referrerpolicy="no-referrer"
              sandbox="allow-scripts allow-same-origin allow-popups allow-forms"></iframe>
    </div>
    <div class="article-card-fallback">
      <span>¿No carga? X bloquea el embebido en algunos casos.</span>
      <a class="article-card-open" href="${escapeAttr(url)}" target="_blank" rel="noopener">
        Abrir en X ${ICONS.external}
      </a>
    </div>
  `;
  card.appendChild(body);
}

function renderUrlCard(t) {
  if (Array.isArray(t.media) && t.media.length > 0) return "";
  if (t.quoted_tweet) return "";
  if (extractArticle(t)) return "";
  if (!Array.isArray(t.urls) || t.urls.length === 0) return "";
  const u = t.urls.find((u) => {
    const e = u.expanded_url || "";
    return e && !/^https?:\/\/(x\.com|twitter\.com)/i.test(e);
  });
  if (!u) return "";
  const display = u.display_url || u.expanded_url || u.url;
  const href = u.expanded_url || u.url;
  // Skeleton — OG metadata fills in via IntersectionObserver after render.
  return `
    <a class="url-card url-card-skel" href="${escapeAttr(href)}" target="_blank" rel="noopener" data-og-url="${escapeAttr(href)}">
      <div class="url-card-body">
        <div class="url-card-link">${escapeHtml(display)}</div>
      </div>
    </a>
  `;
}

// ---------------------------------------------------------------------------
// Link previews (Open Graph). Lazy-fetched per card via /api/og.
// ---------------------------------------------------------------------------

const OG_CACHE = new Map();          // url -> payload
const OG_INFLIGHT = new Map();       // url -> Promise
let _ogObserver = null;

function getOgObserver() {
  if (_ogObserver) return _ogObserver;
  _ogObserver = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      const card = e.target;
      _ogObserver.unobserve(card);
      hydrateOgCard(card);
    }
  }, { rootMargin: "200px" });
  return _ogObserver;
}

// ---------------------------------------------------------------------------
// Read-tracking observer — marks a tweet as "read" after a short dwell in view
// ---------------------------------------------------------------------------

let _readObserver = null;
const _readDwell = new WeakMap(); // article element -> timeout id

function getReadObserver() {
  if (_readObserver) return _readObserver;
  _readObserver = new IntersectionObserver((entries) => {
    for (const e of entries) {
      const el = e.target;
      const id = el.getAttribute("data-tweet-id");
      if (!id) continue;
      if (e.isIntersecting && e.intersectionRatio > 0.6) {
        if (READ_IDS.has(id)) continue;
        if (_readDwell.has(el)) continue;
        const t = setTimeout(() => {
          READ_IDS.add(id);
          el.classList.add("is-read");
          persistReadIds();
          _readObserver.unobserve(el);
          _readDwell.delete(el);
        }, 1200);
        _readDwell.set(el, t);
      } else {
        const t = _readDwell.get(el);
        if (t) {
          clearTimeout(t);
          _readDwell.delete(el);
        }
      }
    }
  }, { threshold: [0, 0.6, 1] });
  return _readObserver;
}

function observeReadTweets(root) {
  const obs = getReadObserver();
  const tweets = (root || contentEl).querySelectorAll("article.tweet[data-tweet-id]");
  tweets.forEach((el) => {
    if (READ_IDS.has(el.getAttribute("data-tweet-id"))) return;
    obs.observe(el);
  });
}

function observeOgCards(root) {
  const obs = getOgObserver();
  const cards = (root || contentEl).querySelectorAll(".url-card[data-og-url]");
  cards.forEach((c) => {
    if (c.dataset.ogHydrated) return;
    obs.observe(c);
  });
}

async function hydrateOgCard(card) {
  const url = card.getAttribute("data-og-url");
  if (!url || card.dataset.ogHydrated) return;
  card.dataset.ogHydrated = "1";
  let data;
  if (OG_CACHE.has(url)) {
    data = OG_CACHE.get(url);
  } else if (OG_INFLIGHT.has(url)) {
    data = await OG_INFLIGHT.get(url);
  } else {
    const p = fetch(`/api/og?url=${encodeURIComponent(url)}`)
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null);
    OG_INFLIGHT.set(url, p);
    data = await p;
    OG_INFLIGHT.delete(url);
    if (data) OG_CACHE.set(url, data);
  }
  if (!data || (!data.title && !data.image && data.kind === "link")) {
    card.classList.remove("url-card-skel");
    return;
  }
  card.classList.remove("url-card-skel");
  card.classList.add(`url-card-${data.kind || "link"}`);
  card.innerHTML = renderOgCardInner(data);
}

function renderOgCardInner(d) {
  if (d.kind === "youtube" && d.youtube_id) {
    const thumb = d.image || `https://i.ytimg.com/vi/${d.youtube_id}/hqdefault.jpg`;
    return `
      <div class="url-card-image yt-thumb">
        <img src="${escapeAttr(thumb)}" alt="" loading="lazy">
        <span class="yt-play"><svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg></span>
      </div>
      <div class="url-card-body">
        <div class="url-card-domain">▶ YouTube · ${escapeHtml(d.domain || "")}</div>
        <div class="url-card-title">${escapeHtml(d.title || "")}</div>
        ${d.description ? `<div class="url-card-desc">${escapeHtml(d.description)}</div>` : ""}
      </div>
    `;
  }
  if (d.kind === "github" && d.github_owner) {
    return `
      <div class="url-card-body url-card-gh">
        <div class="url-card-domain">⌨ github.com</div>
        <div class="url-card-title">${escapeHtml(d.github_owner)}/${escapeHtml(d.github_repo || "")}</div>
        ${d.description ? `<div class="url-card-desc">${escapeHtml(d.description)}</div>` : ""}
      </div>
    `;
  }
  const img = d.image
    ? `<div class="url-card-image"><img src="${escapeAttr(d.image)}" alt="" loading="lazy"></div>`
    : "";
  return `
    ${img}
    <div class="url-card-body">
      <div class="url-card-domain">${escapeHtml(d.domain || "")}</div>
      ${d.title ? `<div class="url-card-title">${escapeHtml(d.title)}</div>` : ""}
      ${d.description ? `<div class="url-card-desc">${escapeHtml(d.description)}</div>` : ""}
    </div>
  `;
}

function badgeFor(author) {
  if (!author) return "";
  if (author.verified) return ICONS.verified_gold;
  if (author.is_blue_verified) return ICONS.verified_blue;
  return "";
}

function upgradeAvatar(url) {
  return url ? url.replace("_normal.", "_bigger.") : url;
}

// ---------------------------------------------------------------------------
// Click delegation
// ---------------------------------------------------------------------------

async function onContentClick(ev) {
  // Profile link in @mention or "responding to" anchor
  const anchorHandle = ev.target.closest("a[data-handle]");
  if (anchorHandle) {
    ev.preventDefault();
    navigateProfile(anchorHandle.dataset.handle);
    return;
  }

  const action = ev.target.closest("[data-action]");
  if (!action) return;
  const tweetEl = action.closest("[data-tweet-id]");
  const tweetId = tweetEl?.getAttribute("data-tweet-id");

  switch (action.dataset.action) {
    case "profile":
      ev.preventDefault();
      // Alt/Option-click filters the feed by this author instead of opening profile.
      if (ev.altKey && action.dataset.handle) {
        applyAuthorFilter(action.dataset.handle);
        return;
      }
      navigateProfile(action.dataset.handle);
      return;
    case "filter-author":
      ev.preventDefault();
      ev.stopPropagation();
      applyAuthorFilter(action.dataset.handle);
      return;
    case "toggle-replies":
      if (!tweetId) return;
      if (REPLIES_OPEN.has(tweetId)) {
        REPLIES_OPEN.delete(tweetId);
        tweetEl.querySelector(".replies-thread")?.remove();
      } else {
        REPLIES_OPEN.add(tweetId);
        REPLIES_THREAD_ONLY.delete(tweetId);
        await renderRepliesInto(tweetEl, tweetId, false);
      }
      return;
    case "toggle-thread":
      if (!tweetId) return;
      if (REPLIES_OPEN.has(tweetId) && REPLIES_THREAD_ONLY.has(tweetId)) {
        REPLIES_OPEN.delete(tweetId);
        REPLIES_THREAD_ONLY.delete(tweetId);
        tweetEl.querySelector(".replies-thread")?.remove();
      } else {
        REPLIES_OPEN.add(tweetId);
        REPLIES_THREAD_ONLY.add(tweetId);
        await renderRepliesInto(tweetEl, tweetId, false);
      }
      return;
    case "refresh-replies":
      if (!tweetId) return;
      await renderRepliesInto(tweetEl, tweetId, true);
      return;
    case "toggle-author-only": {
      if (!tweetId) return;
      if (REPLIES_THREAD_ONLY.has(tweetId)) REPLIES_THREAD_ONLY.delete(tweetId);
      else REPLIES_THREAD_ONLY.add(tweetId);
      await renderRepliesInto(tweetEl, tweetId, false);
      return;
    }
    case "submit-reply":
      await submitReply(tweetEl, tweetId);
      return;
    case "toggle-like":
      await toggleAction(tweetEl, tweetId, "like");
      return;
    case "toggle-retweet":
      await toggleAction(tweetEl, tweetId, "retweet");
      return;
    case "toggle-bookmark":
      await toggleAction(tweetEl, tweetId, "bookmark");
      return;
    case "mark-seen":
      ev.preventDefault();
      markAllAsSeen();
      return;
    case "toggle-article": {
      const card = action.closest(".article-card");
      if (!card) return;
      ev.preventDefault();
      toggleArticleCard(card);
      return;
    }
    case "open-media": {
      const grid = action.closest(".media-grid");
      const idx = parseInt(action.dataset.index || "0", 10);
      if (!grid) return;
      try {
        const items = JSON.parse(decodeURIComponent(grid.dataset.media || "[]"));
        openLightbox(items, idx);
      } catch (e) {
        console.error("media payload error", e);
      }
      return;
    }
  }
}

// ---------------------------------------------------------------------------
// Replies thread
// ---------------------------------------------------------------------------

async function renderRepliesInto(tweetEl, tweetId, refresh) {
  let container = tweetEl.querySelector(".replies-thread");
  if (!container) {
    container = document.createElement("div");
    container.className = "replies-thread";
    tweetEl.querySelector(".tweet-body").appendChild(container);
  }

  // Compose form at the top + loading skeleton below.
  container.innerHTML = composeFormHtml() +
    `<div class="replies-loading">Cargando comentarios…</div>`;
  attachComposeHandlers(container, tweetId);

  let data;
  try {
    if (refresh) REPLIES_CACHE.delete(tweetId);
    if (REPLIES_CACHE.has(tweetId) && !refresh) {
      data = { replies: REPLIES_CACHE.get(tweetId), from_cache: true };
    } else {
      const resp = await fetch(`/api/tweets/${tweetId}/replies${refresh ? "?refresh=1" : ""}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      data = await resp.json();
      REPLIES_CACHE.set(tweetId, data.replies || []);
    }
  } catch (e) {
    const loader = container.querySelector(".replies-loading");
    if (loader) loader.outerHTML = `<div class="replies-empty">Error: ${escapeHtml(e.message)}</div>`;
    return;
  }

  const replies = data.replies || [];

  // Author-only filter: identify the original tweet's author handle so we can
  // narrow the replies down to a single-author thread (🧵 button / toggle).
  const tweetData = currentTweets.find((t) => (t.id === tweetId) || (t.retweeted_tweet && t.retweeted_tweet.id === tweetId));
  const original = tweetData?.is_retweet ? tweetData.retweeted_tweet : tweetData;
  const authorHandle = (original?.author?.username || "").toLowerCase();
  const onlyAuthor = REPLIES_THREAD_ONLY.has(tweetId);
  const visible = (onlyAuthor && authorHandle)
    ? replies.filter((r) => (r.author?.username || "").toLowerCase() === authorHandle)
    : replies;

  const toggle = authorHandle
    ? `<button class="btn-link ${onlyAuthor ? "is-active" : ""}" data-action="toggle-author-only">${onlyAuthor ? "✓ Solo autor" : "Solo autor"}</button>`
    : "";

  const repliesHtml = visible.length === 0
    ? `<div class="replies-empty">${onlyAuthor ? "El autor no continuó el hilo" : "Aún no hay comentarios"}</div>`
    : visible.map(renderReply).join("");

  container.innerHTML = composeFormHtml() +
    repliesHtml +
    `<div class="replies-actions">
       ${toggle}
       <button class="btn-link" data-action="refresh-replies">${data.from_cache ? "Recargar desde X" : "Refrescado"}</button>
     </div>`;
  attachComposeHandlers(container, tweetId);
  observeOgCards(container);
}

function composeFormHtml() {
  return `
    <div class="reply-compose">
      <textarea placeholder="Escribe tu respuesta..." maxlength="280"></textarea>
      <div class="reply-compose-bar">
        <span class="reply-counter">0 / 280</span>
        <button class="btn-primary" data-action="submit-reply">Responder</button>
      </div>
    </div>`;
}

function attachComposeHandlers(container, tweetId) {
  const ta = container.querySelector(".reply-compose textarea");
  const counter = container.querySelector(".reply-compose .reply-counter");
  if (!ta || !counter) return;
  ta.addEventListener("input", () => {
    const n = ta.value.length;
    counter.textContent = `${n} / 280`;
    counter.classList.toggle("over", n > 280);
  });
}

function renderReply(t) {
  const author = t.author || {};
  const m = t.metrics || {};
  const avatar = author.avatar ? `<img src="${escapeAttr(upgradeAvatar(author.avatar))}" loading="lazy">` : "";
  return `
    <div class="reply-card">
      <div class="reply-avatar" data-action="profile" data-handle="${escapeAttr(author.username || "")}">${avatar}</div>
      <div class="reply-body">
        <div class="reply-header">
          <span class="reply-name" data-action="profile" data-handle="${escapeAttr(author.username || "")}">${escapeHtml(author.name || "?")}${badgeFor(author)}</span>
          <span class="reply-handle" data-action="profile" data-handle="${escapeAttr(author.username || "")}">@${escapeHtml(author.username || "?")}</span>
          <span class="reply-date">· ${escapeHtml(formatRelative(t.created_at))}</span>
        </div>
        <div class="reply-text">${renderText(t)}</div>
        ${renderMedia(t.media)}
        <div class="reply-metrics">
          <span>💬 ${formatNum(m.replies)}</span>
          <span>🔁 ${formatNum(m.retweets)}</span>
          <span>❤ ${formatNum(m.likes)}</span>
        </div>
      </div>
    </div>
  `;
}

// ---------------------------------------------------------------------------
// Reply submission
// ---------------------------------------------------------------------------

async function submitReply(tweetEl, tweetId) {
  const compose = tweetEl.querySelector(".replies-thread .reply-compose");
  if (!compose) return;
  const ta = compose.querySelector("textarea");
  const btn = compose.querySelector(".btn-primary");
  const text = ta.value.trim();
  if (!text) {
    showToast("Escribe algo primero", "error");
    return;
  }
  btn.disabled = true;
  btn.textContent = "Enviando…";
  try {
    const resp = await fetch(`/api/tweets/${tweetId}/reply`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (!resp.ok) throw new Error(await resp.text() || `HTTP ${resp.status}`);
    showToast("✓ Respuesta enviada", "success");
    // Bump reply count in UI
    const replyAction = tweetEl.querySelector(".action-reply span");
    if (replyAction) {
      const prev = parseInt(replyAction.textContent) || 0;
      replyAction.textContent = formatNum(prev + 1);
    }
    // Re-fetch the conversation so the new reply appears immediately.
    await renderRepliesInto(tweetEl, tweetId, /*refresh=*/true);
  } catch (e) {
    showToast(`Error: ${e.message}`, "error");
    btn.disabled = false;
    btn.textContent = "Responder";
  }
}

// ---------------------------------------------------------------------------
// Like / RT / Bookmark
// ---------------------------------------------------------------------------

async function toggleAction(tweetEl, tweetId, kind) {
  const btn = tweetEl.querySelector(`.action-${kind}`);
  if (!btn) return;
  const wasActive = btn.classList.contains("is-active");
  // Optimistic UI
  btn.classList.toggle("is-active", !wasActive);
  const span = btn.querySelector("span");
  if (span) {
    const cur = parseInt(span.textContent) || 0;
    span.textContent = formatNum(wasActive ? Math.max(0, cur - 1) : cur + 1);
  }
  if (kind === "like" || kind === "bookmark") {
    const svg = wasActive ? ICONS[kind] : ICONS[`${kind}_filled`];
    btn.innerHTML = svg + (span ? span.outerHTML : "");
  }

  const method = wasActive ? "DELETE" : "POST";
  try {
    const resp = await fetch(`/api/tweets/${tweetId}/${kind}`, { method });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  } catch (e) {
    // Roll back optimistic update
    btn.classList.toggle("is-active", wasActive);
    const span2 = btn.querySelector("span");
    if (span2) {
      const cur = parseInt(span2.textContent) || 0;
      span2.textContent = formatNum(wasActive ? cur + 1 : Math.max(0, cur - 1));
    }
    showToast(`Error: ${e.message}`, "error");
  }
}

// ---------------------------------------------------------------------------
// Profile
// ---------------------------------------------------------------------------

async function renderProfile() {
  // Hide tabs / controls while in profile
  tabsEl.style.display = "none";
  controlsEl.style.display = "none";

  const handle = route.handle;
  contentEl.innerHTML = `
    <div class="profile-back" data-action="back">${ICONS.back} Volver</div>
    <div class="spinner">Cargando @${escapeHtml(handle)}…</div>
  `;
  contentEl.querySelector('[data-action="back"]').addEventListener("click", () => history.back());

  let user, tweets;
  try {
    const [uResp, tResp] = await Promise.all([
      fetch(`/api/users/${encodeURIComponent(handle)}`),
      fetch(`/api/users/${encodeURIComponent(handle)}/tweets?max=40`),
    ]);
    if (!uResp.ok) throw new Error(`Usuario no encontrado (${uResp.status})`);
    user = await uResp.json();
    tweets = tResp.ok ? (await tResp.json()).tweets || [] : [];
  } catch (e) {
    contentEl.innerHTML = `
      <div class="profile-back" data-action="back">${ICONS.back} Volver</div>
      <div class="error">${escapeHtml(e.message)}</div>
    `;
    contentEl.querySelector('[data-action="back"]').addEventListener("click", () => history.back());
    return;
  }

  const banner = user.banner ? `style="background-image:url('${escapeAttr(user.banner)}')"` : "";
  const avatar = user.avatar
    ? `<img src="${escapeAttr(upgradeAvatar(user.avatar).replace("_bigger.", "_400x400."))}" alt="">`
    : "";

  contentEl.innerHTML = `
    <div class="profile-back" data-action="back">${ICONS.back} Volver</div>
    <div class="profile-banner" ${banner}></div>
    <div class="profile-header">
      <div class="profile-avatar">${avatar}</div>
      <div class="profile-name">${escapeHtml(user.name || handle)}${badgeFor(user)}</div>
      <div class="profile-handle">@${escapeHtml(user.username || handle)}</div>
      ${user.description ? `<div class="profile-description">${escapeHtml(user.description)}</div>` : ""}
      ${user.location || user.url ? `<div class="profile-meta">
        ${user.location ? `📍 ${escapeHtml(user.location)}` : ""}
        ${user.url ? `🔗 <a href="${escapeAttr(user.url)}" target="_blank" rel="noopener">${escapeHtml(user.url)}</a>` : ""}
      </div>` : ""}
      <div class="profile-stats">
        <span><strong>${formatNum(user.following_count)}</strong> <span class="label">Siguiendo</span></span>
        <span><strong>${formatNum(user.followers_count)}</strong> <span class="label">Seguidores</span></span>
        <span><strong>${formatNum(user.statuses_count)}</strong> <span class="label">Tweets</span></span>
      </div>
    </div>
    ${tweets.map(renderTweet).join("")}
  `;
  contentEl.querySelector('[data-action="back"]').addEventListener("click", () => history.back());
  observeOgCards();
  observeReadTweets();
}

// ---------------------------------------------------------------------------
// New-tweet seen marker
// ---------------------------------------------------------------------------

function markAllAsSeen() {
  let latest = "";
  for (const t of currentTweets) {
    if (t._first_seen_at && t._first_seen_at > latest) latest = t._first_seen_at;
  }
  if (latest) localStorage.setItem(LAST_SEEN_KEY, latest);
  render();
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
function escapeAttr(str) { return escapeHtml(str); }

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
  return t ? new Date(t).toLocaleString() : "";
}

function rawNum(v) {
  if (v == null || v === "") return 0;
  const n = parseInt(String(v).replace(/,/g, ""), 10);
  return isNaN(n) ? 0 : n;
}

function formatExact(v) {
  const n = rawNum(v);
  return n > 0 ? n.toLocaleString("es-ES") : "0";
}

function renderEngagement(m) {
  const views = rawNum(m?.views);
  if (views < 100) return "";
  const eng = rawNum(m?.likes) + rawNum(m?.retweets) + rawNum(m?.replies) + rawNum(m?.bookmarks);
  if (eng <= 0) return "";
  const pct = (eng / views) * 100;
  const display = pct >= 10 ? pct.toFixed(0) : pct.toFixed(1);
  let cls = "low";
  if (pct >= 5) cls = "high";
  else if (pct >= 1.5) cls = "mid";
  return `<div class="tweet-engagement engagement-${cls}" title="Engagement ratio · ${eng.toLocaleString("es-ES")} interacciones / ${views.toLocaleString("es-ES")} vistas">${display}% engagement</div>`;
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
  contentEl.innerHTML = `<div class="error">${msg}</div>`;
}

function showToast(msg, kind = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3000);
}

init();
