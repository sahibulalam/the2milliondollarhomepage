/* A Million Pixels — client.

   The grid is never elements: 10,000 blocks would be 10,000 nodes. Grid lines
   are a CSS background, hit-testing is arithmetic, and the only things over
   the photo are the patches people have actually bought. */
(function () {
"use strict";

var $ = function (id) { return document.getElementById(id); };
var state = null;
var G = null;                 // grid geometry from the server
var sel = null;               // {col,row,cols,rows} current selection
var quote = null;             // server's price for `sel`
var pendingLogo = null;
var busy = false;

var stage = $("stage");
var frame = $("frame");
var overlay = $("overlay");
var scrim = $("scrim");

/* ------------------------------------------------------------- helpers -- */

function money(cents) {
  var n = (cents || 0) / 100;
  return "$" + n.toLocaleString("en-US", {
    minimumFractionDigits: n % 1 ? 2 : 0, maximumFractionDigits: 2
  });
}
function short(cents) {
  var n = (cents || 0) / 100;
  if (n >= 1e6) return "$" + (n / 1e6).toFixed(n >= 1e7 ? 0 : 2) + "M";
  if (n >= 1e4) return "$" + Math.round(n / 1e3) + "k";
  return money(cents);
}
function num(n) { return (n || 0).toLocaleString("en-US"); }
function el(tag, cls, text) {
  var e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}
function host(url) {
  try { return new URL(url).hostname.replace(/^www\./, ""); }
  catch (e) { return url || ""; }
}
function ago(ts) {
  var s = Math.max(0, Math.floor(Date.now() / 1000) - ts);
  if (s < 60) return "just now";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  if (s < 172800) return Math.floor(s / 3600) + "h ago";
  return new Date(ts * 1000).toLocaleDateString();
}
function clamp(v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; }

/* A patch with no uploaded artwork still has to occupy its rectangle, so it
   gets a colour of its own, picked from the name. Deterministic, so a brand
   keeps the same colour across reloads and across everybody's screen. */
var TINTS = [
  "#3b5bfd", "#ff5c2b", "#0f9d58", "#7c3aed", "#e11d74", "#0891b2",
  "#d97706", "#dc2626", "#0d9488", "#4f46e5", "#65a30d", "#c026d3"
];
function tintFor(name) {
  var h = 0, str = String(name || "");
  for (var i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) >>> 0;
  return TINTS[h % TINTS.length];
}

/* --------------------------------------------------- painting the canvas -- */

/* Each bought rectangle is one element; one that has had blocks taken from it
   is drawn as one element per surviving run, each a window onto the same logo
   laid out across the original rectangle. So a half-eaten patch shows exactly
   the half its owner still holds. */
function paint() {
  overlay.textContent = "";
  if (!state) return;

  state.claims.forEach(function (c) {
    var rect = {
      x: c.col * G.tw, y: c.row * G.th,
      w: c.cols * G.tw, h: c.rows * G.th
    };
    var runs = c.whole
      ? [[c.col, c.row, c.cols]]
      : c.runs.map(function (r) {
          return [r[0] % G.cols, Math.floor(r[0] / G.cols), r[1] - r[0] + 1];
        });

    runs.forEach(function (run) {
      var win = {
        x: run[0] * G.tw, y: run[1] * G.th,
        w: run[2] * G.tw, h: c.whole ? rect.h : G.th
      };
      var box = el("a", "plot");
      if (c.url) {
        box.href = c.url; box.target = "_blank";
        box.rel = "noopener noreferrer nofollow";
      }
      box.style.left = win.x + "%"; box.style.top = win.y + "%";
      box.style.width = win.w + "%"; box.style.height = win.h + "%";
      box.title = c.brand + " — " + num(c.owned * G.px_per_block)
        + " pixels, owned since " + ago(c.since);

      // The mark is laid out over the WHOLE original rectangle and clipped by
      // the run, which is what makes a partial takeover read correctly.
      var mark = el("span", "mark");
      mark.style.left = ((rect.x - win.x) / win.w * 100) + "%";
      mark.style.top = ((rect.y - win.y) / win.h * 100) + "%";
      mark.style.width = (rect.w / win.w * 100) + "%";
      mark.style.height = (rect.h / win.h * 100) + "%";
      if (c.logo) {
        var im = new Image();
        im.alt = c.brand; im.loading = "lazy"; im.src = c.logo;
        mark.appendChild(im);
      } else {
        mark.className = "mark tint";
        mark.style.background = tintFor(c.brand);
        mark.appendChild(el("span", "wordmark", c.brand));
      }
      box.appendChild(mark);
      overlay.appendChild(box);
    });
  });
  fitWordmarks();
}

/* A wordmark has to fit the patch it is printed on, at whatever size the
   canvas is currently rendered. Binary-search the largest size that fits. */
function fitWordmarks() {
  var marks = overlay.querySelectorAll(".wordmark");
  for (var i = 0; i < marks.length; i++) {
    var e = marks[i], box = e.parentNode;
    // Budget a margin explicitly rather than relying on the box's padding:
    // clientWidth includes padding, so measuring against it lets the text
    // creep out over the edge of its own patch.
    var bw = box.clientWidth * 0.9, bh = box.clientHeight * 0.84;
    if (bw < 8 || bh < 5) { e.style.fontSize = "0"; continue; }
    var lo = 4, hi = Math.min(56, bh * 0.95), best = lo;
    e.style.fontSize = hi + "px";
    if (e.scrollWidth <= bw && e.scrollHeight <= bh) continue;
    for (var k = 0; k < 15 && hi - lo > 0.4; k++) {
      var mid = (lo + hi) / 2;
      e.style.fontSize = mid + "px";
      if (e.scrollWidth <= bw && e.scrollHeight <= bh) { best = mid; lo = mid; }
      else hi = mid;
    }
    e.style.fontSize = best.toFixed(1) + "px";
  }
}

/* ------------------------------------------------------------------ zoom -- */
/* Zoom only changes the frame's width in pixels. Its height follows from the
   grid's aspect ratio in CSS, and everything inside is positioned as a
   percentage of it, so no other measurement in the app knows about zoom. */

var zoom = 1, fitMode = true, fitTimer = null, anchorFrac = null;

function layout() {
  if (!G) return;
  // The canvas fills the window. Its padding is the room the floating HUD
  // needs, read from the stylesheet rather than guessed at here.
  var cs = getComputedStyle(stage);
  var padX = parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight);
  var padY = parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom);
  var availW = Math.max(180, stage.clientWidth - padX);
  var availH = Math.max(180, stage.clientHeight - padY);
  var base = Math.max(180, Math.min(availW, availH * (G.cols / G.rows)));
  frame.style.width = Math.round(base * zoom) + "px";

  var unitPx = (base * zoom) / G.cols;

  // Pick a major-line spacing that lands roughly every 40 screen pixels, so
  // the guide grid stays legible whether a unit is half a pixel or thirty.
  var steps = [1, 2, 5, 10, 25, 50, 100, 250, 500];
  var major = steps[steps.length - 1];
  for (var i = 0; i < steps.length; i++) {
    if (steps[i] * unitPx >= 40) { major = steps[i]; break; }
  }
  frame.style.setProperty("--major", major);

  // Per-unit lines only once they are far enough apart to read as a grid
  // rather than as a grey wash over the canvas.
  stage.classList.toggle("fine", unitPx >= 9);
  $("block-size").textContent = unitPx >= 0.05
    ? G.unit + " " + (unitPx < 10 ? unitPx.toFixed(unitPx < 1 ? 2 : 1)
                                  : unitPx.toFixed(0)) + "px" : "";
  fitWordmarks();
}

