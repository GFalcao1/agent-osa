/*
 * Micro-interactions of the panel, served at /motion.js (same origin, no inline script).
 *
 * Progressive enhancement only: every page is complete without this file. It mounts small
 * Lottie animations (lottie-web, light SVG build, served at /assets/lottie.min.js) over or
 * inside elements marked with data-lottie="<name>"; when the library or a file cannot be
 * loaded the static Phosphor icon and the text stay exactly as the server rendered them.
 *
 *   data-lottie="check"   data-lottie-mode="once"   success banner: draws once
 *   data-lottie="theme"   data-lottie-mode="theme"  sun/moon on the theme switch knob
 *   .nav a[aria-current]                            gets .wake after first paint: a CSS pop
 *                                                   (scale only) of the current section's icon
 *   form submit                                     spinner on the pressed button, second
 *                                                   submit refused while the first is in flight
 *   form[data-track="row"]                          the row's Save button (.btn-save) gets
 *                                                   .is-dirty while a field of that row differs
 *                                                   from what the server rendered; the buttons
 *                                                   and fields of a row are tied to its form
 *                                                   with the form="" attribute
 *   .brand (the shield)                             follows the mouse a little and pops softly
 *                                                   on press; a short pop on keyboard focus
 *
 * Reduced motion: nothing autoplays or loops; each animation jumps to its last frame (or the
 * resting frame of the theme switch) and stays still. The setting is followed live: switched
 * on, everything settles and the brand goes plain; switched off again, loops and the brand
 * resume without a reload (one-shot animations stay on their last frame). Loops also pause
 * while the tab is hidden or the element is off-screen, and at most MAX_PLAYING animations run
 * at once (a finished one lets the next waiting one start). The spinner loop pauses when the
 * tab is hidden, and the operating system's reduced-motion setting turns it off.
 * Saved banner: after showing the success sentence (?ok=<code>&id=<n>) or the folder notice
 * (?aviso=pastas), the query is dropped from the address bar with history.replaceState, so a
 * refresh or Back does not show the banner again for a change that is no longer fresh.
 * The brand mark never animates on its own: a damped spring runs only while the pointer is over
 * it, is pressed, or is settling back, and stops (no frame requested) once it is at rest.
 * No storage, no dynamic code, no markup built from strings, no style attribute: the only styling
 * done from here is CSS classes and CSS custom properties set through the CSSOM
 * (element.style.setProperty), which the CSP allows.
 */
