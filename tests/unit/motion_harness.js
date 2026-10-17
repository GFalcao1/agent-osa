/* Node harness for tests/unit/test_motion_script.py: runs /motion.js against a fake DOM
 * and a fake Lottie engine. Usage: node motion_harness.js <motion.js> <scenario> <json config>.
 * Test-only; never served. */
const fs = require("fs");
const vm = require("vm");
const script = fs.readFileSync(process.argv[2], "utf8");
const scenario = process.argv[3];

function makeEl(tag, attrs) {
  const e = {
    tag, attrs: Object.assign({}, attrs || {}), children: [], parent: null,
    classes: new Set(), listeners: {}, qs: {}, closestMap: {}, className: "",
  };
  e.getAttribute = (n) => (n in e.attrs ? e.attrs[n] : null);
  e.setAttribute = (n, v) => { e.attrs[n] = String(v); };
  e.removeAttribute = (n) => { delete e.attrs[n]; };
  e.appendChild = (c) => { c.parent = e; e.children.push(c); return c; };
  e.removeChild = (c) => { e.children = e.children.filter((x) => x !== c); c.parent = null; };
  Object.defineProperty(e, "parentNode", { get: () => e.parent });
  e.classList = {
    add: (c) => e.classes.add(c), remove: (c) => e.classes.delete(c),
    contains: (c) => e.classes.has(c),
  };
  e.addEventListener = (t, f) => { (e.listeners[t] = e.listeners[t] || []).push(f); };
  e.closest = (sel) => (sel in e.closestMap ? e.closestMap[sel] : null);
  e.querySelector = (sel) => (sel in e.qs ? e.qs[sel] : null);
  return e;
}

const cfg = JSON.parse(process.argv[4] || "{}");
const anims = [];
const timers = [];
const fetched = [];
const docListeners = {};
const winListeners = {};
const observers = [];
const mutations = [];
let reduceMatches = !!cfg.reduce;
const mediaListeners = [];
const flipReduce = (v) => { reduceMatches = v; mediaListeners.forEach((f) => f({ matches: v })); };
const historyCalls = [];

const engine = cfg.noLib ? undefined : {
  loadAnimation(opts) {
    if (cfg.loadThrows) throw new Error("engine failure");
    const a = {
      opts, calls: [], totalFrames: 50, isLoaded: false, listeners: {}, destroyed: false,
      name: opts.animationData && opts.animationData.nm,
    };
    a.addEventListener = (t, f) => { (a.listeners[t] = a.listeners[t] || []).push(f); };
    for (const m of ["play", "pause", "goToAndStop", "goToAndPlay", "playSegments", "setLoop"]) {
      a[m] = (...args) => { a.calls.push([m, ...args]); };
    }
    a.destroy = () => { a.destroyed = true; };
    anims.push(a);
    return a;
  },
};

const rafQueue = [];
let rafCalls = 0;
const win = {
  lottie: engine,
  requestAnimationFrame: cfg.noRaf ? undefined : (cb) => { rafCalls += 1; rafQueue.push(cb); return rafCalls; },
  matchMedia: (q) => ({
    get matches() { return q.indexOf("reduced-motion") >= 0 ? reduceMatches : false; },
    addEventListener: (t, f) => { if (q.indexOf("reduced-motion") >= 0) mediaListeners.push(f); },
  }),
  fetch: (url) => {
    fetched.push(url);
    if (cfg.fetch === "reject") return Promise.reject(new Error("offline"));
    if (cfg.fetch === "notok") return Promise.resolve({ ok: false });
    const name = String(url).split("/").pop().replace(".json", "");
    const text = cfg.fetch === "badjson" ? "{nope" : JSON.stringify({ nm: name });
    return Promise.resolve({ ok: true, text: () => Promise.resolve(text) });
  },
  setTimeout: (fn) => { timers.push(fn); return timers.length; },
  location: { pathname: "/departments", search: cfg.search || "", hash: cfg.hash || "" },
  history: {
    state: null,
    replaceState: (...args) => {
      if (cfg.historyThrows) throw new Error("SecurityError");
      historyCalls.push(args);
    },
  },
  addEventListener: (t, f) => { (winListeners[t] = winListeners[t] || []).push(f); },
};
if (cfg.io !== false) {
  win.IntersectionObserver = function (cb) {
    const o = { cb, targets: [], observe: (t) => o.targets.push(t) };
    observers.push(o);
    return o;
  };
}
win.MutationObserver = function (cb) {
  const o = { cb, observe: () => {} };
  mutations.push(o);
  return o;
};