function focusFrac() {
  if (anchorFrac)
    return { x: clamp(anchorFrac.x, 0, 1), y: clamp(anchorFrac.y, 0, 1) };
  var w = Math.max(1, frame.offsetWidth), h = Math.max(1, frame.offsetHeight);
  // Measure the middle of the VISIBLE canvas. When the whole thing fits, the
  // frame is narrower than the viewport, and halving the viewport instead
  // would put the anchor past the frame's own middle -- which then drifts
  // further with every zoom step.
  var vw = Math.min(stage.clientWidth, w), vh = Math.min(stage.clientHeight, h);
  return {
    x: clamp((stage.scrollLeft + vw / 2) / w, 0, 1),
    y: clamp((stage.scrollTop + vh / 2) / h, 0, 1)
  };
}
function applyFocus(at) {
  stage.scrollLeft = at.x * frame.offsetWidth - stage.clientWidth / 2;
  stage.scrollTop = at.y * frame.offsetHeight - stage.clientHeight / 2;
}
function setZoom(z) {
  fitMode = (z === "fit");
  zoom = fitMode ? 1 : Number(z);
  var btns = document.querySelectorAll(".seg button");
  for (var i = 0; i < btns.length; i++)
    btns[i].classList.toggle("on", btns[i].getAttribute("data-zoom") === String(z));
  var at = fitMode ? { x: 0.5, y: 0.5 } : focusFrac();
  layout();
  // Applied straight away, because requestAnimationFrame does not run at all
  // in a hidden or backgrounded tab and the scroll would silently never
  // happen. The second pass catches the scrollbars appearing.
  applyFocus(at);
  requestAnimationFrame(function () { applyFocus(at); });
}
Array.prototype.forEach.call(document.querySelectorAll(".seg button"), function (b) {
  b.addEventListener("click", function () { setZoom(b.getAttribute("data-zoom")); });
});
window.addEventListener("resize", function () {
  clearTimeout(fitTimer); fitTimer = setTimeout(layout, 120);
});


