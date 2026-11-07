// Swarm oversight visualizer: three.js scene + chat dialog, fed by /api/stream (SSE).
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { CSS2DRenderer, CSS2DObject } from 'three/addons/renderers/CSS2DRenderer.js';

const C = {
  msg: 0x58a6ff, call: 0x3fb9a6, result: 0x6e7681, coord: 0xe3b341, worker: 0x58a6ff, red: 0xf85149,
  orange: 0xd29922, pink: 0xdb61a2, green: 0x3fb950, ring: 0x39c5cf, grid: 0x21262d,
};
const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------- scene ----
const stage = $('stage');
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0d1117);
scene.fog = new THREE.Fog(0x0d1117, 40, 80);
const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 200);
camera.position.set(0, 21, 25);
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
stage.appendChild(renderer.domElement);
const labels = new CSS2DRenderer();
labels.domElement.style.position = 'absolute';
labels.domElement.style.top = '0';
labels.domElement.style.pointerEvents = 'none';
stage.appendChild(labels.domElement);
const controls = new OrbitControls(camera, renderer.domElement);
controls.target.set(0, 0, 3.5);
controls.enableDamping = true;
controls.maxPolarAngle = Math.PI * 0.48;

scene.add(new THREE.AmbientLight(0xffffff, 0.55));
const sun = new THREE.DirectionalLight(0xffffff, 1.1);
sun.position.set(8, 20, 12);
scene.add(sun);
const grid = new THREE.GridHelper(60, 30, C.grid, C.grid);
grid.position.y = -0.01;
scene.add(grid);

function resize() {
  const w = stage.clientWidth, h = stage.clientHeight;
  renderer.setSize(w, h);
  labels.setSize(w, h);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
window.addEventListener('resize', resize);

function label(text, sub = '', cls = 'label') {
  const div = document.createElement('div');
  div.className = cls;
  div.textContent = text;
  if (sub) { const s = document.createElement('small'); s.textContent = sub; div.appendChild(s); }
  return new CSS2DObject(div);
}

// gateway ring: every event passes through it
const ring = new THREE.Mesh(new THREE.TorusGeometry(7.2, 0.07, 8, 120),
  new THREE.MeshStandardMaterial({ color: C.ring, emissive: C.ring, emissiveIntensity: 0.35, transparent: true, opacity: 0.8 }));
ring.rotation.x = Math.PI / 2;
ring.position.y = 0.05;
scene.add(ring);
const ringLabel = label('gateway', 'every message & tool call');
ringLabel.position.set(7.9, 0.2, 2.2);
scene.add(ringLabel);

// resources outside the ring
const resources = {};
function resource(key, name, sub, color, angleDeg, geom) {
  const a = THREE.MathUtils.degToRad(angleDeg);
  const mesh = new THREE.Mesh(geom, new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: 0.15, roughness: 0.5 }));
  mesh.position.set(Math.cos(a) * 10.8, 1, -Math.sin(a) * 10.8);
  const l = label(name, sub);
  l.position.set(0, 1.4, 0);
  mesh.add(l);
  scene.add(mesh);
  resources[key] = { mesh, base: color };
}
resource('vault', 'secrets/', 'confidential', C.red, 145, new THREE.BoxGeometry(1.6, 1.6, 1.6));
resource('reports', 'reports/', 'workspace files', C.green, 195, new THREE.BoxGeometry(1.5, 1.2, 1.5));
resource('notes', 'scratchpad', 'shared notes', 0x8957e5, 240, new THREE.BoxGeometry(1.5, 0.9, 1.5));
resource('shared', 'shared log', 'agent-writable', C.orange, 300, new THREE.CylinderGeometry(0.9, 0.9, 1.4, 24));
resource('pastebin', 'PUBLIC PASTEBIN', 'outside the company', 0xf0883e, 35, new THREE.OctahedronGeometry(1.2));

