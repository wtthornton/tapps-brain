/* brain-visual-graph.js — P1 knowledge-graph focus-view panel.
 *
 * Renders GET /snapshot/graph?entity=<uuid>&project=<id>&limit=<n> as an
 * interactive Cytoscape graph. Encodes the signals that make the KG richer than
 * a plain hyperlink graph: edge opacity = confidence, red-dashed = contradicted,
 * faded = stale/superseded. Click a neighbour to re-centre (1-hop expand);
 * click an edge/node for its details.
 *
 * Constraints (match brain-visual-router.js): IIFE, no bundler, XSS-safe —
 * only textContent / attributes, never innerHTML with server data. Cytoscape is
 * vendored locally (cytoscape.min.js) — no CDN, CSP-safe.
 */
(function () {
  "use strict";

  // Amber-leaning palette (stays in the NLT brand wedge; no cyan/blue/purple).
  var TYPE_COLORS = ["#b45309", "#c2831f", "#9a6a12", "#d0a24a", "#7c5410", "#e0b566"];
  var ROOT_COLOR = "#8a3a05";

  function typeColor(type) {
    if (!type) return TYPE_COLORS[0];
    var h = 0;
    for (var i = 0; i < type.length; i++) h = (h * 31 + type.charCodeAt(i)) >>> 0;
    return TYPE_COLORS[h % TYPE_COLORS.length];
  }

  function graphBaseUrl() {
    var meta = document.querySelector('meta[name="tapps-snapshot-url"]');
    var base = "/snapshot";
    if (meta && meta.getAttribute("content") && !meta.getAttribute("content").startsWith("__")) {
      base = meta.getAttribute("content");
    }
    return base + "/graph";
  }

  function currentProject() {
    var sel = document.getElementById("project-filter");
    return sel && sel.value ? sel.value.trim() : "";
  }

  function setMsg(text) {
    var el = document.getElementById("kg-msg");
    if (el) el.textContent = text;
  }

  function setKpis(graph) {
    var contra = (graph.edges || []).filter(function (e) { return e.contradicted; }).length;
    var n = document.getElementById("kg-kpi-nodes");
    var e = document.getElementById("kg-kpi-edges");
    var c = document.getElementById("kg-kpi-contra");
    if (n) n.textContent = String(graph.node_count != null ? graph.node_count : (graph.nodes || []).length);
    if (e) e.textContent = String(graph.edge_count != null ? graph.edge_count : (graph.edges || []).length);
    if (c) c.textContent = String(contra);
  }

  /** Render a {key: value} map into #kg-detail as a <dl> (textContent only). */
  function showDetail(title, pairs) {
    var box = document.getElementById("kg-detail");
    if (!box) return;
    box.textContent = "";
    var h = document.createElement("strong");
    h.textContent = title;
    box.appendChild(h);
    var dl = document.createElement("dl");
    pairs.forEach(function (p) {
      var dt = document.createElement("dt");
      dt.textContent = p[0];
      var dd = document.createElement("dd");
      dd.textContent = String(p[1]);
      dl.appendChild(dt);
      dl.appendChild(dd);
    });
    box.appendChild(dl);
  }

  function toElements(graph) {
    var els = [];
    (graph.nodes || []).forEach(function (nd) {
      els.push({
        data: {
          id: nd.id,
          label: nd.label || nd.id,
          type: nd.type || "",
          confidence: typeof nd.confidence === "number" ? nd.confidence : 0,
          isRoot: !!nd.is_root,
        },
      });
    });
    (graph.edges || []).forEach(function (ed) {
      els.push({
        data: {
          id: ed.id,
          source: ed.source,
          target: ed.target,
          predicate: ed.predicate || "",
          confidence: typeof ed.confidence === "number" ? ed.confidence : 0,
          status: ed.status || "active",
          contradicted: !!ed.contradicted,
          stability: typeof ed.stability === "number" ? ed.stability : 0,
          evidence: typeof ed.evidence_count === "number" ? ed.evidence_count : 0,
        },
      });
    });
    return els;
  }

  var cy = null;

  function styleSheet() {
    return [
      {
        selector: "node",
        style: {
          "background-color": function (n) { return typeColor(n.data("type")); },
          label: "data(label)",
          color: "#3a2f1c",
          "font-size": "9px",
          "text-wrap": "wrap",
          "text-max-width": "90px",
          "text-valign": "bottom",
          "text-margin-y": 3,
          width: 22,
          height: 22,
        },
      },
      {
        selector: "node[?isRoot]",
        style: {
          "background-color": ROOT_COLOR,
          "border-width": 4,
          "border-color": "#e0b566",
          width: 34,
          height: 34,
          "font-size": "11px",
          "font-weight": "bold",
        },
      },
      {
        selector: "edge",
        style: {
          "curve-style": "bezier",
          "line-color": "#9a6a12",
          "target-arrow-color": "#9a6a12",
          "target-arrow-shape": "triangle",
          "arrow-scale": 0.7,
          // Opacity + width encode confidence.
          opacity: function (e) { return 0.25 + 0.75 * Math.max(0, Math.min(1, e.data("confidence"))); },
          width: function (e) { return 1 + 3 * Math.max(0, Math.min(1, e.data("confidence"))); },
        },
      },
      {
        selector: 'edge[status = "stale"], edge[status = "superseded"]',
        style: { opacity: 0.3, "line-style": "dotted" },
      },
      {
        selector: "edge[?contradicted]",
        style: { "line-color": "#c0392b", "target-arrow-color": "#c0392b", "line-style": "dashed" },
      },
      { selector: ".kg-dim", style: { opacity: 0.12 } },
    ];
  }

  function render(graph) {
    var container = document.getElementById("kg-cy");
    if (!container || typeof cytoscape !== "function") {
      setMsg("Cytoscape failed to load; cannot render the graph.");
      return;
    }
    if (cy) { cy.destroy(); cy = null; }
    cy = cytoscape({
      container: container,
      elements: toElements(graph),
      style: styleSheet(),
      layout: { name: "cose", animate: false, padding: 20, nodeRepulsion: 6000 },
      wheelSensitivity: 0.2,
    });

    cy.on("tap", "node", function (evt) {
      var d = evt.target.data();
      if (d.isRoot) {
        showDetail("Focus entity", [["id", d.id], ["type", d.type || "—"], ["label", d.label]]);
        return;
      }
      showDetail("Neighbour", [
        ["label", d.label],
        ["type", d.type || "—"],
        ["confidence", d.confidence.toFixed(3)],
        ["id", d.id],
      ]);
      // Re-centre on the clicked neighbour (1-hop expand).
      loadGraph(d.id);
    });

    cy.on("tap", "edge", function (evt) {
      var d = evt.target.data();
      showDetail("Edge", [
        ["predicate", d.predicate || "—"],
        ["confidence", d.confidence.toFixed(3)],
        ["status", d.status],
        ["contradicted", d.contradicted ? "yes" : "no"],
        ["stability (decay)", d.stability.toFixed(2)],
        ["evidence", d.evidence],
      ]);
    });

    cy.on("tap", function (evt) {
      if (evt.target === cy) cy.elements().removeClass("kg-dim");
    });
  }

  function loadGraph(entity) {
    entity = (entity || "").trim();
    var input = document.getElementById("kg-entity");
    if (input && entity) input.value = entity;
    if (!entity) { setMsg("Enter an entity UUID and press Load."); return; }

    var project = currentProject();
    var limitEl = document.getElementById("kg-limit");
    var limit = limitEl && limitEl.value ? parseInt(limitEl.value, 10) : 40;
    if (!(limit > 0)) limit = 40;

    var url = graphBaseUrl() + "?entity=" + encodeURIComponent(entity) + "&limit=" + limit;
    if (project) url += "&project=" + encodeURIComponent(project);

    setMsg("Loading " + entity + "…");
    fetch(url, { cache: "no-store" })
      .then(function (resp) {
        if (!resp.ok) return resp.json().then(function (b) {
          throw new Error((b && b.detail) || ("HTTP " + resp.status));
        }, function () { throw new Error("HTTP " + resp.status); });
        return resp.json();
      })
      .then(function (graph) {
        setKpis(graph);
        render(graph);
        var nn = (graph.nodes || []).length;
        setMsg(nn <= 1
          ? "No neighbours for this entity (isolated node or unseeded KG)."
          : "Showing " + (nn - 1) + " neighbour(s). Click a node to expand, an edge for evidence.");
      })
      .catch(function (err) {
        var m = String(err && err.message ? err.message : err).slice(0, 120);
        setMsg("Could not load graph: " + m
          + (currentProject() ? "" : " (tip: select a project in the top filter)."));
      });
  }

  function loadHealth() {
    var project = currentProject();
    var url = graphBaseUrl() + "/health";
    if (project) url += "?project=" + encodeURIComponent(project);
    setMsg("Loading graph health…");
    fetch(url, { cache: "no-store" })
      .then(function (resp) {
        if (!resp.ok) return resp.json().then(function (b) {
          throw new Error((b && b.detail) || ("HTTP " + resp.status));
        }, function () { throw new Error("HTTP " + resp.status); });
        return resp.json();
      })
      .then(function (h) {
        var pairs = [
          ["status", h.status],
          ["entities (active)", h.entities_active],
          ["orphans", h.orphan_entities + " (" + h.orphan_ratio + ")"],
          ["edges (total)", h.edges_total],
          ["stale/superseded", (h.edges_stale + h.edges_superseded) + " (" + h.stale_ratio + ")"],
          ["contradicted", h.edges_contradicted + " (" + h.contradicted_ratio + ")"],
        ];
        (h.recommendations || []).forEach(function (r, i) { pairs.push(["fix " + (i + 1), r]); });
        showDetail("Graph health", pairs);
        setMsg("Graph health: " + h.status
          + ((h.recommendations || []).length ? " — " + h.recommendations.length + " item(s) to review." : " — no issues."));
      })
      .catch(function (err) {
        var m = String(err && err.message ? err.message : err).slice(0, 120);
        setMsg("Could not load graph health: " + m
          + (currentProject() ? "" : " (tip: select a project in the top filter)."));
      });
  }

  document.addEventListener("DOMContentLoaded", function () {
    var btn = document.getElementById("kg-load");
    var input = document.getElementById("kg-entity");
    var healthBtn = document.getElementById("kg-health");
    if (btn) btn.addEventListener("click", function () { loadGraph(input ? input.value : ""); });
    if (healthBtn) healthBtn.addEventListener("click", loadHealth);
    if (input) input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); loadGraph(input.value); }
    });
  });

  // Expose for tests / console.
  window.tappsKgLoadGraph = loadGraph;
  window.tappsKgLoadHealth = loadHealth;
})();