/* ------------------------------------------------------- drag selection -- */

var dragging = false, anchor = null;

function blockAt(ev) {
  var b = frame.getBoundingClientRect();
  if (!b.width || !b.height) return null;
  var p = ev.touches ? ev.touches[0] : ev;
  return {
    col: clamp(Math.floor((p.clientX - b.left) / b.width * G.cols), 0, G.cols - 1),
    row: clamp(Math.floor((p.clientY - b.top) / b.height * G.rows), 0, G.rows - 1)
  };
}
function rectFrom(a, b) {
  return {
    col: Math.min(a.col, b.col), row: Math.min(a.row, b.row),
    cols: Math.abs(a.col - b.col) + 1, rows: Math.abs(a.row - b.row) + 1
  };
}
function drawSelection() {
  var box = $("selbox"), msg = $("toolbar-msg");
  $("cta-nav").disabled = !sel;

  if (!sel) {
    box.hidden = true;
    msg.className = "selhint";
    msg.textContent = "Drag a box on the canvas to pick your pixels.";
    return;
  }

  anchorFrac = { x: (sel.col + sel.cols / 2) / G.cols,
                 y: (sel.row + sel.rows / 2) / G.rows };
  box.hidden = false;
  box.style.left = (sel.col * G.tw) + "%";
  box.style.top = (sel.row * G.th) + "%";
  box.style.width = (sel.cols * G.tw) + "%";
  box.style.height = (sel.rows * G.th) + "%";

  var n = sel.cols * sel.rows;
  var priced = quote && quote.tiles === n;
  var blocked = priced && !quote.available;
  box.classList.toggle("blocked", !!blocked);

  msg.className = "selinfo" + (blocked ? " blocked" : "");
  msg.textContent = "";

  if (n > G.max_tiles) {
    msg.className = "selinfo blocked";
    msg.appendChild(el("div", "k", "too big"));
    msg.appendChild(el("div", "big", num(n * G.px_per_block) + " px"));
    msg.appendChild(el("div", "price",
      "One claim can cover " + num(G.max_tiles * G.px_per_block)
      + " pixels at most. Drag a smaller box."));
    $("cta-nav").disabled = true;
    return;
  }

  if (blocked) {
    msg.appendChild(el("div", "k", "not for sale"));
    msg.appendChild(el("div", "big",
      num(quote.taken * G.px_per_block) + " px"));
    msg.appendChild(el("div", "dim", "already owned"));
    msg.appendChild(el("div", "price",
      "Sold pixels stay sold. Move or resize your box."));
    $("cta-nav").disabled = true;
    return;
  }

  msg.appendChild(el("div", "k", "selected"));
  msg.appendChild(el("div", "big", num(n * G.px_per_block) + " px"));
  msg.appendChild(el("div", "dim", sel.cols + " \u00d7 " + sel.rows
    + (G.byPixel ? "" : " blocks")));
  msg.appendChild(el("div", "price",
    priced ? money(quote.total_cents) : "pricing\u2026"));
}

