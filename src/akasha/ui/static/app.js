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

  function getToken() {
    try {
      return window.localStorage.getItem("tm_token");
    } catch (err) {
      return null;
    }
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

  // app.js is loaded in <head>, so defer view init until the DOM (#app and
  // the view containers) is parsed — otherwise getElementById returns null.
  function boot() {
    if (window.location.pathname === "/node") {
      initNodeView();
    }
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
