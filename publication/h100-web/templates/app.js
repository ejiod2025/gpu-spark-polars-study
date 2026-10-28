/* Numerical results are emitted only by the private, fail-closed publication builder. */
"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const labels = {polars_cpu_144:"CPU Polars 1.44.2",polars_cpu_200:"CPU Polars 2.0.0",polars_gpu:"Polars GPU",spark_cpu:"CPU Spark",spark_gpu:"GPU Spark"};
  const queryLabels = {q01:"Q1 · grouped scan",q03:"Q3 · three-table join",q06:"Q6 · filtered sum",q10:"Q10 · four-table join",q12:"Q12 · conditional counts",q14:"Q14 · part join",cx_q09:"Q9 · six-table join",cx_q13:"Q13 · outer join",cx_semi_anti:"Correlated semi/anti",cx_window:"LAG / ROW_NUMBER",cx_distinct:"Exact distinct",cx_self_join:"Fact self-join"};
  const fmt = number => Number(number).toLocaleString("en-US",{maximumSignificantDigits:3});
  const pvalue = number => number === 0 ? "0 (numeric precision)" : number < .0001 ? number.toExponential(2) : number.toFixed(4);
  const escape = value => String(value).replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
  const cell = (text, tag="td", className="") => { const element=document.createElement(tag);element.textContent=text;if(className)element.className=className;return element; };
  const positive = value => typeof value === "number" && Number.isFinite(value) && value > 0;
  const nonnegative = value => typeof value === "number" && Number.isFinite(value) && value >= 0;
  const contentionPairs = {A:["spark_gpu","polars_cpu_200"],B:["spark_cpu","polars_gpu"]};
  const contentionPlan = "670f37af6c1a6f2dbea5fc110d213b148a0acb83858786a21add8252acc89031";
  const contentionPrestart = "4088b5302a7d42c8aa87ebaed06e229bed3b0783bd15cf6556a6110981bf9f25";
  function table(element, headings, rows, caption) {
    element.replaceChildren();const cap=document.createElement("caption");cap.textContent=caption;element.append(cap);
    const head=document.createElement("thead"),headRow=document.createElement("tr");headings.forEach(text=>{const item=cell(text,"th");item.scope="col";headRow.append(item);});head.append(headRow);element.append(head);
    const body=document.createElement("tbody");rows.forEach(values=>{const row=document.createElement("tr");values.forEach(value=>row.append(value instanceof Node?value:cell(value)));body.append(row);});element.append(body);
  }
  function freezePreview() {
    $("preview-banner").hidden=false;$("pending-results").hidden=false;
    document.querySelectorAll('a[href^="downloads/"]').forEach(link=>{link.removeAttribute("href");link.classList.add("disabled-link");link.setAttribute("aria-disabled","true");});
  }
  if (window.H100_PREVIEW === true) { freezePreview();return; }
  function validate(data) {
    if(data.schema_version!=="h100-publication-data-v1"||data.status!=="PASS"||data.final!==true||data.synthetic!==false||data.campaign_id!=="h100-confirmatory-gclocker100-20261027"||data.author!=="Daniel Ejiofor"||data.counts?.sessions!==400||data.primary?.length!==26||data.block_ratios?.length!==260||data.scores?.length!==40)throw new Error("The final publication identity or completeness check failed.");
    if(!data.primary.every(row=>row.n_blocks===10&&positive(row.geometric_mean_speedup)&&row.ci95_pointwise?.every(positive)&&typeof row.p_holm==="number"&&row.p_holm>=0&&row.p_holm<=1))throw new Error("A primary estimate is incomplete.");
    const contention=data.contention,review=contention?.review;
    if(review?.status!=="COMPLETE"||review.experiment_id!=="h100-contention-20261028"||review.plan_sha256!==contentionPlan||review.prestart_sha256!==contentionPrestart||!/^([a-f0-9]{64})$/.test(review.replay_sha256)||!["COMPLETE","STOPPED_WITH_FAILURE"].includes(review.disposition)||!Array.isArray(contention.actions)||!Array.isArray(contention.overlaps)||contention.disposition?.length!==2)throw new Error("The separate contention review is incomplete.");
    if(!contention.actions.every(row=>contentionPairs[row.pair]?.includes(row.config_id)&&["pilot","main"].includes(row.study_phase)&&["warmup","measured"].includes(row.phase)&&[30,100].includes(row.scale)&&[1,2].includes(row.repetition)&&positive(row.seconds)&&positive(row.host_result_seconds)&&typeof row.paired_condition==="boolean"&&typeof row.oracle_passed==="boolean")||!contention.overlaps.every(row=>nonnegative(row.overlap_seconds)&&nonnegative(row.utc_overlap_seconds)&&nonnegative(row.actual_start_skew_seconds)&&typeof row.positive_actual_overlap==="boolean"))throw new Error("A contention observation is incomplete.");
    return data;
  }
  function drawPrimary(data) {
    const [numerator,denominator]=$("pair-select").value.split("|");
    const suite=$("suite-select").value;
    const entries=data.primary.filter(row=>row.numerator===numerator&&row.denominator===denominator&&row.suite===suite).sort((a,b)=>Number(a.scale)-Number(b.scale));
    $("comparison-title").textContent=`${labels[numerator]} / ${labels[denominator]}`;
    $("unavailable-suite").hidden=entries.length!==0;
    $("primary-chart").hidden=entries.length===0;
    $("primary-table").parentElement.hidden=entries.length===0;
    $("primary-chart").replaceChildren();
    if(!entries.length)return;
    const blockMap=new Map(entries.map(row=>[row.contrast_id,data.block_ratios.filter(block=>block.contrast_id===row.contrast_id)]));
    const values=[1,...entries.flatMap(row=>[...row.ci95_pointwise,...blockMap.get(row.contrast_id).map(block=>block.ratio)])];
    const lower=Math.min(...values),upper=Math.max(...values),span=Math.max(.8,Math.log2(upper)-Math.log2(lower));
    const lo=Math.min(-.2,Math.log2(lower)-span*.12),hi=Math.max(.2,Math.log2(upper)+span*.12);
    const width=1000,left=95,right=150,top=28,rowHeight=60,bottom=44,height=top+entries.length*rowHeight+bottom;
    const x=value=>left+(Math.log2(value)-lo)/(hi-lo)*(width-left-right);
    const svg=document.createElementNS("http://www.w3.org/2000/svg","svg");svg.setAttribute("viewBox",`0 0 ${width} ${height}`);svg.setAttribute("aria-hidden","true");
    const ticks=[];const step=hi-lo>12?2:1;
    for(let power=Math.ceil(lo/step)*step;power<=hi;power+=step)ticks.push(2**power);
    if(!ticks.some(value=>value===1))ticks.push(1);
    let markup=ticks.map(value=>`<line x1="${x(value)}" x2="${x(value)}" y1="${top-10}" y2="${height-bottom+2}" stroke="${value===1?"#849d9d":"#e0e4dc"}" stroke-dasharray="${value===1?"4 4":"0"}"/><text x="${x(value)}" y="${height-13}" text-anchor="middle">${escape(fmt(value))}×</text>`).join("");
    entries.forEach((entry,index)=>{
      const y=top+index*rowHeight+rowHeight/2,ratio=entry.geometric_mean_speedup,[low,high]=entry.ci95_pointwise;
      markup+=`<text class="scale-label" x="0" y="${y+4}">SF${escape(entry.scale)}</text><line x1="${left}" x2="${width-right}" y1="${y}" y2="${y}" stroke="#ecede7"/>`;
      if($("show-blocks").checked)blockMap.get(entry.contrast_id).forEach((block,number)=>{const jitter=((number%5)-2)*3+(number>=5?1.5:-1.5);markup+=`<circle cx="${x(block.ratio)}" cy="${y+jitter}" r="3" fill="#8daaa6" opacity=".7"/>`;});
      markup+=`<line x1="${x(low)}" x2="${x(high)}" y1="${y}" y2="${y}" stroke="#116d70" stroke-width="2"/><line x1="${x(low)}" x2="${x(low)}" y1="${y-5}" y2="${y+5}" stroke="#116d70"/><line x1="${x(high)}" x2="${x(high)}" y1="${y-5}" y2="${y+5}" stroke="#116d70"/><path d="M ${x(ratio)} ${y-6} L ${x(ratio)+6} ${y} L ${x(ratio)} ${y+6} L ${x(ratio)-6} ${y} Z" fill="#116d70" stroke="#fcfaf5" stroke-width="1"/><text class="estimate-label" x="${width-120}" y="${y+4}">${escape(fmt(ratio))}×</text>`;
    });
    svg.innerHTML=markup;$("primary-chart").append(svg);
    $("primary-chart").setAttribute("aria-label",`${labels[numerator]} divided by ${labels[denominator]}, ${suite} suite. Geometric means and pointwise 95% intervals for ${entries.length} scale factors. The full numerical table follows.`);
    const rows=entries.map(entry=>{
      const decision=document.createElement("td"),badge=document.createElement("span");badge.className="decision";
      if(entry.p_holm<.05){badge.textContent=entry.geometric_mean_speedup>1?"Favors GPU":"Favors CPU";if(entry.geometric_mean_speedup<1)badge.classList.add("cpu");}
      else{badge.textContent="No rejection";badge.classList.add("neutral");}decision.append(badge);
      return [`SF${entry.scale}`,cell(`${fmt(entry.geometric_mean_speedup)}×`,"td","numeric"),cell(`[${fmt(entry.ci95_pointwise[0])}, ${fmt(entry.ci95_pointwise[1])}]`,"td","numeric"),pvalue(entry.p_holm),decision];
    });
    table($("primary-table"),["Scale","Geometric mean ratio","Pointwise 95% CI","Holm p · family 26","Two-sided α = .05"],rows,"Ten paired blocks per estimate. No rejection does not establish equivalence.");
  }
  function updateScales() {
    const values=$("query-suite").value==="baseline"?["1","10","30","100","300"]:["30","100","300"];
    const previous=$("query-scale").value;$("query-scale").replaceChildren(...values.map(value=>{const option=document.createElement("option");option.value=value;option.textContent=`SF${value}`;return option;}));
    if(values.includes(previous))$("query-scale").value=previous;
  }
  function drawQueries(data) {
    const suite=$("query-suite").value,scale=$("query-scale").value,denominator=$("query-gpu").value;
    const entries=data.queries.filter(row=>row.suite===suite&&String(row.scale)===scale&&row.denominator===denominator);
    $("query-chart").replaceChildren(...entries.map(entry=>{
      const article=document.createElement("article");article.className="query-cell";
      const name=document.createElement("h3");name.className="query-name";name.textContent=queryLabels[entry.query]||entry.query;article.append(name);
      const value=document.createElement("p");value.className="query-value";
      const detail=document.createElement("p");detail.className="query-ci";
      if(entry.status!=="complete"){article.classList.add("unsupported");value.textContent="N/A";detail.textContent="Original expression unsupported · no imputed time";}
      else{value.textContent=`${fmt(entry.geometric_mean_speedup)}×`;detail.textContent=`Pointwise 95% CI [${fmt(entry.ci95_pointwise[0])}, ${fmt(entry.ci95_pointwise[1])}] · 10 blocks`;}
      article.append(value,detail);return article;
    }));
    const scores=data.scores.filter(row=>row.suite===suite&&String(row.scale)===scale);
    table($("scores-table"),["Configuration","Suite score (s)","Complete blocks","Query coverage"],scores.map(row=>[row.configuration,row.geometric_mean_score_seconds===null?"N/A":fmt(row.geometric_mean_score_seconds),`${row.complete_blocks} / ${row.planned_blocks}`,row.query_coverage]),`${suite==="baseline"?"Original":"Complex"} suite · SF${scale}. Sum of six query medians per session.`);
  }
  function updateContentionScales() {
    const values=$("contention-phase").value==="pilot"?["30"]:["30","100"],previous=$("contention-scale").value;
    $("contention-scale").replaceChildren(...values.map(value=>{const option=document.createElement("option");option.value=value;option.textContent=`SF${value}`;return option;}));
    $("contention-scale").value=values.includes(previous)?previous:values[0];
  }
  function drawContention(data) {
    const study=data.contention,phase=$("contention-phase").value,pair=$("contention-pair").value,suite=$("contention-suite").value,scale=Number($("contention-scale").value);
    const selected=row=>row.study_phase===phase&&row.pair===pair&&row.suite===suite&&row.scale===scale;
    const phaseLabel=phase==="pilot"?"Pilot":"Expansion",pairLabel=contentionPairs[pair].map(config=>labels[config]).join(" + ");
    $("contention-selection").textContent=`${phaseLabel} · pair ${pair}: ${pairLabel} · ${suite} · SF${scale}`;
    const rows=study.actions.filter(row=>selected(row)&&row.phase==="measured").sort((a,b)=>a.repetition-b.repetition||a.query.localeCompare(b.query)||a.config_id.localeCompare(b.config_id));
    table($("contention-actions"),["Repetition","Query","Participant","Action (s)","Host result (s)","Condition","Oracle"],rows.map(row=>[
      `Rep ${row.repetition}`,queryLabels[row.query]||row.query,labels[row.config_id],fmt(row.seconds),fmt(row.host_result_seconds),row.paired_condition?"Common query":"CPU-only · unpaired",row.oracle_passed?"Pass":"Failed"]),
      `${phaseLabel} only · every observed measured action for this cell. ${rows.length?"Repetitions remain separate.":"No measured actions were observed; no times are imputed."}`);
    const overlap=study.overlaps.filter(selected).sort((a,b)=>a.repetition-b.repetition||a.query.localeCompare(b.query));
    table($("contention-overlap"),["Repetition","Common query","Monotonic intersection (s)","UTC intersection (s)","Start skew (s)","Positive on both clocks"],overlap.map(row=>[
      `Rep ${row.repetition}`,queryLabels[row.query]||row.query,fmt(row.overlap_seconds),fmt(row.utc_overlap_seconds),fmt(row.actual_start_skew_seconds),row.positive_actual_overlap?"Yes":"No"]),
      `${phaseLabel} only · ${overlap.length} observed common measured query pairs. Pair B complex has four common queries per complete repetition.`);
  }
  function contentionEvidence(data) {
    const study=data.contention,counts=study.counts;
    $("contention-status").textContent=`Terminal contention disposition: ${study.review.disposition==="COMPLETE"?"complete":"stopped with failure"}. ${counts.attempted_waves} attempted waves; ${counts.successful_outputs} oracle-valid outputs; ${counts.expected_strict_rejections_observed} strict translation rejections; ${counts.unexpected_query_errors_observed} unexpected query errors; ${counts.oracle_failed_outputs} output mismatches. Original action separation and same-scheduler resume verified. Unattempted or failed actions receive no imputed timing.`;
    table($("contention-disposition"),["Phase","Disposition","Planned waves","Attempted","Passed","Failed","Unattempted"],study.disposition.map(row=>[
      row.study_phase==="pilot"?"Pilot · one repetition":"Expansion · two repetitions",row.status,row.planned_waves,row.attempted_waves,row.passed_waves,row.failed_waves,row.unattempted_waves.length]),
      "Pilot and expansion are separate descriptive samples. A terminal stop retains every attempted observation and identifies unattempted waves.");
    const figures=study.figure_stems||[];
    $("contention-figures").hidden=figures.length===0;
    $("contention-figure-links").replaceChildren(...figures.map(stem=>{
      const row=document.createElement("div");row.className="figure-row";const title=`${stem.startsWith("pilot_")?"Pilot":"Expansion"} · ${stem.includes("pair_A")?"pair A common actions":stem.includes("pair_B")?"pair B common actions":"actual measured action timeline"}`;
      const label=document.createElement("span");label.textContent=title;const links=document.createElement("div");
      ["svg","png","pdf"].forEach(extension=>{const link=document.createElement("a");link.href=`assets/${stem}.${extension}`;link.download="";link.textContent=extension.toUpperCase();link.setAttribute("aria-label",`${title}, ${extension.toUpperCase()}`);links.append(link);});row.append(label,links);return row;
    }));
  }
  function evidence(data) {
    const figures={suite_ratios_baseline:"Original-suite primary ratios",suite_ratios_complex:"Complex-suite primary ratios",query_ratios_action:"Descriptive query ratios",complete_case_scores:"Complete-case scores & coverage"};
    $("figure-links").replaceChildren(...Object.entries(figures).map(([stem,title])=>{const row=document.createElement("div");row.className="figure-row";const label=document.createElement("span");label.textContent=title;const links=document.createElement("div");["svg","png","pdf"].forEach(extension=>{const link=document.createElement("a");link.href=`assets/${stem}.${extension}`;link.download="";link.textContent=extension.toUpperCase();link.setAttribute("aria-label",`${title}, ${extension.toUpperCase()}`);links.append(link);});row.append(label,links);return row;}));
    const fields={campaign_id:"Campaign",campaign_sha256:"Campaign SHA256",prestart_sha256:"Prestart SHA256",execution_profile_sha256:"Recovery profile SHA256",source_commit:"Frozen source commit",source_tree_sha256:"52-file source tree SHA256",analysis_sha256:"Analysis SHA256"};
    $("provenance-list").replaceChildren(...Object.entries(fields).map(([key,label])=>{const row=document.createElement("div"),dt=document.createElement("dt"),dd=document.createElement("dd");dt.textContent=label;dd.textContent=data[key];row.append(dt,dd);return row;}));
    Object.entries(data.contention.review).forEach(([key,value])=>{const row=document.createElement("div");row.append(cell(`Contention ${key}`,"dt"),cell(value,"dd"));$("provenance-list").append(row);});
  }
  fetch("data.json",{cache:"no-store"}).then(response=>{if(!response.ok)throw new Error("The checked result snapshot is unavailable.");return response.json();}).then(validate).then(data=>{
    $("verified-results").hidden=false;$("query-results").hidden=false;$("query-pending").hidden=true;$("evidence-links").hidden=false;$("evidence-pending").hidden=true;
    $("contention-results").hidden=false;$("contention-pending").hidden=true;
    const completed=new Date(data.completed_at_utc);if(!Number.isNaN(completed.getTime()))$("completion-date").textContent=`Replay completed ${completed.toLocaleDateString("en-US",{year:"numeric",month:"short",day:"numeric",timeZone:"UTC"})} UTC`;
    ["pair-select","suite-select","show-blocks"].forEach(id=>$(id).addEventListener("change",()=>drawPrimary(data)));
    $("query-suite").addEventListener("change",()=>{updateScales();drawQueries(data);});
    ["query-scale","query-gpu"].forEach(id=>$(id).addEventListener("change",()=>drawQueries(data)));
    $("contention-phase").addEventListener("change",()=>{updateContentionScales();drawContention(data);});
    ["contention-pair","contention-suite","contention-scale"].forEach(id=>$(id).addEventListener("change",()=>drawContention(data)));
    updateScales();drawPrimary(data);drawQueries(data);evidence(data);updateContentionScales();drawContention(data);contentionEvidence(data);
  }).catch(error=>{
    $("result-error").hidden=false;$("result-error").textContent=`Results could not be displayed: ${error.message}`;
    $("verified-results").hidden=true;$("query-results").hidden=true;$("evidence-links").hidden=true;
    $("contention-results").hidden=true;$("contention-pending").hidden=false;
  });
})();
