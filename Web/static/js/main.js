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

    // 如果选择的是 "all"，就显示所有行，否则只显示指定数量 (if the user selected "all", show all rows)
    if (rowsToShow === "all") {
        for (var i = 0; i < dataRows.length; i++) {
            var dataRow = dataRows[i];
            var subRow = dataRow.nextElementSibling;

            dataRow.style.display = "";
            if (subRow) {
                subRow.style.display = "";
            }
        }
    } else {
        // 将字符串转成数字 (convert string to number)
        var showCount = parseInt(rowsToShow);

        for (var i = 0; i < dataRows.length; i++) {
            var dataRow = dataRows[i];
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



// 页面加载后初始化显示 (initialize display on page load)
document.addEventListener("DOMContentLoaded", function() {
    updateRowsDisplay();

    // 监听行数选择变化 (listen for row selection changes)
    var select = document.getElementById("rowsSelect");
    if (select) {
        select.addEventListener("change", updateRowsDisplay);
    }

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