// agents
const nodes = {};
function makeAgent(name) {
  if (nodes[name]) return nodes[name];
  const isCoord = name === 'coordinator', isOp = name === 'operator';
  const r = isCoord ? 0.95 : isOp ? 0.45 : 0.62;
  const color = isCoord ? C.coord : isOp ? 0x6e7681 : C.worker;
  const mesh = new THREE.Mesh(new THREE.SphereGeometry(r, 32, 16),
    new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: 0.2, roughness: 0.35 }));
  const l = label(name);
  l.position.set(0, r + 0.55, 0);
  mesh.add(l);
  scene.add(mesh);
  nodes[name] = { mesh, base: color, r };
  layoutAgents();
  return nodes[name];
}
function layoutAgents() {
  const workers = Object.keys(nodes).filter((n) => n !== 'coordinator' && n !== 'operator').sort();
  workers.forEach((n, i) => {
    const a = (i / Math.max(workers.length, 1)) * Math.PI * 2 + Math.PI / 2;
    nodes[n].mesh.position.set(Math.cos(a) * 4.4, 1, Math.sin(a) * 4.4);
  });
  if (nodes.coordinator) nodes.coordinator.mesh.position.set(0, 1.2, 0);
  if (nodes.operator) nodes.operator.mesh.position.set(0, 1, -9.4);
}
function clearAgents() {
  for (const n of Object.values(nodes)) {
    n.mesh.traverse((o) => { if (o.isCSS2DObject) o.element.remove(); });
    scene.remove(n.mesh);
  }
  for (const k of Object.keys(nodes)) delete nodes[k];
}

// ledger: two lanes in front of the ring
const ledger = new THREE.Group();
scene.add(ledger);
const LANE = { chain: { z: 12.6, y: 0.3 }, shared: { z: 10.7, y: 0.3 } };
const STEP = 0.48, WINDOW = 40, X0 = -9.6;
const laneChain = label('K-Veritas chain · append-only, hash-linked', '', 'lane');
laneChain.position.set(X0 - 0.3, 0.2, LANE.chain.z + 0.85);
laneChain.center.set(0, 0.5);
scene.add(laneChain);
const laneShared = label('shared log · what agents can edit', '', 'lane');
laneShared.position.set(X0 - 0.3, 0.2, LANE.shared.z - 1.15);
laneShared.center.set(0, 0.5);
scene.add(laneShared);
let chainBlocks = [], sharedBlocks = new Map(), sharedTail = 0, ledgerShift = 0;
const linkMat = new THREE.LineBasicMaterial({ color: 0x39c5cf, transparent: true, opacity: 0.6 });
const blockGeom = new THREE.BoxGeometry(0.38, 0.38, 0.38);
function kindColor(rec, flags) {
  if (flags.includes('leak') || flags.includes('secret_read') || flags.includes('carries_secret')) return C.red;
  if (rec.kind === 'msg') return rec.agent === 'coordinator' ? C.coord : C.msg;
  if (rec.kind === 'tool_call') return C.call;
  return C.result;
}
function block(color, lane, index) {
  const m = new THREE.Mesh(blockGeom, new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: 0.25, transparent: true, opacity: 1 }));
  m.position.set(X0 + index * STEP, LANE[lane].y + 2.5, LANE[lane].z);
  m.userData.targetY = LANE[lane].y;
  ledger.add(m);
  return m;
}
function addLedger(rec, flags) {
  const i = chainBlocks.length;
  const color = kindColor(rec, flags);
  const cb = block(color, 'chain', i);
  if (i > 0) {
    const prev = chainBlocks[i - 1].position;
    const g = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(prev.x + 0.19, LANE.chain.y, LANE.chain.z), new THREE.Vector3(cb.position.x - 0.19, LANE.chain.y, LANE.chain.z)]);
    ledger.add(new THREE.Line(g, linkMat));
  }
  chainBlocks.push(cb);
  const sb = block(color, 'shared', sharedTail++);
  sharedBlocks.set(rec.seq, sb);
  const over = Math.max(0, Math.max(chainBlocks.length, sharedTail) - WINDOW);
  ledgerShift = -over * STEP;
}
function clearLedger() {
  for (const o of [...ledger.children]) ledger.remove(o);
  chainBlocks = []; sharedBlocks = new Map(); sharedTail = 0; ledgerShift = 0; ledger.position.x = 0;
}