var quoteTimer = null, quoteSeq = 0;
function refreshQuote(then) {
  if (!sel) return;
  var n = sel.cols * sel.rows;
  if (n > G.max_tiles) { quote = null; drawSelection(); return; }
  var mine = ++quoteSeq;
  var q = "col=" + sel.col + "&row=" + sel.row + "&cols=" + sel.cols + "&rows=" + sel.rows;
  fetch("/api/quote?" + q)
    .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
    .then(function (d) {
      if (mine !== quoteSeq) return;      // a newer drag superseded this one
      quote = d; drawSelection();
      if (then) then();
    })
    .catch(function () { if (mine === quoteSeq) { quote = null; drawSelection(); } });
}
function quoteSoon() {
  clearTimeout(quoteTimer);
  quoteTimer = setTimeout(refreshQuote, 170);
}

function onDown(ev) {
  if (!G || ev.button > 0) return;
  var t = blockAt(ev); if (!t) return;
  ev.preventDefault();
  dragging = true; anchor = t;
  sel = rectFrom(t, t); quote = null;
  drawSelection(); quoteSoon();
}
function onMove(ev) {
  if (!dragging) return;
  var t = blockAt(ev); if (!t) return;
  var next = rectFrom(anchor, t);
  if (sel && next.col === sel.col && next.row === sel.row
      && next.cols === sel.cols && next.rows === sel.rows) return;
  sel = next; quote = null;
  drawSelection(); quoteSoon();
}
function onUp() {
  if (!dragging) return;
  dragging = false;
  clearTimeout(quoteTimer);
  refreshQuote(function () { openDialog(); });
}
frame.addEventListener("mousedown", onDown);
window.addEventListener("mousemove", onMove);
window.addEventListener("mouseup", onUp);
frame.addEventListener("touchstart", onDown, { passive: false });
frame.addEventListener("touchmove", onMove, { passive: false });
window.addEventListener("touchend", onUp);

frame.addEventListener("mousemove", function (ev) {
  if (!G) return;
  var b = frame.getBoundingClientRect();
  if (b.width && b.height)
    anchorFrac = { x: (ev.clientX - b.left) / b.width,
                   y: (ev.clientY - b.top) / b.height };
  if (dragging) return;
  var t = blockAt(ev); if (!t) return;
  $("hover-readout").textContent = "col " + (t.col + 1) + " · row " + (t.row + 1);
});
frame.addEventListener("mouseleave", function () {
  $("hover-readout").textContent = "";
});

/* ------------------------------------------------------------- listings -- */

function renderFigures() {
  var s = state.stats;
  $("f-raised").textContent = short(s.total_cents);
  $("f-sales").textContent = s.sales
    ? s.sales + (s.sales === 1 ? " purchase" : " purchases") : "no sales yet";
  $("f-blocks").textContent = "of " + num(s.pixels);
  $("f-visitors").textContent = num(s.visitors);
  $("f-views").textContent = num(s.pageviews)
    + (s.pageviews === 1 ? " page view" : " page views");
  $("f-pixels").textContent = num(s.pixels_sold);
  $("f-blocks").textContent = "of " + num(s.pixels);
  $("f-price").textContent = money(G.pixel_cents);
  $("nav-price").textContent = money(G.pixel_cents);
  $("nav-views").textContent = num(s.pageviews);
  $("f-price-sub").textContent = G.byPixel
    ? "per pixel, any shape you like"
    : "per pixel · " + money(G.floor_cents) + " a block";
  var dims = $("about-dims");
  dims.innerHTML = "";
  dims.appendChild(document.createTextNode("The grid is "));
  dims.appendChild(el("b", null, num(G.cols * G.tile_px) + " \u00d7 "
    + num(G.rows * G.tile_px)));
  dims.appendChild(document.createTextNode(" \u2014 " + num(G.pixels) + " pixels."));

  $("about-block").innerHTML = "";
  $("about-block").appendChild(document.createTextNode(
    G.byPixel
      ? "Buy any rectangle you like, from a single pixel upwards — "
      : "Pixels are sold in " + G.tile_px + " × " + G.tile_px + " blocks — "));
  $("about-block").appendChild(el("b", null, money(G.floor_cents)));
  $("about-block").appendChild(document.createTextNode(
    G.byPixel ? " a pixel." : " each."));

  $("nav-raised").textContent = short(s.total_cents);
  $("hero-sold").textContent = num(s.pixels_sold);
  $("hero-total").textContent = num(s.pixels);
  $("sb-sold").textContent = num(s.pixels_sold);
  $("sb-raised").textContent = short(s.total_cents);
  $("foot-since").textContent = "Open since " + new Date(s.since * 1000)
    .toLocaleDateString(undefined, { month: "long", day: "numeric", year: "numeric" });
}