const docEl = makeEl("html");
const hosts = [];
const rowForms = [];
const lists = { "[data-lottie]": hosts, form: [], "[type=\"submit\"]": [], 'form[data-track="row"]': rowForms };
const doc = {
  readyState: "complete", hidden: !!cfg.hiddenAtStart, documentElement: docEl,
  createElement: (t) => makeEl(t),
  querySelectorAll: (sel) => {
    const has = (e, n) => e.getAttribute(n) === "true";
    if (sel === 'form[data-busy="true"]') return [form].filter((f) => has(f, "data-busy"));
    if (sel === '[aria-busy="true"]') return [button].filter((b) => has(b, "aria-busy"));
    return lists[sel] || [];
  },
  querySelector: (sel) => {
    if (sel === ".nav") return nav;
    if (sel === ".brand") return brand;
    if (sel === ".alert-success" && cfg.banner === "success") return makeEl("div");
    if (sel === ".alert-info" && cfg.banner === "info") return makeEl("div");
    return null;
  },
  addEventListener: (t, f) => { (docListeners[t] = docListeners[t] || []).push(f); },
};
const fire = (t, ev) => (docListeners[t] || []).forEach((f) => f(ev));

let nav = null;
let brand = null;
const props = {};
const propLog = [];
const mark = makeEl("svg");
mark.offsetWidth = 32; mark.offsetLeft = 0; mark.offsetTop = 0;
mark.style = {
  setProperty: (n, v) => { props[n] = v; propLog.push([n, v]); },
  removeProperty: (n) => { delete props[n]; propLog.push([n, null]); },
};
if (cfg.brand) {
  brand = makeEl("a");
  brand.offsetLeft = 0; brand.offsetTop = 0;
  brand.qs.svg = mark;
  mark.getBoundingClientRect = () => {
    const mx = parseFloat(props["--mx"] || "0"), my = parseFloat(props["--my"] || "0");
    const k = parseFloat(props["--lift"] || "1");
    return { left: 100 + mx - (32 * k - 32) / 2, top: 100 + my - (32 * k - 32) / 2, width: 32 * k, height: 32 * k };
  };
  brand.getBoundingClientRect = () => ({ left: 100, top: 100, width: 150, height: 40 });
  brand.matches = (sel) => sel === ":focus-visible" && !!cfg.focusVisible;
}
const fireBrand = (type, ev) => (brand.listeners[type] || []).forEach((f) => f(ev));
let clock = 1000;
function frames(n) {
  const rows = [];
  for (let i = 0; i < n && rafQueue.length; i += 1) {
    clock += 16.7;
    const cb = rafQueue.shift();
    cb(clock);
    rows.push({
      x: parseFloat(props["--mx"] || "0"), y: parseFloat(props["--my"] || "0"),
      r: parseFloat(props["--tilt"] || "0"), k: parseFloat(props["--lift"] || "1"),
    });
  }
  return rows;
}
const mouse = (x, y) => ({ clientX: x, clientY: y, pointerType: "mouse" });
const span = (rows, key) => rows.map((r) => r[key]);
const maxAbs = (rows, key) => Math.max(...span(rows, key).map(Math.abs));
const maxStep = (rows, key) => rows.slice(1).reduce((m, r, i) => Math.max(m, Math.abs(r[key] - rows[i][key])), 0);
const button = makeEl("button", { type: "submit" });
const form = makeEl("form");
form.qs['[type="submit"]'] = button;
let themeButton = null;
const links = [];

