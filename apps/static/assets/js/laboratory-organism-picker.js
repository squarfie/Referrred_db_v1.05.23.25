/* Searchable WHONET organism picker shared by laboratory ID, review and harvest. */
(function () {
  "use strict";
  document.querySelectorAll("[data-organism-picker]").forEach(function (picker) {
    const search = picker.querySelector("[data-organism-search]");
    const code = picker.querySelector("[data-organism-code]");
    const name = picker.querySelector("[data-organism-name]");
    const results = picker.querySelector("[data-organism-results]");
    if (!search || !code || !name || !results) return;
    const endpoint = picker.dataset.endpoint;
    const allowUnlisted = picker.hasAttribute("data-allow-unlisted");
    const optionalOrganism = picker.hasAttribute("data-optional-organism");
    const optionalDetails = optionalOrganism ? picker.closest("details") : null;
    const form = picker.closest("form");
    // A blank/closed optional organism must not carry a stale browser error.
    function isUnusedOptional() {
      return optionalOrganism && (
        (optionalDetails && !optionalDetails.open) ||
        (!search.value.trim() && !code.value.trim() && !name.value.trim())
      );
    }
    function validatePicker() {
      if (isUnusedOptional()) {
        search.setCustomValidity("");
        return true;
      }
      const valid = Boolean(name.value.trim()) &&
        (allowUnlisted || Boolean(code.value.trim()));
      search.setCustomValidity(valid ? "" : "Choose an organism from the search results.");
      if (!valid) search.reportValidity();
      return valid;
    }
    // Clear any previously set custom error as soon as an optional disclosure
    // closes; stale errors otherwise block the browser before submit fires.
    optionalDetails?.addEventListener("toggle", function () {
      if (!optionalDetails.open) search.setCustomValidity("");
    });
    search.setCustomValidity("");

    let requestId = 0;
    let controller = null;

    function close() { results.innerHTML = ""; results.hidden = true; }
    function choose(item) {
      code.value = item.code;
      name.value = item.name;
      search.value = item.code + " — " + item.name;
      close();
      search.setCustomValidity("");
    }
    search.addEventListener("input", async function () {
      code.value = ""; name.value = "";
      const term = search.value.trim();
      const id = ++requestId;
      if (controller) controller.abort();
      if (!term) { close(); return; }
      controller = new AbortController();
      try {
        const url = new URL(endpoint, location.origin);
        url.searchParams.set("q", term.slice(0, 100));
        const response = await fetch(url.toString(), {
          credentials: "same-origin", headers: {"Accept": "application/json"},
          signal: controller.signal
        });
        if (!response.ok) throw new Error("Search failed");
        const payload = await response.json();
        if (id !== requestId) return;
        results.replaceChildren();
        const items = payload.results || [];
        if (!items.length) {
          const hint = document.createElement("div");
          hint.className = "organism-picker-empty";
          hint.textContent = "No organism found in the reference table.";
          results.appendChild(hint);
        }
        items.forEach(function (item) {
          const option = document.createElement("button");
          option.type = "button";
          option.className = "organism-picker-option";
          option.textContent = item.code + " — " + item.name;
          option.setAttribute("role", "option");
          option.addEventListener("click", function () { choose(item); });
          results.appendChild(option);
        });
        if (allowUnlisted) {
          const custom = document.createElement("button");
          custom.type = "button";
          custom.className = "organism-picker-option organism-picker-custom";
          custom.textContent = "Use unlisted name: " + term;
          custom.addEventListener("click", function () {
            code.value = "";
            name.value = term;
            search.value = term;
            search.setCustomValidity("");
            close();
          });
          results.appendChild(custom);
        }
        results.hidden = false;
      } catch (error) {
        if (error.name !== "AbortError") {
          results.replaceChildren();
          const hint = document.createElement("div");
          hint.className = "organism-picker-empty";
          hint.textContent = "Organism lookup is unavailable. Try again.";
          results.appendChild(hint);
          results.hidden = false;
        }
      }
    });
    picker.addEventListener("keydown", function (event) {
      if (event.key === "Escape") { close(); return; }
      if (event.target === search && !results.hidden &&
          (event.key === "Enter" || event.key === "ArrowDown")) {
        const first = results.querySelector("button");
        if (first) {
          event.preventDefault();
          if (event.key === "Enter") first.click();
          else first.focus();
        }
      } else if ((event.key === "ArrowDown" || event.key === "ArrowUp") &&
                 event.target.matches(".organism-picker-option")) {
        const buttons = Array.from(results.querySelectorAll("button"));
        const position = buttons.indexOf(event.target);
        const next = buttons[position + (event.key === "ArrowDown" ? 1 : -1)];
        if (next) { event.preventDefault(); next.focus(); }
      }
    });
    // Submit can be skipped entirely by native constraint validation when a
    // stale custom error exists, so clean the optional picker *before* the
    // browser validates a clicked submit button.
    form?.addEventListener("click", function (event) {
      if (event.target.closest('button[type="submit"], input[type="submit"]')) {
        if (isUnusedOptional()) search.setCustomValidity("");
      }
    }, true);
    form?.addEventListener("submit", function (event) {
      if (!validatePicker()) event.preventDefault();
    });
    search.addEventListener("input", function () { search.setCustomValidity(""); });
    search.addEventListener("focus", function () { search.setCustomValidity(""); });
  });
})();
