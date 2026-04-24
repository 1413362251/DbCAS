/** Phone only (match in CSS @media max-width: 768px): fold tag list after this many visible tags. Tune here — CSS cannot count children. */
var SEARCH_TAG_COLLAPSE_MAX_VISIBLE = 4;
var SEARCH_TAG_MOBILE_MQ = "(max-width: 768px)";

function searchTagCollapseIsMobile() {
    return window.matchMedia(SEARCH_TAG_MOBILE_MQ).matches;
}

function teardownSearchTagCell(cell) {
    cell.classList.remove("tag-cell--tags-collapsed", "tag-cell--tags-expanded");
    cell.querySelectorAll(".tag-item").forEach(function(el) {
        el.classList.remove("tag-item--folded");
    });
    var oldBtn = cell.querySelector(".tag-cell__more");
    if (oldBtn) {
        oldBtn.remove();
    }
}

function syncSearchTagFolded(cell) {
    var items = cell.querySelectorAll(".tag-item");
    var expanded = cell.classList.contains("tag-cell--tags-expanded");
    items.forEach(function(el, idx) {
        el.classList.toggle("tag-item--folded", !expanded && idx >= SEARCH_TAG_COLLAPSE_MAX_VISIBLE);
    });
}

function setupSearchTagCell(cell) {
    var items = cell.querySelectorAll(".tag-item");
    var total = items.length;
    if (total <= SEARCH_TAG_COLLAPSE_MAX_VISIBLE) {
        teardownSearchTagCell(cell);
        return;
    }
    if (!searchTagCollapseIsMobile()) {
        teardownSearchTagCell(cell);
        return;
    }
    cell.classList.add("tag-cell--tags-collapsed");
    syncSearchTagFolded(cell);
    var btn = cell.querySelector(".tag-cell__more");
    if (!btn) {
        btn = document.createElement("button");
        btn.type = "button";
        btn.className = "tag-cell__more";
        btn.addEventListener("click", function(ev) {
            ev.stopPropagation();
            var c = ev.target.closest("td.tag-cell");
            if (!c) return;
            c.classList.toggle("tag-cell--tags-expanded");
            syncSearchTagFolded(c);
            var n = c.querySelectorAll(".tag-item").length;
            var expanded = c.classList.contains("tag-cell--tags-expanded");
            ev.target.textContent = expanded ? "Show less" : ("+" + String(n - SEARCH_TAG_COLLAPSE_MAX_VISIBLE) + " more");
            ev.target.setAttribute("aria-expanded", expanded ? "true" : "false");
        });
        cell.appendChild(btn);
    }
    var expandedNow = cell.classList.contains("tag-cell--tags-expanded");
    btn.textContent = expandedNow ? "Show less" : ("+" + String(total - SEARCH_TAG_COLLAPSE_MAX_VISIBLE) + " more");
    btn.setAttribute("aria-expanded", expandedNow ? "true" : "false");
}

function initSearchTagCollapse() {
    if (!document.body.classList.contains("page-search")) return;
    document.querySelectorAll("td.tag-cell").forEach(setupSearchTagCell);
}

function toggleSubTable(button) {
    var currentRow = button.parentNode.parentNode;
    var subRow = currentRow.nextElementSibling;
    if (subRow.classList.contains("hidden")) {
        subRow.classList.remove("hidden");
        button.innerText = "Collapse";
        button.classList.remove("expand-btn--expand");
        button.classList.add("expand-btn--collapse");
        button.setAttribute("aria-expanded", "true");
    } else {
        subRow.classList.add("hidden");
        button.innerText = "Expand";
        button.classList.remove("expand-btn--collapse");
        button.classList.add("expand-btn--expand");
        button.setAttribute("aria-expanded", "false");
    }
}

function updateRowsDisplay() {
    var select = document.getElementById("rowsSelect");
    if (!select) return;

    var rowsToShow = select.value;
    var dataRows = document.querySelectorAll(".data-row");
    var visibleRows = [];
    for (var i = 0; i < dataRows.length; i++) {
        if (!dataRows[i].classList.contains("filter-hidden")) {
            visibleRows.push(dataRows[i]);
        } else {
            var subRow = dataRows[i].nextElementSibling;
            dataRows[i].style.display = "none";
            if (subRow) {
                subRow.style.display = "none";
            }
        }
    }

    if (rowsToShow === "all") {
        for (var i = 0; i < visibleRows.length; i++) {
            var dataRow = visibleRows[i];
            var subRow = dataRow.nextElementSibling;

            dataRow.style.display = "";
            if (subRow) {
                subRow.style.display = "";
            }
        }
    } else {
        var showCount = parseInt(rowsToShow, 10);

        for (var i = 0; i < visibleRows.length; i++) {
            var dataRow = visibleRows[i];
            var subRow = dataRow.nextElementSibling;

            if (i < showCount) {
                dataRow.style.display = "";
            } else {
                dataRow.style.display = "none";
                if (subRow) {
                    subRow.style.display = "none";
                }
            }
        }
    }
}

