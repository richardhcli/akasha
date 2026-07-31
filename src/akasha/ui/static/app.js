console.debug("tm ui loaded");

// Node view (task T8.2, spec §4.13): all dynamic rendering happens here,
// client-side, against the existing /v1 JSON endpoints. Server free-text
// (node body, facet spans, history messages) is rendered exclusively via
// element.textContent / createElement -- never innerHTML on API data -- so
// the seed XSS canary in a node body renders as inert text.
(function () {
  "use strict";

  function el(tag, opts) {
    var node = document.createElement(tag);
    if (opts && opts.text !== undefined) {
      node.textContent = opts.text;
    }
    if (opts && opts.className) {
      node.className = opts.className;
    }
    return node;
  }

  function renderNotice(container, message) {
    container.textContent = "";
    container.appendChild(el("p", { text: message }));
  }

  // debug-plan D8: search results and review items showed a node's id as
  // plain text ("id: abc123 (claim)" / "node_id: abc123") with no way to
  // actually get to that node's detail view -- the only path was manually
  // editing the URL to /node?id=<id>. Found via dogfooding: after D5 added
  // a real way to authenticate, the very next natural action (click a
  // search hit or a review item to see the full node) had no affordance at
  // all. A plain <a href="/node?id=..."> is enough -- no new data, no new
  // endpoint, just linking a value the response already carries.
  function nodeLink(nodeId, text) {
    var a = document.createElement("a");
    a.href = "/node?id=" + encodeURIComponent(nodeId);
    a.textContent = text;
    return a;
  }

  function getToken() {
    try {
      return window.localStorage.getItem("tm_token");
    } catch (err) {
      return null;
    }
  }

  // Auth bar (debug-plan D5): every view previously had exactly one way to
  // set the bearer token -- open DevTools and call
  // `localStorage.setItem('tm_token', ...)` by hand. Found via dogfooding:
  // a real (non-developer) user has no in-page affordance at all, just the
  // "Set tm_token in localStorage to use this view." notice each view
  // already renders. This is the smallest possible affordance to close that
  // gap -- one shared, always-visible bar (container `#tm-auth-bar`,
  // present in every template's shell next to <nav>) that shows a masked
  // token + "Change"/"Clear" once set, or an inline password-type input +
  // "Save token" before that. Storage/format are unchanged (still
  // `localStorage.tm_token`, still read by every existing `getToken()`
  // call) -- this only adds a UI affordance around what already existed,
  // no new persistence mechanism. SPEC-QUESTION (see docs/spec-questions.md
  // D5): spec §4.13 names four views (Node/Review/Search/Sync, +Dashboard
  // from M10) with no fifth "settings"/auth affordance specified -- same
  // "spec silent on a UI affordance" situation T8.3's inline revise-textarea
  // hit, resolved the same way: implement the narrowest thing that closes
  // the gap, log it, don't block on a spec amendment.
  function maskToken(token) {
    if (token.length <= 8) {
      return "••••";
    }
    return token.slice(0, 4) + "…" + token.slice(-4);
  }

  function renderAuthForm(container) {
    container.textContent = "";
    var input = document.createElement("input");
    input.type = "password";
    input.placeholder = "Bearer token";
    input.className = "tm-auth-input";
    var saveBtn = el("button", { text: "Save token" });
    saveBtn.addEventListener("click", function () {
      var value = input.value.trim();
      if (!value) {
        return;
      }
      try {
        window.localStorage.setItem("tm_token", value);
      } catch (err) {
        renderNotice(container, "Could not save token: " + err.message);
        return;
      }
      // Every view's init function reads the token once at load time, so
      // the simplest correct way to make an already-rendered view pick up
      // a freshly saved token is a reload, not a second code path that
      // re-runs each view's own init() from here.
      window.location.reload();
    });
    container.appendChild(input);
    container.appendChild(saveBtn);
  }

  function renderAuthBar(container) {
    container.textContent = "";
    var token = getToken();
    if (!token) {
      renderAuthForm(container);
      return;
    }
    container.appendChild(
      el("span", { text: "Token set (" + maskToken(token) + ")", className: "tm-auth-status" })
    );
    var changeBtn = el("button", { text: "Change token" });
    changeBtn.addEventListener("click", function () {
      renderAuthForm(container);
    });
    container.appendChild(changeBtn);
    var clearBtn = el("button", { text: "Clear token" });
    clearBtn.addEventListener("click", function () {
      try {
        window.localStorage.removeItem("tm_token");
      } catch (err) {
        // ignore -- worst case the stale token is still there to overwrite
      }
      window.location.reload();
    });
    container.appendChild(clearBtn);
  }

  function initAuthBar() {
    var container = document.getElementById("tm-auth-bar");
    if (!container) {
      return;
    }
    renderAuthBar(container);
  }

  function fetchJson(path, token) {
    return fetch(path, { headers: { Authorization: "Bearer " + token } }).then(
      function (resp) {
        if (!resp.ok) {
          throw new Error("request failed: " + path + " (" + resp.status + ")");
        }
        return resp.json();
      }
    );
  }

  function renderBody(container, node) {
    container.textContent = "";
    container.appendChild(el("h2", { text: "Body" }));
    container.appendChild(el("p", { text: node.body }));
  }

  function renderFacets(container, node) {
    container.textContent = "";
    container.appendChild(el("h2", { text: "Facets" }));
    var facets = node.facets || [];
    if (facets.length === 0) {
      container.appendChild(el("p", { text: "No facets." }));
      return;
    }
    var ul = el("ul");
    facets.forEach(function (facet) {
      ul.appendChild(el("li", { text: facet.name + ": " + facet.span }));
    });
    container.appendChild(ul);
  }

  function renderNeighborhood(container, neighborhood) {
    container.textContent = "";
    container.appendChild(el("h2", { text: "Neighborhood" }));
    var edges = neighborhood.edges || [];
    if (edges.length === 0) {
      container.appendChild(el("p", { text: "No neighbors." }));
      return;
    }
    var ul = el("ul");
    edges.forEach(function (edge) {
      ul.appendChild(
        el("li", { text: edge.src + " -" + edge.edge_type + "-> " + edge.dst })
      );
    });
    container.appendChild(ul);
  }

  function renderHistory(container, history) {
    container.textContent = "";
    container.appendChild(el("h2", { text: "History" }));
    var entries = history.history || [];
    if (entries.length === 0) {
      container.appendChild(el("p", { text: "No history." }));
      return;
    }
    var ul = el("ul");
    entries.forEach(function (entry) {
      ul.appendChild(
        el("li", { text: entry.hash + " " + entry.message + " (" + entry.ts + ")" })
      );
    });
    container.appendChild(ul);
  }

  // R9 (PRD): badge/vetting copy uses "vetted by you" language for a vetted
  // node and words like "stale"/"needs recheck" for a facet_break -- it
  // must NEVER contain the word "true".
  function renderBadge(container, node, reviews) {
    container.textContent = "";
    var openBreak = (reviews.reviews || []).find(function (r) {
      return r.cause_kind === "facet_break";
    });
    if (openBreak) {
      container.appendChild(
        el("p", {
          text: "Stale -- needs recheck (cause: facet_break).",
          className: "badge badge-stale",
        })
      );
      return;
    }
    if (node.vetted) {
      container.appendChild(
        el("p", { text: "Vetted by you.", className: "badge badge-vetted" })
      );
      return;
    }
    container.appendChild(
      el("p", { text: "Not yet vetted.", className: "badge badge-neutral" })
    );
  }

  function postJson(path, token, payload) {
    return fetch(path, {
      method: "POST",
      headers: {
        Authorization: "Bearer " + token,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    }).then(function (resp) {
      return resp
        .json()
        .catch(function () {
          return null;
        })
        .then(function (data) {
          return { ok: resp.ok, status: resp.status, data: data };
        });
    });
  }

  // Review view (task T8.3, spec §4.13 / §4.9). One-click resolutions for
  // still_holds/retracted/dismissed; "dismissed" is only rendered for
  // cause_kind === "violation" to mirror the server's 409 rule (spec §4.11)
  // rather than let the user hit a predictable error.
  //
  // SPEC-QUESTION (T8.3): §4.13 asks for "one-click resolutions", but the
  // `revised` resolution inherently requires a new node body (plus
  // change_class/facets_touched) the server cannot infer -- it cannot be
  // truly one-click. This renders a minimal inline <textarea> + a "submit
  // revised" button as the smallest possible affordance; see
  // docs/spec-questions.md T8.3 for the logged ambiguity.
  function renderReviewItem(review, token, onResolved) {
    var li = el("li", { className: "review-item" });
    li.appendChild(el("p", { text: "id: " + review.id }));
    var nodeIdLine = el("p");
    if (review.node_id) {
      nodeIdLine.appendChild(document.createTextNode("node_id: "));
      nodeIdLine.appendChild(nodeLink(review.node_id, review.node_id));
    } else {
      nodeIdLine.textContent = "node_id: (none)";
    }
    li.appendChild(nodeIdLine);
    li.appendChild(el("p", { text: "cause_kind: " + review.cause_kind }));
    li.appendChild(el("p", { text: "facet: " + (review.facet || "(none)") }));
    li.appendChild(el("p", { text: "created_at: " + review.created_at }));

    var errorEl = el("p", { className: "review-error" });

    function resolve(payload) {
      errorEl.textContent = "";
      postJson(
        "/v1/review/" + encodeURIComponent(review.id) + "/resolve",
        token,
        payload
      )
        .then(function (result) {
          if (result.ok) {
            onResolved();
          } else {
            errorEl.textContent = "Resolve failed (" + result.status + ").";
          }
        })
        .catch(function (err) {
          errorEl.textContent = "Resolve failed: " + err.message;
        });
    }

    var stillHoldsBtn = el("button", { text: "still_holds" });
    stillHoldsBtn.addEventListener("click", function () {
      resolve({ resolution: "still_holds" });
    });
    li.appendChild(stillHoldsBtn);

    var retractedBtn = el("button", { text: "retracted" });
    retractedBtn.addEventListener("click", function () {
      resolve({ resolution: "retracted" });
    });
    li.appendChild(retractedBtn);

    if (review.cause_kind === "violation") {
      var dismissedBtn = el("button", { text: "dismissed" });
      dismissedBtn.addEventListener("click", function () {
        resolve({ resolution: "dismissed" });
      });
      li.appendChild(dismissedBtn);
    }

    var revisedTextarea = document.createElement("textarea");
    li.appendChild(revisedTextarea);
    // T8.3 ruling: disclose the invalidation blast radius rather than silently
    // guessing it. The human picks change_class (default minor) — a revision
    // that removes/renames a facet is MAJOR, and under-classifying it would
    // suppress downstream invalidation. facets_touched stays [] in this
    // minimal affordance (SPEC-QUESTION T8.3).
    var classLabel = el("label", { text: "commit as change_class: " });
    var classSelect = document.createElement("select");
    classSelect.className = "revised-change-class";
    ["minor", "major"].forEach(function (c) {
      var opt = document.createElement("option");
      opt.value = c;
      opt.textContent = c;
      classSelect.appendChild(opt);
    });
    classLabel.appendChild(classSelect);
    li.appendChild(classLabel);
    var revisedBtn = el("button", { text: "submit revised" });
    revisedBtn.addEventListener("click", function () {
      resolve({
        resolution: "revised",
        new_body: revisedTextarea.value,
        change_class: classSelect.value,
        facets_touched: [],
      });
    });
    li.appendChild(revisedBtn);

    li.appendChild(errorEl);
    return li;
  }

  function renderReviewQueue(container, reviews, token, onResolved) {
    container.textContent = "";
    if (reviews.length === 0) {
      container.appendChild(el("li", { text: "No open reviews." }));
      return;
    }
    reviews.forEach(function (review) {
      container.appendChild(renderReviewItem(review, token, onResolved));
    });
  }

  // Daily-cap banner (spec §4.9, cap=10). GET /v1/review is uncapped by
  // design (T8.0); the cap-10 is purely this display concern, so the full
  // list still renders and the banner is only an additional signal.
  function renderReviewBanner(container, count) {
    container.textContent = "";
    if (count >= 10) {
      container.appendChild(
        el("p", {
          text: "Daily review cap reached (10 active items).",
          className: "review-cap-banner",
        })
      );
    }
  }

  function initReviewView() {
    var app = document.getElementById("app");
    var token = getToken();
    if (!token) {
      renderNotice(app, "Set tm_token in localStorage to use this view.");
      return;
    }

    var bannerEl = document.getElementById("review-banner");
    var queueEl = document.getElementById("review-queue");

    function load() {
      fetchJson("/v1/review?status=open", token)
        .then(function (result) {
          var reviews = result.reviews || [];
          renderReviewBanner(bannerEl, reviews.length);
          renderReviewQueue(queueEl, reviews, token, load);
        })
        .catch(function (err) {
          renderNotice(app, "Failed to load reviews: " + err.message);
        });
    }

    load();
  }

  function initNodeView() {
    var app = document.getElementById("app");
    var token = getToken();
    if (!token) {
      renderNotice(app, "Set tm_token in localStorage to use this view.");
      return;
    }
    var params = new URLSearchParams(window.location.search);
    var nodeId = params.get("id");
    if (!nodeId) {
      renderNotice(app, "No node id given: add ?id=<node_id> to the URL.");
      return;
    }

    var badgeEl = document.getElementById("node-badge");
    var bodyEl = document.getElementById("node-body");
    var facetsEl = document.getElementById("node-facets");
    var neighborhoodEl = document.getElementById("node-neighborhood");
    var historyEl = document.getElementById("node-history");
    var encodedId = encodeURIComponent(nodeId);

    Promise.all([
      fetchJson("/v1/nodes/" + encodedId, token),
      fetchJson("/v1/nodes/" + encodedId + "/neighborhood", token),
      fetchJson("/v1/nodes/" + encodedId + "/history", token),
      fetchJson("/v1/review?status=open&node=" + encodedId, token),
    ])
      .then(function (results) {
        var node = results[0];
        var neighborhood = results[1];
        var history = results[2];
        var reviews = results[3];
        renderBody(bodyEl, node);
        renderFacets(facetsEl, node);
        renderNeighborhood(neighborhoodEl, neighborhood);
        renderHistory(historyEl, history);
        renderBadge(badgeEl, node, reviews);
      })
      .catch(function (err) {
        renderNotice(app, "Failed to load node: " + err.message);
      });
  }

  // Search view (T8.4, spec §4.13): FTS over /v1/search. Node bodies are
  // free-text (the seed XSS canary lives in a node body), so results render
  // exclusively via textContent, same discipline as the node/review views.
  function truncate(text, max) {
    var str = text || "";
    if (str.length <= max) {
      return str;
    }
    return str.slice(0, max) + "...";
  }

  function renderSearchResults(container, results) {
    container.textContent = "";
    if (results.length === 0) {
      container.appendChild(el("li", { text: "No results." }));
      return;
    }
    results.forEach(function (node) {
      var li = el("li", { className: "search-result" });
      var idLine = el("p");
      idLine.appendChild(nodeLink(node.id, "id: " + node.id));
      idLine.appendChild(document.createTextNode(" (" + node.node_type + ")"));
      li.appendChild(idLine);
      li.appendChild(el("p", { text: truncate(node.body, 200) }));
      container.appendChild(li);
    });
  }

  function initSearchView() {
    var app = document.getElementById("app");
    var token = getToken();
    if (!token) {
      renderNotice(app, "Set tm_token in localStorage to use this view.");
      return;
    }

    var formEl = document.getElementById("search-form");
    var inputEl = document.getElementById("search-input");
    var resultsEl = document.getElementById("search-results");

    function runSearch(q) {
      if (!q) {
        renderNotice(resultsEl, "Enter a search query.");
        return;
      }
      fetchJson("/v1/search?q=" + encodeURIComponent(q), token)
        .then(function (result) {
          renderSearchResults(resultsEl, result.results || []);
        })
        .catch(function (err) {
          renderNotice(resultsEl, "Search failed: " + err.message);
        });
    }

    formEl.addEventListener("submit", function (evt) {
      evt.preventDefault();
      runSearch(inputEl.value.trim());
    });

    // debug-plan D5b: deep-linking straight to /search?q=term previously did
    // nothing until the user retyped and resubmitted -- the querystring was
    // never read on load, only on a real form submit. Hydrate the input and
    // run the same search immediately if a query param is already present,
    // so a bookmarked/shared search URL actually works.
    var initialQuery = new URLSearchParams(window.location.search).get("q");
    if (initialQuery) {
      inputEl.value = initialQuery;
      runSearch(initialQuery.trim());
    }
  }

  // Sync view (T8.4, spec §4.13): per-sync-root status + pause&diff
  // inspector. This MVP labels the resource generically as "sync root"
  // rather than the spec's example Obsidian-specific copy ("Obsidian
  // vault") -- that wording is Obsidian-client-specific presentation (the
  // Obsidian thin client, M6), out of scope for this generic daemon UI.
  //
  // Diffs (unified diffs of arbitrary file text) and violation/pause
  // metadata are free-text -- rendered exclusively via textContent /
  // <pre>.textContent, never innerHTML, same XSS discipline as node/review.
  function renderReviewSummary(item) {
    var p = el("p");
    p.appendChild(document.createTextNode("id: " + item.id + " "));
    // debug-plan D8: same node-detail-link gap as the Review/Search views --
    // sync status items carry a node_id (when one exists) with no way to
    // reach that node's page.
    if (item.node_id) {
      p.appendChild(document.createTextNode("node_id: "));
      p.appendChild(nodeLink(item.node_id, item.node_id));
      p.appendChild(document.createTextNode(" "));
    }
    p.appendChild(
      document.createTextNode(
        "path: " +
          (item.path || "(none)") +
          " cause_kind: " +
          item.cause_kind +
          " created_at: " +
          item.created_at
      )
    );
    return p;
  }

  function renderPauseItem(pause) {
    var li = el("li", { className: "sync-pause" });
    li.appendChild(renderReviewSummary(pause));
    var detail = {};
    try {
      detail = JSON.parse(pause.cause_ref || "{}");
    } catch (err) {
      detail = {};
    }
    var pre = document.createElement("pre");
    pre.textContent = detail.diff || "(no diff available)";
    li.appendChild(pre);
    return li;
  }

  function renderReviewList(title, items, className) {
    var section = el("div", { className: className });
    section.appendChild(el("h3", { text: title + " (" + items.length + ")" }));
    if (items.length === 0) {
      section.appendChild(el("p", { text: "None." }));
      return section;
    }
    var ul = el("ul");
    items.forEach(function (item) {
      var li = el("li");
      li.appendChild(renderReviewSummary(item));
      ul.appendChild(li);
    });
    section.appendChild(ul);
    return section;
  }

  function renderSyncRoot(root) {
    var section = el("div", { className: "sync-root" });
    section.appendChild(
      el("h2", { text: root.name + " (" + root.root_path + ")" })
    );
    section.appendChild(
      el("p", { text: "files: " + (root.files || []).length })
    );
    section.appendChild(
      renderReviewList("Violations", root.violations || [], "sync-violations")
    );

    var pausesSection = el("div", { className: "sync-pauses" });
    var pauses = root.pauses || [];
    pausesSection.appendChild(
      el("h3", { text: "Pause & diff inspector (" + pauses.length + ")" })
    );
    if (pauses.length === 0) {
      pausesSection.appendChild(el("p", { text: "None." }));
    } else {
      var pauseList = el("ul");
      pauses.forEach(function (pause) {
        pauseList.appendChild(renderPauseItem(pause));
      });
      pausesSection.appendChild(pauseList);
    }
    section.appendChild(pausesSection);

    section.appendChild(
      renderReviewList("Conflicts", root.conflicts || [], "sync-conflicts")
    );
    return section;
  }

  function renderSyncRoots(container, roots) {
    container.textContent = "";
    if (roots.length === 0) {
      container.appendChild(el("p", { text: "No sync roots registered." }));
      return;
    }
    roots.forEach(function (root) {
      container.appendChild(renderSyncRoot(root));
    });
  }

  function renderSyncUnresolved(container, unresolved) {
    container.textContent = "";
    container.appendChild(el("h2", { text: "Unresolved (" + unresolved.length + ")" }));
    if (unresolved.length === 0) {
      container.appendChild(el("p", { text: "None." }));
      return;
    }
    var ul = el("ul");
    unresolved.forEach(function (item) {
      var li = el("li");
      li.appendChild(
        el("p", { text: "bucket: " + item.bucket })
      );
      li.appendChild(renderReviewSummary(item));
      ul.appendChild(li);
    });
    container.appendChild(ul);
  }

  function initSyncView() {
    var app = document.getElementById("app");
    var token = getToken();
    if (!token) {
      renderNotice(app, "Set tm_token in localStorage to use this view.");
      return;
    }

    var rootsEl = document.getElementById("sync-roots");
    var unresolvedEl = document.getElementById("sync-unresolved");

    fetchJson("/v1/sync/status", token)
      .then(function (result) {
        renderSyncRoots(rootsEl, result.sync_roots || []);
        renderSyncUnresolved(unresolvedEl, result.unresolved || []);
      })
      .catch(function (err) {
        renderNotice(app, "Failed to load sync status: " + err.message);
      });
  }

  // Dashboard view (T10.1, spec M10 / §7 / §9 story 6): read-only display of
  // the four metric groups the milestone names -- facet coverage, review
  // inflow-vs-resolved with variance, violation rate, crossing rate -- all
  // sourced verbatim from GET /v1/metrics (T9.2). No new metric definitions:
  // this view only formats fields compute_metrics() already returns.
  function formatPct(ratio) {
    return (ratio * 100).toFixed(1) + "%";
  }

  function formatRate(rate) {
    return rate.toFixed(3);
  }

  function renderFacetCoverage(container, metrics) {
    container.textContent = "";
    container.appendChild(el("h2", { text: "Facet coverage" }));
    container.appendChild(
      el("p", { text: formatPct(metrics.facet_coverage) })
    );
  }

  function renderReviewEconomy(container, metrics) {
    container.textContent = "";
    container.appendChild(el("h2", { text: "Review inflow vs. resolution" }));
    var ul = el("ul");
    ul.appendChild(
      el("li", { text: "Inflow (7d): " + metrics.review_inflow_7d })
    );
    ul.appendChild(
      el("li", { text: "Resolved (7d): " + metrics.review_resolved_7d })
    );
    ul.appendChild(
      el("li", {
        text: "Inflow variance (30d): " + formatRate(metrics.inflow_variance_30d),
      })
    );
    container.appendChild(ul);
  }

  function renderViolationRate(container, metrics) {
    container.textContent = "";
    container.appendChild(el("h2", { text: "Violation rate" }));
    container.appendChild(
      el("p", { text: formatRate(metrics.violation_rate) })
    );
  }

  function renderCrossingRate(container, metrics) {
    container.textContent = "";
    container.appendChild(el("h2", { text: "Crossing rate" }));
    container.appendChild(
      el("p", { text: formatRate(metrics.crossing_rate) + " nodes/day" })
    );
  }

  function initDashboardView() {
    var app = document.getElementById("app");
    var token = getToken();
    if (!token) {
      renderNotice(app, "Set tm_token in localStorage to use this view.");
      return;
    }

    var facetCoverageEl = document.getElementById("dashboard-facet-coverage");
    var reviewEconomyEl = document.getElementById("dashboard-review-economy");
    var violationRateEl = document.getElementById("dashboard-violation-rate");
    var crossingRateEl = document.getElementById("dashboard-crossing-rate");

    fetchJson("/v1/metrics", token)
      .then(function (metrics) {
        renderFacetCoverage(facetCoverageEl, metrics);
        renderReviewEconomy(reviewEconomyEl, metrics);
        renderViolationRate(violationRateEl, metrics);
        renderCrossingRate(crossingRateEl, metrics);
      })
      .catch(function (err) {
        renderNotice(app, "Failed to load metrics: " + err.message);
      });
  }

  // app.js is loaded in <head>, so defer view init until the DOM (#app and
  // the view containers) is parsed — otherwise getElementById returns null.
  function boot() {
    initAuthBar();
    if (window.location.pathname === "/node") {
      initNodeView();
    } else if (window.location.pathname === "/review") {
      initReviewView();
    } else if (window.location.pathname === "/search") {
      initSearchView();
    } else if (window.location.pathname === "/sync") {
      initSyncView();
    } else if (window.location.pathname === "/dashboard") {
      initDashboardView();
    }
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