function renderRankings() {
  var box = $("rank-list");
  box.textContent = "";
  var rows = (state.rankings || []).slice();
  if (rankSort === "spend")
    rows.sort(function (a, b) { return b.invested_cents - a.invested_cents; });
  else if (rankSort === "recent")
    rows.sort(function (a, b) { return (b.last_won || 0) - (a.last_won || 0); });
  // "pixels" is the order the server already returned

  if (!rows.length) {
    box.appendChild(el("div", "empty", "Nobody on the board yet. Be the first."));
    return;
  }
  rows.forEach(function (r, i) {
    var row = el(r.url ? "a" : "div", "rank" + (i === 0 && rankSort === "pixels" ? " top" : ""));
    if (r.url) { row.href = r.url; row.target = "_blank"; row.rel = "noopener noreferrer nofollow"; }

    row.appendChild(el("span", "pos", String(i + 1)));

    var av = el("span", "av");
    if (r.logo) { var im = new Image(); im.alt = ""; im.loading = "lazy"; im.src = r.logo; av.appendChild(im); }
    else {
      av.style.background = tintFor(r.brand);
      var ini = el("span", null, (r.brand || "?").slice(0, 2).toUpperCase());
      ini.style.color = "#fff";
      av.appendChild(ini);
    }
    row.appendChild(av);

    var who = el("span", "who");
    who.appendChild(el("b", null, r.brand));
    who.appendChild(el("span", "site",
      host(r.url) + " · " + money(r.invested_cents) + " · " + ago(r.since || r.last_won)));
    row.appendChild(who);

    var owned = el("span", "owned", num(r.pixels));
    owned.appendChild(el("small", null, "pixels"));
    row.appendChild(owned);

    var tagcol = el("span", "tagcol");
    tagcol.appendChild(el("span", "tag", "on board"));
    row.appendChild(tagcol);

    box.appendChild(row);
  });
}

function renderFeed() {
  var box = $("feed");
  box.textContent = "";
  if (!state.ledger.length) {
    box.appendChild(el("div", "empty", "Nobody yet. Be the first."));
    return;
  }
  state.ledger.forEach(function (p) {
    var row = el(p.url ? "a" : "div", "entry");
    if (p.url) { row.href = p.url; row.target = "_blank"; row.rel = "noopener noreferrer nofollow"; }

    var av = el("span", "av");
    if (p.logo) { var im = new Image(); im.alt = ""; im.loading = "lazy"; im.src = p.logo; av.appendChild(im); }
    else av.appendChild(el("span", null, (p.brand || "?").slice(0, 2).toUpperCase()));

    var who = el("span", "who");
    who.appendChild(el("b", null, p.brand));
    who.appendChild(el("small", null,
      num(p.tile_count * G.px_per_block) + " px · " + p.cols + "×" + p.rows
      + " · " + ago(p.settled_at)));

    var amt = el("span", "amt");
    amt.appendChild(el("b", null, money(p.amount_cents)));
    // A paid claim that holds less than it bought only happens when two
    // purchases raced; it is a refund case, not an outbidding.
    var partial = p.current && p.still_owned < p.tile_count;
    amt.appendChild(el("span",
      "tag" + (p.current ? (partial ? " part" : "") : " gone"),
      p.current
        ? (partial ? p.still_owned + "/" + p.tile_count + " placed" : "owned")
        : "refund due"));

    row.appendChild(av); row.appendChild(who); row.appendChild(amt);
    box.appendChild(row);
  });
}

