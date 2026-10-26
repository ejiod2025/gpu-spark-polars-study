"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const state = { data: null, suite: "original", query: "__suite__" };
  const engines = ["polars", "spark_cpu", "spark_gpu"];
  const coverageLabels = {
    gpu_operators_present: "GPU operators present",
    hybrid: "Hybrid CPU / GPU",
    cpu_fallback: "CPU fallback",
  };
  const formatInteger = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
  const list = (value) => Array.isArray(value) ? value : [];
  const finite = (value) => typeof value === "number" && Number.isFinite(value);
  const positive = (value) => finite(value) && value > 0;
  const seconds = (value) => positive(value) ? value.toFixed(value < 0.01 ? 4 : 3) : "—";
  const observedSeconds = (value) => finite(value) && value >= 0 ? value.toFixed(value > 0 && value < 0.01 ? 4 : 3) : "—";
  const ratioText = (value) => positive(value) ? `${value.toFixed(2)}×` : "—";
  const scaleOrder = (left, right) => Number(left.scale_factor) - Number(right.scale_factor);

  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined && text !== null) element.textContent = String(text);
    if (className) element.className = className;
    return element;
  }

  function show(id, visible) { $(id).hidden = !visible; }

  function safeUrl(value, localOnly = false) {
    if (typeof value !== "string" || !value.trim() || /[\\\u0000-\u001f]/.test(value)) return null;
    const raw = value.trim();
    if (/^(?:file|javascript|data|vbscript):/i.test(raw) || raw.startsWith("/") || raw.startsWith("#")) return null;
    let decoded;
    try { decoded = decodeURIComponent(raw); } catch { return null; }
    if (decoded.split(/[/?#]/).includes("..") || /^(?:Users|home|private|var)\//i.test(decoded)) return null;
    try {
      const url = new URL(raw, document.baseURI);
      if (!["http:", "https:"].includes(url.protocol)) return null;
      if (localOnly && (url.origin !== location.origin || /^[a-z][a-z\d+.-]*:/i.test(raw))) return null;
      return url.href;
    } catch { return null; }
  }

  function link(label, url) {
    const element = node("a", label);
    element.href = url;
    if (new URL(url).origin !== location.origin) {
      element.target = "_blank";
      element.rel = "noopener noreferrer";
      element.setAttribute("aria-label", `${label} (opens in a new tab)`);
    }
    return element;
  }

  function renderFacts(id, facts) {
    $(id).replaceChildren(...list(facts).map((fact) => {
      const row = node("div", null, "fact-row");
      row.append(node("dt", fact.label), node("dd", fact.value));
      return row;
    }));
  }

  function renderStudy() {
    const { publication = {}, study = {}, suites = {} } = state.data;
    if (publication.banner) $("draft-banner-text").textContent = publication.banner;
    if (study.title) {
      $("study-title").textContent = study.title;
      document.title = study.title;
    }
    if (study.subtitle) $("study-subtitle").textContent = study.subtitle;
    const totals = list(suites.original?.totals).slice().sort(scaleOrder);
    if (totals.length) {
      const largest = totals[totals.length - 1];
      const allPolarsWins = totals.every((row) => positive(row.ratios?.polars_over_gpu) && row.ratios.polars_over_gpu < 1);
      if (allPolarsWins) {
        $("baseline-finding").textContent = `In the original six-query baseline, Polars has the lower sum of per-query medians at every measured scale, through ${largest.scale.toUpperCase()}. One A100 host; a bounded workload and configuration.`;
      }
    }
    renderFacts("hardware-facts", study.hardware);
    renderFacts("software-facts", study.software);
    $("limitations").replaceChildren(...list(study.limitations).map((item) => node("li", item)));
    const sourceHeading = node("h3", "Context and documentation");
    const sourceList = node("ul");
    for (const source of list(study.sources)) {
      const url = safeUrl(source.url);
      if (!url) continue;
      const item = node("li");
      item.append(link(source.label, url));
      sourceList.append(item);
    }
    $("study-sources").replaceChildren(...(sourceList.children.length ? [sourceHeading, sourceList] : []));
    renderIncident();
  }

  function suiteData() { return state.data.suites[state.suite]; }

  function hasVerifiedCleanup(suite) {
    return suite.protocol?.id === "verified_gc" && suite.cleanup?.status === "verified";
  }

  function hasFormalResults(suite) {
    const allowed = state.suite === "original" ? suite.status === "verified" : ["partial", "complete"].includes(suite.status);
    // A smoke-only session never becomes a formal timing finding in this UI.
    const uniformProtocol = state.suite === "original" || (hasVerifiedCleanup(suite) && list(suite.rows).every((row) => row.query_cleanup_policy === "verified_gc"));
    return allowed && uniformProtocol && list(suite.rows).length > 0 && list(suite.rows).every((row) => Number.isInteger(row.measured_trials) && row.measured_trials >= 3);
  }

  function renderProtocol(suite) {
    const complex = state.suite === "complex";
    const protocol = suite.protocol || {};
    $("protocol-heading").textContent = complex
      ? protocol.label || "Verified cleanup after every query"
      : "Original within-session measurements";
    $("protocol-description").textContent = complex
      ? protocol.description || "Every included complex v2 scale and engine must use the verified_gc cleanup protocol."
      : "The original six-query suite retains its recorded within-session protocol. It is displayed separately from the complex suite’s verified cleanup protocol.";
    $("protocol-timing-note").textContent = complex
      ? protocol.timing_note || "Query medians exclude cleanup. Observed cleanup costs and process wall time are disclosed separately below."
      : "Original query timers include query construction, optimization, reads, execution and collection; startup and setup are excluded.";
  }

  function cleanupOrder(left, right) {
    const scale = (item) => Number(String(item.scale).replace(/^sf/, "").replace("p", "."));
    return scale(left) - scale(right) || String(left.query || "").localeCompare(String(right.query || "")) || engines.indexOf(left.engine) - engines.indexOf(right.engine);
  }

  function cleanupTable(id, rows, byCase) {
    const caption = node("caption", byCase
      ? "Observed cleanup barriers by query and engine · includes saved warmup and measured-trial barriers"
      : "All saved session barriers by scale and engine · process wall time reported separately");
    const head = node("thead");
    const header = node("tr");
    const labels = byCase
      ? ["Scale", "Query", "Engine", "Cleanup barriers", "Cleanup sum (s)"]
      : ["Scale", "Engine", "Cleanup barriers", "Cleanup sum (s)", "Process wall (s)"];
    for (const label of labels) {
      const cell = node("th", label);
      cell.scope = "col";
      header.append(cell);
    }
    head.append(header);
    const body = node("tbody");
    const names = { polars: "Polars CPU", spark_cpu: "CPU Spark", spark_gpu: "Spark + GPU plugin" };
    for (const row of rows.slice().sort(cleanupOrder)) {
      const tr = node("tr");
      const scale = node("th", String(row.scale).toUpperCase());
      scale.scope = "row";
      tr.append(scale);
      if (byCase) tr.append(node("td", String(row.query).toUpperCase()));
      const count = Number.isInteger(row.barrier_count) && row.barrier_count >= 0 ? formatInteger.format(row.barrier_count) : "—";
      tr.append(node("td", names[row.engine] || row.engine), node("td", count), node("td", observedSeconds(row.cleanup_seconds)));
      if (!byCase) tr.append(node("td", observedSeconds(row.process_wall_seconds)));
      body.append(tr);
    }
    $(id).replaceChildren(caption, head, body);
  }

  function renderCleanup(suite) {
    const complex = state.suite === "complex";
    show("cleanup-section", complex);
    if (!complex) return;
    const cleanup = suite.cleanup || {};
    const verified = hasVerifiedCleanup(suite);
    const sessions = verified ? list(cleanup.sessions) : [];
    const cases = verified ? list(cleanup.cases).filter((item) => state.query === "__suite__" || item.query === state.query) : [];
    $("cleanup-status").textContent = verified ? "Verified cleanup observations · reported separately" : "Cleanup evidence pending";
    $("cleanup-scope").textContent = cleanup.scope || "Cleanup durations summarize all saved session barriers. They are observed maintenance costs, not query-time medians or an added performance score.";
    show("cleanup-pending", sessions.length === 0);
    $("cleanup-pending").textContent = verified
      ? "No saved session cleanup rows are supplied for this snapshot. No cleanup cost is inferred."
      : "Verified cleanup observations will appear here when uniform verified_gc evidence is supplied. Legacy or mixed-protocol observations are not used for formal comparisons.";
    show("cleanup-sessions-wrapper", sessions.length > 0);
    show("cleanup-cases-details", cases.length > 0);
    $("cleanup-cases-summary").textContent = state.query === "__suite__"
      ? "Inspect cleanup observations by query and engine"
      : `Inspect cleanup observations for ${state.query.toUpperCase()} by engine`;
    if (sessions.length) cleanupTable("cleanup-sessions-table", sessions, false);
    else $("cleanup-sessions-table").replaceChildren();
    if (cases.length) cleanupTable("cleanup-cases-table", cases, true);
    else $("cleanup-cases-table").replaceChildren();
  }

  function renderIncident() {
    const incident = state.data.incident;
    show("incident-card", Boolean(incident));
    if (!incident) return;
    const documented = incident.status === "documented";
    $("incident-status").textContent = documented ? "Documented method incident" : "Incident documentation pending";
    $("incident-title").textContent = incident.title || "Method incident";
    $("incident-summary").textContent = incident.summary || "The incident record and supporting evidence will be supplied separately from performance results.";
    $("incident-exclusion").textContent = incident.performance_use || "Excluded from performance comparisons";
    $("incident-resources").replaceChildren(...list(incident.evidence).map(resourceCard));
  }

  function renderSelector(suite) {
    const select = $("query-selector");
    const options = [node("option", "Suite aggregate — sum of per-query medians")];
    options[0].value = "__suite__";
    for (const query of list(suite.queries)) {
      const option = node("option", `${query.id.toUpperCase()} — ${query.description}`);
      option.value = query.id;
      options.push(option);
    }
    if (!options.some((option) => option.value === state.query)) state.query = "__suite__";
    select.replaceChildren(...options);
    select.value = state.query;
    select.disabled = false;
  }

  function sqlDetails(query) {
    if (typeof query.sql !== "string" || !query.sql.trim()) return null;
    const details = node("details");
    details.append(node("summary", "Read the SQL"));
    const pre = node("pre");
    pre.append(node("code", query.sql.trim()));
    details.append(pre);
    return details;
  }

  function queryTags(query) {
    const tags = node("div", null, "tags");
    if (query.category) tags.append(node("span", query.category, "tag"));
    if (Number.isInteger(query.join_count)) tags.append(node("span", `${query.join_count} logical join operation${query.join_count === 1 ? "" : "s"}`, "tag"));
    if (list(query.tables).length) tags.append(node("span", query.tables.join(" · "), "tag"));
    return tags;
  }

  function renderQueryDetail(suite) {
    const query = list(suite.queries).find((item) => item.id === state.query);
    show("query-detail", Boolean(query));
    if (!query) { $("query-detail").replaceChildren(); return; }
    const content = [node("h3", query.id.toUpperCase()), node("p", query.description), queryTags(query)];
    if (query.plan_note) content.push(node("p", query.plan_note, "small-note"));
    const sql = sqlDetails(query);
    if (sql) content.push(sql);
    $("query-detail").replaceChildren(...content);
  }

  function renderCatalog(suite) {
    $("catalog-intro").textContent = state.suite === "original"
      ? "Four of these six original queries already contain joins: Q03, Q10, Q12 and Q14. This is a selected workload, not a complete TPC-H benchmark."
      : "These six designs form a separate suite. Pending designs are visible for inspection; smoke checks establish no formal performance finding.";
    $("query-catalog").replaceChildren(...list(suite.queries).map((query, index) => {
      const article = node("article", null, "design-card");
      article.append(node("span", String(index + 1).padStart(2, "0"), "design-index"));
      const content = node("div");
      content.append(node("h3", query.id.toUpperCase(), "design-title"), node("p", query.description, "design-description"), queryTags(query));
      if (query.plan_note) content.append(node("p", query.plan_note, "small-note"));
      const sql = sqlDetails(query);
      if (sql) content.append(sql);
      article.append(content);
      return article;
    }));
  }

  function metric(label, value, detail, tone) {
    const item = node("div", null, "metric");
    if (tone) item.dataset.tone = tone;
    item.append(node("p", label, "metric-label"), node("p", value, "metric-value"), node("p", detail, "metric-detail"));
    return item;
  }

  function renderMetrics(rows, suiteView) {
    const latest = rows[rows.length - 1];
    const ratio = latest.ratios?.polars_over_gpu;
    const cpuRatio = latest.ratios?.spark_cpu_over_gpu;
    const scale = String(latest.scale).toUpperCase();
    let direction = "Ratio unavailable";
    if (positive(ratio)) direction = ratio < 1 ? `Polars is faster by ${ratioText(1 / ratio)} at this observation.` : ratio > 1 ? `The GPU-plugin Spark session is faster by ${ratioText(ratio)} at this observation.` : "Equal median time at this observation.";
    $("summary-metrics").replaceChildren(
      metric(`Polars / GPU at ${scale}`, ratioText(ratio), direction, positive(ratio) && ratio < 1 ? "polars" : "gpu"),
      metric(`CPU Spark / GPU at ${scale}`, ratioText(cpuRatio), "Above 1× favors the GPU-plugin Spark session.", "spark"),
      metric("Measured trials / query", String(latest.measured_trials), suiteView ? "Aggregate is a sum of query medians; no suite-level trial was timed." : "Within one engine session; min–max ranges are descriptive."),
    );
    show("summary-metrics", true);
  }

  function timeCell(row, engine, suiteView) {
    const cell = node("td");
    const values = row.times?.[engine];
    const median = suiteView ? values?.sum_medians_seconds : values?.median;
    cell.append(node("span", seconds(median), "time-value"));
    if (!suiteView && positive(values?.min) && positive(values?.max)) cell.append(node("span", `[${seconds(values.min)}, ${seconds(values.max)}]`, "time-range"));
    return cell;
  }

  function renderTable(rows, suite) {
    const suiteView = state.query === "__suite__";
    const table = $("results-table");
    const caption = node("caption", `${suite.label} · ${suiteView ? "sum of per-query medians" : state.query.toUpperCase() + " median [minimum, maximum]"} · seconds`);
    const head = node("thead");
    const header = node("tr");
    for (const label of ["Scale", "Polars CPU", "CPU Spark", "Spark + GPU plugin", "Polars / GPU", "CPU Spark / GPU", suiteView ? "Stored dataset" : "GPU plan coverage"]) {
      const cell = node("th", label);
      cell.scope = "col";
      header.append(cell);
    }
    head.append(header);
    const body = node("tbody");
    for (const row of rows) {
      const tr = node("tr");
      const scale = node("th", String(row.scale).toUpperCase());
      scale.scope = "row";
      tr.append(scale, ...engines.map((engine) => timeCell(row, engine, suiteView)), node("td", ratioText(row.ratios?.polars_over_gpu)), node("td", ratioText(row.ratios?.spark_cpu_over_gpu)));
      const detail = node("td");
      if (suiteView) {
        if (positive(row.parquet_bytes)) detail.append(node("span", `${(row.parquet_bytes / (1024 ** 3)).toFixed(3)} GiB stored`, "time-value"));
        if (finite(row.lineitem_rows)) detail.append(node("span", `${formatInteger.format(row.lineitem_rows)} lineitem rows`, "time-range"));
        if (!detail.children.length) detail.append(node("span", "Not provided", "pending-value"));
      } else {
        detail.append(node("span", coverageLabels[row.coverage] || row.coverage || "Not provided", "coverage-label"));
      }
      tr.append(detail);
      body.append(tr);
    }
    table.replaceChildren(caption, head, body);
    show("results-table-wrapper", true);
    show("ratio-definition", true);
  }

  function renderChart(suite, formal) {
    const url = formal && safeUrl(suite.chart?.url, true);
    show("report-figure", Boolean(url));
    if (!url) { $("report-chart").removeAttribute("src"); return; }
    $("report-chart").src = url;
    $("report-chart").alt = suite.chart.alt || "Verified measurement figure from the selected suite report.";
    const caption = state.query === "__suite__"
      ? "Verified report figure. Observed points only; no fitted threshold or extrapolation."
      : "Verified figure for the selected suite. The table below shows the individual query selected above.";
    $("chart-caption").textContent = suite.chart.scope ? `${suite.chart.scope} ${caption}` : caption;
  }

  function renderPending(suite, formal, hasRows) {
    show("pending-state", !hasRows);
    if (hasRows) return;
    const awaitingProtocol = state.suite === "complex" && !hasVerifiedCleanup(suite);
    const title = formal ? "No aggregate supplied for this selection" : awaitingProtocol ? "Verified complex protocol evidence is pending" : "Complex performance results are pending";
    const text = formal
      ? "Only included measurements are shown. Choose an individual query to inspect any supplied cases; an incomplete suite is not given an invented aggregate."
      : awaitingProtocol
        ? "Formal complex comparisons require uniform verified_gc sessions. Legacy or mixed-protocol observations are not displayed as a performance curve. Query designs remain available; smoke checks establish no formal performance finding."
        : "The query designs below are available to inspect. Smoke runs are execution checks, not formal performance results; no timings or crossover claim are inferred from them.";
    $("pending-state").replaceChildren(node("span", formal ? "Selection coverage" : "Research in progress", "empty-kicker"), node("h3", title), node("p", text), node("p", suite.status_text || "", "small-note"));
  }

  function resourceCard(resource) {
    const url = resource.enabled === true ? safeUrl(resource.url) : null;
    const article = node("article", null, `resource-card${url ? "" : " is-disabled"}`);
    const label = node("h3", null, "resource-label");
    label.append(url ? link(resource.label, url) : node("span", resource.label));
    article.append(label, node("p", resource.note || (url ? "Available in this research snapshot." : "Pending — no resource link is enabled."), "resource-note"));
    article.append(node("span", url ? "Available for review" : "Pending · unavailable", "resource-status"));
    if (typeof resource.sha256 === "string" && /^[a-f\d]{64}$/i.test(resource.sha256)) {
      const hash = node("p", null, "resource-note");
      hash.append(node("span", "SHA-256 "), node("code", resource.sha256));
      article.append(hash);
    }
    return article;
  }

  function renderEvidence(suite) {
    const resources = [...list(suite.evidence), ...list(state.data.evidence)];
    $("evidence-resources").replaceChildren(...resources.map(resourceCard));
    const excluded = list(suite.excluded_cases);
    const unselected = list(suite.not_selected_cases);
    show("excluded-details", excluded.length + unselected.length > 0);
    const content = [];
    for (const [label, items] of [["Excluded", excluded], ["Not selected", unselected]]) {
      if (!items.length) continue;
      content.push(node("h3", `${label} (${items.length})`));
      const ul = node("ul");
      for (const item of items) {
        const reasons = list(item.reasons).join("; ") || item.reason || "No result is included for this case.";
        ul.append(node("li", `${item.scale || ""} / ${item.query || "suite"}: ${reasons}`));
      }
      content.push(ul);
    }
    $("excluded-cases").replaceChildren(...content);
  }

  function render() {
    const suite = suiteData();
    const formal = hasFormalResults(suite);
    const suiteView = state.query === "__suite__";
    for (const tab of document.querySelectorAll('[role="tab"][data-suite]')) {
      const active = tab.dataset.suite === state.suite;
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
      tab.classList.toggle("is-active", active);
    }
    $("explorer-panel").setAttribute("aria-labelledby", `tab-${state.suite}`);
    const awaitingProtocol = state.suite === "complex" && !hasVerifiedCleanup(suite) && suite.status !== "smoke_only";
    const status = awaitingProtocol ? "Protocol evidence pending" : suite.status === "verified" ? "Verified baseline" : suite.status === "complete" ? "Complete included cases" : suite.status === "partial" ? "Partial coverage" : suite.status === "smoke_only" ? "Smoke checks only" : "Results pending";
    $("suite-status-chip").textContent = status;
    $("suite-status-chip").className = `status-chip ${formal ? "is-verified" : suite.status === "smoke_only" ? "is-smoke" : "is-pending"}`;
    $("suite-status-text").textContent = suite.status_text || "";
    $("result-definition").textContent = suiteView
      ? "Suite aggregate = sum of six per-query medians, not a directly measured suite runtime. Three measured trials within one session; preheated filesystem policy; startup excluded. Stored Parquet bytes count all generated tables, not bytes scanned by a query."
      : "Individual query time = median [minimum, maximum] of three measured trials within one engine session. Preheated filesystem policy; startup excluded. These descriptive ranges do not establish tail latency or independent experimental repeatability.";
    renderProtocol(suite);
    renderQueryDetail(suite);
    renderCatalog(suite);
    const rows = formal ? (suiteView ? list(suite.totals) : list(suite.rows).filter((row) => row.query === state.query)).slice().sort(scaleOrder) : [];
    show("summary-metrics", false);
    show("results-table-wrapper", false);
    show("ratio-definition", false);
    renderPending(suite, formal, rows.length > 0);
    if (rows.length) { renderMetrics(rows, suiteView); renderTable(rows, suite); }
    renderChart(suite, formal);
    const validation = suite.validation || {};
    $("validation-summary").textContent = formal && finite(validation.outputs)
      ? `${formatInteger.format(validation.outputs)} saved outputs checked (${formatInteger.format(validation.measured_outputs || 0)} measured, ${formatInteger.format(validation.warmup_outputs || 0)} warmup); ${formatInteger.format(validation.gpu_plans || 0)} GPU-session plans inspected. Numeric tolerance: relative ${validation.relative_tolerance ?? "not provided"}, absolute ${validation.absolute_tolerance ?? "not provided"}. Plan inspection is not a runtime-share measurement.`
      : "Formal complex-result validation will be reported with the included measurements. Smoke checks are not a formal comparison.";
    renderCleanup(suite);
    renderEvidence(suite);
  }

  function selectSuite(key, moveFocus = false) {
    if (!state.data || !state.data.suites[key]) return;
    state.suite = key;
    state.query = "__suite__";
    renderSelector(suiteData());
    render();
    if (moveFocus) $(`tab-${key}`).focus();
  }

  for (const tab of document.querySelectorAll('[role="tab"][data-suite]')) {
    tab.addEventListener("click", () => selectSuite(tab.dataset.suite));
    tab.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const key = event.key === "Home" ? "original" : event.key === "End" ? "complex" : state.suite === "original" ? "complex" : "original";
      selectSuite(key, true);
    });
  }
  $("query-selector").addEventListener("change", (event) => { state.query = event.target.value; render(); });
  $("report-chart").addEventListener("error", () => {
    show("report-figure", false);
    $("suite-status-text").textContent += " The report image is unavailable in this local snapshot; the supplied measurement table remains accessible.";
  });

  fetch("data.json", { cache: "no-store", credentials: "same-origin" })
    .then((response) => { if (!response.ok) throw new Error("Snapshot request failed"); return response.json(); })
    .then((data) => {
      if (!data || !data.suites?.original || !data.suites?.complex) throw new Error("Snapshot schema is incomplete");
      state.data = data;
      renderStudy();
      renderSelector(suiteData());
      render();
      show("loading-state", false);
    })
    .catch(() => {
      show("loading-state", false);
      $("load-error").textContent = "The local evidence snapshot could not be loaded. Reload this study page; its evidence snapshot must be available.";
      show("load-error", true);
      $("suite-status-chip").textContent = "Evidence unavailable";
      $("suite-status-text").textContent = "No measurement table is displayed without its data snapshot.";
    });
})();