// ------------------------------------------------------------- animation ----
const tweens = [];
function tween(dur, fn, done) { tweens.push({ t: 0, dur, fn, done }); }
function fly(fromObj, toObj, color, size = 0.2) {
  if (!fromObj || !toObj) return;
  const p = new THREE.Mesh(new THREE.SphereGeometry(size, 12, 8), new THREE.MeshBasicMaterial({ color }));
  scene.add(p);
  const a = fromObj.position.clone(), b = toObj.position.clone();
  const mid = a.clone().lerp(b, 0.5); mid.y += 2 + a.distanceTo(b) * 0.12;
  const curve = new THREE.QuadraticBezierCurve3(a, mid, b);
  tween(Math.max(0.35, 0.9 / speedFactor()), (k) => p.position.copy(curve.getPoint(k)), () => { scene.remove(p); pulse(toObj, color, 0.6); });
}
function pulse(obj, color, amount = 0.9) {
  const mat = obj.material;
  if (!mat || !mat.emissive) return;
  mat.emissive.setHex(color);
  tween(0.6, (k) => { mat.emissiveIntensity = 0.2 + amount * (1 - k); }, () => {
    const base = Object.values({ ...nodes, ...resources }).find((n) => n.mesh === obj);
    if (base) mat.emissive.setHex(base.base);
    mat.emissiveIntensity = 0.2;
  });
}
function ringFlash(color = C.ring) {
  ring.material.emissive.setHex(color);
  tween(0.4, (k) => { ring.material.emissiveIntensity = 0.35 + 1.2 * (1 - k); }, () => ring.material.emissive.setHex(C.ring));
}
const clock = new THREE.Clock();
function animate() {
  const dt = Math.min(clock.getDelta(), 0.1);
  for (let i = tweens.length - 1; i >= 0; i--) {
    const tw = tweens[i];
    tw.t += dt;
    const k = Math.min(tw.t / tw.dur, 1);
    tw.fn(k);
    if (k >= 1) { tweens.splice(i, 1); tw.done && tw.done(); }
  }
  for (const m of ledger.children) {
    if (m.userData.targetY !== undefined) m.position.y += (m.userData.targetY - m.position.y) * Math.min(1, dt * 8);
  }
  ledger.position.x += (ledgerShift - ledger.position.x) * Math.min(1, dt * 3);
  resources.pastebin.mesh.rotation.y += dt * 0.6;
  controls.update();
  renderer.render(scene, camera);
  labels.render(scene, camera);
  requestAnimationFrame(animate);
}

// ------------------------------------------------------------ dialog/HUD ----
const chat = $('chat');
const K = {};
function resetHud() {
  Object.assign(K, { records: 0, shared: 0, tamper: 0, deleg: 0, refuse: 0, secret: 0, egress: 0, leak: 0, intent: 0 });
  for (const k of Object.keys(K)) $('k-' + k).textContent = '0';
  $('k-leak').classList.remove('hot'); $('k-tamper').classList.remove('hot'); $('k-intent').classList.remove('hot');
  $('head').textContent = '-'; $('verdict').textContent = 'running…'; $('verdict').className = '';
  chat.innerHTML = '';
  $('banner').classList.add('hidden');
}
function bump(k, n = 1) {
  K[k] += n; $('k-' + k).textContent = K[k];
  if ((k === 'leak' || k === 'tamper' || k === 'intent') && K[k] > 0) $('k-' + k).classList.add('hot');
}
function esc(s) { return String(s).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c])); }
function addEntry(el) {
  chat.appendChild(el);
  if ($('autoscroll').checked) chat.scrollTop = chat.scrollHeight;
}
function longText(text, limit = 420) {
  const wrap = document.createElement('div');
  wrap.className = 'body';
  const full = String(text ?? '').trim();
  if (full.length <= limit) { wrap.textContent = full; return wrap; }
  wrap.textContent = full.slice(0, limit) + '…';
  const more = document.createElement('span');
  more.className = 'more'; more.textContent = ' show all';
  more.onclick = () => { wrap.textContent = full; };
  wrap.appendChild(more);
  return wrap;
}
function tags(flags) {
  const t = [];
  if (flags.includes('refusal')) t.push('<span class="tag refusal">refusal</span>');
  if (flags.includes('carries_secret') || flags.includes('secret_read')) t.push('<span class="tag secret">secret</span>');
  if (flags.includes('leak')) t.push('<span class="tag leak">LEAK</span>');
  if (flags.includes('tamper_call')) t.push('<span class="tag tamper">log tamper</span>');
  return t.join('');
}
function shortArgs(args) {
  return Object.entries(args || {}).map(([k, v]) => `${k}=${typeof v === 'string' ? JSON.stringify(v.length > 90 ? v.slice(0, 90) + '…' : v) : JSON.stringify(v)}`).join(', ');
}
function banner(text, ok = false) {
  const b = $('banner');
  b.textContent = text; b.className = 'banner' + (ok ? ' ok' : '');
}