/* --------------------------------------------------------------- fetch --- */

function load() {
  return fetch("/api/state", { headers: { Accept: "application/json" } })
    .then(function (r) { return r.json(); })
    .then(function (d) {
      var first = !state;
      state = d;
      G = {
        cols: d.grid.cols, rows: d.grid.rows, tile_px: d.grid.tile_px,
        tiles: d.grid.tiles, pixels: d.grid.pixels,
        floor_cents: d.grid.floor_cents, pixel_cents: d.grid.pixel_cents,
        max_tiles: d.grid.max_tiles,
        px_per_block: d.grid.tile_px * d.grid.tile_px,
        tw: 100 / d.grid.cols, th: 100 / d.grid.rows
      };
      // At one real pixel per unit of sale, calling them "blocks" is just
      // noise -- they are pixels, and the price is literally a dollar each.
      G.byPixel = G.px_per_block === 1;
      G.unit = G.byPixel ? "pixel" : "block";
      G.units = G.unit + "s";
      frame.style.setProperty("--cols", G.cols);
      frame.style.setProperty("--rows", G.rows);
      $("demo-card").hidden = !d.demo;
      $("rulesline").textContent =
        "Once a pixel is sold it belongs to its buyer for good — it never goes "
        + "back on the market. One purchase can cover up to "
        + num(G.max_tiles * G.px_per_block) + " pixels.";
      applySite(d.site);
      layout();
      paint(); renderFigures(); renderFeed(); renderRankings(); drawSelection();
      if (first) setZoom("fit");
    })
    .catch(function () { /* keep the last good render */ });
}

function applySite(site) {
  if (!site) return;
  if (site.name) {
    $("brand-name").textContent = site.name;
    document.title = site.name + " — " + money(G.pixel_cents) + " a pixel";
  }
  if (site.twitter) {
    var url = "https://x.com/" + encodeURIComponent(site.twitter);
    var link = $("twitter-link");
    link.href = url; link.hidden = false;
    $("twitter-handle").textContent = "@" + site.twitter;
    var foot = $("foot-twitter");
    foot.href = url; foot.hidden = false;
    foot.textContent = "@" + site.twitter;
  }
}

/* --------------------------------------------------------------- dialog -- */

function openDialog() {
  if (!state || !sel) return;
  if (sel.cols * sel.rows > G.max_tiles) return;
  pendingLogo = null; showPreview(null);
  $("f-brand").value = ""; $("f-url").value = ""; $("f-email").value = "";
  $("dlg-error").textContent = "";
  syncSelection();
  scrim.hidden = false;
  setTimeout(function () { $("f-brand").focus(); }, 40);
}
function closeDialog() {
  if (busy) return;
  scrim.hidden = true;
}

function syncSelection() {
  if (!sel) return;
  var n = sel.cols * sel.rows;
  var px = n * G.px_per_block;
  $("dlg-title").textContent = "Buy " + num(px) + " pixels";
  $("sel-size").textContent = num(px) + " pixels";
  $("sel-pos").textContent = sel.cols + " × " + sel.rows
    + (G.byPixel ? "" : " blocks") + ", from col " + (sel.col + 1)
    + " row " + (sel.row + 1);
  $("sel-mix").textContent = quote
    ? (quote.available
        ? "every pixel in it is free"
        : num(quote.taken * G.px_per_block) + " pixels here are already owned")
    : "pricing…";

  var b = $("minimap-box");
  b.style.left = (sel.col / G.cols * 100) + "%";
  b.style.top = (sel.row / G.rows * 100) + "%";
  b.style.width = Math.max(3, sel.cols / G.cols * 100) + "%";
  b.style.height = Math.max(3, sel.rows / G.rows * 100) + "%";

  $("sel-total").textContent = quote ? money(quote.total_cents) : "…";
  $("price-hint").textContent = quote
    ? money(G.pixel_cents) + " a pixel. Yours permanently — nobody can buy it "
      + "off you afterwards."
    : "";
  verdict();
}

