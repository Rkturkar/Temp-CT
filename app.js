const API = "/api";
const state = { page: "overview", shipments: [], pageNo: 1, pageSize: 10, totalPages: 1, query: "", status: "", detailCode: null, evaluation: null, summary: null, generation: null };
const $ = (s, root=document) => root.querySelector(s);
const $$ = (s, root=document) => [...root.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmtDate = v => v ? new Date(v).toLocaleString([], {dateStyle:"medium", timeStyle:"short"}) : "—";
const fmtNum = (v, digits=0) => v == null || !Number.isFinite(Number(v)) ? "—" : Number(v).toLocaleString(undefined,{maximumFractionDigits:digits,minimumFractionDigits:digits});
const statusLabel = s => ({COMPLIANT:"Compliant", NON_COMPLIANT:"Non-compliant", REVIEW_REQUIRED:"Review required"}[s] || s || "Unknown");
const statusClass = s => ({COMPLIANT:"compliant", NON_COMPLIANT:"non-compliant", REVIEW_REQUIRED:"review-required"}[s] || "neutral");
const badge = s => `<span class="status ${statusClass(s)}">${esc(statusLabel(s))}</span>`;
async function api(path, options={}) {
  const res = await fetch(API + path, {headers:{"Content-Type":"application/json", ...(options.headers||{})}, ...options});
  if (!res.ok) {
    let message = `Request failed (${res.status})`;
    try { const body = await res.json(); message = body.detail || message; } catch {}
    throw new Error(message);
  }
  const type = res.headers.get("content-type") || "";
  return type.includes("application/json") ? res.json() : res.text();
}
function toast(message) { const el=$("#toast"); el.textContent=message; el.classList.add("show"); clearTimeout(toast.timer); toast.timer=setTimeout(()=>el.classList.remove("show"),3300); }
function setApiStatus(ok) { const el=$(".api-status"); el.classList.toggle("online",ok); el.classList.toggle("offline",!ok); $("#api-status-text").textContent=ok?"API connected":"API unavailable"; $("#api-dot").style.background=ok?"#35b982":"#d84b55"; }
function nav(page) {
  state.page=page; state.detailCode=null;
  $$(".nav-item").forEach(b=>b.classList.toggle("active",b.dataset.page===page));
  const names={overview:"Overview",shipments:"Shipments",evaluation:"Evaluation",data:"Data & Export",detail:"Shipment detail"};
  $("#breadcrumb-current").textContent=names[page]||"Overview";
  $("#sidebar").classList.remove("open");
  renderPage().catch(showPageError);
}
function heading(title, subtitle, actions="") {
 return `<div class="page-heading"><div><div class="eyebrow">COLD CHAIN OPERATIONS</div><h1>${title}</h1><p>${subtitle}</p></div><div class="heading-actions">${actions}</div></div>`;
}
function card(title, subtitle, body, extra="") {
 return `<section class="card ${extra}"><div class="card-header"><div><div class="card-title">${title}</div>${subtitle?`<div class="card-subtitle">${subtitle}</div>`:""}</div></div><div class="card-body">${body}</div></section>`;
}
function kpi(label,value,foot,icon,footClass="") {
 return `<section class="card kpi"><div class="kpi-top"><span class="kpi-label">${label}</span><span class="kpi-icon">${icon}</span></div><div class="kpi-value">${value}</div><div class="kpi-foot ${footClass}">${foot}</div></section>`;
}
function tempChart(readings, policy) {
 const pts=(readings||[]).filter(r=>Number.isFinite(Number(r.temperature_c)));
 if(!pts.length) return `<div class="empty">No temperature readings available.</div>`;
 const width=640,height=230,left=42,right=12,top=15,bottom=28;
 const vals=pts.map(p=>Number(p.temperature_c));
 const min=Math.min(...vals, Number(policy.min_temp_c))-1, max=Math.max(...vals, Number(policy.max_temp_c))+1;
 const x=i=>left+(width-left-right)*(pts.length===1?0:i/(pts.length-1));
 const y=v=>top+(height-top-bottom)*(1-(v-min)/(max-min||1));
 const line=pts.map((p,i)=>`${i?"L":"M"} ${x(i).toFixed(1)} ${y(Number(p.temperature_c)).toFixed(1)}`).join(" ");
 const yLines=[min,(min+max)/2,max];
 const grid=yLines.map(v=>`<line x1="${left}" y1="${y(v)}" x2="${width-right}" y2="${y(v)}" stroke="#e9edf3" stroke-dasharray="3 4"/><text x="${left-8}" y="${y(v)+3}" font-size="10" fill="#8a94a4" text-anchor="end">${v.toFixed(1)}°</text>`).join("");
 const lo=Number(policy.min_temp_c),hi=Number(policy.max_temp_c);
 const bandTop=y(hi),bandBottom=y(lo);
 const bandY=Math.min(bandTop,bandBottom), bandH=Math.abs(bandBottom-bandTop);
 const points=pts.filter((p,i)=>i===0||i===pts.length-1||Number(p.temperature_c)<lo||Number(p.temperature_c)>hi).map(p=>`<circle cx="${x(pts.indexOf(p))}" cy="${y(Number(p.temperature_c))}" r="3" fill="${Number(p.temperature_c)<lo||Number(p.temperature_c)>hi?"#c64c52":"#3569d4"}"/>`).join("");
 return `<div class="chart-wrap"><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Temperature readings chart">
 <rect x="${left}" y="${bandY}" width="${width-left-right}" height="${bandH}" fill="#eaf7f0" opacity=".8"/>
 ${grid}<line x1="${left}" y1="${y(lo)}" x2="${width-right}" y2="${y(lo)}" stroke="#18845b" stroke-dasharray="5 4"/><line x1="${left}" y1="${y(hi)}" x2="${width-right}" y2="${y(hi)}" stroke="#18845b" stroke-dasharray="5 4"/>
 <path d="${line}" fill="none" stroke="#3569d4" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>${points}
 <text x="${left}" y="${height-5}" font-size="10" fill="#8a94a4">${esc(fmtDate(pts[0].recorded_at))}</text>
 <text x="${width-right}" y="${height-5}" font-size="10" fill="#8a94a4" text-anchor="end">${esc(fmtDate(pts[pts.length-1].recorded_at))}</text>
 </svg></div><div class="chart-legend"><span><i class="legend-dot" style="background:#3569d4"></i>Recorded temperature</span><span><i class="legend-dot" style="background:#18845b"></i>Permitted range (${lo}°C to ${hi}°C)</span></div>`;
}
async function renderPage() {
 const root=$("#content"); root.innerHTML='<div class="loading-state">Loading…</div>';
 if(state.page==="overview") return renderOverview(root);
 if(state.page==="shipments") return renderShipments(root);
 if(state.page==="evaluation") return renderEvaluation(root);
 if(state.page==="data") return renderData(root);
 if(state.page==="detail") return renderDetail(root,state.detailCode);
}
async function renderOverview(root) {
 const s=await api("/summary"); state.summary=s; setApiStatus(true);
 const action=`<button class="btn" data-action="refresh">↻ Refresh</button><button class="btn btn-primary" data-page="shipments">View shipments →</button>`;
 root.innerHTML=heading("Operations overview","Monitor shipment compliance, temperature excursions, and evidence quality.",action)+`
 <div class="grid kpi-grid">
 ${kpi("Total shipments",fmtNum(s.total),"In the current dataset","▤")}
 ${kpi("Compliant",fmtNum(s.compliant),`${s.total?Math.round(s.compliant/s.total*100):0}% of shipments`,"✓","good")}
 ${kpi("Non-compliant",fmtNum(s.non_compliant),"Confirmed policy violations","!")}
 ${kpi("Review required",fmtNum(s.review_required),"Evidence needs attention","◷")}
 </div>
 <div class="grid two-col">
 ${card("Shipment outcomes","Current dataset status distribution",`<div class="status-list">
 ${statusBar("Compliant",s.compliant,s.total,"#21845d")}
 ${statusBar("Non-compliant",s.non_compliant,s.total,"#c64c52")}
 ${statusBar("Review required",s.review_required,s.total,"#d29a2e")}
 </div><div class="notice" style="margin-top:18px;margin-bottom:0">A shipment is only compliant when sufficient evidence supports the decision.</div>`)}
 ${card("Data quality snapshot","Signals that may need investigation",`<div class="status-list">
 ${miniMetric("Open exceptions",s.open_exceptions,"Non-compliant + review required")}
 ${miniMetric("Shipments with missing data",s.missing_data_cases,"One or more logger gaps")}
 ${miniMetric("Confirmed excursions",s.excursions,"Total excursion duration: "+fmtNum(s.excursion_minutes)+" min")}
 </div>`)}
 </div>
 <section class="card table-card">
 <div class="card-header"><div><div class="card-title">Shipments needing attention</div><div class="card-subtitle">Prioritized from the latest compliance evaluation</div></div><button class="btn" data-page="shipments">All shipments →</button></div>
 <div class="table-wrap">${attentionTable(s.attention||[])}</div>
 </section>`;
}
function statusBar(label,count,total,color){const pct=total?count/total*100:0;return `<div class="status-row"><span class="status-name">${label}</span><span class="status-count">${fmtNum(count)} <span class="muted small">${Math.round(pct)}%</span></span><div class="bar-track"><div class="bar-fill" style="width:${pct}%;background:${color}"></div></div></div>`}
function miniMetric(label,value,sub){return `<div class="status-row"><span class="status-name">${label}<span class="card-subtitle" style="display:block">${sub}</span></span><span class="status-count">${fmtNum(value)}</span></div>`}
function attentionTable(items) {
 if(!items.length) return `<div class="empty">No shipments currently need attention.</div>`;
 return `<table><thead><tr><th>Shipment</th><th>Status</th><th>Reason</th><th></th></tr></thead><tbody>${items.map(x=>`<tr><td class="code">${esc(x.code)}</td><td>${badge(x.status)}</td><td style="white-space:normal;min-width:220px;max-width:520px">${esc(x.reason||"Requires review")}</td><td><button class="link-button" data-detail="${esc(x.code)}">View details →</button></td></tr>`).join("")}</tbody></table>`;
}
async function renderShipments(root) {
 const params=new URLSearchParams({q:state.query,page:String(state.pageNo),size:String(state.pageSize)});
 if(state.status) params.set("status",state.status);
 const result=await api("/shipments?"+params.toString()); state.shipments=result.items||[];state.totalPages=result.pages||1;setApiStatus(true);
 root.innerHTML=heading("Shipments","Search and review shipment compliance decisions.",`<button class="btn" data-action="export-csv">↓ Export CSV</button><button class="btn btn-primary" data-action="refresh">↻ Refresh</button>`)+`
 <section class="card table-card"><div class="card-header"><div><div class="card-title">All shipments</div><div class="card-subtitle">${fmtNum(result.total)} shipments match your filters</div></div></div>
 <div class="table-toolbar"><input id="shipment-search" class="field search" type="search" placeholder="Search shipment ID…" value="${esc(state.query)}" aria-label="Search shipment ID"/>
 <select id="status-filter" class="field" aria-label="Filter status"><option value="">All statuses</option><option value="COMPLIANT" ${state.status==="COMPLIANT"?"selected":""}>Compliant</option><option value="NON_COMPLIANT" ${state.status==="NON_COMPLIANT"?"selected":""}>Non-compliant</option><option value="REVIEW_REQUIRED" ${state.status==="REVIEW_REQUIRED"?"selected":""}>Review required</option></select></div>
 <div class="table-wrap">${shipmentTable(result.items||[])}</div>
 <div class="pagination"><span>Page ${result.page} of ${result.pages} · ${fmtNum(result.total)} results</span><div class="pagination-actions"><button class="btn" data-action="prev-page" ${result.page<=1?"disabled":""}>← Previous</button><button class="btn" data-action="next-page" ${result.page>=result.pages?"disabled":""}>Next →</button></div></div></section>`;
 const search=$("#shipment-search");
 let timer; search.addEventListener("input",()=>{clearTimeout(timer);timer=setTimeout(()=>{state.query=search.value.trim();state.pageNo=1;renderShipments($("#content")).catch(showPageError)},250)});
 $("#status-filter").addEventListener("change",e=>{state.status=e.target.value;state.pageNo=1;renderShipments($("#content")).catch(showPageError)});
}
function shipmentTable(items) {
 if(!items.length)return `<div class="empty">No shipments found. Try changing your search or filters.</div>`;
 return `<table><thead><tr><th>Shipment ID</th><th>Product</th><th>Route</th><th>Departure</th><th>Excursions</th><th>Status</th><th></th></tr></thead><tbody>${items.map(s=>`<tr><td class="code">${esc(s.code)}</td><td>${esc(s.product_name)}<div class="card-subtitle">${esc(s.product_code)}</div></td><td>${esc(s.origin)} <span class="muted">→</span> ${esc(s.destination)}</td><td>${esc(fmtDate(s.departed_at))}</td><td>${fmtNum(s.excursion_count)}</td><td>${badge(s.status)}</td><td><button class="link-button" data-detail="${esc(s.code)}">Details →</button></td></tr>`).join("")}</tbody></table>`;
}
async function renderDetail(root,code) {
 if(!code) return nav("shipments");
 const data=await api("/shipments/"+encodeURIComponent(code));setApiStatus(true);
 const s=data.shipment,d=data.decision,p=data.product;
 root.innerHTML=heading(s.code,`${s.origin} → ${s.destination}`,`<button class="btn" data-page="shipments">← Back to shipments</button><button class="btn" data-action="export-json">Export JSON</button>`)+`
 <div class="detail-summary">
 <div class="detail-stat"><span>Compliance status</span><strong>${badge(d.status)}</strong></div>
 <div class="detail-stat"><span>Temperature readings</span><strong>${fmtNum(d.stats?.count)}</strong></div>
 <div class="detail-stat"><span>Confirmed excursions</span><strong>${fmtNum((d.excursions||[]).filter(e=>e.confirmed).length)}</strong></div>
 </div>
 <div class="grid detail-grid">
 <div>
 <section class="card detail-block"><div class="card-header"><div><div class="card-title">Temperature history</div><div class="card-subtitle">Policy range ${p.min_temp_c}°C to ${p.max_temp_c}°C · timestamps displayed in local time</div></div></div><div class="card-body">${tempChart(data.readings,p)}</div></section>
 <section class="card detail-block"><div class="card-header"><div><div class="card-title">Compliance findings</div><div class="card-subtitle">Reasons generated by the deterministic rules engine</div></div></div><div class="card-body">${(d.reasons||[]).length?`<ul class="reason-list">${d.reasons.map(r=>`<li>${esc(r)}</li>`).join("")}</ul>`:`<div class="empty">No findings returned.</div>`}</div></section>
 <section class="card detail-block"><div class="card-header"><div><div class="card-title">Temperature readings</div><div class="card-subtitle">Source records used for this shipment</div></div></div><div class="table-wrap">${readingTable(data.readings||[])}</div></section>
 </div>
 <div>
 ${card("Shipment details","Route and policy",`<div class="status-list">
 ${miniMetric("Product",p.name,"Code: "+p.code)}
 ${miniMetric("Origin",s.origin,"Departure: "+fmtDate(s.departed_at))}
 ${miniMetric("Destination",s.destination,"Arrival: "+fmtDate(s.arrived_at))}
 ${miniMetric("Allowed range",`${p.min_temp_c}°C to ${p.max_temp_c}°C`,"Maximum excursion: "+p.max_excursion_min+" min")}
 ${miniMetric("Maximum data gap",p.max_gap_min+" min","Logger accuracy: ±"+p.accuracy_c+"°C")}
 </div>`)}
 <section class="card detail-block" style="margin-top:16px"><div class="card-header"><div><div class="card-title">Custody timeline</div><div class="card-subtitle">Recorded shipment handoffs</div></div></div><div class="card-body">${timeline(data.custody||[])}</div></section>
 <section class="card detail-block"><div class="card-header"><div><div class="card-title">Excursion intervals</div></div></div><div class="card-body">${excursionList(d.excursions||[])}</div></section>
 <section class="card detail-block"><div class="card-header"><div><div class="card-title">Missing-data intervals</div></div></div><div class="card-body">${gapList(d.gaps||[])}</div></section>
 </div></div>`;
}
function readingTable(readings) {
 if(!readings.length)return `<div class="empty">No readings available.</div>`;
 const shown=readings.length>100?readings.slice(0,100):readings;
 return `<table class="reading-table"><thead><tr><th>Recorded at</th><th>Temperature</th><th>Range check</th></tr></thead><tbody>${shown.map(r=>`<tr><td>${esc(fmtDate(r.recorded_at))}</td><td>${fmtNum(r.temperature_c,1)}°C</td><td>${r.in_range?'<span class="status compliant">In range</span>':'<span class="status non-compliant">Out of range</span>'}</td></tr>`).join("")}</tbody></table>${readings.length>100?`<div class="pagination">Showing first 100 of ${readings.length} readings.</div>`:""}`;
}
function timeline(events) {
 if(!events.length)return `<div class="empty">No custody events.</div>`;
 return `<div class="timeline">${events.map(e=>`<div class="timeline-item"><span class="timeline-dot"></span><div><strong>${esc(e.event_type)} · ${esc(e.party)}</strong><small>${esc(e.location)} · ${esc(fmtDate(e.occurred_at))}</small></div></div>`).join("")}</div>`;
}
function excursionList(items) {
 if(!items.length)return `<div class="empty">No excursion intervals detected.</div>`;
 return `<div class="status-list">${items.map(e=>`<div class="status-row"><span class="status-name"><strong>${esc(fmtDate(e.start))}</strong><span class="card-subtitle" style="display:block">${esc(fmtDate(e.end))}</span></span><span>${e.confirmed?'<span class="status non-compliant">Confirmed</span>':'<span class="status review-required">Uncertain</span>'}<div class="card-subtitle">${fmtNum(e.duration_min)} min</div></span></div>`).join("")}</div>`;
}
function gapList(items) {
 if(!items.length)return `<div class="empty">No significant logger gaps detected.</div>`;
 return `<div class="status-list">${items.map(g=>`<div class="status-row"><span class="status-name"><strong>${esc(fmtDate(g.start))}</strong><span class="card-subtitle" style="display:block">to ${esc(fmtDate(g.end))}</span></span><span class="status review-required">${fmtNum(g.minutes)} min</span></div>`).join("")}</div>`;
}
async function renderEvaluation(root) {
 const e=await api("/evaluation");state.evaluation=e;setApiStatus(true);
 if(!e.available){root.innerHTML=heading("Evaluation","Compare rule-engine decisions with the separately stored synthetic ground truth.")+`<div class="notice">Ground truth is not available yet. Generate a dataset from the Data &amp; Export page first.</div>`;return;}
 const pct=v=>`${(Number(v||0)*100).toFixed(1)}%`;
 root.innerHTML=heading("Evaluation","Compare rule-engine decisions with the separately stored synthetic ground truth.",`<button class="btn" data-action="refresh">↻ Refresh</button>`)+`
 <div class="notice">These metrics measure agreement with the generated scenarios. They do not establish real-world performance or validate the business rules against live shipments.</div>
 <div class="grid metric-grid">
 ${metricCard("Precision",pct(e.precision),"Of predicted problem cases, the share that are labelled problems.")}
 ${metricCard("Recall",pct(e.recall),"Share of ground-truth problem cases detected.")}
 ${metricCard("F1 score",pct(e.f1),"Harmonic mean of precision and recall.")}
 ${metricCard("Exact status match",pct(e.exact_match),"Predicted status matches expected status.")}
 ${metricCard("False-compliant rate",pct(e.false_compliant_rate),"Problem cases incorrectly marked compliant.")}
 ${metricCard("Total evaluated",fmtNum(e.total),"Ground-truth shipment records.")}
 </div>
 <section class="card table-card"><div class="card-header"><div><div class="card-title">Scenario coverage</div><div class="card-subtitle">Expected and predicted status agreement grouped by scenario</div></div></div>
 <div class="table-wrap"><table><thead><tr><th>Scenario</th><th>Cases</th><th>Exact matches</th><th>Match rate</th></tr></thead><tbody>${Object.entries(e.by_scenario||{}).map(([name,v])=>`<tr><td class="code">${esc(name)}</td><td>${fmtNum(v.total)}</td><td>${fmtNum(v.matched)}</td><td>${pct(v.total?v.matched/v.total:0)}</td></tr>`).join("")}</tbody></table></div></section>
 <section class="card" style="margin-top:16px"><div class="card-header"><div><div class="card-title">Confusion counts</div><div class="card-subtitle">Positive = any expected status other than COMPLIANT</div></div></div><div class="card-body"><div class="grid metric-grid" style="margin:0">${metricCard("True positives",fmtNum(e.tp),"Problem correctly flagged")}${metricCard("False positives",fmtNum(e.fp),"Compliant case flagged")}${metricCard("True negatives",fmtNum(e.tn),"Compliant case passed")}${metricCard("False negatives",fmtNum(e.fn),"Problem case passed as compliant")}</div></div></section>`;
}
function metricCard(title,value,desc){return `<section class="card metric"><span>${title}</span><strong>${value}</strong><small>${desc}</small></section>`}
async function renderData(root) {
 const health=await api("/health");setApiStatus(true);
 const gen=state.summary?.generation||{shipments:"—",seed:"—",generated_at:null};
 root.innerHTML=heading("Data & Export","Generate repeatable synthetic data, validate outcomes, and download reports.")+`
 <div class="notice">This backend currently supports up to 200 shipments per generation request. Its database resets when the server starts. Use only in a local demo environment.</div>
 <div class="grid two-col">
 <section class="card"><div class="card-header"><div><div class="card-title">Generate synthetic dataset</div><div class="card-subtitle">Fixed seed and configurable shipment count</div></div></div><div class="card-body">
 <form id="generate-form"><div class="form-row"><div class="form-field"><label for="count">Number of shipments (1–200)</label><input class="field" id="count" name="count" type="number" min="1" max="200" value="50" required></div><div class="form-field"><label for="seed">Random seed</label><input class="field" id="seed" name="seed" type="number" min="0" value="20261022" required></div><button class="btn btn-primary" type="submit">Generate dataset</button></div></form>
 <div id="generation-result" style="margin-top:16px"></div></div></section>
 <section class="card"><div class="card-header"><div><div class="card-title">Current environment</div><div class="card-subtitle">Backend connection and latest generation</div></div></div><div class="card-body"><div class="status-list">
 ${miniMetric("API status",health.status==="ok"?"Healthy":"Unavailable","FastAPI backend")}
 ${miniMetric("Database engine",health.database||"Unknown","Configured database backend")}
 ${miniMetric("Last generated shipments",gen.shipments??"—","Most recent summary response")}
 ${miniMetric("Last seed",gen.seed??"—","Use the same seed to reproduce a dataset")}
 ${miniMetric("Generated at",fmtDate(gen.generated_at),"Timestamp of the latest run")}
 </div></div></section>
 </div>
 <section class="card" style="margin-top:16px"><div class="card-header"><div><div class="card-title">Export data</div><div class="card-subtitle">Download the shipment-level compliance summary</div></div></div><div class="card-body"><div class="export-list">
 <div class="export-item"><div><strong>Shipment report · CSV</strong><small>Spreadsheet-friendly decisions and reasons</small></div><button class="btn" data-action="export-csv">Download CSV ↓</button></div>
 <div class="export-item"><div><strong>Shipment report · JSON</strong><small>Structured records for downstream use</small></div><button class="btn" data-action="export-json">Download JSON ↓</button></div>
 <div class="export-item"><div><strong>Ground-truth evaluation</strong><small>Precision, recall, F1, and scenario match</small></div><button class="btn" data-page="evaluation">Open evaluation →</button></div>
 <div class="export-item"><div><strong>API documentation</strong><small>Inspect and try available endpoints</small><a href="/docs" target="_blank" rel="noopener" class="link-button">Open Swagger docs ↗</a></div></div>
 </div></div></section>`;
 $("#generate-form").addEventListener("submit",async e=>{
   e.preventDefault();const count=Number($("#count").value),seed=Number($("#seed").value);
   if(!Number.isInteger(count)||count<1||count>200||!Number.isInteger(seed)||seed<0){toast("Enter a shipment count from 1 to 200 and a non-negative seed.");return;}
   const btn=$('button[type="submit"]',e.currentTarget);btn.disabled=true;btn.textContent="Generating…";
   $("#generation-result").innerHTML='<div class="muted small">Generating data and updating the database…</div>';
   try{const result=await api(`/data/generate?count=${count}&seed=${seed}`,{method:"POST"});$("#generation-result").innerHTML=`<div class="notice">Generated ${fmtNum(result.shipments)} shipments with seed ${fmtNum(result.seed)}. The dataset is ready.</div>`;toast("Dataset generated successfully.");state.summary=null;}
   catch(err){$("#generation-result").innerHTML=`<div class="error-box">${esc(err.message)}</div>`;}
   finally{btn.disabled=false;btn.textContent="Generate dataset";}
 });
}
function downloadFile(path,filename) {
 fetch(API+path).then(async r=>{if(!r.ok){let b={};try{b=await r.json()}catch{}throw new Error(b.detail||"Export failed");}return r.blob()})
 .then(blob=>{const url=URL.createObjectURL(blob),a=document.createElement("a");a.href=url;a.download=filename;document.body.appendChild(a);a.click();a.remove();URL.revokeObjectURL(url);toast("Export downloaded.");})
 .catch(e=>toast(e.message));
}
function showPageError(err) {
 console.error(err);setApiStatus(false);
 $("#content").innerHTML=heading("Unable to load data","The frontend could not complete its request.")+`<div class="error-box"><strong>Backend request failed.</strong><p>${esc(err.message||"Unknown error")}</p><p>Start FastAPI from the backend directory with <code>uvicorn main:app --reload</code>, then refresh this page.</p><button class="btn" data-action="refresh">Try again</button></div>`;
}
async function refresh() { if(state.page==="detail") return renderDetail($("#content"),state.detailCode); return renderPage(); }
document.addEventListener("click",e=>{
 const pageBtn=e.target.closest("[data-page]");if(pageBtn){e.preventDefault();nav(pageBtn.dataset.page);return;}
 const detail=e.target.closest("[data-detail]");if(detail){state.detailCode=detail.dataset.detail;state.page="detail";$$(".nav-item").forEach(b=>b.classList.toggle("active",b.dataset.page==="shipments"));$("#breadcrumb-current").textContent="Shipment detail";renderDetail($("#content"),state.detailCode).catch(showPageError);return;}
 const act=e.target.closest("[data-action]")?.dataset.action;if(!act)return;
 if(act==="refresh"){refresh().catch(showPageError);}
 if(act==="prev-page"){state.pageNo=Math.max(1,state.pageNo-1);renderShipments($("#content")).catch(showPageError);}
 if(act==="next-page"){state.pageNo=Math.min(state.totalPages,state.pageNo+1);renderShipments($("#content")).catch(showPageError);}
 if(act==="export-csv")downloadFile("/export/csv","shipments.csv");
 if(act==="export-json")downloadFile("/export/json","shipments.json");
});
$("#mobile-menu").addEventListener("click",()=>$("#sidebar").classList.toggle("open"));
async function boot(){
 try{await api("/health");setApiStatus(true);}catch{setApiStatus(false);}
 nav("overview");
}
boot();
