/* brain-visual-router.js — STORY-068.1: vanilla JS hash router (IIFE, no bundler)
 *
 * Constraints:
 *  - No setInterval / clearInterval calls — poll timer lives in initLivePolling IIFE
 *  - View Transitions API gated behind prefers-reduced-motion: no-preference
 *  - XSS safe: only sets textContent / attributes, never innerHTML with unsanitized data
 */
(function () {
  "use strict";

  var PAGES = ["overview", "health", "memory", "retrieval", "agents", "integrity"];
  var DEFAULT_PAGE = "overview";

  /** Read location.hash, strip '#', validate against PAGES, return page name. */
  function getPage() {
    var hash = location.hash.replace(/^#/, "");
    return PAGES.indexOf(hash) !== -1 ? hash : DEFAULT_PAGE;
  }

  /** Toggle hidden attribute on [data-page] elements; set aria-current on nav anchors. */
  function applyRoute() {
    var page = getPage();

    // Show / hide page sections
    PAGES.forEach(function (name) {
      var sections = document.querySelectorAll('[data-page="' + name + '"]');
      sections.forEach(function (el) {
        if (name === page) {
          el.removeAttribute("hidden");
        } else {
          el.setAttribute("hidden", "");
        }
      });
    });

    // Update aria-current on nav anchors
    PAGES.forEach(function (name) {
      var anchors = document.querySelectorAll('[data-nav="' + name + '"]');
      anchors.forEach(function (anchor) {
        if (name === page) {
          anchor.setAttribute("aria-current", "page");
        } else {
          anchor.removeAttribute("aria-current");
        }
      });
    });
  }

  /** Call View Transitions API when available + motion OK; else applyRoute directly. */
  function router() {
    var motionOk = window.matchMedia("(prefers-reduced-motion: no-preference)").matches;
    if (motionOk && typeof document.startViewTransition === "function") {
      document.startViewTransition(applyRoute);
    } else {
      applyRoute();
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    // Hamburger toggle for mobile
    var hamburger = document.getElementById("nav-hamburger");
    var sideNav = document.getElementById("side-nav");
    if (hamburger && sideNav) {
      hamburger.addEventListener("click", function () {
        var isOpen = sideNav.classList.toggle("nav-open");
        hamburger.setAttribute("aria-expanded", isOpen ? "true" : "false");
      });
    }

    router();
  });

  window.addEventListener("hashchange", router);
})();