(function () {
  "use strict";

  var win = window;
  var doc = document;
  var BASE = "/assets/lottie/";
  var NAMES = { check: 1, spinner: 1, theme: 1 };
  var MAX_PLAYING = 4;
  var BUSY_LIMIT_MS = 15000;
  var THEME_FRAMES = { light: 0, dark: 24 };

  var reduce = false;
  var mediaReduce = null;
  try {
    mediaReduce = win.matchMedia("(prefers-reduced-motion: reduce)");
    reduce = !!mediaReduce.matches;
  } catch (error) {
    reduce = false;
  }

  var handles = [];
  var cache = {};

  function lib() {
    var lottie = win.lottie;
    return lottie && typeof lottie.loadAnimation === "function" ? lottie : null;
  }

  function lastFrame(anim) {
    var total = anim.totalFrames;
    return typeof total === "number" && total > 1 ? total - 1 : 0;
  }

  /* One fetch per file and page; every mount parses its own copy (lottie edits its data). */
  function fetchText(name) {
    if (!Object.prototype.hasOwnProperty.call(NAMES, name)) {
      return Promise.resolve(null);
    }
    if (!cache[name]) {
      cache[name] = new Promise(function (resolve) {
        try {
          win
            .fetch(BASE + name + ".json", { credentials: "same-origin" })
            .then(function (response) {
              return response && response.ok ? response.text() : null;
            })
            .then(resolve, function () {
              resolve(null);
            });
        } catch (error) {
          resolve(null);
        }
      });
    }
    return cache[name];
  }

  /* ---------- scheduling: which animations may run right now ---------- */

  function playingCount() {
    var count = 0;
    for (var i = 0; i < handles.length; i += 1) {
      if (handles[i].playing) {
        count += 1;
      }
    }
    return count;
  }

  function wants(handle) {
    if (!handle.running || reduce || doc.hidden) {
      return false;
    }
    return handle.loop ? handle.inView : true;
  }

  function sync() {
    for (var i = 0; i < handles.length; i += 1) {
      var handle = handles[i];
      if (wants(handle)) {
        if (!handle.playing && playingCount() < MAX_PLAYING) {
          try {
            if (handle.segment) {
              var segment = handle.segment;
              handle.segment = null;
              handle.anim.playSegments(segment, true);
            } else if (handle.restart) {
              handle.restart = false;
              handle.anim.goToAndPlay(0, true);
            } else {
              handle.anim.play();
            }
            handle.playing = true;
          } catch (error) {
            handle.running = false;
          }
        }
      } else if (handle.playing) {
        try {
          handle.anim.pause();
        } catch (error) {
          /* a destroyed animation has nothing to pause */
        }
        handle.playing = false;
      }
    }
  }

  function settle(handle, frame) {
    handle.running = false;
    handle.playing = false;
    try {
      handle.anim.goToAndStop(frame, true);
    } catch (error) {
      /* nothing to settle */
    }
  }

  /* ---------- mounting ---------- */

  /*
   * Mounts animation `name` as a child layer of `host`. Resolves to a handle, or to null when
   * the library, the file or the host is unavailable (the static markup then stays as is).
   */
  function mount(host, name, options) {
    if (!host || typeof host.appendChild !== "function" || !lib()) {
      return Promise.resolve(null);
    }
    var loop = !!options.loop && !reduce;
    return fetchText(name).then(function (text) {
      var engine = lib();
      if (text === null || !engine) {
        return null;
      }
      var data;
      try {
        data = JSON.parse(text);
      } catch (error) {
        return null;
      }
      var layer = doc.createElement("span");
      layer.className = "lottie-layer";
      layer.setAttribute("aria-hidden", "true");
      host.appendChild(layer);
      var anim;
      try {
        anim = engine.loadAnimation({
          container: layer,
          renderer: "svg",
          loop: loop,
          autoplay: false,
          animationData: data,
          rendererSettings: { preserveAspectRatio: "xMidYMid meet" }
        });
      } catch (error) {
        anim = null;
      }
      if (!anim) {
        if (typeof host.removeChild === "function") {
          host.removeChild(layer);
        }
        return null;
      }
      var handle = {
        anim: anim,
        host: host,
        layer: layer,
        loop: loop,
        wantsLoop: !!options.loop,
        watch: options.watch !== false, /* false: always on screen (the pressed button) */
        inView: true,
        running: false,
        playing: false,
        segment: null,
        restart: false,
        rest: null /* theme switch: the frame to show when motion is off */
      };
      handles.push(handle);
      anim.addEventListener("complete", function () {
        handle.running = false;
        handle.playing = false;
        sync(); /* a slot is free: start the next waiting animation, if any */
      });
      return new Promise(function (resolve) {
        var done = false;
        var ready = function () {
          if (done) {
            return;
          }
          done = true;
          host.classList.add("lottie-on");
          resolve(handle);
        };
        anim.addEventListener("DOMLoaded", ready);
        if (anim.isLoaded) {
          ready();
        }
      });
    });
  }

  function watchVisibility(handle) {
    if (handle.watched || typeof win.IntersectionObserver !== "function") {
      return;
    }
    handle.watched = true;
    try {
      var observer = new win.IntersectionObserver(function (entries) {
        for (var i = 0; i < entries.length; i += 1) {
          handle.inView = !!entries[i].isIntersecting;
        }
        sync();
      });
      observer.observe(handle.host);
    } catch (error) {
      handle.inView = true;
    }
  }

  function begin(handle) {
    if (!handle) {
      return;
    }
    if (reduce) {
      settle(handle, lastFrame(handle.anim));
      return;
    }
    if (handle.wantsLoop) {
      handle.running = true;
      handle.inView = !handle.watch || typeof win.IntersectionObserver !== "function";
      if (handle.watch) {
        watchVisibility(handle);
      }
    } else {
      handle.running = true;
      handle.restart = true;
    }
    sync();
  }

  /* Reduced motion switched off again: loops play once more (one-shot ones stay settled). */
  function resume() {
    for (var i = 0; i < handles.length; i += 1) {
      var handle = handles[i];
      if (!handle.wantsLoop) {
        continue;
      }
      handle.loop = true;
      try {
        if (typeof handle.anim.setLoop === "function") {
          handle.anim.setLoop(true); /* mounted under reduced motion without looping */
        }
      } catch (error) {
        continue;
      }
      handle.running = true;
      if (handle.watch && !handle.watched) {
        handle.inView = typeof win.IntersectionObserver !== "function";
        watchVisibility(handle);
      }
    }
    sync();
  }

  /* ---------- hooks ---------- */

  function initDeclared() {
    var hosts = doc.querySelectorAll("[data-lottie]");
    for (var i = 0; i < hosts.length; i += 1) {
      var host = hosts[i];
      var name = host.getAttribute("data-lottie");
      var mode = host.getAttribute("data-lottie-mode");
      if (mode === "theme") {
        initTheme(host, name);
      } else {
        mount(host, name, { loop: mode === "loop" }).then(begin);
      }
    }
  }

  function isDark() {
    var pinned = doc.documentElement.getAttribute("data-theme");
    if (pinned === "light" || pinned === "dark") {
      return pinned === "dark";
    }
    try {
      return !!win.matchMedia("(prefers-color-scheme: dark)").matches;
    } catch (error) {
      return false;
    }
  }

  function initTheme(host, name) {
    var button = host.closest ? host.closest(".theme-switch") : null;
    var state = isDark() ? "dark" : "light";
    mount(host, name, { loop: false }).then(function (handle) {
      if (!handle) {
        return;
      }
      handle.rest = function () {
        return THEME_FRAMES[state];
      };
      settle(handle, handle.rest());
      if (!button || typeof win.MutationObserver !== "function") {
        return;
      }
      var observer = new win.MutationObserver(function () {
        var next = button.getAttribute("aria-pressed") === "true" ? "dark" : "light";
        if (next === state) {
          return;
        }
        var from = THEME_FRAMES[state];
        var to = THEME_FRAMES[next];
        state = next;
        if (reduce) {
          settle(handle, to);
          return;
        }
        handle.running = true;
        handle.playing = false;
        handle.segment = from < to ? [0, THEME_FRAMES.dark] : [THEME_FRAMES.dark, 2 * THEME_FRAMES.dark];
        sync();
      });
      observer.observe(button, { attributes: true, attributeFilter: ["aria-pressed"] });
    });
  }

  function initNav() {
    var nav = doc.querySelector(".nav");
    if (!nav) {
      return;
    }
    var current = nav.querySelector('a[aria-current="page"]');
    if (current && !reduce) {
      current.classList.add("wake"); /* CSS pop of the icon (scale only); after first paint */
    }
  }

  /* Drops the animation mounted on `host` (a restored page must not keep a spinner). */
  function release(host) {
    for (var i = handles.length - 1; i >= 0; i -= 1) {
      var handle = handles[i];
      if (handle.host === host) {
        handles.splice(i, 1);
        try {
          handle.anim.destroy();
        } catch (error) {
          /* already gone */
        }
        if (handle.layer.parentNode) {
          handle.layer.parentNode.removeChild(handle.layer);
        }
      }
    }
  }

  function initForms() {
    var forms = doc.querySelectorAll("form");
    if (forms.length) {
      fetchText("spinner"); /* warm the cache: the file is ~1.5 KB */
    }
    doc.addEventListener("submit", function (event) {
      var form = event.target;
      if (!form || !form.getAttribute) {
        return;
      }
      if (form.getAttribute("data-busy") === "true") {
        event.preventDefault(); /* a second click while the first POST is in flight */
        return;
      }
      form.setAttribute("data-busy", "true");
      var button = event.submitter || (form.querySelector ? form.querySelector('[type="submit"]') : null);
      /* If the navigation is cancelled (Esc, network), unlock the form after a while. Set
         before anything else can return, so a form never stays locked without it. */
      win.setTimeout(function () {
        form.removeAttribute("data-busy");
        if (button) {
          button.removeAttribute("aria-busy");
          release(button);
        }
      }, BUSY_LIMIT_MS);
      if (!button) {
        return;
      }
      button.setAttribute("aria-busy", "true");
      mount(button, "spinner", { loop: true, watch: false }).then(function (handle) {
        if (handle && form.getAttribute("data-busy") === "true") {
          begin(handle);
        }
      });
    });
    /* Back/forward cache: a restored page must not stay locked. */
    win.addEventListener("pageshow", function (event) {
      if (!event || !event.persisted) {
        return;
      }
      var busy = doc.querySelectorAll('form[data-busy="true"]');
      for (var i = 0; i < busy.length; i += 1) {
        busy[i].removeAttribute("data-busy");
      }
      var buttons = doc.querySelectorAll('[aria-busy="true"]');
      for (var j = 0; j < buttons.length; j += 1) {
        buttons[j].removeAttribute("aria-busy");
        release(buttons[j]);
      }
    });
  }


  /* ---------- brand mark: spring that follows the mouse, soft pop on press ---------- */

  /*
   * One damped spring per channel (x, y, tilt, scale), stepped by requestAnimationFrame only
   * while there is something to move. stiffness 150 / damping 17 gives ~0.7 damping ratio: a
   * subtle overshoot, settling in about half a second, and any change of target (the mouse
   * leaves mid-motion) continues from the current position and speed, so nothing jumps.
   * The CSS consumes --mx, --my, --tilt and --lift as a transform (no layout shift).
   */
  var BRAND = {
    stiffness: 150,
    damping: 17,
    maxX: 3.2, /* px */
    maxY: 3.2, /* px */
    maxTilt: 6.5, /* degrees */
    hover: 1.08,
    press: 0.93,
    pop: 1.1, /* keyboard focus */
    popMs: 160,
    reach: 1.3 /* pointer distance, in mark sizes, that counts as full deflection */
  };

  var brandOff = function () {};
  var brandOn = null; /* set once the brand is wired; null: wire it on demand */

  function clamp(value, low, high) {
    return value < low ? low : value > high ? high : value;
  }

  function initBrand() {
    if (reduce || typeof win.requestAnimationFrame !== "function") {
      return;
    }
    var anchor = doc.querySelector(".brand");
    var mark = anchor && anchor.querySelector ? anchor.querySelector("svg") : null;
    if (!mark || !mark.style || typeof mark.style.setProperty !== "function") {
      return;
    }
    var pos = { x: 0, y: 0, r: 0, k: 1 };
    var vel = { x: 0, y: 0, r: 0, k: 0 };
    var goal = { x: 0, y: 0, r: 0, k: 1 };
    var over = false; /* a mouse is over the brand */
    var pressed = false;
    var pointer = null;
    var popPending = false;
    var popUntil = 0;
    var frame = 0;
    var last = 0;
    var enabled = true;
    anchor.classList.add("live");

    function human(event) {
      var type = event && event.pointerType;
      return type !== "touch" && type !== "pen"; /* touch and pen: the press pop only */
    }

    function request() {
      if (!frame && enabled) {
        frame = win.requestAnimationFrame(step);
      }
    }

    function activate() {
      anchor.classList.add("brand-active"); /* will-change: transform, only now */
    }

    function aim(now) {
      if (over && pointer) {
        /* The mark's box includes its own transform: take the current translation and
           scale back out to find where its resting centre is. */
        var rect = mark.getBoundingClientRect();
        var size = rect.width / pos.k || 32;
        var cx = rect.left + rect.width / 2 - pos.x;
        var cy = rect.top + rect.height / 2 - pos.y;
        var dx = clamp((pointer.x - cx) / (size * BRAND.reach), -1, 1);
        var dy = clamp((pointer.y - cy) / (size * BRAND.reach), -1, 1);
        goal.x = dx * BRAND.maxX;
        goal.y = dy * BRAND.maxY;
        goal.r = dx * BRAND.maxTilt;
      } else {
        goal.x = 0;
        goal.y = 0;
        goal.r = 0;
      }
      if (popPending) {
        popPending = false;
        popUntil = now + BRAND.popMs;
      }
      if (pressed) {
        goal.k = BRAND.press;
      } else if (now < popUntil) {
        goal.k = BRAND.pop;
      } else {
        goal.k = over ? BRAND.hover : 1;
      }
    }

    function integrate(dt) {
      var keys = ["x", "y", "r", "k"];
      for (var i = 0; i < keys.length; i += 1) {
        var key = keys[i];
        var accel = -BRAND.stiffness * (pos[key] - goal[key]) - BRAND.damping * vel[key];
        vel[key] += accel * dt;
        pos[key] += vel[key] * dt;
      }
    }

    function atRest() {
      var keys = ["x", "y", "r", "k"];
      for (var i = 0; i < keys.length; i += 1) {
        var key = keys[i];
        if (Math.abs(pos[key] - goal[key]) > 0.01 || Math.abs(vel[key]) > 0.05) {
          return false;
        }
      }
      return true;
    }

    function paint() {
      var style = mark.style;
      style.setProperty("--mx", pos.x.toFixed(2) + "px");
      style.setProperty("--my", pos.y.toFixed(2) + "px");
      style.setProperty("--tilt", pos.r.toFixed(2) + "deg");
      style.setProperty("--lift", pos.k.toFixed(3));
    }

    function clear() {
      var names = ["--mx", "--my", "--tilt", "--lift"];
      for (var i = 0; i < names.length; i += 1) {
        mark.style.removeProperty(names[i]);
      }
      anchor.classList.remove("brand-active");
      pos.x = pos.y = pos.r = 0;
      pos.k = 1;
      vel.x = vel.y = vel.r = vel.k = 0;
    }

    function step(now) {
      frame = 0;
      if (!enabled) {
        return;
      }
      var dt = last ? Math.min((now - last) / 1000, 1 / 30) : 1 / 60;
      last = now;
      aim(now);
      integrate(dt / 2);
      integrate(dt / 2);
      var idle = atRest() && now >= popUntil;
      if (idle && !over && !pressed && !popPending) {
        clear();
        last = 0;
        return; /* at rest: no more frames until the pointer comes back */
      }
      paint();
      if (idle) {
        last = 0; /* resting under the pointer: wait for the next move */
        return;
      }
      request();
    }

    anchor.addEventListener("pointerenter", function (event) {
      if (!enabled || !human(event)) {
        return;
      }
      over = true;
      pointer = { x: event.clientX, y: event.clientY };
      activate();
      request();
    });
    anchor.addEventListener("pointermove", function (event) {
      if (!enabled || !human(event)) {
        return;
      }
      over = true;
      pointer = { x: event.clientX, y: event.clientY }; /* applied once per frame */
      activate();
      request();
    });
    anchor.addEventListener("pointerleave", function (event) {
      if (!enabled) {
        return;
      }
      pressed = false; /* dragged off while pressed: the press is cancelled, like the click */
      if (human(event)) {
        over = false;
        pointer = null;
      }
      request();
    });
    anchor.addEventListener("pointerdown", function () {
      if (!enabled) {
        return;
      }
      pressed = true;
      activate();
      request();
    });
    var release = function () {
      if (!pressed) {
        return;
      }
      pressed = false;
      request();
    };
    /* On the window: a release over another element (no implicit capture for a mouse) or a
       cancelled touch must still end the press. */
    anchor.addEventListener("pointerup", release);
    anchor.addEventListener("pointercancel", release);
    win.addEventListener("pointerup", release);
    win.addEventListener("pointercancel", release);
    anchor.addEventListener("focusin", function () {
      var visible = false;
      try {
        visible = !!(anchor.matches && anchor.matches(":focus-visible"));
      } catch (error) {
        visible = false;
      }
      if (enabled && visible) {
        popPending = true; /* keyboard focus: the pop only, no tracking */
        activate();
        request();
      }
    });

    brandOff = function () {
      enabled = false;
      over = pressed = popPending = false;
      pointer = null;
      if (frame && typeof win.cancelAnimationFrame === "function") {
        win.cancelAnimationFrame(frame);
      }
      frame = 0;
      last = 0;
      anchor.classList.remove("live");
      clear();
    };
    brandOn = function () {
      enabled = true;
      anchor.classList.add("live");
    };
  }

  /* Drops the ?ok=<code>&id=<n> / ?aviso=pastas query once its banner is on screen (see header). */
  var BANNER_QUERY = /^\?(?:ok=[a-z]+-[a-z]+(?:&id=[0-9]+)?(?:&aviso=pastas)?|aviso=pastas)$/;

  function forgetBanner() {
    try {
      var location = win.location;
      var shown = doc.querySelector(".alert-success") || doc.querySelector(".alert-info");
      if (!shown || !location || !BANNER_QUERY.test(location.search)) {
        return;
      }
      var history = win.history;
      if (history && typeof history.replaceState === "function") {
        history.replaceState(history.state, "", location.pathname + location.hash);
      }
    } catch (error) {
      /* the address bar keeps the query; the page itself is unaffected */
    }
  }

  function onReduceChange() {
    reduce = !!(mediaReduce && mediaReduce.matches);
    if (reduce) {
      brandOff();
      for (var i = 0; i < handles.length; i += 1) {
        var handle = handles[i];
        handle.loop = false;
        settle(handle, handle.rest ? handle.rest() : lastFrame(handle.anim));
      }
      return;
    }
    if (brandOn) {
      brandOn();
    } else {
      initBrand(); /* loaded under reduced motion: wire it now */
    }
    resume();
  }

  /* ---------- rows: the Save button lights up only when its row changed ---------- */

  function controlChanged(control) {
    var type = control.type;
    if (type === "checkbox" || type === "radio") {
      return control.checked !== control.defaultChecked;
    }
    if (control.options) {
      for (var i = 0; i < control.options.length; i += 1) {
        if (control.options[i].selected !== control.options[i].defaultSelected) {
          return true;
        }
      }
      return false;
    }
    if (typeof control.defaultValue === "string") {
      return control.value !== control.defaultValue;
    }
    return false;
  }

  function isRowForm(form) {
    return !!form && typeof form.getAttribute === "function" && form.getAttribute("data-track") === "row";
  }

  /* form.elements also lists the controls and buttons tied to the form with form="id". */
  function refreshRow(form) {
    var controls = form.elements || [];
    var changed = false;
    var i;
    for (i = 0; i < controls.length && !changed; i += 1) {
      var type = controls[i].type;
      if (type !== "hidden" && type !== "submit" && type !== "button") {
        changed = controlChanged(controls[i]);
      }
    }
    for (i = 0; i < controls.length; i += 1) {
      var button = controls[i];
      if (button.type === "submit" && button.classList && button.classList.contains("btn-save")) {
        if (changed) {
          button.classList.add("is-dirty");
        } else {
          button.classList.remove("is-dirty");
        }
      }
    }
  }

  function refreshAllRows() {
    var forms = doc.querySelectorAll('form[data-track="row"]');
    for (var i = 0; i < forms.length; i += 1) {
      refreshRow(forms[i]);
    }
  }

  function initRows() {
    var onEdit = function (event) {
      var form = event && event.target ? event.target.form : null;
      if (isRowForm(form)) {
        refreshRow(form);
      }
    };
    doc.addEventListener("input", onEdit);
    doc.addEventListener("change", onEdit);
    refreshAllRows(); /* the browser may restore edited values on reload */
    win.addEventListener("pageshow", function (event) {
      if (event && event.persisted) {
        refreshAllRows();
      }
    });
  }

  function init() {
    forgetBanner();
    initRows();
    initBrand();
    if (mediaReduce && typeof mediaReduce.addEventListener === "function") {
      mediaReduce.addEventListener("change", onReduceChange);
    }
    if (!lib()) {
      return; /* no library: the page keeps its static icons */
    }
    initDeclared();
    initNav();
    initForms();
    doc.addEventListener("visibilitychange", sync);
  }

  if (doc.readyState === "loading") {
    doc.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
