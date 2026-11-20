"use strict";
// EvalGate report viewer: sorts and filters tables that are already complete in the HTML.
// Loaded inline under a CSP hash. It makes no requests, never parses markup from strings,
// and only moves or hides existing rows, so the page shows exactly what the server rendered.

// A cell's sort key: [0, number] | [1, text] | [2, ""] (empty or n/a: always last).
function cellKey(cell) {
  var raw = cell.getAttribute("data-sort");
  if (raw !== null && raw !== "") {
    var n = Number(raw);
    if (!isNaN(n)) return [0, n];
  }
  var text = cell.textContent.trim();
  if (text === "" || text === "n/a") return [2, ""];
  return [1, text];
}

// dir is 1 (ascending) or -1 (descending). Empty keys sort last either way. Text compares by
// code unit, never by locale, so the order is the same in every browser.
function compareKeys(a, b, dir) {
  if (a[0] === 2 || b[0] === 2) return (a[0] === 2 ? 1 : 0) - (b[0] === 2 ? 1 : 0);
  if (a[0] !== b[0]) return (a[0] - b[0]) * dir;
  if (a[1] < b[1]) return -dir;
  if (a[1] > b[1]) return dir;
  return 0;
}

function sortTable(table, column, dir) {
  var body = table.tBodies[0];
  var rows = Array.prototype.slice.call(body.rows).map(function (row, index) {
    return { row: row, key: cellKey(row.cells[column]), index: index };
  });
  rows.sort(function (x, y) {
    return compareKeys(x.key, y.key, dir) || x.index - y.index; // stable
  });
  rows.forEach(function (item) {
    body.appendChild(item.row);
  });
}

function makeSortable(doc, table) {
  var headers = table.tHead.rows[0].cells;
  Array.prototype.forEach.call(headers, function (th, column) {
    if (th.textContent.trim() === "") return;
    var button = doc.createElement("button");
    button.type = "button";
    button.textContent = th.textContent;
    th.textContent = "";
    th.appendChild(button);
    button.addEventListener("click", function () {
      var dir = th.getAttribute("aria-sort") === "ascending" ? -1 : 1;
      Array.prototype.forEach.call(headers, function (other) {
        other.removeAttribute("aria-sort");
      });
      th.setAttribute("aria-sort", dir === 1 ? "ascending" : "descending");
      sortTable(table, column, dir);
    });
  });
}

function makeFilterable(doc, table) {
  var rows = table.tBodies[0].rows;
  var input = doc.createElement("input");
  input.type = "search";
  input.className = "filter";
  input.placeholder = "filter rows";
  input.setAttribute("aria-label", "filter rows");
  var note = doc.createElement("span");
  note.className = "muted";
  var wrapper = table.parentNode;
  wrapper.parentNode.insertBefore(input, wrapper);
  wrapper.parentNode.insertBefore(note, wrapper);
  input.addEventListener("input", function () {
    var query = input.value.trim().toLowerCase();
    var shown = 0;
    Array.prototype.forEach.call(rows, function (row) {
      var match = query === "" || row.textContent.toLowerCase().indexOf(query) !== -1;
      row.hidden = !match;
      if (match) shown += 1;
    });
    note.textContent = query === "" ? "" : " " + shown + " of " + rows.length + " rows";
  });
}

function init(doc) {
  Array.prototype.forEach.call(doc.querySelectorAll("table[data-sortable]"), function (t) {
    makeSortable(doc, t);
  });
  Array.prototype.forEach.call(doc.querySelectorAll("table[data-filter]"), function (t) {
    makeFilterable(doc, t);
  });
}

if (typeof document !== "undefined") {
  init(document);
} else if (typeof module !== "undefined") {
  module.exports = { cellKey: cellKey, compareKeys: compareKeys };
}
