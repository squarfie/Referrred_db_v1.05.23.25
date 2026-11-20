(function () {
  'use strict';
  var STORAGE_KEY = 'referred-sidebar-groups-v1';

  function readGroups() {
    try {
      var saved = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || '{}');
      return saved && typeof saved === 'object' && !Array.isArray(saved) ? saved : {};
    } catch (error) {
      return {};
    }
  }

  function saveGroups(groups) {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(groups));
    } catch (error) {
      // The current page remains fully usable when storage is blocked.
    }
  }

  function initSidebarGroups() {
    var sidenav = document.getElementById('sidenav-main');
    if (!sidenav) return;

    var preference = readGroups();
    sidenav.querySelectorAll('[data-sidebar-group]').forEach(function (button) {
      var list = document.getElementById(button.getAttribute('aria-controls'));
      if (!list) return;

      var group = button.getAttribute('data-sidebar-group');
      var activeChild = !!list.querySelector('a.nav-link.active');

      function setOpen(open) {
        button.setAttribute('aria-expanded', String(open));
        list.hidden = !open;
      }

      button.classList.toggle('has-active-child', activeChild);
      setOpen(activeChild || preference[group] === true);

      button.addEventListener('click', function () {
        // The icon-only rail hides submenus. Expand the rail before opening.
        if (document.body.classList.contains('sidenav-icon-rail-collapsed') &&
            window.matchMedia('(min-width: 1200px)').matches) {
          var iconRailToggle = document.getElementById('sidenavIconRailToggle');
          if (iconRailToggle) iconRailToggle.click();
          setOpen(true);
        } else {
          setOpen(button.getAttribute('aria-expanded') !== 'true');
        }
        preference[group] = button.getAttribute('aria-expanded') === 'true';
        saveGroups(preference);
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initSidebarGroups);
  } else {
    initSidebarGroups();
  }
})();