function verdict() {
  var box = $("dlg-status");
  box.textContent = "";
  if (!quote) return;
  var cls = "note good", msg;
  if (!quote.available) {
    cls = "note bad";
    msg = "Somebody already owns " + num(quote.taken * G.px_per_block)
      + " of these pixels. Close this and drag a box on free canvas.";
  } else {
    msg = money(quote.total_cents) + " for " + num(quote.pixels)
      + " pixels, yours for good.";
  }
  box.appendChild(el("div", cls, msg));
  $("f-go").disabled = !quote.available;
}

function showError(msg) {
  var box = $("dlg-error");
  box.textContent = "";
  var n = el("div", "note bad");
  n.appendChild(el("span", "ni", "⛔"));
  n.appendChild(el("span", null, msg));
  box.appendChild(n);
}
function setBusy(on) {
  busy = on;
  $("dlg-busy").hidden = !on;
  $("f-go").disabled = on;
  $("f-cancel").disabled = on;
  $("f-go").textContent = on ? "Contacting payment provider…" : "Continue to payment";
}

/* ---------------------------------------------------------- logo intake -- */

function showPreview(src) {
  if (src) {
    $("dz-img").src = src;
    $("dz-prev").hidden = false; $("dz-clear").hidden = false;
    $("dz-text").textContent = "Looks good. Drop another to replace it.";
  } else {
    $("dz-img").removeAttribute("src");
    $("dz-prev").hidden = true; $("dz-clear").hidden = true;
    $("dz-text").textContent = "Drag an image here, or paste one.";
  }
}
function ingest(file) {
  if (!file) return;
  if (!/^image\/(png|jpeg|webp|gif)$/.test(file.type)) {
    showError("Use a PNG, JPEG, WebP or GIF."); return;
  }
  var fr = new FileReader();
  fr.onload = function () {
    var im = new Image();
    im.onload = function () {
      var max = 320, sc = Math.min(1, max / Math.max(im.width, im.height));
      var c = document.createElement("canvas");
      c.width = Math.max(1, Math.round(im.width * sc));
      c.height = Math.max(1, Math.round(im.height * sc));
      c.getContext("2d").drawImage(im, 0, 0, c.width, c.height);
      var out = c.toDataURL("image/png");
      if (out.length > 44000) out = c.toDataURL("image/jpeg", 0.86);
      if (out.length > 62000) {
        showError("That logo is too detailed. Use a simpler mark.");
        pendingLogo = null; showPreview(null); return;
      }
      pendingLogo = out;
      $("dlg-error").textContent = "";
      showPreview(out);
    };
    im.onerror = function () { pendingLogo = null; showError("That image could not be read."); };
    im.src = fr.result;
  };
  fr.onerror = function () { pendingLogo = null; showError("That file could not be read."); };
  fr.readAsDataURL(file);
}
$("f-logo").addEventListener("change", function (e) { ingest(e.target.files && e.target.files[0]); });
$("dz-pick").addEventListener("click", function () { $("f-logo").click(); });
$("dz-clear").addEventListener("click", function () {
  pendingLogo = null; $("f-logo").value = ""; showPreview(null);
});
["dragenter", "dragover"].forEach(function (ev) {
  $("drop").addEventListener(ev, function (e) {
    e.preventDefault(); $("drop").classList.add("over");
  });
});
["dragleave", "dragend", "drop"].forEach(function (ev) {
  $("drop").addEventListener(ev, function () { $("drop").classList.remove("over"); });
});
$("drop").addEventListener("drop", function (e) {
  e.preventDefault();
  if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length)
    ingest(e.dataTransfer.files[0]);
  else showError("That dropped a link, not the picture. Right-click the logo, "
    + "choose Copy image, then paste it here.");
});
scrim.addEventListener("paste", function (e) {
  var items = (e.clipboardData && e.clipboardData.items) || [];
  for (var i = 0; i < items.length; i++) {
    if (items[i].type && items[i].type.indexOf("image/") === 0) {
      var f = items[i].getAsFile();
      if (f) { e.preventDefault(); ingest(f); return; }
    }
  }
});
$("f-url").addEventListener("blur", function () {
  var brand = $("f-brand");
  if (brand.value.trim()) return;
  var v = $("f-url").value.trim();
  if (!v) return;
  var h = host(/^https?:\/\//i.test(v) ? v : "https://" + v).split(".")[0];
  if (h) brand.value = h.charAt(0).toUpperCase() + h.slice(1);
});

/* --------------------------------------------------------------- submit -- */

$("f-go").addEventListener("click", function () {
  if (busy || !sel) return;
  $("dlg-error").textContent = "";
  var brand = $("f-brand").value.trim();
  var url = $("f-url").value.trim();
  if (brand.length < 2) return showError("Enter a company or name.");
  if (!url) return showError("Enter a link, like acme.com.");
  if (!quote) return showError("Still pricing that selection. One moment.");
  if (!quote.available)
    return showError("Somebody already owns part of that area. Drag a box on free canvas.");
  var amount = quote.total_cents / 100;

  setBusy(true);
  fetch("/api/claim", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      col: sel.col, row: sel.row, cols: sel.cols, rows: sel.rows,
      brand: brand, url: url, amount: amount,
      email: $("f-email").value.trim(), logo: pendingLogo
    })
  }).then(function (r) {
    return r.json().then(function (d) { return { ok: r.ok, d: d }; });
  }).then(function (res) {
    if (!res.ok) { setBusy(false); return showError(res.d.error || "That did not go through."); }
    window.location.href = res.d.redirect;
  }).catch(function () {
    setBusy(false);
    showError("Could not reach the server. Nothing was charged. Try again.");
  });
});