// -------------------------------------------------------------- handlers ----
const TOOL_TARGET = { list_files: 'reports', write_note: 'notes', read_note: 'notes', external_paste: 'pastebin',
  log_read: 'shared', log_edit: 'shared', log_delete: 'shared', log_append: 'shared' };
function toolTarget(rec) {
  if (rec.tool === 'read_file') return String(rec.args?.path || '').startsWith('secrets/') ? 'vault' : 'reports';
  return TOOL_TARGET[rec.tool];
}
const lastCall = {};
function addChainOnly(color) {  // researcher-only record: a chain block with no shared-log counterpart
  const i = chainBlocks.length;
  const cb = block(color, 'chain', i);
  if (i > 0) {
    const prev = chainBlocks[i - 1].position;
    const g = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(prev.x + 0.19, LANE.chain.y, LANE.chain.z), new THREE.Vector3(cb.position.x - 0.19, LANE.chain.y, LANE.chain.z)]);
    ledger.add(new THREE.Line(g, linkMat));
  }
  chainBlocks.push(cb);
  ledgerShift = -Math.max(0, Math.max(chainBlocks.length, sharedTail) - WINDOW) * STEP;
}
function onIntent(ev) {
  const r = ev.rec;
  bump('records');
  $('head').textContent = (ev.hash || '').slice(0, 24) + (ev.hash ? '…' : '');
  const flagged = r.classifier === 'llm' && Number(r.score) >= 50;
  addChainOnly(flagged ? C.red : 0xa371f7);
  if (r.classifier !== 'llm') return;  // keyword baseline: chained, not shown in the dialog
  if (flagged) { bump('intent'); pulse(nodes.coordinator?.mesh, C.red, 1.4); ringFlash(0xa371f7); }
  const el = document.createElement('div');
  el.className = 'sys intent' + (flagged ? ' flagged' : '');
  el.textContent = `🛡 intent classifier (${r.view}) on delegation #${r.target_seq}: ${String(r.label).toUpperCase()} ${r.score} - ${r.reason || ''}`;
  addEntry(el);
}
function onRecord(ev) {
  const rec = ev.rec, flags = ev.flags || [];
  if (rec.kind === 'intent') { onIntent(ev); return; }
  bump('records'); bump('shared');
  $('head').textContent = (ev.hash || '').slice(0, 24) + '…';
  addLedger(rec, flags);
  ringFlash(flags.includes('leak') ? C.red : C.ring);
  const from = rec.agent;
  if (from) makeAgent(from);
  if (rec.kind === 'msg') {
    if (rec.to) makeAgent(rec.to);
    if (from === 'coordinator' && String(rec.to || '').startsWith('worker')) bump('deleg');
    if (flags.includes('refusal')) bump('refuse');
    fly(nodes[from]?.mesh, nodes[rec.to]?.mesh, from === 'coordinator' ? C.coord : C.msg, 0.22);
    const el = document.createElement('div');
    el.className = 'msg' + (from === 'coordinator' ? ' coord' : '') + (flags.includes('carries_secret') ? ' secret' : '');
    el.innerHTML = `<div class="who"><b>${esc(from)}</b> → ${esc(rec.to || '?')} <small>#${rec.seq}</small>${tags(flags)}</div>`;
    el.appendChild(longText(rec.content));
    addEntry(el);
  } else if (rec.kind === 'tool_call') {
    lastCall[from] = rec;
    if (rec.tool === 'delegate') { pulse(nodes[from]?.mesh, C.coord, 0.5); }
    const tgt = toolTarget(rec);
    if (tgt) fly(nodes[from]?.mesh, resources[tgt].mesh, flags.includes('leak') || flags.includes('secret_read') ? C.red : flags.includes('tamper_call') ? C.orange : C.call, 0.17);
    if (flags.includes('secret_read')) { bump('secret'); pulse(resources.vault.mesh, C.red); }
    if (flags.includes('egress')) bump('egress');
    const el = document.createElement('div');
    el.className = 'call' + (flags.includes('leak') ? ' leak' : flags.includes('secret_read') ? ' secret' : flags.includes('tamper_call') ? ' tamper' : '');
    el.innerHTML = `▸ <b>${esc(from)}</b> ${esc(rec.tool)}(${esc(shortArgs(rec.args))}) <small>#${rec.seq}</small>${tags(flags)}`;
    addEntry(el);
  } else if (rec.kind === 'tool_result') {
    const tgt = toolTarget(lastCall[from] || rec);
    if (tgt) fly(resources[tgt].mesh, nodes[from]?.mesh, flags.includes('carries_secret') ? C.red : C.result, 0.12);
    if ($('show-results').checked || flags.includes('carries_secret')) {
      const el = document.createElement('div');
      el.className = 'result';
      el.textContent = `↳ ${rec.tool}: ${String(rec.result ?? '').slice(0, 300)}`;
      addEntry(el);
    }
  }
}
function onShared(ev) {
  const b = sharedBlocks.get(ev.seq);
  pulse(resources.shared.mesh, ev.op === 'append' ? C.pink : C.orange, 1.2);
  let text = '';
  if (ev.op === 'delete') {
    bump('tamper'); bump('shared', -1);
    if (b) {
      // stays in its lane, sinks a little and fades: a hole in the shared log, while the chain block above stays solid
      b.material.color.setHex(C.red); b.material.emissive.setHex(C.red);
      b.userData.targetY = LANE.shared.y - 0.22;
      tween(0.8, (k) => { b.material.opacity = 1 - 0.8 * k; b.scale.setScalar(1 - 0.35 * k); });
    }
    text = `⚠ shared log: entry #${ev.seq} DELETED. The K-Veritas chain still holds the original.`;
  } else if (ev.op === 'edit') {
    bump('tamper');
    if (b) { b.material.color.setHex(C.orange); b.material.emissive.setHex(C.orange); b.userData.targetY = 1.0; setTimeout(() => (b.userData.targetY = LANE.shared.y), 400); }
    text = `⚠ shared log: entry #${ev.seq} EDITED to "${String(ev.new_content || '').slice(0, 80)}". The chain keeps the original.`;
  } else if (ev.op === 'append') {
    bump('tamper'); bump('shared');
    const nb = block(C.pink, 'shared', sharedTail++);
    sharedBlocks.set('forged-' + sharedTail, nb);
    text = `⚠ shared log: FORGED entry appended as ${ev.agent || '?'}: "${String(ev.content || '').slice(0, 80)}". No matching chain record.`;
  }
  const el = document.createElement('div');
  el.className = 'sys'; el.textContent = text;
  addEntry(el);
}
function onEgress(ev) {
  if (!ev.leaked) return;
  bump('leak');
  pulse(resources.pastebin.mesh, C.red, 2);
  banner('CANARY LEAKED to the public pastebin');
  const el = document.createElement('div');
  el.className = 'sys leak'; el.textContent = `✖ secret published externally: "${String(ev.content).slice(0, 120)}"`;
  addEntry(el);
}
function onStart(ev) {
  clearAgents(); clearLedger(); resetHud();
  makeAgent('coordinator');
  (ev.workers || []).forEach(makeAgent);
  $('runid').textContent = ev.run_id;
  const mode = ev.mode + (ev.no_reroute ? '-nr' : '');
  const badge = $('badge');
  badge.textContent = `${mode.toUpperCase()} · ${ev.cond} · seed ${ev.seed} · ${ev.models?.worker || ''}`;
  badge.className = 'badge ' + (ev.mode || '');
  const el = document.createElement('div');
  el.className = 'sys end';
  el.textContent = `episode ${ev.run_id}${ev.replayed ? ' (replayed from the chain)' : ''}: coordinator ${ev.models?.coordinator}, workers ${ev.models?.worker}`;
  addEntry(el);
}
function onEnd(ev) {
  const ok = ev.chain_ok;
  const v = $('verdict');
  const parts = [ok ? 'chain verified ✓' : 'CHAIN FAILED ✖'];
  if (ev.tampered) parts.push(`shared log tampered: ${ev.edited?.length || 0} edited, ${ev.deleted?.length || 0} deleted, ${ev.forged || 0} forged, ${ev.reordered?.length || 0} reordered`);
  else parts.push('shared log matches the chain');
  v.textContent = parts.join(' · '); v.className = ok ? 'ok' : 'bad';
  if (!(ev.leaked || K.leak)) banner(ev.tampered ? 'no leak · tampering caught by reconciliation' : 'no leak', true);
  const s = ev.stats || {};
  const el = document.createElement('div');
  el.className = 'sys end';
  el.textContent = `■ end · leaked: ${ev.leaked || K.leak ? 'YES' : 'no'} · ${parts.join(' · ')} · delegations ${s.delegations ?? '-'}, ` +
    `real refusals ${s.refusals ?? '-'}, said-no-did-it ${s.said_no_did_it ?? '-'}, harmful delegations ${s.harmful_delegations ?? '-'}, re-routes ${s.reroutes ?? '-'}`;
  addEntry(el);
}
const HANDLERS = { episode_start: onStart, record: onRecord, shared: onShared, egress: onEgress, episode_end: onEnd };

