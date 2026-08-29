(function () {
    "use strict";

    function numberValue(value) {
        var normalized = value.replace(/,/g, "").replace(/%$/, "").trim();
        var number = Number(normalized);
        return Number.isFinite(number) ? number : null;
    }

    function sortTable(header) {
        var table = header.closest("table");
        var body = table && table.tBodies[0];
        if (!body || body.rows.length < 2) return;
        var headers = Array.prototype.slice.call(header.parentElement.cells);
        var column = headers.indexOf(header);
        var current = header.getAttribute("aria-sort");
        var numeric = header.dataset.sortKind === "number";
        var descending = current === "ascending" || (current === "none" && numeric);
        headers.forEach(function (item) { item.setAttribute("aria-sort", "none"); });
        header.setAttribute("aria-sort", descending ? "descending" : "ascending");
        var rows = Array.prototype.map.call(body.rows, function (row, index) {
            var cell = row.cells[column];
            var raw = (cell && cell.dataset.sortValue || cell && cell.textContent || "").trim();
            return { row: row, index: index, raw: raw,
                     value: numeric ? numberValue(raw) : raw.toLocaleLowerCase() };
        });
        rows.sort(function (left, right) {
            var leftMissing = left.value === null || left.value === "" || left.raw === "—";
            var rightMissing = right.value === null || right.value === "" || right.raw === "—";
            if (leftMissing !== rightMissing) return leftMissing ? 1 : -1;
            if (left.value === right.value) return left.index - right.index;
            var order = left.value < right.value ? -1 : 1;
            return descending ? -order : order;
        });
        rows.forEach(function (item) { body.appendChild(item.row); });
    }

    document.addEventListener("click", function (event) {
        var header = event.target.closest("th[data-sortable='true']");
        if (header) sortTable(header);
    });
    document.addEventListener("keydown", function (event) {
        if (event.key !== "Enter" && event.key !== " ") return;
        var header = event.target.closest("th[data-sortable='true']");
        if (!header) return;
        event.preventDefault();
        sortTable(header);
    });
}());

