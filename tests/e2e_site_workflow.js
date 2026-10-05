/* Portable browser gate using synthetic imagery: node tests/e2e_site_workflow.js URL CHROME_EXE PROFILE TIFF_PATH */
"use strict";
const { spawn } = require("child_process");
const fs = require("fs");
const [url, exe, profileArg, imageArg] = process.argv.slice(2);
const withUse = process.argv[6] === "use";
const profile = require("path").resolve(profileArg), image = require("path").resolve(imageArg);
const port = 10000 + Math.floor(Math.random() * 1000);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function main() {
  const browser = spawn(exe, ["--headless=new", "--disable-gpu", "--no-first-run", `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, "--window-size=1600,1100", url], {stdio:"ignore"});
  let socket;
  try {
    let target;
    for (let i=0; i<60 && !target; i++) {
      await sleep(250);
      try { target = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find((t) => t.type === "page"); } catch (_) {}
    }
    if (!target) throw new Error("Browser did not start");
    socket = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((r,j) => { socket.onopen=r; socket.onerror=j; });
    let id=0; const pending=new Map(), errors=[];
    socket.onmessage = ({data}) => { const m=JSON.parse(data); if (m.id) { pending.get(m.id)?.(m); pending.delete(m.id); }
      if (m.method === "Runtime.exceptionThrown") errors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text); };
    const send = (method, params={}) => new Promise((r,j) => { const next=++id;
      const timer=setTimeout(()=>{pending.delete(next);j(new Error("CDP timed out: " + method));},10000);
      pending.set(next,(m)=>{clearTimeout(timer); if(m.error) j(new Error(method + ": " + m.error.message)); else r(m);});
      socket.send(JSON.stringify({id:next,method,params})); });
    const evaluate = async (expression) => { const r=await send("Runtime.evaluate",{expression,awaitPromise:true,returnByValue:true});
      if (r.result.exceptionDetails) throw new Error(r.result.exceptionDetails.exception?.description || "Evaluation failed"); return r.result.result.value; };
    const until = async (expression) => { for (let i=0;i<100;i++) { if (await evaluate(expression)) return; await sleep(100); }
      throw new Error("Timed out: " + expression + " · " + await evaluate("document.getElementById('site-result').textContent + ' / ' + document.getElementById('status').textContent")); };
    const reloadApp = async () => {
      // Clear the old document's hook so it cannot satisfy readiness before navigation starts.
      await evaluate("window.__app=null");
      await send("Page.reload",{ignoreCache:true});
      await until("!!window.__app");
    };
    const check = (value, message) => { if (!value) throw new Error(message); };
    await send("Runtime.enable"); await send("Page.reload",{ignoreCache:true});
    await until("document.getElementById('site-import').open && document.getElementById('hdr-conn').textContent.includes('no sites')");
    check(await evaluate("document.getElementById('btn-draw').disabled"), "Empty-site tools should be disabled");
    await evaluate("document.querySelector('[name=site_id]').value='browser-fixture'; document.querySelector('[name=name]').value='Synthetic browser fixture'; document.querySelector('[name=licence]').value='synthetic fixture';");
    const doc=await send("DOM.getDocument");
    const input=await send("DOM.querySelector",{nodeId:doc.result.root.nodeId,selector:'input[name="imagery"]'});
    await send("DOM.setFileInputFiles",{nodeId:input.result.nodeId,files:[image]});
    await evaluate("document.getElementById('site-submit').click()");
    await until("!!window.__app");
    check(await evaluate("window.__app.api.site.crs === 'EPSG:32643' && document.querySelectorAll('img.leaflet-tile').length > 0"), "Site coordinates/tiles were not loaded");
    const job=await evaluate(`(async () => {
      const geom = {type:'Polygon',coordinates:[]};
      // Use fixture bounds to generate a rectangle well inside the source extent.
      const [w,s,e,n]=window.__app.api.site.bounds;
      geom.coordinates=[[[w+(e-w)*.1,s+(n-s)*.1],[w+(e-w)*.4,s+(n-s)*.1],[w+(e-w)*.4,s+(n-s)*.4],[w+(e-w)*.1,s+(n-s)*.4],[w+(e-w)*.1,s+(n-s)*.1]]];
      const r=await fetch('/api/sites/browser-fixture/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind:'import_layer',name:'Synthetic candidate',source:'synthetic browser test',features:{type:'FeatureCollection',features:[{type:'Feature',geometry:geom,properties:{}}]}})});
      return r.json();
    })()`);
    check(job.id, "Fixture job was not queued");
    await until(`fetch('/api/jobs/${job.id}').then(r=>r.json()).then(j=>j.status==='succeeded')`);
    let useJob;
    if (withUse) {
      const road = await evaluate(`(async () => {
        const [w,s,e,n]=window.__app.api.site.bounds;
        const geometry={type:'Polygon',coordinates:[[[w+(e-w)*.65,s+(n-s)*.1],[w+(e-w)*.8,s+(n-s)*.1],[w+(e-w)*.8,s+(n-s)*.7],[w+(e-w)*.65,s+(n-s)*.7],[w+(e-w)*.65,s+(n-s)*.1]]]};
        return (await fetch('/api/sites/browser-fixture/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind:'import_layer',name:'Synthetic road',feature_type:'road_surface',features:{type:'FeatureCollection',features:[{type:'Feature',geometry,properties:{}}]}})})).json();
      })()`);
      check(road.id,"Road fixture was not queued");
      await until(`fetch('/api/jobs/${road.id}').then(r=>r.json()).then(j=>j.status==='succeeded')`);
      await reloadApp();
      await until("!document.getElementById('job-run-use').disabled");
      await evaluate(`document.getElementById('job-controls').open=true; document.getElementById('use-model').value='synthetic-use'; document.getElementById('use-buildings').value='${job.id}'; document.getElementById('use-roads').value='${road.id}'; document.getElementById('job-run-use').click();`);
      await until("fetch('/api/sites/browser-fixture/jobs').then(r=>r.json()).then(j=>j.jobs.some(row=>row.kind==='use_classification' && row.status==='succeeded'))");
      useJob = await evaluate("fetch('/api/sites/browser-fixture/jobs').then(r=>r.json()).then(j=>j.jobs.find(row=>row.kind==='use_classification' && row.status==='succeeded').id)");
    }
    await reloadApp(); await until("document.getElementById('btn-add-view').disabled === false");
    if (withUse) {
      await evaluate(`document.getElementById('review-candidate').value='${useJob}'; document.getElementById('review-candidate').dispatchEvent(new Event('change'));`);
      check(await evaluate(`fetch('/api/sites/browser-fixture/layers/${useJob}/features').then(r=>r.json()).then(j=>j.features[0].properties.functional_use==='unknown' && j.features[0].properties.functional_use_suggestion==='class-a')`), "Use suggestions were promoted or missing");
    }
    await evaluate("document.getElementById('btn-add-view').click()"); await until("window.__app.ws.length===1 && !window.__app.saving");
    await evaluate("document.getElementById('btn-accept').click()"); await until("window.__app.ws[0].properties.review_status==='accepted' && !window.__app.saving");
    const revision=await evaluate("window.__app.revision"); check(revision===2, "Review revision should be 2");
    await evaluate("document.getElementById('btn-history').click()"); await until("document.getElementById('review-history').textContent.includes('Revision 2')");
    await evaluate("document.getElementById('btn-export').click()"); await until("document.getElementById('export-result').textContent.startsWith('Exported')");
    await reloadApp();
    check(await evaluate("window.__app.ws.length===1 && window.__app.revision===2 && window.__app.ws[0].properties.review_status==='accepted'"), "Saved review did not survive browser reload");
    await evaluate(`(() => { const id=window.__app.ws[0].id; window.__app.mapR.eachLayer(l=>{if(l.feature?.id===id && l._path) l._path.dispatchEvent(new MouseEvent('click',{bubbles:true}));}); })()`);
    await until("!document.getElementById('use-review').hidden && !document.getElementById('btn-use-save').disabled");
    await evaluate("document.getElementById('use-label').value='residential'; document.getElementById('use-evidence').value=''; document.getElementById('btn-use-save').click()");
    await until("!window.__app.saving && document.getElementById('status').textContent.startsWith('Change failed')");
    check(await evaluate("window.__app.revision===2 && window.__app.ws[0].properties.functional_use==='unknown'"), "Use changed without evidence");
    await evaluate(`(() => { const id=window.__app.ws[0].id; window.__app.mapR.eachLayer(l=>{if(l.feature?.id===id && l._path) l._path.dispatchEvent(new MouseEvent('click',{bubbles:true}));}); document.getElementById('review-actor').value='synthetic-reviewer'; document.getElementById('use-label').value='residential'; document.getElementById('use-evidence').value='synthetic-observation-001'; document.getElementById('btn-use-save').click(); })()`);
    await until("!window.__app.saving && window.__app.revision===3 && window.__app.ws[0].properties.functional_use==='residential'");
    check(await evaluate("window.__app.ws[0].properties.review_status==='suggested' && window.__app.ws[0].properties.functional_use_review.evidence_refs[0]==='synthetic-observation-001'"), "Use review did not preserve its source reference");
    await evaluate("document.getElementById('btn-accept').click()");
    await until("!window.__app.saving && window.__app.revision===4 && window.__app.ws[0].properties.review_status==='accepted'");
    await evaluate("document.getElementById('use-review').scrollIntoView({block:'center'})");
    const useShot=await send("Page.captureScreenshot",{format:"png"});
    fs.writeFileSync(profile+"/use-review.png",Buffer.from(useShot.result.data,"base64"));
    await reloadApp(); await until("window.__app.revision===4");
    check(await evaluate("window.__app.ws[0].properties.functional_use==='residential' && window.__app.ws[0].properties.functional_use_review.actor==='synthetic-reviewer'"), "Use review did not survive reload");
    await evaluate(`(() => { const id=window.__app.ws[0].id; window.__app.mapR.eachLayer(l=>{if(l.feature?.id===id && l._path) l._path.dispatchEvent(new MouseEvent('click',{bubbles:true}));}); document.getElementById('btn-edit').click(); })()`);
    await until("!!document.querySelector('.marker-icon:not(.marker-icon-middle)')");
    const marker=await evaluate("(() => { const r=document.querySelector('.marker-icon:not(.marker-icon-middle)').getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2}; })()");
    const mouse=(type,x,y)=>send("Input.dispatchMouseEvent",{type,x,y,button:"left",buttons:type==="mouseReleased"?0:1,clickCount:1});
    await mouse("mouseMoved",marker.x,marker.y); await mouse("mousePressed",marker.x,marker.y);
    for(let i=1;i<=4;i++){ await mouse("mouseMoved",marker.x+i*2,marker.y); await sleep(50); }
    await mouse("mouseReleased",marker.x+8,marker.y);
    await evaluate("document.getElementById('btn-edit').click()");
    await until("!window.__app.saving && window.__app.ws[0].properties.origin==='human_edited' && window.__app.ws[0].properties.review_status==='suggested'");
    check(await evaluate("window.__app.ws[0].properties.functional_use==='unknown' && window.__app.ws[0].properties.functional_use_review.status==='needs_review'"), "Geometry edit kept an outdated use label");
    await evaluate("document.getElementById('btn-reject').click()"); await until("!window.__app.saving && window.__app.ws[0].properties.review_status==='rejected'");
    await evaluate("document.getElementById('btn-reset').click()"); await until("!window.__app.saving && window.__app.ws[0].properties.review_status==='suggested'");
    await evaluate("document.getElementById('btn-remove').click()"); await until("!window.__app.saving && window.__app.ws.length===0");
    await evaluate("document.getElementById('export-revision').value=2; document.getElementById('export-revision').dispatchEvent(new Event('input')); document.getElementById('btn-export').click()");
    await until("document.getElementById('export-result').textContent.startsWith('Exported')");
    const historical = await evaluate("fetch('/api/review/workspace?site_id=browser-fixture&revision=2').then(r=>r.json()).then(j=>j.features.length===1 && j.features[0].properties.review_status==='accepted')");
    check(historical,"Historical snapshot changed after edit/removal");
    check(await evaluate("fetch('/api/review/workspace?site_id=browser-fixture&revision=4').then(r=>r.json()).then(j=>j.features[0].properties.functional_use==='residential')"), "Historical use review changed after edit/removal");
    await evaluate("document.getElementById('btn-next-review').click(); window.__app.toggleEval(true)");
    await until("document.getElementById('metric-banner').textContent.startsWith('No evaluation')");
    const shot=await send("Page.captureScreenshot",{format:"png"});
    fs.writeFileSync(profile+"/site-workflow.png",Buffer.from(shot.result.data,"base64"));
    check(!errors.length, "Browser errors: " + errors.join("\n"));
    console.log(JSON.stringify({passed:true,checks:["empty-site screen","GeoTIFF form upload","site CRS and tiles","worker layer publication","server review","history","revision export","reload persistence","use reference requirement","use review and reload","geometry invalidates use review","real vertex drag","reject/reset/remove","historical revision export","empty evaluation",...(withUse ? ["functional-use job form","use suggestions remain unknown"] : [])],accepted_revision:revision,use_review_revision:4,final_revision:await evaluate("window.__app.revision"),screenshot:profile+"/site-workflow.png",use_screenshot:profile+"/use-review.png"},null,2));
  } finally { if (socket) socket.close(); browser.kill(); }
}
main().catch((e)=>{ console.error(e); process.exitCode=1; });