// -------------------------------------------------------------- playback ----
let source = null, queue = [], paused = false, lastWall = null, timer = null, liveMode = false, currentRun = null, ended = false;
function speedFactor() { const s = parseFloat($('speed').value); return s === 0 ? 50 : s; }
function delayFor(ev) {
  if (parseFloat($('speed').value) === 0) return 0;
  const w = ev.wall ?? ev.rec?.t;
  let d = 0.08;
  if (w && lastWall) d = (w - lastWall) / speedFactor();
  if (w) lastWall = w;
  return Math.min(Math.max(d, 0.05), liveMode ? 0.4 : 1.6) * 1000;
}
function pump() {
  timer = null;
  if (paused || !queue.length) return;
  const ev = queue.shift();
  try { HANDLERS[ev.type]?.(ev); } catch (e) { console.error(e, ev); }
  if (ev.type === 'episode_end') ended = true;
  timer = setTimeout(pump, queue.length ? delayFor(queue[0]) : 0);
}
function open(runId, live = false) {
  if (source) source.close();
  if (timer) clearTimeout(timer);
  queue = []; lastWall = null; liveMode = live; currentRun = runId; ended = false;
  source = new EventSource(`/api/stream?run_id=${encodeURIComponent(runId)}`);
  source.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    if (ev.type === 'stream_end') { source.close(); return; }
    if (ev.type === 'error') { banner(ev.message); return; }
    queue.push(ev);
    if (!timer && !paused) pump();
  };
  source.onerror = () => { source.close(); };
}

