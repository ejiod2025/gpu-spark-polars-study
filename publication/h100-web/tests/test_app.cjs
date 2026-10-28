/* Fabricated UI observations exercise display only; no release files are built. */
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

class Element {
  constructor() { this.children=[]; this.value=""; this.hidden=false; this.textContent=""; this.attributes={}; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children=[...nodes]; }
  setAttribute(key,value) { this.attributes[key]=value; }
}
function setup(preview=false) {
  const elements=new Map();
  const document={getElementById(id){if(!elements.has(id))elements.set(id,new Element());return elements.get(id);},
    createElement(){return new Element();},querySelectorAll(){return [];}};
  const window={H100_PREVIEW:preview};
  let code=fs.readFileSync(path.join(__dirname,"../templates/app.js"),"utf8");
  // Exercise the actual UI functions with a tiny DOM; bypass network/bootstrap.
  code=code.replace('  fetch("data.json",', '  window.testUI={validate,drawContention,updateContentionScales,contentionEvidence};return;\n  fetch("data.json",');
  vm.runInNewContext(code,{document,window,Node:Element});
  return {elements,document,window,ui:window.testUI};
}
function fixture(disposition="COMPLETE") {
  return {schema_version:"h100-publication-data-v1",status:"PASS",final:true,synthetic:false,
    campaign_id:"h100-confirmatory-gclocker100-20261027",author:"Daniel Ejiofor",counts:{sessions:400},
    primary:Array.from({length:26},()=>({n_blocks:10,geometric_mean_speedup:1,ci95_pointwise:[.8,1.2],p_holm:.5})),
    block_ratios:Array(260),scores:Array(40),contention:{review:{status:"COMPLETE",experiment_id:"h100-contention-20261028",
      plan_sha256:"670f37af6c1a6f2dbea5fc110d213b148a0acb83858786a21add8252acc89031",
      prestart_sha256:"4088b5302a7d42c8aa87ebaed06e229bed3b0783bd15cf6556a6110981bf9f25",replay_sha256:"a".repeat(64),disposition},
      counts:{attempted_waves:2,successful_outputs:4,expected_strict_rejections_observed:1,unexpected_query_errors_observed:0,oracle_failed_outputs:0},
      actions:[{study_phase:"main",pair:"B",suite:"complex",scale:30,repetition:1,query:"cx_q13",config_id:"spark_cpu",phase:"measured",seconds:2.5,host_result_seconds:2.6,paired_condition:false,oracle_passed:true},
        {study_phase:"main",pair:"B",suite:"complex",scale:30,repetition:2,query:"cx_q13",config_id:"spark_cpu",phase:"measured",seconds:3.5,host_result_seconds:3.6,paired_condition:false,oracle_passed:true},
        {study_phase:"pilot",pair:"B",suite:"complex",scale:30,repetition:1,query:"cx_q13",config_id:"spark_cpu",phase:"measured",seconds:99,host_result_seconds:100,paired_condition:false,oracle_passed:true}],
      overlaps:[],figure_stems:[],disposition:[{study_phase:"pilot",status:"PASS",planned_waves:4,attempted_waves:4,passed_waves:4,failed_waves:0,unattempted_waves:[]},
        {study_phase:"main",status:"FAIL",planned_waves:16,attempted_waves:1,passed_waves:0,failed_waves:1,unattempted_waves:["fixture-unattempted"]}]}};
}
const text = node => [node.textContent,...node.children.map(text)].join(" ");

test("recovery identity loads; aborted or partial roles fail closed",()=>{
  const {ui}=setup(),data=fixture();
  assert.equal(ui.validate(data),data);
  assert.throws(()=>ui.validate({...data,campaign_id:"h100-confirmatory-replacement-20261027"}),/identity/);
  assert.throws(()=>ui.validate({...data,contention:{...data.contention,review:{...data.contention.review,disposition:"PARTIAL"}}}),/contention review/);
  assert.throws(()=>ui.validate({...data,contention:{...data.contention,actions:[{...data.contention.actions[0],seconds:null}]}}),/observation/);
});
test("expansion shows individual repetitions and CPU-only diagnostics without pooling pilot",()=>{
  const {document,ui}=setup();
  for(const [id,value] of Object.entries({"contention-phase":"main","contention-pair":"B","contention-suite":"complex","contention-scale":"30"}))document.getElementById(id).value=value;
  ui.drawContention(fixture());
  const actions=text(document.getElementById("contention-actions"));
  assert.match(actions,/Rep 1/); assert.match(actions,/Rep 2/);
  assert.match(actions,/2\.5/); assert.match(actions,/3\.5/);
  assert.match(actions,/CPU-only · unpaired/); assert.doesNotMatch(actions,/99/);
  assert.match(text(document.getElementById("contention-overlap")),/four common queries/);
  document.getElementById("contention-phase").value="pilot";
  document.getElementById("contention-scale").value="100";
  ui.updateContentionScales();
  assert.equal(document.getElementById("contention-scale").value,"30");
  ui.drawContention(fixture());
  assert.match(text(document.getElementById("contention-actions")),/99/);
  assert.doesNotMatch(text(document.getElementById("contention-actions")),/Rep 2/);
});
test("honest terminal failure remains visible and unavailable times stay empty",()=>{
  const {document,ui}=setup();
  ui.contentionEvidence(fixture("STOPPED_WITH_FAILURE"));
  assert.match(document.getElementById("contention-status").textContent,/stopped with failure/);
  assert.match(text(document.getElementById("contention-disposition")),/Unattempted/);
  for(const [id,value] of Object.entries({"contention-phase":"main","contention-pair":"A","contention-suite":"baseline","contention-scale":"100"}))document.getElementById(id).value=value;
  ui.drawContention(fixture("STOPPED_WITH_FAILURE"));
  assert.match(text(document.getElementById("contention-actions")),/No measured actions were observed; no times are imputed/);
});
test("methods preview stops before numerical UI initialization",()=>{
  const {document,ui}=setup(true);
  assert.equal(ui,undefined);
  assert.equal(document.getElementById("preview-banner").hidden,false);
  assert.equal(document.getElementById("pending-results").hidden,false);
});