(function () {
    "use strict";

    var mobile = window.matchMedia("(max-width: 767px)");

    function setup(nav) {
        var buttons = Array.prototype.slice.call(nav.querySelectorAll("[data-mobile-tab]"));
        var panels = Array.prototype.slice.call(document.querySelectorAll("[data-mobile-tab-panel]"));
        if (!buttons.length || !panels.length) return;

        buttons.forEach(function (button) {
            var name = button.dataset.mobileTab;
            button.id = "mobile-tab-" + name;
            var controlled = [];
            panels.forEach(function (panel, index) {
                if (panel.dataset.mobileTabPanel !== name) return;
                panel.id = "mobile-panel-" + name + "-" + index;
                panel.setAttribute("role", "tabpanel");
                panel.setAttribute("aria-labelledby", button.id);
                controlled.push(panel.id);
            });
            button.setAttribute("aria-controls", controlled.join(" "));
        });

        function known(name) {
            return buttons.some(function (button) { return button.dataset.mobileTab === name; });
        }

        function selectedFromHash() {
            var match = window.location.hash.match(/^#tab-([a-z0-9_-]+)$/);
            if (match && known(match[1])) return match[1];
            try {
                var stored = window.sessionStorage.getItem(
                    "cfb-page-tab:" + window.location.pathname
                );
                if (stored && known(stored)) return stored;
            } catch (error) {}
            return buttons[0].dataset.mobileTab;
        }

        function select(name, moveFocus, updateHash) {
            if (!known(name)) name = buttons[0].dataset.mobileTab;
            buttons.forEach(function (button) {
                var active = button.dataset.mobileTab === name;
                button.setAttribute("aria-selected", active ? "true" : "false");
                button.tabIndex = active ? 0 : -1;
                if (active && moveFocus) button.focus({ preventScroll: true });
            });
            panels.forEach(function (panel) {
                panel.hidden = mobile.matches && panel.dataset.mobileTabPanel !== name;
            });
            try {
                window.sessionStorage.setItem("cfb-page-tab:" + window.location.pathname, name);
            } catch (error) {}
            if (updateHash && window.history && window.history.replaceState) {
                window.history.replaceState(null, "", "#tab-" + name);
            }
        }

        function applyMode() {
            nav.hidden = !mobile.matches;
            if (mobile.matches) {
                select(selectedFromHash(), false, false);
            } else {
                panels.forEach(function (panel) { panel.hidden = false; });
            }
        }

        buttons.forEach(function (button, index) {
            button.addEventListener("click", function () {
                select(button.dataset.mobileTab, false, true);
                nav.scrollIntoView({ behavior: "smooth", block: "start" });
            });
            button.addEventListener("keydown", function (event) {
                if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
                event.preventDefault();
                var direction = event.key === "ArrowRight" ? 1 : -1;
                var next = (index + direction + buttons.length) % buttons.length;
                select(buttons[next].dataset.mobileTab, true, true);
            });
        });
        window.addEventListener("hashchange", function () {
            if (mobile.matches) select(selectedFromHash(), false, false);
        });
        if (mobile.addEventListener) mobile.addEventListener("change", applyMode);
        else mobile.addListener(applyMode);
        applyMode();
    }

    document.querySelectorAll("[data-mobile-page-tabs]").forEach(setup);
}());

(function () {
    "use strict";

    /* The canonical feed can call the late-August opener Week 1 even when the
       football calendar treats it as Week 0.  Games-to-Watch carries the
       normalized display week, so keep the matchup-section heading aligned
       with the slate the reader is actually seeing. */
    var firstWatchLabel = document.querySelector(".watch-card-top span:first-child");
    if (!firstWatchLabel) return;
    var match = firstWatchLabel.textContent.match(/Week\s+(\d+)/i);
    if (!match) return;
    Array.prototype.forEach.call(document.querySelectorAll(".section-title h2"), function (heading) {
        if (!/^Upcoming Week\b/i.test(heading.textContent.trim())) return;
        heading.textContent = heading.textContent.replace(
            /Upcoming Week(?:\s+\d+)?/i, "Upcoming Week " + match[1]
        );
    });
}());

(function () {
    "use strict";

    /* Long intelligence pages should behave like workspaces, not documents.
       Build one in-page navigator from the headings that already define the
       page so team, matchup, player and history pages stay in sync automatically. */
    var sections = Array.prototype.slice.call(document.querySelectorAll("main .section")).map(
        function (node) {
            var heading = node.querySelector(":scope > h2, :scope > .section-title h2");
            return heading ? { node: node, heading: heading } : null;
        }
    ).filter(Boolean);

    if (sections.length < 3) return;

    var stylesheet = document.createElement("link");
    stylesheet.rel = "stylesheet";
    stylesheet.href = "/static/cfb_section_nav.css?v=20260829";
    document.head.appendChild(stylesheet);

    function slug(text) {
        return text.toLowerCase()
            .replace(/&/g, " and ")
            .replace(/[^a-z0-9]+/g, "-")
            .replace(/^-+|-+$/g, "") || "section";
    }

    var used = {};
    sections.forEach(function (item) {
        var base = slug(item.heading.textContent.trim());
        var count = used[base] || 0;
        used[base] = count + 1;
        item.id = count ? base + "-" + (count + 1) : base;
        item.node.id = item.node.id || item.id;
        item.node.dataset.sectionNavTarget = "true";
    });

    var nav = document.createElement("nav");
    nav.className = "section-nav";
    nav.setAttribute("aria-label", "On this page");
    nav.innerHTML =
        '<div class="section-nav-inner">' +
          '<span class="section-nav-label">On this page</span>' +
          '<div class="section-nav-links"></div>' +
          '<div class="section-nav-actions">' +
            '<button type="button" data-section-prev title="Previous section" aria-label="Previous section">↑</button>' +
            '<button type="button" data-section-next title="Next section" aria-label="Next section">↓</button>' +
            '<button type="button" data-section-top title="Back to top" aria-label="Back to top">Top</button>' +
          '</div>' +
        '</div>';

    var header = document.querySelector(".site-header");
    if (header) header.insertAdjacentElement("afterend", nav);
    else document.body.insertAdjacentElement("afterbegin", nav);

    var linksHost = nav.querySelector(".section-nav-links");
    sections.forEach(function (item, index) {
        var link = document.createElement("a");
        link.href = "#" + item.node.id;
        link.textContent = item.heading.textContent.trim();
        link.dataset.sectionIndex = String(index);
        link.addEventListener("click", function (event) {
            event.preventDefault();
            item.node.scrollIntoView({ behavior: "smooth", block: "start" });
            if (window.history && window.history.replaceState) {
                window.history.replaceState(null, "", "#" + item.node.id);
            }
        });
        linksHost.appendChild(link);
        item.link = link;
    });

    var previous = nav.querySelector("[data-section-prev]");
    var next = nav.querySelector("[data-section-next]");
    var top = nav.querySelector("[data-section-top]");
    var active = 0;

    function select(index, move) {
        index = Math.max(0, Math.min(sections.length - 1, index));
        active = index;
        sections.forEach(function (item, position) {
            if (position === index) item.link.setAttribute("aria-current", "location");
            else item.link.removeAttribute("aria-current");
        });
        previous.disabled = index === 0;
        next.disabled = index === sections.length - 1;
        sections[index].link.scrollIntoView({ block: "nearest", inline: "nearest" });
        if (move) sections[index].node.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    function currentSection() {
        var threshold = nav.getBoundingClientRect().bottom + 18;
        var index = 0;
        sections.forEach(function (item, position) {
            if (item.node.getBoundingClientRect().top <= threshold) index = position;
        });
        select(index, false);
    }

    previous.addEventListener("click", function () { select(active - 1, true); });
    next.addEventListener("click", function () { select(active + 1, true); });
    top.addEventListener("click", function () {
        window.scrollTo({ top: 0, behavior: "smooth" });
        if (window.history && window.history.replaceState) {
            window.history.replaceState(null, "", window.location.pathname + window.location.search);
        }
    });

    var queued = false;
    window.addEventListener("scroll", function () {
        if (queued) return;
        queued = true;
        window.requestAnimationFrame(function () {
            currentSection();
            queued = false;
        });
    }, { passive: true });

    currentSection();
}());

