(function () {
  "use strict";

  var POLL_MS = 5000;
  var seatmap = document.getElementById("seatmap");
  var availabilityCount = document.getElementById("availability-count");
  var pollTimer = null;

  // The physical floor plan (room → seat coordinates) is embedded by the
  // server so the client renders seats in exactly the same positions as the
  // printed seating chart. /api/seats only supplies live status.
  var FLOOR_PLAN = (function () {
    var el = document.getElementById("floor-plan-data");
    if (!el) return [];
    try {
      return JSON.parse(el.textContent) || [];
    } catch (e) {
      return [];
    }
  })();

  // ---- helpers -------------------------------------------------------------

  function showToast(message, kind) {
    var el = document.getElementById("toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "toast";
      el.className = "toast";
      el.setAttribute("role", "status");
      document.body.appendChild(el);
    }
    el.textContent = message;
    el.className = "toast " + (kind || "") + " show";
    clearTimeout(el._hideTimer);
    el._hideTimer = setTimeout(function () {
      el.className = "toast " + (kind || "");
    }, 3200);
  }

  // ---- rendering -----------------------------------------------------------

  var TITLES = { available: "Available", booked: "Booked", expired: "Expired" };

  function buildSeat(code, status, col, row) {
    var el = document.createElement("span");
    el.className = "seat " + status;
    el.setAttribute("data-code", code);
    el.style.gridColumn = col;
    el.style.gridRow = row;
    el.textContent = code;
    el.title = TITLES[status] || "Booked";
    return el;
  }

  // Render the floor plan, colouring each seat from the live status map
  // (code → "available" | "booked"). Positions come from the embedded layout.
  function render(seats) {
    var statusByCode = {};
    (seats || []).forEach(function (s) {
      // Trust the server's derived status: available | booked | expired.
      statusByCode[s.code] =
        TITLES[s.status] ? s.status : s.status === "available" ? "available" : "booked";
    });

    var totalFree = 0;
    var frag = document.createDocumentFragment();

    FLOOR_PLAN.forEach(function (room) {
      var cells = room.cells || [];
      var free = cells.filter(function (c) {
        return (statusByCode[c.code] || c.status) === "available";
      }).length;
      totalFree += free;

      var section = document.createElement("section");
      section.className = "room";
      section.setAttribute("data-room", room.room);

      var head = document.createElement("div");
      head.className = "room-head";

      var h2 = document.createElement("h2");
      h2.className = "room-title";
      h2.textContent = room.room;

      var meta = document.createElement("span");
      meta.className = "room-meta";
      meta.textContent = free + " of " + cells.length + " free";

      head.appendChild(h2);
      head.appendChild(meta);
      section.appendChild(head);

      var grid = document.createElement("div");
      grid.className = "floor-grid";
      grid.style.gridTemplateColumns = "repeat(" + room.cols + ", 1fr)";
      grid.style.gridTemplateRows = "repeat(" + room.rows + ", 1fr)";

      cells.forEach(function (c) {
        var status = statusByCode[c.code] || c.status || "available";
        grid.appendChild(buildSeat(c.code, status, c.col, c.row));
      });

      (room.doors || []).forEach(function (d) {
        var door = document.createElement("span");
        door.className = "floor-door";
        door.style.gridColumn = d.col;
        door.style.gridRow = d.row;
        door.textContent = "⌞ door";
        grid.appendChild(door);
      });

      section.appendChild(grid);
      frag.appendChild(section);
    });

    seatmap.innerHTML = "";
    seatmap.appendChild(frag);

    if (availabilityCount) {
      availabilityCount.textContent =
        totalFree + " seat" + (totalFree === 1 ? "" : "s") + " available";
    }
  }

  // ---- data ----------------------------------------------------------------

  function fetchSeats() {
    return fetch("/api/seats", { headers: { Accept: "application/json" } })
      .then(function (res) {
        if (!res.ok) throw new Error("Failed to load seats");
        return res.json();
      })
      .then(function (seats) {
        render(Array.isArray(seats) ? seats : []);
      })
      .catch(function () {
        showToast("Could not refresh the seat map. Retrying…", "error");
      });
  }

  // The student map is view-only. Seats are assigned by the admin after
  // payment is received, so there is no booking interaction here.

  // ---- live polling --------------------------------------------------------

  function startPolling() {
    stopPolling();
    pollTimer = setInterval(function () {
      if (document.hidden) return;
      fetchSeats();
    }, POLL_MS);
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) fetchSeats();
  });

  // ---- init ----------------------------------------------------------------

  // Replace the server-rendered paint with live data immediately,
  // then keep it fresh on an interval.
  fetchSeats();
  startPolling();
})();