function applyFilters() {
    var tagFilters = {};
    document.querySelectorAll(".tag-filter").forEach(select => {
        var col = select.getAttribute("data-col");
        var value = select.value;
        if (col && value) {
            tagFilters[col] = value.toLowerCase();
        }
    });

    var boolFilters = {};
    document.querySelectorAll(".bool-filter").forEach(select => {
        var col = select.getAttribute("data-col");
        var value = select.value;
        if (col && value) {
            boolFilters[col] = value.toLowerCase();
        }
    });

    var dataRows = document.querySelectorAll(".data-row");
    dataRows.forEach(row => {
        var subRow = row.nextElementSibling;
        var matches = true;

        for (var col in tagFilters) {
            var target = tagFilters[col];
            var cells = Array.from(row.querySelectorAll('.tag-cell[data-col="' + col + '"]'));
            if (subRow) {
                cells = cells.concat(Array.from(subRow.querySelectorAll('.tag-cell[data-col="' + col + '"]')));
            }
            var found = false;
            cells.forEach(cell => {
                var raw = cell.getAttribute("data-tags") || "";
                var tags = raw.split(";").map(t => t.trim().toLowerCase()).filter(t => t);
                if (tags.includes(target)) {
                    found = true;
                }
            });
            if (!found) {
                matches = false;
                break;
            }
        }

        if (matches) {
            for (var boolCol in boolFilters) {
                var boolTarget = boolFilters[boolCol];
                var boolCells = Array.from(row.querySelectorAll('.bool-cell[data-col="' + boolCol + '"]'));
                if (subRow) {
                    boolCells = boolCells.concat(Array.from(subRow.querySelectorAll('.bool-cell[data-col="' + boolCol + '"]')));
                }
                var boolFound = false;
                boolCells.forEach(cell => {
                    var rawBool = (cell.getAttribute("data-bool") || "").toLowerCase();
                    if (rawBool === "true" && boolTarget === "yes") {
                        boolFound = true;
                    } else if (rawBool === "false" && boolTarget === "no") {
                        boolFound = true;
                    }
                });
                if (!boolFound) {
                    matches = false;
                    break;
                }
            }
        }

        if (matches) {
            row.classList.remove("filter-hidden");
        } else {
            row.classList.add("filter-hidden");
        }
    });
    updateRowsDisplay();
}

function getNumericValue(row, col) {
    var cell = row.querySelector('.num-cell[data-col="' + col + '"]');
    if (!cell) {
        return NaN;
    }
    var raw = cell.getAttribute("data-num");
    if (!raw) {
        return NaN;
    }
    var num = parseFloat(raw);
    return isNaN(num) ? NaN : num;
}

function sortByNumericColumn(col, direction) {
    var tbody = document.querySelector("section.shell table tbody");
    if (!tbody) return;
    var rows = Array.from(tbody.querySelectorAll("tr.data-row"));
    var pairs = rows.map(row => {
        return {
            row: row,
            sub: row.nextElementSibling,
            value: getNumericValue(row, col)
        };
    });
    pairs.sort(function(a, b) {
        var aNaN = isNaN(a.value);
        var bNaN = isNaN(b.value);
        if (aNaN && bNaN) return 0;
        if (aNaN) return 1;
        if (bNaN) return -1;
        return direction === "asc" ? a.value - b.value : b.value - a.value;
    });
    pairs.forEach(pair => {
        tbody.appendChild(pair.row);
        if (pair.sub) {
            tbody.appendChild(pair.sub);
        }
    });
    updateRowsDisplay();
}



document.addEventListener("DOMContentLoaded", function() {
    updateRowsDisplay();

    var select = document.getElementById("rowsSelect");
    if (select) {
        select.addEventListener("change", updateRowsDisplay);
    }

    document.querySelectorAll(".tag-filter").forEach(select => {
        select.addEventListener("change", function() {
            var col = this.getAttribute("data-col");
            var value = this.value;
            document.querySelectorAll('.tag-filter[data-col="' + col + '"]').forEach(other => {
                if (other !== this) {
                    other.value = value;
                }
            });
            applyFilters();
        });
    });

    document.querySelectorAll(".bool-filter").forEach(select => {
        select.addEventListener("change", function() {
            var col = this.getAttribute("data-col");
            var value = this.value;
            document.querySelectorAll('.bool-filter[data-col="' + col + '"]').forEach(other => {
                if (other !== this) {
                    other.value = value;
                }
            });
            applyFilters();
        });
    });

    document.querySelectorAll(".num-sort").forEach(select => {
        select.addEventListener("change", function() {
            var col = this.getAttribute("data-col");
            var value = this.value;
            if (!col) return;
            document.querySelectorAll('.num-sort[data-col="' + col + '"]').forEach(other => {
                if (other !== this) {
                    other.value = value;
                }
            });
            if (value === "asc" || value === "desc") {
                sortByNumericColumn(col, value);
            }
        });
    });

    applyFilters();

    initSearchTagCollapse();
    var tagCollapseResizeTimer;
    window.addEventListener("resize", function() {
        clearTimeout(tagCollapseResizeTimer);
        tagCollapseResizeTimer = setTimeout(initSearchTagCollapse, 150);
    });

    var searchbar = document.querySelector(".searchbar");
    var mainContent = document.querySelector(".main-content");
    if (searchbar && mainContent) {
        searchbar.addEventListener("mouseover", function() {
            mainContent.style.backgroundColor = "rgba(0, 0, 0, 0.8)";
        });
        searchbar.addEventListener("mouseout", function() {
            mainContent.style.backgroundColor = "";
        });
    }
});