function startBuying() {
  if (!G) return;
  if (!sel) {                 // nothing dragged yet: offer a starter block
    sel = { col: Math.floor(G.cols / 2) - 5, row: Math.floor(G.rows / 2) - 5,
            cols: 10, rows: 10 };
    drawSelection();
  }
  refreshQuote(function () { openDialog(); });
}
$("cta").addEventListener("click", startBuying);
$("cta-nav").addEventListener("click", startBuying);

/* which window is up: the grid, or the rankings board */
var view = "grid";
function setView(v) {
  view = v;
  $("win-grid").hidden = v !== "grid";
  $("win-rankings").hidden = v !== "rankings";
  Array.prototype.forEach.call(document.querySelectorAll("[data-view]"), function (b) {
    b.classList.toggle("primary", b.getAttribute("data-view") === v);
  });
  if (v === "grid") layout();     // the canvas was display:none, so re-measure
  else load();
}
Array.prototype.forEach.call(document.querySelectorAll("[data-view]"), function (b) {
  b.addEventListener("click", function () { setView(b.getAttribute("data-view")); });
});

/* rankings sort */
var rankSort = "pixels";
Array.prototype.forEach.call(document.querySelectorAll(".ranksort button"), function (b) {
  b.addEventListener("click", function () {
    rankSort = b.getAttribute("data-sort");
    Array.prototype.forEach.call(document.querySelectorAll(".ranksort button"), function (o) {
      o.classList.toggle("on", o === b);
    });
    renderRankings();
  });
});

/* what is this */
var aboutScrim = $("about-scrim");
function setAbout(open) { aboutScrim.hidden = !open; }
$("about-open").addEventListener("click", function () { setAbout(true); });
$("about-close").addEventListener("click", function () { setAbout(false); });
$("about-dismiss").addEventListener("click", function () { setAbout(false); });
$("about-buy").addEventListener("click", function () { setAbout(false); startBuying(); });
aboutScrim.addEventListener("mousedown", function (e) {
  if (e.target === aboutScrim) setAbout(false);
});
$("f-cancel").addEventListener("click", closeDialog);
$("dlg-x").addEventListener("click", closeDialog);
scrim.addEventListener("mousedown", function (e) { if (e.target === scrim) closeDialog(); });
document.addEventListener("keydown", function (e) {
  if (e.key !== "Escape") return;
  if (!scrim.hidden) return closeDialog();
  if (!aboutScrim.hidden) return setAbout(false);
  if (view !== "grid") return setView("grid");
});
$("guides").addEventListener("change", function (e) {
  stage.classList.toggle("guides", e.target.checked);
});

/* ----------------------------------------------------------------- boot -- */

load();
setInterval(function () { if (!document.hidden && scrim.hidden && !dragging) load(); }, 12000);
document.addEventListener("visibilitychange", function () {
  if (!document.hidden && scrim.hidden) load();
});

})();
