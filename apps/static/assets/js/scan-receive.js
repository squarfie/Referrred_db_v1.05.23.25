(function () {
  "use strict";
  function receive() {
    var form = document.getElementById("scan-receive-form");
    if (!form || form.dataset.submitted === "true") return;
    form.dataset.submitted = "true";
    form.requestSubmit();
  }
  function start() {
    // Browser speculative loading must not record a physical receipt.
    if (document.prerendering) {
      document.addEventListener("prerenderingchange", receive, { once: true });
    } else {
      receive();
    }
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
}());
