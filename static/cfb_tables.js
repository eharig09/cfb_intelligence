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
