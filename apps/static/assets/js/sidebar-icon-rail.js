(function () {
  'use strict';

  var STORAGE_KEY = 'referred-sidenav-icon-rail';
  var COLLAPSED_CLASS = 'sidenav-icon-rail-collapsed';

  function readPreference() {
    try {
      return window.localStorage.getItem(STORAGE_KEY) === 'collapsed'
        ? 'collapsed'
        : 'expanded';
    } catch (error) {
      return document.body.classList.contains(COLLAPSED_CLASS)
        ? 'collapsed'
        : 'expanded';
    }
  }

  function writePreference(preference) {
    try {
      window.localStorage.setItem(STORAGE_KEY, preference);
    } catch (error) {
      // The current-page state still works when storage is unavailable.
    }
  }

  function initializeIconRail() {
    var toggle = document.getElementById('sidenavIconRailToggle');
    var sidenav = document.getElementById('sidenav-main');

    if (!toggle || !sidenav) {
      return;
    }

    var desktopQuery = window.matchMedia('(min-width: 1200px)');
    var collapseIcon = toggle.querySelector('.sidenav-icon-rail-collapse-icon');
    var expandIcon = toggle.querySelector('.sidenav-icon-rail-expand-icon');
    var preference = readPreference();

    sidenav.querySelectorAll('.nav-link').forEach(function (link) {
      var label = link.querySelector('.nav-link-text');
      if (label && !link.getAttribute('title')) {
        link.setAttribute('title', label.textContent.trim());
      }
    });

    function syncControl() {
      var isCollapsed = desktopQuery.matches &&
        document.body.classList.contains(COLLAPSED_CLASS);
      var label = isCollapsed ? 'Expand sidebar' : 'Collapse sidebar';

      toggle.setAttribute('aria-expanded', String(!isCollapsed));
      toggle.setAttribute('aria-label', label);
      toggle.setAttribute('title', label);

      if (collapseIcon) {
        collapseIcon.hidden = isCollapsed;
      }
      if (expandIcon) {
        expandIcon.hidden = !isCollapsed;
      }
    }

    function applyPreference() {
      document.body.classList.toggle(
        COLLAPSED_CLASS,
        desktopQuery.matches && preference === 'collapsed'
      );
      syncControl();
    }

    toggle.addEventListener('click', function () {
      if (!desktopQuery.matches) {
        return;
      }

      preference = document.body.classList.contains(COLLAPSED_CLASS)
        ? 'expanded'
        : 'collapsed';
      writePreference(preference);
      applyPreference();
    });

    if (typeof desktopQuery.addEventListener === 'function') {
      desktopQuery.addEventListener('change', applyPreference);
    } else if (typeof desktopQuery.addListener === 'function') {
      desktopQuery.addListener(applyPreference);
    }

    applyPreference();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeIconRail);
  } else {
    initializeIconRail();
  }
})();
