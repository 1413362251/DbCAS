console.log("JavaScript is working!");

// 切换子表格显示状态 (toggle sub-table display)
function toggleSubTable(button) {
    var currentRow = button.parentNode.parentNode;  // 获取当前数据行 (get current data row)
    var subRow = currentRow.nextElementSibling;       // 获取对应的子表格行 (get corresponding sub-row)
    if (subRow.classList.contains("hidden")) {
        subRow.classList.remove("hidden");  // 展开 (expand)
        button.innerText = "Collapse";
    } else {
        subRow.classList.add("hidden");      // 收起 (collapse)
        button.innerText = "Expand";
    }
}

// 更新表格显示的行数 (update displayed rows)
function updateRowsDisplay() {
    var select = document.getElementById("rowsSelect");
    if (!select) return;

    // 取得用户选择的值 (get the user-selected value)
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

    // 如果选择的是 "all"，就显示所有行，否则只显示指定数量 (if the user selected "all", show all rows)
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
        // 将字符串转成数字 (convert string to number)
        var showCount = parseInt(rowsToShow);

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



// 页面加载后初始化显示 (initialize display on page load)
document.addEventListener("DOMContentLoaded", function() {
    updateRowsDisplay();

    // 监听行数选择变化 (listen for row selection changes)
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

    // 监听所有按钮的点击事件 (listen for all toggle buttons)
    document.querySelectorAll(".toggle-button").forEach(button => {
        button.addEventListener("click", function() {
            toggleSubTable(this);
        });
    });
});

// search 时的变色
document.addEventListener("DOMContentLoaded", function(){
    var rows = document.querySelectorAll(".data-row");
    rows.forEach(function(row, index){
        row.style.backgroundColor = (index % 2 === 0) ? "white" : "#f0f0f0"; // 更浅的灰色
    });
});
document.querySelector('.searchbar').addEventListener('mouseover', function() {
    document.querySelector('.main-content').style.backgroundColor = 'rgb(0,0,0,0.8)';
});

document.querySelector('.searchbar').addEventListener('mouseout', function() {
    document.querySelector('.main-content').style.backgroundColor = 'rgb(112, 112, 105)';
});

// Biglogo in welcome page flip
document.addEventListener("DOMContentLoaded", function(){
      const card = document.querySelector('.flip-card');
      if (!card) return;

      // 设置一点延时，确保初始状态先渲染完
      setTimeout(() => {
          card.classList.add('animate');
      }, 100);

      // 监听 transition 动画结束事件
      card.addEventListener('transitionend', () => {
          // 动画结束后移除 .animate 类
          card.classList.remove('animate');
      });
  });