function addHost(name, mode, extra) {
  const h = makeEl("span", { "data-lottie": name });
  if (mode) h.attrs["data-lottie-mode"] = mode;
  if (extra) extra(h);
  hosts.push(h);
  return h;
}
for (const spec of cfg.hosts || []) {
  if (spec.kind === "theme") {
    themeButton = makeEl("button", { "aria-pressed": "false" });
    addHost("theme", "theme", (h) => { h.closestMap[".theme-switch"] = themeButton; });
  } else {
    addHost(spec.name, spec.mode);
  }
}
if (cfg.nav) {
  nav = makeEl("nav");
  for (let i = 0; i < 3; i += 1) {
    const link = makeEl("a");
    link.qs[".nav-ico"] = makeEl("span");
    link.closestMap[".nav a"] = link;
    links.push(link);
  }
  nav.qs['a[aria-current="page"]'] = links[0];
}
if (cfg.forms) lists.form = [form];

// Row forms: the controls and the Save button are tied to the form (form.elements), as with
// the form="id" attribute; spec: {controls: [{kind, ...}], track: bool}.
const rowControls = {};
function addRow(name, spec) {
  const f = makeEl("form", spec.track === false ? {} : { "data-track": "row" });
  const elements = [];
  for (const c of spec.controls) {
    const e = makeEl(c.kind === "save" ? "button" : "input", {});
    e.form = f;
    if (c.kind === "hidden") { e.type = "hidden"; e.value = e.defaultValue = "token"; }
    if (c.kind === "text") { e.type = "text"; e.defaultValue = c.value; e.value = c.current || c.value; }
    if (c.kind === "checkbox") { e.type = "checkbox"; e.defaultChecked = !!c.checked; e.checked = !!c.checked; }
    if (c.kind === "select") {
      e.type = "select-one";
      e.options = c.options.map((o) => ({ selected: !!o.selected, defaultSelected: !!o.selected }));
    }
    if (c.kind === "save" || c.kind === "other") {
      e.type = c.kind === "save" ? "submit" : "button";
      if (c.kind === "save") e.classes.add("btn-save");
    }
    elements.push(e);
    (rowControls[name] = rowControls[name] || []).push(e);
  }
  f.elements = elements;
  if (spec.track !== false) rowForms.push(f);
  return f;
}
for (const [name, spec] of Object.entries(cfg.rows || {})) addRow(name, spec);
const saveOf = (name) => rowControls[name].find((e) => e.classes.has("btn-save") || e.type === "submit");
const dirty = (name) => saveOf(name).classes.has("is-dirty");

async function flush() { for (let i = 0; i < 6; i += 1) await new Promise((r) => setImmediate(r)); }
function loaded() { anims.forEach((a) => (a.listeners.DOMLoaded || []).forEach((f) => f())); }
const summary = () => anims.map((a) => ({
  name: a.name, loop: a.opts.loop, autoplay: a.opts.autoplay, calls: a.calls.slice(),
  destroyed: a.destroyed,
}));
const verbs = (a) => a.calls.map((c) => c[0]);

