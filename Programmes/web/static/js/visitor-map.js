(function () {
    "use strict";

    var section = document.getElementById("visitor-map");
    var host = document.getElementById("visitor-map-widget");
    var status = document.getElementById("visitor-map-status");
    if (!section || !host || !status || section.dataset.mapInitialized) return;
    section.dataset.mapInitialized = "true";

    var timeout;
    var observer;
    var ready = false;
    var backgroundUrl = "";
    var backgroundReady = false;

    function showUnavailable() {
        if (ready) return;
        window.clearTimeout(timeout);
        section.dataset.mapState = "unavailable";
        host.setAttribute("aria-busy", "false");
        status.textContent = "Visitor map is temporarily unavailable.";
        status.hidden = false;
        // Keep watching: a slow third-party response may still render the map.
    }

    function checkRenderedMap() {
        // The provider draws its land background as an image and live points as
        // SVG circles. Empty visit data may legitimately contain no SVG paths.
        var map = host.querySelector(".mapmyvisitors-map");
        var svg = map && map.querySelector("svg");
        if (!svg) return;

        var image = window.getComputedStyle(map).backgroundImage;
        var match = /^url\(["']?(.*?)["']?\)$/.exec(image);
        if (!match) return;
        if (backgroundUrl !== match[1]) {
            backgroundUrl = match[1];
            backgroundReady = false;
            var probe = new Image();
            probe.addEventListener("load", function () {
                backgroundReady = true;
                checkRenderedMap();
            }, { once: true });
            probe.addEventListener("error", showUnavailable, { once: true });
            // Reuses the browser-cached background; this URL has no visitor ID.
            probe.src = backgroundUrl;
            return;
        }
        if (!backgroundReady) return;
        ready = true;
        window.clearTimeout(timeout);
        observer.disconnect();
        section.dataset.mapState = "ready";
        host.setAttribute("aria-busy", "false");
        status.hidden = true;
    }

    section.dataset.mapState = "loading";
    host.setAttribute("aria-busy", "true");
    status.textContent = "Loading visitor map…";
    status.hidden = false;

    observer = new MutationObserver(checkRenderedMap);
    observer.observe(host, { childList: true, subtree: true });
    timeout = window.setTimeout(showUnavailable, 15000);

    // The provider uses this ID as its insertion point. Never load it twice,
    // including when the page is resized or this initializer runs again.
    if (!document.getElementById("mapmyvisitors")) {
        var script = document.createElement("script");
        script.id = "mapmyvisitors";
        script.async = true;
        script.src = host.dataset.scriptSrc;
        script.addEventListener("load", checkRenderedMap, { once: true });
        script.addEventListener("error", showUnavailable, { once: true });
        host.appendChild(script);
    }
    checkRenderedMap();
})();
