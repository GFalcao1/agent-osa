"""Script of the light/dark switch, served by ``/theme.js``.

The CSP has no inline script (``script-src 'self'``), so the logic is one small file served
by the panel itself and loaded from ``<head>`` without ``defer``/``async``: the saved choice
is applied to ``<html>`` before the first paint (no flash of the wrong theme). It only reads
and writes one ``localStorage`` key, every access wrapped in try/catch (without storage the
switch still works for the current page: the choice is kept in memory, so a change of the
system theme does not undo it), and it never touches the network or the DOM
beyond ``data-theme``, the ``js`` class and ``aria-pressed``.
"""

from typing import Final

JS: Final = """\
(function () {
  "use strict";
  var KEY = "osa-theme";
  var root = document.documentElement;
  root.classList.add("js");

  function saved() {
    try {
      var value = window.localStorage.getItem(KEY);
      return value === "light" || value === "dark" ? value : null;
    } catch (error) {
      return null;
    }
  }

  function system() {
    try {
      return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    } catch (error) {
      return "light";
    }
  }

  function current() {
    var pinned = root.getAttribute("data-theme");
    return pinned === "light" || pinned === "dark" ? pinned : system();
  }

  function sync() {
    var pressed = current() === "dark" ? "true" : "false";
    var buttons = document.querySelectorAll(".theme-switch");
    for (var i = 0; i < buttons.length; i += 1) {
      buttons[i].setAttribute("aria-pressed", pressed);
    }
  }

  var choice = saved();
  if (choice) {
    root.setAttribute("data-theme", choice);
  }

  document.addEventListener("click", function (event) {
    var target = event.target;
    if (!target || !target.closest) {
      return;
    }
    var button = target.closest(".theme-switch");
    if (!button) {
      return;
    }
    var next = current() === "dark" ? "light" : "dark";
    choice = next; /* kept in memory: without storage it still beats system changes */
    root.setAttribute("data-theme", next);
    try {
      window.localStorage.setItem(KEY, next);
    } catch (error) {
      /* no storage: the choice lasts until the page is closed */
    }
    sync();
  });

  document.addEventListener("DOMContentLoaded", sync);
  try {
    var query = window.matchMedia("(prefers-color-scheme: dark)");
    var onChange = function () {
      if (!choice && !saved()) {
        root.removeAttribute("data-theme");
        sync();
      }
    };
    if (query.addEventListener) {
      query.addEventListener("change", onChange);
    }
  } catch (error) {
    /* no matchMedia: the page stays light unless a choice was saved */
  }
})();
"""