(async () => {
  const out = { errors: [] };
  try {
    vm.runInNewContext(script, { window: win, document: doc });
    await flush(); loaded(); await flush();
    out.afterLoad = summary();
    out.hostClasses = hosts.map((h) => Array.from(h.classes));
    out.fetched = fetched.slice();
    out.docListeners = Object.keys(docListeners);
    if (scenario === "visibility") {
      const io = observers.slice();
      io.forEach((o) => o.cb(o.targets.map(() => ({ isIntersecting: true }))));
      out.visible = summary().map((a) => verbs(a));
      io[0] && io[0].cb([{ isIntersecting: false }]);
      out.offscreen = summary().map((a) => verbs(a));
      io[0] && io[0].cb([{ isIntersecting: true }]);
      doc.hidden = true; fire("visibilitychange", {});
      out.hidden = summary().map((a) => verbs(a));
      doc.hidden = false; fire("visibilitychange", {});
      out.back = summary().map((a) => verbs(a));
    }
    if (scenario === "cap") {
      observers.forEach((o) => o.cb(o.targets.map(() => ({ isIntersecting: true }))));
      const live = summary().filter(
        (a) => a.calls.some((c) => c[0] === "play" || c[0] === "goToAndPlay"));
      out.started = live.length;
    }
    if (scenario === "theme") {
      const flip = (v) => { themeButton.attrs["aria-pressed"] = v; mutations[0].cb([]); };
      flip("true"); out.toDark = summary().find((a) => a.name === "theme").calls.slice();
      flip("false"); out.toLight = summary().find((a) => a.name === "theme").calls.slice();
      flip("false"); out.noop = summary().find((a) => a.name === "theme").calls.length;
    }
    if (scenario.startsWith("brand")) {
      out.live = brand ? brand.classes.has("live") : null;
      out.rafAtStart = rafQueue.length;
      out.listeners = brand ? Object.keys(brand.listeners).sort() : [];
    }
    if (scenario === "brand-track") {
      fireBrand("pointerenter", mouse(20, 20));
      for (let i = 0; i < 50; i += 1) fireBrand("pointermove", mouse(20 - i * 0.1, 20));
      out.queuedAfterManyMoves = rafQueue.length; // throttled: one frame, not fifty
      out.active = brand.classes.has("brand-active");
      const tl = frames(70);
      fireBrand("pointermove", mouse(600, 400));
      const br = frames(70);
      fireBrand("pointermove", mouse(70, 500));
      const mid = frames(70);
      out.tl = tl[tl.length - 1]; out.br = br[br.length - 1]; out.mid = mid[mid.length - 1];
      const all = tl.concat(br, mid);
      out.limits = { x: maxAbs(all, "x"), y: maxAbs(all, "y"), r: maxAbs(all, "r"),
        kMax: Math.max(...span(all, "k")), kMin: Math.min(...span(all, "k")) };
      out.steps = { x: maxStep(all, "x"), y: maxStep(all, "y"), r: maxStep(all, "r"), k: maxStep(all, "k") };
      out.queuedWhileResting = rafQueue.length; // resting under the pointer: no frame requested
      fireBrand("pointerleave", mouse(600, 400));
      const back = frames(120);
      out.back = back[back.length - 1];
      out.backFirst = back[0];
      out.propsAfterRest = Object.assign({}, props);
      out.activeAfterRest = brand.classes.has("brand-active");
      out.queuedAfterRest = rafQueue.length;
      out.usesOnlyCustomProps = propLog.every((p) => p[0].startsWith("--"));
    }
    if (scenario === "brand-centre") {
      fireBrand("pointerenter", mouse(116, 116));
      const rows = frames(60);
      out.x = maxAbs(rows, "x"); out.y = maxAbs(rows, "y"); out.r = maxAbs(rows, "r");
      fireBrand("pointermove", mouse(116 + 20.8, 116));
      const half = frames(60);
      out.half = half[half.length - 1];
    }
    if (scenario === "brand-touch") {
      const touch = (type, x, y) => fireBrand(type, { clientX: x, clientY: y, pointerType: type2 });
      let type2 = "touch";
      for (const kind of ["touch", "pen"]) {
        type2 = kind;
        touch("pointerenter", 95, 95); touch("pointermove", 95, 95);
        out["queued_" + kind] = rafQueue.length;
        touch("pointerdown", 95, 95);
        const down = frames(30);
        touch("pointerup", 95, 95);
        const up = frames(120);
        out["press_" + kind] = { minK: Math.min(...span(down, "k")), x: maxAbs(down.concat(up), "x"),
          y: maxAbs(down.concat(up), "y"), r: maxAbs(down.concat(up), "r"), end: up[up.length - 1] || null,
          propsLeft: Object.keys(props).length, queued: rafQueue.length };
      }
    }
    if (scenario === "brand-press") {
      fireBrand("pointerenter", mouse(130, 120));
      const hover = frames(60);
      fireBrand("pointerdown", mouse(130, 120));
      const down = frames(40);
      fireBrand("pointerup", mouse(130, 120));
      const up = frames(60);
      out.hoverK = hover[hover.length - 1].k;
      out.minK = Math.min(...span(down, "k"));
      out.maxKAfterRelease = Math.max(...span(up, "k"));
      out.endK = up[up.length - 1].k;
      out.steps = { k: maxStep(down.concat(up), "k") };
    }
    if (scenario === "brand-focus") {
      fireBrand("focusin", {});
      const rows = frames(80);
      out.rows = rows.length;
      out.maxK = Math.max(...span(rows, "k"));
      out.xyr = Math.max(maxAbs(rows, "x"), maxAbs(rows, "y"), maxAbs(rows, "r"));
      const settle = frames(80);
      out.propsAfter = Object.assign({}, props);
      out.queuedAfter = rafQueue.length;
    }
    if (scenario === "submit") {
      const ev = () => ({ target: form, submitter: button, defaultPrevented: false,
        preventDefault() { this.defaultPrevented = true; } });
      const first = ev(); fire("submit", first);
      out.firstPrevented = first.defaultPrevented;
      out.busy = [form.getAttribute("data-busy"), button.getAttribute("aria-busy")];
      await flush(); loaded(); await flush();
      out.spinner = summary().filter((a) => a.name === "spinner");
      const second = ev(); fire("submit", second);
      out.secondPrevented = second.defaultPrevented;
      (winListeners.pageshow || []).forEach((f) => f({ persisted: true }));
      out.afterRestore = [form.getAttribute("data-busy"), button.getAttribute("aria-busy")];
      out.spinnerDestroyed = summary().filter((a) => a.name === "spinner")
        .every((a) => a.destroyed);
      const third = ev(); fire("submit", third);
      out.thirdPrevented = third.defaultPrevented;
      out.timerCount = timers.length;
    }
    if (scenario === "brand-drag-out") {
      // Mouse pressed on the mark, dragged away, released outside: no pointerup reaches it.
      fireBrand("pointerenter", mouse(116, 116));
      fireBrand("pointerdown", mouse(116, 116));
      const down = frames(30);
      out.sunk = Math.min(...span(down, "k"));
      fireBrand("pointerleave", mouse(400, 400));
      frames(200);
      out.leaveProps = Object.assign({}, props);
      out.leaveActive = brand.classes.has("brand-active");
      out.leaveQueued = rafQueue.length;
      // Touch press cancelled by the browser (scroll): the cancel arrives on the window.
      const touch = { clientX: 116, clientY: 116, pointerType: "touch" };
      fireBrand("pointerdown", touch);
      frames(30);
      (winListeners.pointercancel || []).forEach((f) => f(touch));
      frames(200);
      out.cancelProps = Object.assign({}, props);
      out.cancelActive = brand.classes.has("brand-active");
      out.cancelQueued = rafQueue.length;
      // Mouse press released over another element: the pointerup arrives on the window.
      fireBrand("pointerdown", mouse(116, 116));
      frames(30);
      (winListeners.pointerup || []).forEach((f) => f(mouse(400, 400)));
      frames(200);
      out.upProps = Object.assign({}, props);
      out.upQueued = rafQueue.length;
      out.winEvents = Object.keys(winListeners).sort();
    }
    if (scenario === "reduce-flip") {
      const floatAnim = () => summary().find((a) => a.name === "spinner");
      const play = () => observers.forEach((o) => o.cb(o.targets.map(() => ({ isIntersecting: true }))));
      play();
      out.start = verbs(floatAnim());
      out.liveStart = brand.classes.has("live");
      flipReduce(true);
      out.on = verbs(floatAnim());
      out.onLast = floatAnim().calls[floatAnim().calls.length - 1];
      out.liveOn = brand.classes.has("live");
      fireBrand("pointerenter", mouse(20, 20));
      out.queuedOn = rafQueue.length;
      flipReduce(false);
      play();
      out.off = verbs(floatAnim());
      out.liveOff = brand.classes.has("live");
      fireBrand("pointerenter", mouse(20, 20));
      out.queuedOff = rafQueue.length;
      frames(400); fireBrand("pointerleave", mouse(600, 600)); frames(400);
      out.restAfter = Object.assign({}, props);
      out.wake = Array.from(links.length ? links[0].classes : []);
      out.media = mediaListeners.length;
    }
    if (scenario === "cap-complete") {
      const started = () => summary().filter(
        (a) => a.calls.some((c) => c[0] === "goToAndPlay")).length;
      out.before = started();
      const first = anims.find((a) => a.calls.some((c) => c[0] === "goToAndPlay"));
      (first.listeners.complete || []).forEach((f) => f());
      out.after = started();
    }
    if (scenario === "submit-nobutton") {
      delete form.qs['[type="submit"]'];
      const ev = () => ({ target: form, submitter: null, defaultPrevented: false,
        preventDefault() { this.defaultPrevented = true; } });
      fire("submit", ev());
      out.busy = form.getAttribute("data-busy");
      out.timers = timers.length;
      timers.forEach((f) => f());
      out.afterTimer = form.getAttribute("data-busy");
      const again = ev(); fire("submit", again);
      out.againPrevented = again.defaultPrevented;
    }
    if (scenario === "rows") {
      const [text, checkbox, select] = ["text", "checkbox", "select-one"].map(
        (t) => rowControls.a.find((e) => e.type === t));
      out.start = { a: dirty("a"), b: dirty("b") };
      text.value = "changed"; fire("input", { target: text });
      out.afterText = dirty("a");
      out.otherRowUntouched = dirty("b");
      text.value = text.defaultValue; fire("input", { target: text });
      out.afterRevert = dirty("a");
      checkbox.checked = true; fire("change", { target: checkbox });
      out.afterCheckbox = dirty("a");
      checkbox.checked = false; fire("change", { target: checkbox });
      select.options[1].selected = true; select.options[0].selected = false;
      fire("change", { target: select });
      out.afterSelect = dirty("a");
      select.options[1].selected = false; select.options[0].selected = true;
      fire("change", { target: select });
      out.afterSelectRevert = dirty("a");
      // events from controls of a form that is not a row, or without a form: ignored
      const loose = makeEl("input", {}); loose.type = "text"; loose.form = null;
      fire("input", { target: loose });
      if (rowControls.plain) {
        const plain = rowControls.plain[0]; plain.value = "x"; fire("input", { target: plain });
        out.plainDirty = rowControls.plain.some((e) => e.classes.has("is-dirty"));
      }
      fire("input", {}); fire("input", { target: null });
      // bfcache restore: values changed without an event, then the page is shown again
      text.value = "restored";
      (winListeners.pageshow || []).forEach((f) => f({ persisted: true }));
      out.afterRestore = dirty("a");
      (winListeners.pageshow || []).forEach((f) => f({ persisted: false }));
      out.listeners = Object.keys(docListeners).filter((k) => k === "input" || k === "change");
    }
    if (scenario === "banner") {
      out.history = historyCalls;
    }
    if (scenario === "nav") {
      out.timers = timers.length;
      timers.forEach((f) => f());
      await flush(); loaded(); await flush();
      out.anims = summary().length;
      out.wake = Array.from(links[0].classes);
      (nav.listeners.mouseover || []).forEach((f) => f({ target: links[1] }));
      (nav.listeners.focusin || []).forEach((f) => f({ target: links[1] }));
      await flush(); loaded(); await flush();
      out.animsAfterHover = summary().length;
      out.navFocus = Object.keys(nav.listeners);
    }
  } catch (error) {
    out.errors.push(String(error && error.stack || error));
  }
  process.stdout.write(JSON.stringify(out));
})();