// -------------------------------------------------------------- controls ----
async function loadEpisodes() {
  const eps = await (await fetch('/api/episodes')).json();
  const sel = $('episode');
  const keep = sel.value;
  sel.innerHTML = '';
  const groups = {};
  for (const e of eps) (groups[e.file] ??= []).push(e);
  for (const [file, list] of Object.entries(groups)) {
    const og = document.createElement('optgroup');
    og.label = file;
    for (const e of list) {
      const o = document.createElement('option');
      o.value = e.run_id;
      o.textContent = `${e.status === 'running' ? '● ' : ''}${e.mode} s${e.seed} · ${e.worker}${e.leaked ? ' · LEAK' : ''}${e.tampered ? ' · tampered' : ''} · ${e.n_records ?? '?'} rec`;
      og.appendChild(o);
    }
    sel.appendChild(og);
  }
  if (keep) sel.value = keep;
}
$('play').onclick = () => { liveFollow(false); paused = false; $('pause').textContent = 'Pause'; open($('episode').value, false); };
$('pause').onclick = () => {
  paused = !paused;
  $('pause').textContent = paused ? 'Resume' : 'Pause';
  if (!paused && !timer) pump();
};
let livePoll = null;
function liveFollow(on) {
  $('live').classList.toggle('on', on);
  if (livePoll) { clearInterval(livePoll); livePoll = null; }
  if (!on) return;
  const check = async () => {
    const { run_id } = await (await fetch('/api/latest')).json();
    if (run_id && run_id !== currentRun && (ended || !currentRun || !liveMode)) {
      loadEpisodes();
      open(run_id, true);
    }
  };
  check();
  livePoll = setInterval(check, 3000);
}
$('live').onclick = () => liveFollow(!$('live').classList.contains('on'));
$('show-results').onchange = () => {};

resize();
animate();
loadEpisodes().then(() => {
  const params = new URLSearchParams(location.search);
  if (params.get('run')) { $('episode').value = params.get('run'); open(params.get('run')); }
  else if (params.get('live')) liveFollow(true);
});
setInterval(loadEpisodes, 15000);
