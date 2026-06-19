"use strict";

// The admin dashboard is form-driven (server-rendered). This file is a
// placeholder for future progressive enhancement. Auto-expand an assign card
// if the page was loaded with #seat-CODE in the URL.
(function () {
  var hash = window.location.hash;
  if (hash && hash.indexOf("#seat-") === 0) {
    var code = decodeURIComponent(hash.slice("#seat-".length));
    var cards = document.querySelectorAll(".assign-card");
    cards.forEach(function (card) {
      var summary = card.querySelector("summary");
      if (summary && summary.textContent.trim().indexOf(code) === 0) {
        card.open = true;
        card.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    });
  }
})();

// Pass type: auto-fill amount and expiry from the chosen pass (Monthly = flat
// fee + one month; Daily = rate × days). Values stay editable; we only seed an
// empty expiry on load so server-prefilled renewal dates aren't clobbered.
(function () {
  function isoDate(d) {
    return (
      d.getFullYear() +
      "-" + String(d.getMonth() + 1).padStart(2, "0") +
      "-" + String(d.getDate()).padStart(2, "0")
    );
  }

  function wire(form) {
    var monthly = parseFloat(form.getAttribute("data-monthly") || "0");
    var daily = parseFloat(form.getAttribute("data-daily") || "0");
    var type = form.querySelector('[name="pass_type"]');
    if (!type) return;
    var daysWrap = form.querySelector(".days-field");
    var days = form.querySelector('[name="days"]');
    var amount = form.querySelector('[name="amount"]');
    var start = form.querySelector('[name="period_start"]');
    var end = form.querySelector('[name="period_end"]');

    function recompute() {
      var daily_mode = type.value === "daily";
      if (daysWrap) daysWrap.style.display = daily_mode ? "" : "none";
      var s = start && start.value ? new Date(start.value + "T00:00:00") : null;
      if (daily_mode) {
        var n = Math.max(1, parseInt(days && days.value, 10) || 1);
        if (amount) amount.value = (daily * n).toFixed(0);
        if (s && end) {
          var e = new Date(s);
          e.setDate(e.getDate() + n - 1);
          end.value = isoDate(e);
        }
      } else {
        if (amount) amount.value = monthly.toFixed(0);
        if (s && end) {
          var e2 = new Date(s);
          e2.setMonth(e2.getMonth() + 1);
          e2.setDate(e2.getDate() - 1);
          end.value = isoDate(e2);
        }
      }
    }

    type.addEventListener("change", recompute);
    if (days) days.addEventListener("input", recompute);
    if (start) start.addEventListener("change", recompute);
    // Show the days field if daily is preselected; seed expiry only if empty.
    if (daysWrap) daysWrap.style.display = type.value === "daily" ? "" : "none";
    if (end && !end.value) recompute();
  }

  document.querySelectorAll("form.pass-form").forEach(wire);
})();

// Sortable tables: click a header marked `.sortable` to reorder rows. Seat
// columns use natural sort (A2 before A10); date columns sort on the ISO
// `data-sort-value` (blanks always sink to the bottom). Clicking the active
// column again flips the direction.
(function () {
  function cellValue(row, index) {
    var cell = row.children[index];
    if (!cell) return "";
    var v = cell.getAttribute("data-sort-value");
    return (v !== null ? v : cell.textContent).trim();
  }

  function comparator(type, dir) {
    return function (a, b) {
      var av = cellValue(a.row, a.index);
      var bv = cellValue(b.row, b.index);
      // Empty values always go last, regardless of direction.
      if (!av && !bv) return 0;
      if (!av) return 1;
      if (!bv) return -1;
      var cmp;
      if (type === "seat") {
        cmp = av.localeCompare(bv, undefined, { numeric: true, sensitivity: "base" });
      } else {
        cmp = av < bv ? -1 : av > bv ? 1 : 0;
      }
      return dir === "desc" ? -cmp : cmp;
    };
  }

  function wire(table) {
    var headers = Array.prototype.slice.call(
      table.querySelectorAll("thead th.sortable")
    );
    headers.forEach(function (th, i) {
      // The header's position among all th's is the cell index to read.
      var index = Array.prototype.indexOf.call(th.parentNode.children, th);
      var type = th.getAttribute("data-sort-type") || "text";
      th.addEventListener("click", function () {
        var current = th.getAttribute("aria-sort");
        var dir = current === "ascending" ? "desc" : "asc";
        headers.forEach(function (h) {
          if (h !== th) h.removeAttribute("aria-sort");
        });
        th.setAttribute("aria-sort", dir === "asc" ? "ascending" : "descending");

        var tbody = table.querySelector("tbody");
        var rows = Array.prototype.slice.call(tbody.querySelectorAll("tr"));
        rows
          .map(function (row) { return { row: row, index: index }; })
          .sort(comparator(type, dir))
          .forEach(function (item) { tbody.appendChild(item.row); });
      });
    });
  }

  document.querySelectorAll("table.sortable-table").forEach(wire);
})();

// Renewal reminders: open the member's WhatsApp chat and record the reminder
// without a full page navigation. Falls back to the plain form POST (which the
// server redirects to wa.me) when JavaScript is unavailable.
(function () {
  var forms = document.querySelectorAll(".remind-form");
  forms.forEach(function (form) {
    form.addEventListener("submit", function (e) {
      var wa = form.getAttribute("data-wa");
      if (!wa) return; // no number — let the form do its default thing
      e.preventDefault();

      // Triggered by the user's click, so the popup is allowed.
      window.open(wa, "_blank", "noopener");

      fetch(form.action, { method: "POST", headers: { Accept: "application/json" } })
        .catch(function () {})
        .finally(function () {
          var row = form.closest("tr");
          if (!row) return;
          var status = row.querySelector(".reminder-status");
          if (status) {
            status.innerHTML = '<span class="pill pill-ok">Reminded ✓</span>';
          }
          var btn = form.querySelector("button");
          if (btn) btn.textContent = "Send again";
        });
    });
  });
})();
