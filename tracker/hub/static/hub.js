(function () {
  "use strict";

  var meta = document.querySelector('meta[name="hub-token"]');
  var token = meta ? meta.getAttribute("content") : "";
  var NEXT = { " ": "x", "x": "-", "-": " " };
  var STATUS_LABEL = {
    overdue: "Overdue",
    "due-soon": "Due soon",
    upcoming: "Upcoming",
    done: "Done",
    "not-needed": "N/A",
    open: "No date"
  };

  function toast(message, ms) {
    var box = document.getElementById("hub-toast");
    if (!box) { return; }
    box.textContent = message;
    box.hidden = false;
    window.clearTimeout(toast._timer);
    toast._timer = window.setTimeout(function () { box.hidden = true; }, ms || 4000);
  }

  function post(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Hub-Token": token },
      body: JSON.stringify(body || {})
    }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok) {
          var error = new Error(data.error || ("HTTP " + response.status));
          error.status = response.status;
          error.data = data;
          throw error;
        }
        return data;
      });
    });
  }

  function fail(error) {
    toast(error && error.message ? error.message : "request failed");
  }

  function busy(button, on) {
    if (!button) { return; }
    button.classList.toggle("is-busy", !!on);
    button.disabled = !!on;
  }

  function legacyCopy(text) {
    var area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "readonly");
    area.style.position = "fixed";
    area.style.top = "-1000px";
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    document.body.removeChild(area);
  }

  function copyText(text, message) {
    var done = function () { toast(message); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(function () {
        legacyCopy(text);
        done();
      });
      return;
    }
    legacyCopy(text);
    done();
  }

  function pollStatus() {
    var label = document.getElementById("refresh-state");
    fetch("/api/status").then(function (r) { return r.json(); }).then(function (data) {
      if (data.running) {
        if (label) { label.textContent = "Refreshing..."; }
        window.setTimeout(pollStatus, 2000);
      } else {
        window.location.reload();
      }
    }).catch(function () { window.setTimeout(pollStatus, 3000); });
  }

  function today() {
    return document.body.getAttribute("data-today") || "";
  }

  var WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

  function weekdayName(iso) {
    if (!iso) { return ""; }
    var when = new Date(iso + "T00:00:00");
    if (isNaN(when.getTime())) { return ""; }
    return WEEKDAYS[when.getDay()];
  }

  function ordinal(value) {
    if (value % 100 >= 11 && value % 100 <= 13) { return value + "th"; }
    return value + ({ 1: "st", 2: "nd", 3: "rd" }[value % 10] || "th");
  }

  function buildRule(kind, weekday, due) {
    if (kind === "day") { return "every day"; }
    if (kind === "weekday") { return "every weekday"; }
    if (kind === "week") { return "every week on " + (weekday || "Friday"); }
    if (kind === "2weeks") { return "every 2 weeks on " + (weekday || "Friday"); }
    if (kind === "month") {
      var base = due || today();
      var when = new Date(base + "T00:00:00");
      var day = isNaN(when.getTime()) ? 1 : when.getDate();
      return "every month on the " + ordinal(day);
    }
    return "";
  }

  function dayDiff(due) {
    if (!due) { return null; }
    var base = today();
    if (!base) { return null; }
    return Math.round((Date.parse(due + "T00:00:00") - Date.parse(base + "T00:00:00")) / 86400000);
  }

  function statusFor(mark, due) {
    if (mark === "-") { return "not-needed"; }
    if (mark === "x") { return "done"; }
    if (!due) { return "open"; }
    var soon = parseInt(document.body.getAttribute("data-due-soon"), 10);
    if (isNaN(soon)) { soon = 7; }
    var diff = dayDiff(due);
    if (diff === null) { return "open"; }
    if (diff < 0) { return "overdue"; }
    return diff <= soon ? "due-soon" : "upcoming";
  }

  function applyMark(wrapper, mark) {
    wrapper.setAttribute("data-mark", mark);
    wrapper.className = "tick mark-" + (mark === " " ? "open" : mark === "x" ? "x" : "dash");
    var box = wrapper.querySelector(".tick-box");
    if (box) { box.checked = mark === "x"; }
    var row = wrapper.closest(".task-row") || wrapper.closest("tr");
    if (!row) { return; }
    row.setAttribute("data-mark", mark);
    row.classList.toggle("is-done", mark === "x");
    row.classList.toggle("is-na", mark === "-");
    var status = statusFor(mark, row.getAttribute("data-due"));
    row.setAttribute("data-status", status);
    var pill = row.querySelector(".pill");
    if (pill) {
      pill.className = "pill pill-" + status;
      pill.textContent = STATUS_LABEL[status] || status;
    }
    var days = row.querySelector(".days-cell");
    if (days) {
      var diff = dayDiff(row.getAttribute("data-due"));
      days.textContent = diff === null ? "" : String(diff);
    }
  }

  function syncTask(taskId, mark, source) {
    if (!taskId) { return; }
    document.querySelectorAll('.task-row[data-task-id="' + taskId + '"]').forEach(function (row) {
      var tick = row.querySelector(".tick");
      if (tick && tick !== source) { applyMark(tick, mark); }
    });
  }

  function handleTick(wrapper, event) {
    var current = wrapper.getAttribute("data-mark") || " ";
    var mark;
    if (event.target.closest(".tick-na")) {
      mark = current === "-" ? " " : "-";
    } else if (event.target.closest(".tick-box")) {
      mark = event.target.checked ? "x" : " ";
    } else {
      mark = NEXT[current] || "x";
    }
    if (mark === current) { return; }
    var action = wrapper.getAttribute("data-action");
    var body = { key: wrapper.getAttribute("data-key") || "", mark: mark };
    var url = "/api/task/toggle";
    var id = wrapper.getAttribute("data-id") || "";
    if (action === "toggle") {
      url = "/api/note/toggle";
      body.line_index = parseInt(wrapper.getAttribute("data-index"), 10);
      body.expected_line = wrapper.getAttribute("data-line");
      var noteTask = wrapper.getAttribute("data-note-task");
      if (noteTask) { body.task_id = noteTask; }
    } else if (id) {
      body.id = id;
    } else {
      body.line_index = parseInt(wrapper.getAttribute("data-index"), 10);
      body.expected_line = wrapper.getAttribute("data-line");
    }
    wrapper.classList.add("busy");
    post(url, body).then(function (data) {
      wrapper.classList.remove("busy");
      applyMark(wrapper, data.mark);
      if (data.line) { wrapper.setAttribute("data-line", data.line); }
      syncTask(id || wrapper.getAttribute("data-task-id") || "", data.mark, wrapper);
      var reload = null;
      (data.cascaded || []).forEach(function (item) { syncTask(item.id, item.mark, null); });
      if (action === "toggle" && data.cascaded && data.cascaded.length) {
        reload = function () { window.location.reload(); };
      }
      if (data.spawned) {
        toast("Next occurrence added for " + (data.spawned.due || ""));
        reload = function () { window.location.reload(); };
      }
      if (data.mark === "x") {
        var tickedRow = wrapper.closest(".task-row") || wrapper.closest("tr");
        var trackedChip = tickedRow ? tickedRow.querySelector('[data-action="time-entries"]') : null;
        openTimePop(wrapper, {
          key: body.key,
          id: body.id || "",
          line_index: body.line_index,
          expected_line: data.line || body.expected_line,
          wrapper: wrapper
        }, {
          hours: "",
          days: today() ? [today()] : [],
          tracked: trackedChip ? trackedChip.getAttribute("data-tracked") : "",
          after: reload
        });
        return;
      }
      if (reload) { window.setTimeout(reload, 800); }
    }).catch(function (error) {
      wrapper.classList.remove("busy");
      applyMark(wrapper, current);
      if (error.status === 409) {
        toast("Note changed elsewhere - reloading");
        window.location.reload();
        return;
      }
      fail(error);
    });
  }

  var timePop = null;

  function closeTimePop() {
    if (!timePop) { return; }
    var node = timePop.node;
    var after = timePop.after;
    timePop = null;
    if (node && node.parentNode) { node.parentNode.removeChild(node); }
    if (after) { after(); }
  }

  function isoDay(value) {
    var month = String(value.getMonth() + 1);
    var day = String(value.getDate());
    return value.getFullYear() + "-" + (month.length < 2 ? "0" + month : month) + "-" + (day.length < 2 ? "0" + day : day);
  }

  function weekDates() {
    var base = today() || isoDay(new Date());
    var start = new Date(base + "T00:00:00");
    if (isNaN(start.getTime())) { start = new Date(); }
    start.setDate(start.getDate() - ((start.getDay() + 6) % 7));
    var out = [];
    for (var i = 0; i < 7; i += 1) {
      var day = new Date(start.getTime());
      day.setDate(start.getDate() + i);
      out.push(isoDay(day));
    }
    return out;
  }

  function openTimePop(anchor, address, preset) {
    closeTimePop();
    var node = document.createElement("div");
    node.className = "time-pop";
    var selected = {};
    ((preset && preset.days) || []).forEach(function (day) { selected[day] = true; });
    var label = document.createElement("div");
    label.className = "time-pop-label";
    label.textContent = preset && preset.tracked
      ? "Extra hours (optional) \u2014 already tracked " + preset.tracked + "h"
      : "Hours spent (optional)";
    node.appendChild(label);
    var input = document.createElement("input");
    input.type = "number";
    input.step = "0.25";
    input.min = "0";
    input.className = "time-pop-hours";
    input.placeholder = "0";
    input.value = (preset && preset.hours) || "";
    node.appendChild(input);
    var quick = document.createElement("div");
    quick.className = "time-pop-quick";
    [0.25, 0.5, 1, 2, 4].forEach(function (value) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "chip-btn";
      button.textContent = String(value);
      button.addEventListener("click", function () {
        input.value = String(value);
        input.focus();
      });
      quick.appendChild(button);
    });
    node.appendChild(quick);
    var daysLabel = document.createElement("div");
    daysLabel.className = "time-pop-label";
    daysLabel.textContent = "Worked on";
    node.appendChild(daysLabel);
    var chips = document.createElement("div");
    chips.className = "time-pop-days";
    var names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
    var base = today();
    weekDates().forEach(function (day, index) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "chip-day" + (selected[day] ? " selected" : "");
      button.textContent = names[index];
      button.setAttribute("data-day", day);
      if (base && day > base) { button.disabled = true; }
      button.addEventListener("click", function () {
        if (selected[day]) { delete selected[day]; } else { selected[day] = true; }
        button.classList.toggle("selected", !!selected[day]);
      });
      chips.appendChild(button);
    });
    node.appendChild(chips);
    var foot = document.createElement("div");
    foot.className = "time-pop-foot";
    var save = document.createElement("button");
    save.type = "button";
    save.className = "btn small primary";
    save.textContent = "Save";
    var skip = document.createElement("button");
    skip.type = "button";
    skip.className = "btn small";
    skip.textContent = "Skip";
    skip.addEventListener("click", function () { closeTimePop(); });
    save.addEventListener("click", function () { saveTimePop(address, input, selected, save); });
    foot.appendChild(save);
    foot.appendChild(skip);
    node.appendChild(foot);
    document.body.appendChild(node);
    var box = anchor.getBoundingClientRect();
    var width = node.offsetWidth || 220;
    var left = box.left + window.scrollX;
    var limit = window.scrollX + document.documentElement.clientWidth - width - 8;
    node.style.top = box.bottom + window.scrollY + 6 + "px";
    node.style.left = Math.max(8, Math.min(left, limit)) + "px";
    timePop = { node: node, after: (preset && preset.after) || null };
    input.addEventListener("keydown", function (event) {
      if (event.key === "Enter") {
        event.preventDefault();
        saveTimePop(address, input, selected, save);
      }
    });
    input.focus();
  }

  function saveTimePop(address, input, selected, button) {
    var raw = input.value.trim();
    if (!raw) { closeTimePop(); return; }
    var body = { key: address.key || "", hours: raw, days: Object.keys(selected).sort() };
    if (address.id) {
      body.id = address.id;
    } else {
      body.line_index = address.line_index;
      body.expected_line = address.expected_line;
    }
    busy(button, true);
    post("/api/task/time", body).then(function (data) {
      busy(button, false);
      if (address.wrapper && data.line) { address.wrapper.setAttribute("data-line", data.line); }
      toast("Logged " + data.hours + "h");
      closeTimePop();
    }).catch(function (error) {
      busy(button, false);
      fail(error);
    });
  }

  function editTime(button) {
    var days = (button.getAttribute("data-days") || "").split(",").filter(function (part) { return part; });
    openTimePop(button, {
      key: button.getAttribute("data-key") || "",
      id: button.getAttribute("data-id") || "",
      line_index: parseInt(button.getAttribute("data-index"), 10),
      expected_line: button.getAttribute("data-line") || "",
      wrapper: button.closest(".tick")
    }, { hours: button.getAttribute("data-hours") || "", days: days });
  }

  var trackPop = null;

  function closeTrackPop() {
    if (!trackPop) { return; }
    var node = trackPop.node;
    trackPop = null;
    if (node && node.parentNode) { node.parentNode.removeChild(node); }
  }

  function placePop(node, anchor) {
    var box = anchor.getBoundingClientRect();
    var width = node.offsetWidth || 220;
    var left = box.left + window.scrollX;
    var limit = window.scrollX + document.documentElement.clientWidth - width - 8;
    node.style.top = box.bottom + window.scrollY + 6 + "px";
    node.style.left = Math.max(8, Math.min(left, limit)) + "px";
  }

  function shiftDay(iso, days) {
    var when = new Date(iso + "T00:00:00");
    if (isNaN(when.getTime())) { return iso; }
    when.setDate(when.getDate() + days);
    return isoDay(when);
  }

  function shortDay(iso) {
    var when = new Date(iso + "T00:00:00");
    if (isNaN(when.getTime())) { return iso; }
    return when.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }

  function entriesOf(button) {
    return (button.getAttribute("data-entries") || "").split(",").filter(function (part) {
      return part;
    }).map(function (part) {
      var bits = part.split("=");
      return { day: bits[0], hours: parseFloat(bits[1]) };
    });
  }

  function addressOf(button) {
    return {
      key: button.getAttribute("data-key") || "",
      id: button.getAttribute("data-id") || "",
      line_index: parseInt(button.getAttribute("data-index"), 10),
      expected_line: button.getAttribute("data-line") || ""
    };
  }

  function trackBody(address, extra) {
    var body = { key: address.key || "" };
    if (address.id) {
      body.id = address.id;
    } else {
      body.line_index = address.line_index;
      body.expected_line = address.expected_line;
    }
    Object.keys(extra || {}).forEach(function (name) { body[name] = extra[name]; });
    return body;
  }

  function openTrackPop(anchor, address, preset) {
    closeTimePop();
    closeTrackPop();
    var node = document.createElement("div");
    node.className = "time-pop";
    var label = document.createElement("div");
    label.className = "time-pop-label";
    label.textContent = "Track time: " + (preset.title || "task");
    node.appendChild(label);
    var input = document.createElement("input");
    input.type = "number";
    input.step = "0.25";
    input.min = "0";
    input.max = "24";
    input.className = "time-pop-hours";
    input.placeholder = "0";
    node.appendChild(input);
    var quick = document.createElement("div");
    quick.className = "time-pop-quick";
    [0.25, 0.5, 1, 2, 4].forEach(function (value) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "chip-btn";
      button.textContent = String(value);
      button.addEventListener("click", function () {
        input.value = String(value);
        input.focus();
      });
      quick.appendChild(button);
    });
    node.appendChild(quick);
    var dayLabel = document.createElement("div");
    dayLabel.className = "time-pop-label";
    dayLabel.textContent = "Day";
    node.appendChild(dayLabel);
    var chips = document.createElement("div");
    chips.className = "time-pop-days";
    var names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
    var base = today() || isoDay(new Date());
    var selected = preset.day || base;
    var limitDay = shiftDay(base, 1);
    weekDates().forEach(function (day, index) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "chip-day" + (day === selected ? " selected" : "");
      button.textContent = names[index];
      button.setAttribute("data-day", day);
      if (day > limitDay) { button.disabled = true; }
      button.addEventListener("click", function () {
        selected = day;
        chips.querySelectorAll(".chip-day").forEach(function (other) {
          other.classList.toggle("selected", other === button);
        });
      });
      chips.appendChild(button);
    });
    node.appendChild(chips);
    if (preset.tracked && preset.tracked.length) {
      var note = document.createElement("div");
      note.className = "time-pop-note";
      note.textContent = "Already tracked: " + (preset.trackedLabel || "0") + "h (" + preset.tracked.map(function (entry) {
        return shortDay(entry.day) + " " + entry.hours;
      }).join(", ") + ")";
      node.appendChild(note);
    }
    var foot = document.createElement("div");
    foot.className = "time-pop-foot";
    var add = document.createElement("button");
    add.type = "button";
    add.className = "btn small primary";
    add.textContent = "Add time";
    var cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "btn small";
    cancel.textContent = "Cancel";
    cancel.addEventListener("click", function () { closeTrackPop(); });
    add.addEventListener("click", function () { saveTrackPop(address, input, selected, add); });
    foot.appendChild(add);
    foot.appendChild(cancel);
    node.appendChild(foot);
    document.body.appendChild(node);
    placePop(node, anchor);
    trackPop = { node: node };
    input.addEventListener("keydown", function (event) {
      if (event.key === "Enter") {
        event.preventDefault();
        saveTrackPop(address, input, selected, add);
      }
    });
    input.focus();
  }

  function saveTrackPop(address, input, day, button) {
    var raw = input.value.trim();
    var hours = parseFloat(raw);
    if (!raw || !isFinite(hours) || hours <= 0 || hours > 24) {
      toast("Enter hours between 0 and 24");
      return;
    }
    busy(button, true);
    post("/api/task/time/add", trackBody(address, { hours: hours, day: day })).then(function () {
      busy(button, false);
      toast("Tracked " + hours + "h");
      closeTrackPop();
      window.location.reload();
    }).catch(function (error) {
      busy(button, false);
      fail(error);
    });
  }

  function openEntriesPop(anchor, address, preset) {
    closeTimePop();
    closeTrackPop();
    var node = document.createElement("div");
    node.className = "time-pop";
    var label = document.createElement("div");
    label.className = "time-pop-label";
    label.textContent = "Tracked: " + (preset.trackedLabel || "0") + "h";
    node.appendChild(label);
    var list = document.createElement("div");
    list.className = "time-pop-entries";
    (preset.tracked || []).forEach(function (entry, index) {
      var row = document.createElement("div");
      row.className = "time-pop-entry";
      var text = document.createElement("span");
      text.textContent = shortDay(entry.day) + " \u2014 " + entry.hours + "h";
      var remove = document.createElement("button");
      remove.type = "button";
      remove.className = "chip-btn";
      remove.textContent = "\u00d7";
      remove.title = "Remove entry";
      remove.addEventListener("click", function () {
        busy(remove, true);
        post("/api/task/time/remove", trackBody(address, { entry_index: index })).then(function () {
          busy(remove, false);
          toast("Removed " + entry.hours + "h");
          closeTrackPop();
          window.location.reload();
        }).catch(function (error) {
          busy(remove, false);
          fail(error);
        });
      });
      row.appendChild(text);
      row.appendChild(remove);
      list.appendChild(row);
    });
    node.appendChild(list);
    var foot = document.createElement("div");
    foot.className = "time-pop-foot";
    var close = document.createElement("button");
    close.type = "button";
    close.className = "btn small";
    close.textContent = "Close";
    close.addEventListener("click", function () { closeTrackPop(); });
    foot.appendChild(close);
    node.appendChild(foot);
    document.body.appendChild(node);
    placePop(node, anchor);
    trackPop = { node: node };
  }

  function trackTime(button) {
    openTrackPop(button, addressOf(button), {
      title: button.getAttribute("data-title") || "",
      tracked: entriesOf(button),
      trackedLabel: button.getAttribute("data-tracked") || "",
      day: today()
    });
  }

  function entriesTime(button) {
    openEntriesPop(button, addressOf(button), {
      tracked: entriesOf(button),
      trackedLabel: button.getAttribute("data-tracked") || ""
    });
  }

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && timePop) { closeTimePop(); }
    if (event.key === "Escape" && trackPop) { closeTrackPop(); }
  });

  document.addEventListener("mousedown", function (event) {
    if (timePop && !timePop.node.contains(event.target)) { closeTimePop(); }
    if (trackPop && !trackPop.node.contains(event.target)) { closeTrackPop(); }
  });

  document.addEventListener("click", function (event) {
    var target = event.target.closest(
      "[data-action], [data-layout], #refresh-now, #open-tab-group, .open-tab-group-btn, #note-save, #copy-monday"
    );
    if (!target) { return; }

    if (target.hasAttribute("data-layout")) {
      event.preventDefault();
      if (target.disabled) { return; }
      post("/api/ui", { layout: target.getAttribute("data-layout") })
        .then(function () { window.location.reload(); })
        .catch(fail);
      return;
    }

    if (target.id === "refresh-now") {
      event.preventDefault();
      busy(target, true);
      post("/api/rerun", {}).then(function () {
        var label = document.getElementById("refresh-state");
        if (label) { label.textContent = "Refreshing..."; }
        window.setTimeout(pollStatus, 1500);
      }).catch(function (error) {
        busy(target, false);
        fail(error);
      });
      return;
    }

    if (target.id === "copy-monday") {
      event.preventDefault();
      var area = document.getElementById("monday-text");
      if (!area) { return; }
      var done = function () { toast("Copied"); };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(area.value).then(done).catch(function () {
          area.hidden = false;
          area.select();
          document.execCommand("copy");
          area.hidden = true;
          done();
        });
      } else {
        area.hidden = false;
        area.select();
        document.execCommand("copy");
        area.hidden = true;
        done();
      }
      return;
    }

    if (target.id === "open-tab-group" || target.classList.contains("open-tab-group-btn")) {
      event.preventDefault();
      openTabGroup(target);
      return;
    }

    if (target.id === "note-save") {
      event.preventDefault();
      saveNote(target);
      return;
    }

    var action = target.getAttribute("data-action");
    if (action === "create") { return createPending(target); }
    if (action === "link") { return linkPending(target); }
    if (action === "dismiss") { return dismissPending(target); }
    if (action === "preview") { return previewPending(target); }
    if (action === "simulate-pending") { return simulatePending(target); }
    if (action === "manual-create") { return manualCreate(target); }
    if (action === "project-archive") { return archiveProject(target); }
    if (action === "project-unarchive") { return unarchiveProject(target); }
    if (action === "project-delete") { return deleteProject(target); }
    if (action === "project-restore") { return restoreProject(target); }
    if (action === "project-restore-deleted") { return restoreDeletedProject(target); }
    if (action === "project-open-folder") { return openProjectFolder(target); }
    if (action === "classify") { return classify(target); }
    if (action === "open-email") { return openEmail(target, event); }
    if (action === "due-set") { return setDue(target); }
    if (action === "due-clear") { return clearDue(target); }
    if (action === "due-toggle") { return toggleDue(target); }
    if (action === "task-launch") { return launchTask(target); }
    if (action === "project-file-open") { return openProjectFile(target); }
    if (action === "stage-bulk") { return bulkStage(target); }
    if (action === "archive-selected") { return archiveSelected(target); }
    if (action === "import-scan") { return importScan(target); }
    if (action === "import-create") { return importCreate(target); }
    if (action === "auto-created-dismiss") {
      return post("/api/auto-created/dismiss", { key: target.getAttribute("data-key") || "" }).then(function () { window.location.reload(); }).catch(fail);
    }
    if (action === "owner-flag-dismiss") {
      return post("/api/owner-flags/dismiss", { key: target.getAttribute("data-key") || "" }).then(function () { window.location.reload(); }).catch(fail);
    }
    if (action === "team-reassign") { return teamReassignOpen(target); }
    if (action === "team-reassign-cancel") { document.getElementById("team-reassign-box").hidden = true; return; }
    if (action === "team-reassign-send") { return teamReassignSend(target); }
    if (action === "transfer-import") {
      if (!window.confirm("Copy the previous owner's folder into your vault?")) { return; }
      busy(target, true);
      return post("/api/team/transfer/import", { id: target.getAttribute("data-id") }).then(function (data) {
        toast("Imported to " + data.folder);
        window.location.reload();
      }).catch(function (error) { busy(target, false); fail(error); });
    }
    if (action === "transfer-ack") {
      return post("/api/team/transfer/ack", { id: target.getAttribute("data-id"), side: target.getAttribute("data-side") }).then(function () { window.location.reload(); }).catch(fail);
    }
    if (action === "sync-preview") { return syncPreview(target); }
    if (action === "sync-apply") { return syncApply(target); }
    if (action === "so-add") { return addSo(target); }
    if (action === "backup-export") { return backupExport(target); }
    if (action === "backup-preview") { return backupPreview(target); }
    if (action === "backup-apply") { return backupApply(target); }
    if (action === "backup-dismiss") { return post("/api/backup/dismiss", {}).then(function () { window.location.reload(); }).catch(fail); }
    if (action === "so-dismiss") { return dismissSo(target); }
    if (action === "schedule-link") { return setScheduleLink(target); }
    if (action === "launch-set") { return setLaunch(target); }
    if (action === "launch-reset") { return resetLaunch(target); }
    if (action === "launch-none") { return clearLaunch(target); }
    if (action === "link-add") { return addLink(target); }
    if (action === "link-remove") { return removeLink(target); }
    if (action === "support-email") { return supportEmail(target); }
    if (action === "task-link-add") { return taskLinkAdd(target); }
    if (action === "task-link-remove") { return taskLinkRemove(target); }
    if (action === "task-folder-set") { return taskFolderSet(target); }
    if (action === "time-remove") { return removeTimeEntry(target); }
    if (action === "open") { event.preventDefault(); return openTarget(target); }
    if (action === "open-all") { return openAll(target); }
    if (action === "toggle") { return handleTick(target, event); }
    if (action === "task-toggle") { return handleTick(target, event); }
    if (action === "task-add") { return addTask(target); }
    if (action === "task-edit") { return editTask(target); }
    if (action === "task-save") { return saveTask(target); }
    if (action === "task-cancel") { return cancelTask(target); }
    if (action === "task-delete") { return deleteTask(target); }
    if (action === "task-archive") { return archiveTask(target); }
    if (action === "task-unarchive") { return unarchiveTask(target); }
    if (action === "time-edit") { return editTime(target); }
    if (action === "time-track") { return trackTime(target); }
    if (action === "time-entries") { return entriesTime(target); }
    if (action === "quick-due") { return quickDue(target); }
    if (action === "card-expand") { return expandCard(target); }
    if (action === "new-task") { return openNewTask(); }
    if (action === "ts-copy") {
      return copyText(target.getAttribute("data-text") || "", "Copied " + (target.getAttribute("data-text") || ""));
    }
    if (action === "ts-spread") { return spreadRow(target); }
    if (action === "ts-copy-week") { return copyWeek(); }
  });

  document.addEventListener("change", function (event) {
    var choice = event.target.closest('[data-action="project-file-choose"]');
    if (choice) { chooseProjectFile(choice); return; }
    var stageSelect = event.target.closest('[data-action="stage-set"]');
    if (stageSelect) { setStage(stageSelect); return; }
    if (event.target.id === "stage-filter") { filterStage(event.target.value); return; }
    if (event.target.id === "stage-select-all") { selectAllStage(event.target.checked); return; }
    if (event.target.id === "sync-select-all") { syncSelectAll(event.target.checked); return; }
    if (event.target.classList && event.target.classList.contains("sync-pick")) { syncCount(); return; }
    if (event.target.classList && event.target.classList.contains("backup-pick")) { backupCount(); return; }
    if (event.target.classList && event.target.classList.contains("stage-pick")) { countStagePicks(); return; }
    if (event.target.id === "import-select-all") { importSelectAll(event.target.checked); return; }
    if (event.target.classList && event.target.classList.contains("import-pick")) { importCount(); return; }
    var cell = event.target.closest(".ts-cell");
    if (!cell) { return; }
    var value = cell.value.trim();
    var hours = value === "" ? "" : parseFloat(value);
    if (hours !== "" && !isFinite(hours)) { return; }
    post("/api/timesheet/cell", {
      row_id: cell.getAttribute("data-row"),
      day: cell.getAttribute("data-day"),
      hours: hours
    }).then(function () {
      if (hours === "") {
        window.location.reload();
        return;
      }
      cell.classList.add("edited");
      recalcTimesheet();
    }).catch(fail);
  });

  document.addEventListener("input", function (event) {
    var form = event.target.closest("#manual-project");
    if (!form) { return; }
    if (event.target.name === "folder_name") {
      event.target.setAttribute("data-touched", "1");
      return;
    }
    suggestFolder();
  });

  var groupRequests = {};

  window.addEventListener("message", function (event) {
    if (event.source !== window || !event.data || event.data.source !== "project-hub-ext") { return; }
    var pending = groupRequests[event.data.requestId];
    if (!pending) { return; }
    delete groupRequests[event.data.requestId];
    window.clearTimeout(pending.timer);
    busy(pending.button, false);
    var data = event.data;
    if (!data.ok) {
      toast("Tab group failed: " + (data.error || "unknown error"));
      return;
    }
    if (data.groupId === null) {
      toast("No links to open for this project");
      return;
    }
    var parts = [];
    if (data.created) { parts.push(data.created + " opened"); }
    if (data.adopted) { parts.push(data.adopted + " already-open moved in"); }
    toast("Tab group \"" + (data.title || "") + "\" ready" + (parts.length ? " (" + parts.join(", ") + ")" : ""));
  });

  function openTabGroup(button) {
    var key = button.getAttribute("data-key");
    if (!key) { return; }
    var flag = document.documentElement.dataset.hubExtension;
    if (flag === "1") {
      toast("Old extension version loaded - click Reload on Project Hub Tab Groups in chrome://extensions, then refresh this page.");
      return;
    }
    if (flag !== "2") {
      toast("The Project Hub Tab Groups extension is not active on this page. Load or reload chrome_extension in chrome://extensions, then refresh (F5).");
      return;
    }
    var requestId = "g" + Date.now() + Math.random().toString(16).slice(2);
    busy(button, true);
    groupRequests[requestId] = {
      button: button,
      timer: window.setTimeout(function () {
        if (!groupRequests[requestId]) { return; }
        delete groupRequests[requestId];
        busy(button, false);
        toast("No answer from the tab-group extension - refresh this page (F5) and try again.");
      }, 15000)
    };
    window.postMessage({ source: "project-hub-page", type: "openGroup", key: key, requestId: requestId }, window.location.origin);
  }

  function waitForRefresh(url, timeoutMs) {
    var deadline = Date.now() + (timeoutMs || 180000);
    function tick() {
      fetch("/api/status").then(function (r) { return r.json(); }).then(function (data) {
        if (!data.running || Date.now() > deadline) {
          window.location.href = url;
          return;
        }
        window.setTimeout(tick, 1500);
      }).catch(function () { window.setTimeout(tick, 1500); });
    }
    tick();
  }

  // {group: option} from the file-choice selects inside scope; null (after a toast) if one is unanswered.
  function fileChoices(scope) {
    var choices = {};
    var selects = scope ? scope.querySelectorAll("select.file-choice") : [];
    for (var i = 0; i < selects.length; i++) {
      if (!selects[i].value) {
        toast("Choose a " + (selects[i].getAttribute("aria-label") || "file").toLowerCase() + " first");
        selects[i].focus();
        return null;
      }
      choices[selects[i].getAttribute("data-group")] = selects[i].value;
    }
    return choices;
  }

  function createdMessage(data) {
    var skipped = (data && data.files_skipped) || [];
    var text = "Created " + data.folder;
    if (skipped.length) { text += " - not copied: " + skipped.join("; "); }
    return text + " - building project page\u2026";
  }

  function createPending(button) {
    var messageId = button.getAttribute("data-message-id");
    var input = document.querySelector('.folder-input[data-message-id="' + messageId + '"]');
    var choices = fileChoices(button.closest(".pending"));
    if (!choices) { return; }
    busy(button, true);
    toast("Creating project\u2026");
    post("/api/pending/create", {
      message_id: messageId,
      folder_name: input ? input.value : "",
      choices: choices
    }).then(function (data) {
      toast(createdMessage(data));
      waitForRefresh(data.key_url, (data.files_skipped || []).length ? 8000 : 180000);
    }).catch(function (error) {
      busy(button, false);
      fail(error);
    });
  }

  function linkPending(button) {
    var messageId = button.getAttribute("data-message-id");
    var select = document.querySelector('.link-select[data-message-id="' + messageId + '"]');
    if (!select || !select.value) { toast("Pick a folder first"); return; }
    busy(button, true);
    toast("Linking folder\u2026");
    post("/api/pending/link", { message_id: messageId, folder: select.value }).then(function (data) {
      toast("Linked " + data.folder + " - building project page\u2026");
      waitForRefresh(data.key_url, 180000);
    }).catch(function (error) {
      busy(button, false);
      fail(error);
    });
  }

  function simulatePending(button) {
    busy(button, true);
    post("/api/pending/simulate", {}).then(function () {
      toast("Test assignment added - go to the Dashboard to Create / Link / Dismiss it");
      window.setTimeout(function () { window.location.href = "/"; }, 1000);
    }).catch(function (error) {
      busy(button, false);
      fail(error);
    });
  }

  function manualCreate(button) {
    var form = document.getElementById("manual-project");
    if (!form) { return; }
    var value = function (name) {
      var input = form.elements[name];
      return input ? input.value.trim() : "";
    };
    var customer = value("customer");
    if (!customer) { toast("Customer is required"); return; }
    var choices = fileChoices(form);
    if (!choices) { return; }
    busy(button, true);
    toast("Creating project\u2026");
    post("/api/projects/create", {
      customer: customer,
      so: value("so"),
      qte: value("qte"),
      project_no: value("project_no"),
      description: value("description"),
      level: value("level"),
      folder_name: value("folder_name"),
      choices: choices
    }).then(function (data) {
      toast(createdMessage(data));
      waitForRefresh(data.key_url, (data.files_skipped || []).length ? 8000 : 180000);
    }).catch(function (error) {
      busy(button, false);
      fail(error);
    });
  }

  function suggestFolder() {
    var form = document.getElementById("manual-project");
    if (!form) { return; }
    var folder = form.elements["folder_name"];
    if (!folder || folder.getAttribute("data-touched") === "1") { return; }
    var value = function (name) {
      var input = form.elements[name];
      return input ? input.value.trim() : "";
    };
    var first = function (raw) {
      var parts = raw.split(/[,;\s]+/).filter(function (part) { return part; });
      return parts.length ? parts[0] : "";
    };
    var ident = first(value("so")) || first(value("qte"));
    if (form.getAttribute("data-layout") === "customer/so") {
      var leaf = ident || value("project_no");
      folder.placeholder = (value("customer") || leaf)
        ? (value("customer") || "Unknown Customer") + "/" + (leaf || "Project")
        : "auto: customer/SO";
      return;
    }
    var parts = [value("customer"), ident, value("project_no")].filter(function (part) { return part; });
    folder.placeholder = parts.length ? parts.join(" ") : "auto from customer / SO / project #";
  }

  function projectAction(button, url, body, message) {
    busy(button, true);
    post(url, body).then(function (data) {
      toast(message(data));
      window.setTimeout(pollStatus, 1500);
    }).catch(function (error) {
      busy(button, false);
      fail(error);
    });
  }

  function archiveProject(button) {
    var name = button.getAttribute("data-name") || button.getAttribute("data-key");
    if (!window.confirm('Archive "' + name + '"?\n\nThe folder stays where it is. The hub stops reading it and hides it from the boards; emails with its SO/QTE still match it. Use Reactivate to bring it back.')) { return; }
    busy(button, true);
    post("/api/projects/archive", { key: button.getAttribute("data-key") }).then(function () {
      window.location.reload();
    }).catch(function (error) {
      busy(button, false);
      fail(error);
    });
  }

  function unarchiveProject(button) {
    busy(button, true);
    post("/api/projects/unarchive", { key: button.getAttribute("data-key") }).then(function () {
      toast("Reactivated: the note is re-read on this refresh");
      window.setTimeout(pollStatus, 1500);
    }).catch(function (error) {
      busy(button, false);
      fail(error);
    });
  }

  function deleteProject(button) {
    var key = button.getAttribute("data-key");
    var name = button.getAttribute("data-name") || key;
    if (!window.confirm('Delete "' + name + '"?\n\nThe folder is moved to the deleted-projects backup (not erased) and can be restored from this page.')) { return; }
    projectAction(button, "/api/projects/delete", { key: key, confirm: key }, function (data) {
      return "Moved to " + data.moved_to;
    });
  }

  function restoreProject(button) {
    projectAction(button, "/api/projects/restore", { name: button.getAttribute("data-name") }, function (data) {
      return "Restored to " + data.moved_to;
    });
  }

  function restoreDeletedProject(button) {
    projectAction(button, "/api/projects/restore_deleted", { name: button.getAttribute("data-name") }, function (data) {
      return "Restored to " + data.moved_to;
    });
  }

  function openProjectFolder(button) {
    var where = button.getAttribute("data-where") || "";
    if (where) {
      post("/api/projects/open_folder", { where: where, name: button.getAttribute("data-name") }).catch(fail);
      return;
    }
    post("/api/open", { key: button.getAttribute("data-key"), what: "folder" }).catch(fail);
  }

  function dismissPending(button) {
    post("/api/pending/dismiss", { message_id: button.getAttribute("data-message-id") })
      .then(function () { window.location.reload(); })
      .catch(fail);
  }

  function previewPending(button) {
    var messageId = button.getAttribute("data-message-id");
    var box = document.querySelector('.preview[data-message-id="' + messageId + '"]');
    if (!box) { return; }
    if (!box.hidden) { box.hidden = true; return; }
    fetch("/api/pending/preview?message_id=" + encodeURIComponent(messageId))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        box.textContent = data.text || data.error || "";
        box.hidden = false;
      })
      .catch(fail);
  }

  function classify(button) {
    post("/api/classify", {
      message_id: button.getAttribute("data-message-id"),
      trigger: button.getAttribute("data-trigger")
    }).then(function () { window.location.reload(); }).catch(fail);
  }

  function openEmail(button, event) {
    if (event) { event.preventDefault(); }
    busy(button, true);
    post("/api/email/open", { message_id: button.getAttribute("data-message-id") })
      .then(function () { busy(button, false); toast("Opened in Outlook"); })
      .catch(function (error) { busy(button, false); fail(error); });
  }

  function setDue(button) {
    var taskId = button.getAttribute("data-task-id");
    var due = document.querySelector('.due-input[data-task-id="' + taskId + '"]');
    var note = document.querySelector('.due-note[data-task-id="' + taskId + '"]');
    post("/api/due_override", {
      task_id: taskId,
      due: due ? due.value : "",
      note: note ? note.value : ""
    }).then(function () { window.location.reload(); }).catch(fail);
  }

  function clearDue(button) {
    post("/api/due_override", { task_id: button.getAttribute("data-task-id"), clear: true })
      .then(function () { window.location.reload(); })
      .catch(fail);
  }

  function toggleDue(button) {
    var controls = button.parentElement.querySelector(".due-controls");
    if (!controls) { return; }
    controls.hidden = !controls.hidden;
  }

  function fileName(path) {
    return String(path || "").split(/[\\/]/).pop();
  }

  function openProjectFile(button) {
    busy(button, true);
    post("/api/projects/files/open", {
      key: button.getAttribute("data-key"),
      item: button.getAttribute("data-item")
    }).then(function (data) {
      busy(button, false);
      if ((data.created || []).length) {
        toast("Created " + fileName(data.created[0]));
        button.textContent = "Open";
        var row = button.closest(".project-file");
        var name = row && row.querySelector(".file-name");
        if (name) { name.textContent = fileName(data.created[0]); }
      } else {
        toast("Opened " + fileName(data.launched));
      }
    }).catch(function (error) {
      busy(button, false);
      fail(error);
    });
  }

  function setStage(select) {
    select.disabled = true;
    post("/api/projects/stage", { keys: [select.getAttribute("data-key")], stage: select.value }).then(function (data) {
      toast(data.stage ? "Stage set to " + data.stage : "Stage follows emails again - refreshing\u2026");
      window.setTimeout(function () { window.location.reload(); }, data.stage ? 400 : 3000);
    }).catch(function (error) {
      select.disabled = false;
      fail(error);
    });
  }

  function stageRows() {
    return Array.prototype.slice.call(document.querySelectorAll("tr.project-row"));
  }

  function filterStage(value) {
    stageRows().forEach(function (row) {
      var show = !value || row.getAttribute("data-stage") === value;
      row.style.display = show ? "" : "none";
      if (!show) {
        var box = row.querySelector(".stage-pick");
        if (box) { box.checked = false; }
      }
    });
    var all = document.getElementById("stage-select-all");
    if (all) { all.checked = false; }
    countStagePicks();
  }

  function selectAllStage(checked) {
    stageRows().forEach(function (row) {
      var box = row.querySelector(".stage-pick");
      if (box && row.style.display !== "none") { box.checked = checked; }
    });
    countStagePicks();
  }

  function countStagePicks() {
    var label = document.getElementById("stage-selected-count");
    if (label) { label.textContent = document.querySelectorAll(".stage-pick:checked").length + " selected"; }
  }

  function bulkStage(button) {
    var select = document.getElementById("stage-bulk-value");
    var keys = Array.prototype.map.call(document.querySelectorAll(".stage-pick:checked"), function (box) {
      return box.getAttribute("data-key");
    });
    if (!keys.length) { toast("Tick the projects to move first"); return; }
    if (!select || !select.value) { toast("Choose a stage first"); return; }
    var stage = select.value === "__clear__" ? "" : select.value;
    busy(button, true);
    post("/api/projects/stage", { keys: keys, stage: stage }).then(function (data) {
      var text = "Moved " + data.updated + " project" + (data.updated === 1 ? "" : "s") + (stage ? " to " + stage : " back to following emails");
      if ((data.failed || []).length) { text += " - failed: " + data.failed.join("; "); }
      toast(text);
      window.setTimeout(function () { window.location.reload(); }, (data.failed || []).length ? 6000 : 800);
    }).catch(function (error) {
      busy(button, false);
      fail(error);
    });
  }

  var teamPick = null;

  function teamReassignOpen(button) {
    var box = document.getElementById("team-reassign-box");
    if (!box) { return; }
    teamPick = { key: button.getAttribute("data-key"), from: button.getAttribute("data-from") };
    document.getElementById("team-reassign-label").textContent = button.getAttribute("data-label") || teamPick.key;
    document.getElementById("team-reassign-from").textContent = teamPick.from;
    box.hidden = false;
    box.scrollIntoView({ block: "nearest" });
  }

  function teamReassignSend(button) {
    if (!teamPick) { return; }
    var to = document.getElementById("team-reassign-to").value;
    if (to === teamPick.from) { toast("Pick someone other than the current owner"); return; }
    busy(button, true);
    post("/api/team/reassign", { key: teamPick.key, from: teamPick.from, to: to }).then(function () {
      toast("Reassign request sent to " + to);
      window.location.reload();
    }).catch(function (error) { busy(button, false); fail(error); });
  }

  function archiveSelected(button) {
    var keys = Array.prototype.map.call(document.querySelectorAll(".stage-pick:checked"), function (box) {
      return box.getAttribute("data-key");
    });
    if (!keys.length) { toast("Tick the projects to archive first"); return; }
    if (!window.confirm("Archive " + keys.length + " project" + (keys.length === 1 ? "" : "s") + "?\n\nThe folders stay where they are. The hub stops reading them and hides them from the boards; emails with their SO/QTE still match. Use Reactivate to bring one back.")) { return; }
    busy(button, true);
    post("/api/projects/archive", { keys: keys }).then(function (data) {
      var text = "Archived " + data.archived + " project" + (data.archived === 1 ? "" : "s");
      if ((data.failed || []).length) { text += " - failed: " + data.failed.join("; "); }
      toast(text);
      window.setTimeout(function () { window.location.reload(); }, (data.failed || []).length ? 6000 : 800);
    }).catch(function (error) {
      busy(button, false);
      fail(error);
    });
  }

  function importCount() {
    var box = document.getElementById("import-count");
    var picked = document.querySelectorAll(".import-pick:checked").length;
    var total = document.querySelectorAll(".import-pick").length;
    if (box) { box.textContent = picked + " of " + total + " selected"; }
  }

  function importSelectAll(checked) {
    Array.prototype.forEach.call(document.querySelectorAll(".import-pick"), function (box) { box.checked = checked; });
    importCount();
  }

  function importRender(rows) {
    var body = document.getElementById("import-rows");
    var panel = document.getElementById("import-preview");
    if (!body || !panel) { return; }
    body.innerHTML = rows.map(function (row) {
      var numbers = (row.so || []).concat(row.qte || []);
      if (row.project_no) { numbers.push(row.project_no); }
      return "<tr><td><input type=\"checkbox\" class=\"import-pick\" data-id=\"" + esc(row.id) + "\"" + (row.checked ? " checked" : "") +
        " aria-label=\"Create " + esc(row.folder) + "\"></td>" +
        "<td class=\"nowrap\">" + esc(String(row.received || "").slice(0, 10)) + "</td>" +
        "<td>" + esc(row.customer) + "</td>" +
        "<td class=\"nowrap small\">" + esc(numbers.join(", ")) + "</td>" +
        "<td>" + esc(row.stage) + (row.sheet_status ? "<div class=\"small muted\">sheet: " + esc(row.sheet_status) + "</div>" : "") + "</td>" +
        "<td class=\"small mono\">" + esc(row.folder) + "</td>" +
        "<td class=\"small\">" + esc(row.notes) + "</td></tr>";
    }).join("");
    panel.hidden = !rows.length;
    var all = document.getElementById("import-select-all");
    if (all) { all.checked = false; }
    importCount();
  }

  function importPoll(button, done) {
    var status = document.getElementById("import-status");
    fetch("/api/import/status").then(function (r) { return r.json(); }).then(function (data) {
      if (status) { status.textContent = data.error || data.message || ""; }
      if (data.running) {
        window.setTimeout(function () { importPoll(button, done); }, 2000);
        return;
      }
      busy(button, false);
      done(data);
    }).catch(function () { window.setTimeout(function () { importPoll(button, done); }, 3000); });
  }

  function importScan(button) {
    var days = document.getElementById("import-days");
    var archive = document.getElementById("import-archive");
    busy(button, true);
    post("/api/import/scan", { days: days ? days.value : "", include_archive: archive ? archive.checked : false }).then(function () {
      importPoll(button, function (data) {
        if (data.error) { fail(new Error(data.error)); return; }
        importRender(data.rows || []);
      });
    }).catch(function (error) {
      busy(button, false);
      fail(error);
    });
  }

  function importCreate(button) {
    var ids = Array.prototype.map.call(document.querySelectorAll(".import-pick:checked"), function (box) {
      return box.getAttribute("data-id");
    });
    if (!ids.length) { toast("Tick the projects to create first"); return; }
    if (!window.confirm("Create " + ids.length + " project folder" + (ids.length === 1 ? "" : "s") + " in the vault?")) { return; }
    busy(button, true);
    post("/api/import/create", { ids: ids }).then(function () {
      importPoll(button, function (data) {
        if (data.error) { fail(new Error(data.error)); return; }
        var result = data.result || {};
        var text = "Created " + (result.created || 0) + " project" + (result.created === 1 ? "" : "s");
        if ((result.failed || []).length) { text += " - failed: " + result.failed.join("; "); }
        toast(text);
        importRender(data.rows || []);
      });
    }).catch(function (error) {
      busy(button, false);
      fail(error);
    });
  }

  function syncCount() {
    var box = document.getElementById("sync-count");
    var picked = document.querySelectorAll(".sync-pick:checked").length;
    var total = document.querySelectorAll(".sync-pick").length;
    if (box) { box.textContent = picked + " of " + total + " selected"; }
  }

  function syncSelectAll(checked) {
    Array.prototype.forEach.call(document.querySelectorAll(".sync-pick:not(.sync-so)"), function (box) { box.checked = checked; });
    syncCount();
  }

  function syncPreview(button) {
    var status = document.getElementById("sync-status");
    var panel = document.getElementById("sync-preview");
    var body = document.getElementById("sync-rows");
    busy(button, true);
    if (status) { status.textContent = "Reading the Project Tracking sheet\u2026"; }
    post("/api/tracker-sync/preview", {}).then(function (data) {
      busy(button, false);
      var items = data.proposals || [];
      var sos = data.so_suggestions || [];
      var sheet = data.sheet || {};
      var tabs = sheet.tabs || {};
      var text = "Sheet: " + (tabs.Active || 0) + " active, " + (tabs.Completed || 0) + " completed rows" +
        (sheet.updated ? " (copy " + sheet.updated + ")" : "") + ". " + items.length + " suggested change" + (items.length === 1 ? "" : "s") + ", " +
        sos.length + " SO number" + (sos.length === 1 ? "" : "s") + " to add.";
      if (sheet.warning) { text += " " + sheet.warning; }
      if (status) { status.textContent = text; }
      body.innerHTML = items.map(function (item) {
        return "<tr><td><input type=\"checkbox\" class=\"sync-pick\" data-key=\"" + esc(item.key) + "\" data-stage=\"" + esc(item.proposed) + "\"" +
          (item.checked ? " checked" : "") + " aria-label=\"Apply " + esc(item.name) + "\"></td>" +
          "<td><a href=\"/p/" + encodeURIComponent(item.key) + "\">" + esc(item.name) + "</a></td>" +
          "<td>" + esc(item.customer) + "</td><td>" + esc(item.current) + "</td><td><strong>" + esc(item.proposed) + "</strong></td>" +
          "<td class=\"small\">" + esc(item.reason) + (item.note ? " <span class=\"badge level\">" + esc(item.note) + "</span>" : "") + "</td></tr>";
      }).join("");
      var soBody = document.getElementById("sync-so-rows");
      var soWrap = document.getElementById("sync-so-wrap");
      if (soBody) {
        soBody.innerHTML = sos.map(function (item) {
          return "<tr><td><input type=\"checkbox\" class=\"sync-pick sync-so\" data-key=\"" + esc(item.key) + "\" data-so=\"" + esc(item.so) + "\"" +
            (item.checked ? " checked" : "") + " aria-label=\"Add " + esc(item.so) + " to " + esc(item.name) + "\"></td>" +
            "<td><a href=\"/p/" + encodeURIComponent(item.key) + "\">" + esc(item.name) + "</a></td>" +
            "<td>" + esc(item.customer) + "</td><td><strong>" + esc(item.so) + "</strong></td>" +
            "<td class=\"small\">" + esc(item.rename_to || "(no rename)") + "</td>" +
            "<td class=\"small\">" + esc(item.reason) + (item.note ? " <span class=\"badge level\">" + esc(item.note) + "</span>" : "") + "</td></tr>";
        }).join("");
      }
      if (soWrap) { soWrap.hidden = !sos.length; }
      panel.hidden = !items.length && !sos.length;
      var all = document.getElementById("sync-select-all");
      if (all) { all.checked = false; }
      syncCount();
    }).catch(function (error) {
      busy(button, false);
      if (status) { status.textContent = ""; }
      fail(error);
    });
  }

  function syncApply(button) {
    var items = Array.prototype.map.call(document.querySelectorAll(".sync-pick:not(.sync-so):checked"), function (box) {
      return { key: box.getAttribute("data-key"), stage: box.getAttribute("data-stage") };
    });
    var soItems = Array.prototype.map.call(document.querySelectorAll(".sync-so:checked"), function (box) {
      return { key: box.getAttribute("data-key"), so: box.getAttribute("data-so") };
    });
    if (!items.length && !soItems.length) { toast("Tick the suggestions to apply first"); return; }
    var rename = document.getElementById("sync-rename");
    busy(button, true);
    post("/api/tracker-sync/apply", { items: items, so_items: soItems, rename: rename ? rename.checked : true }).then(function (data) {
      var text = "Updated " + data.updated + " stage" + (data.updated === 1 ? "" : "s");
      if (data.so_added) { text += ", added " + data.so_added + " SO number" + (data.so_added === 1 ? "" : "s") + ", renamed " + data.renamed + " folder" + (data.renamed === 1 ? "" : "s"); }
      var notes = (data.warnings || []).concat((data.failed || []).map(function (line) { return "failed: " + line; }));
      if (notes.length) { text += " - " + notes.join("; "); }
      toast(text);
      window.setTimeout(function () { window.location.reload(); }, notes.length ? 8000 : 800);
    }).catch(function (error) {
      busy(button, false);
      fail(error);
    });
  }

  var BACKUP_KINDS = {
    "new": "new in backup",
    "changed": "changed in backup",
    "conflict": "changed on both: saved beside the R: file as (offline date)",
    "newer": "newer in backup (no sync record yet)",
    "old": "only in the older backup copy (from before the first backup)",
    "deleted": "deleted in backup: tick to delete from R:"
  };

  function backupCount() {
    var box = document.getElementById("backup-count");
    var picked = document.querySelectorAll(".backup-pick:checked").length;
    var total = document.querySelectorAll(".backup-pick").length;
    if (box) { box.textContent = picked + " of " + total + " selected"; }
  }

  function backupPoll() {
    var state = document.getElementById("backup-state");
    fetch("/api/backup/status").then(function (r) { return r.json(); }).then(function (data) {
      var job = data.job || {};
      if (job.running) {
        if (state) {
          state.textContent = "Backing up " + (job.pair || "") + (job.total ? ": " + job.done + " of " + job.total + " files" : "\u2026") +
            (job.file ? " (" + job.file + ")" : "");
        }
        window.setTimeout(backupPoll, 1500);
        return;
      }
      var result = job.result || {};
      var parts = (result.pairs || []).map(function (item) { return item.name + ": " + item.copied + " copied"; });
      toast("Backup finished" + (parts.length ? " - " + parts.join(", ") : "") + ((result.errors || []).length ? " - " + result.errors.join("; ") : ""));
      window.setTimeout(function () { window.location.reload(); }, 1500);
    }).catch(function () { window.setTimeout(backupPoll, 5000); });
  }

  function backupExport(button) {
    busy(button, true);
    post("/api/backup/export", {}).then(function () { backupPoll(); }).catch(function (error) {
      busy(button, false);
      fail(error);
    });
  }

  function backupPreview(button) {
    var state = document.getElementById("backup-state");
    var panel = document.getElementById("backup-preview");
    var body = document.getElementById("backup-rows");
    busy(button, true);
    if (state) { state.textContent = "Comparing the backup with R:\u2026"; }
    post("/api/backup/import/preview", {}).then(function (data) {
      busy(button, false);
      var items = data.items || [];
      if (state) { state.textContent = items.length + " file" + (items.length === 1 ? "" : "s") + " changed in the backup." + ((data.errors || []).length ? " " + data.errors.join("; ") : ""); }
      body.innerHTML = items.map(function (item) {
        return "<tr><td><input type=\"checkbox\" class=\"backup-pick\" data-pair=\"" + esc(item.pair) + "\" data-rel=\"" + esc(item.rel) + "\"" +
          (item.checked ? " checked" : "") + " aria-label=\"Import " + esc(item.rel) + "\"></td><td>" + esc(item.pair) + "</td>" +
          "<td class=\"mono small\">" + esc(item.rel) + "</td><td class=\"small\">" + esc(BACKUP_KINDS[item.kind] || item.kind) + "</td>" +
          "<td class=\"nowrap small\">" + esc(item.modified || "") + "</td></tr>";
      }).join("");
      panel.hidden = !items.length;
      backupCount();
    }).catch(function (error) {
      busy(button, false);
      if (state) { state.textContent = ""; }
      fail(error);
    });
  }

  function backupApply(button) {
    var items = Array.prototype.map.call(document.querySelectorAll(".backup-pick:checked"), function (box) {
      return { pair: box.getAttribute("data-pair"), rel: box.getAttribute("data-rel") };
    });
    if (!items.length) { toast("Tick the files to import first"); return; }
    busy(button, true);
    post("/api/backup/import/apply", { items: items }).then(function (data) {
      var text = "Imported " + (data.applied || []).length + " file" + ((data.applied || []).length === 1 ? "" : "s");
      if ((data.failed || []).length) { text += " - failed: " + data.failed.join("; "); }
      toast(text);
      window.setTimeout(function () { window.location.reload(); }, (data.failed || []).length ? 8000 : 1200);
    }).catch(function (error) {
      busy(button, false);
      fail(error);
    });
  }

  function addSo(button) {
    var input = document.getElementById("so-input");
    var value = button.getAttribute("data-so") || (input ? input.value : "");
    if (!/SO[-\s]?\d{6}/i.test(value)) { toast("Enter an SO number like SO-285770"); return; }
    var rename = document.getElementById("so-rename");
    busy(button, true);
    post("/api/projects/so", { key: button.getAttribute("data-key"), so: value, rename: rename ? rename.checked : true }).then(function (data) {
      var text = "Added " + data.added.join(", ") + (data.renamed ? " - folder renamed to " + data.renamed : "");
      if ((data.warnings || []).length) { text += " - " + data.warnings.join("; "); }
      toast(text);
      window.setTimeout(function () { window.location.href = "/p/" + encodeURIComponent(data.key); }, (data.warnings || []).length ? 6000 : 900);
    }).catch(function (error) {
      busy(button, false);
      fail(error);
    });
  }

  function dismissSo(button) {
    busy(button, true);
    post("/api/projects/so/dismiss", { key: button.getAttribute("data-key"), so: button.getAttribute("data-so") }).then(function () {
      var row = button.closest(".so-hint");
      if (row) { row.parentNode.removeChild(row); }
    }).catch(function (error) {
      busy(button, false);
      fail(error);
    });
  }

  function setScheduleLink(button) {
    var linked = button.getAttribute("data-linked") === "1";
    busy(button, true);
    post("/api/projects/schedule_link", { key: button.getAttribute("data-key"), linked: linked }).then(function () {
      toast(linked ? "Using schedule again - refreshing\u2026" : "Unlinked from schedule - refreshing\u2026");
      window.setTimeout(function () { window.location.reload(); }, 3000);
    }).catch(function (error) {
      busy(button, false);
      fail(error);
    });
  }

  function chooseProjectFile(select) {
    if (!select.value) { return; }
    select.disabled = true;
    post("/api/projects/files/choose", {
      key: select.getAttribute("data-key"),
      group: select.getAttribute("data-group"),
      option: select.value
    }).then(function () {
      window.location.reload();
    }).catch(function (error) {
      select.disabled = false;
      fail(error);
    });
  }

  function launchTask(button) {
    var label = button.textContent.replace(/\u25B6|\uD83D\uDCC4/g, "").trim();
    busy(button, true);
    toast("Launching " + label + "\u2026");
    post("/api/task/launch", {
      key: button.getAttribute("data-key"),
      task_id: button.getAttribute("data-task-id")
    }).then(function (data) {
      busy(button, false);
      var created = (data && data.created) || [];
      var existing = (data && data.existing) || [];
      if (created.length) {
        toast("Created " + fileName(created[0]));
      } else if (existing.length) {
        toast("Opened existing " + fileName(existing[0]));
      } else {
        toast("Opened");
      }
    }).catch(function (error) {
      busy(button, false);
      fail(error);
    });
  }

  function setLaunch(button) {
    var row = button.closest(".task-row");
    var taskId = button.getAttribute("data-task-id");
    var input = row ? row.querySelector('.launch-input[data-task-id="' + taskId + '"]') : null;
    post("/api/task/launch_set", {
      key: button.getAttribute("data-key"),
      task_id: taskId,
      target: input ? input.value : ""
    }).then(function () { window.location.reload(); }).catch(fail);
  }

  function resetLaunch(button) {
    post("/api/task/launch_set", {
      key: button.getAttribute("data-key"),
      task_id: button.getAttribute("data-task-id"),
      reset: true
    }).then(function () { window.location.reload(); }).catch(fail);
  }

  function clearLaunch(button) {
    post("/api/task/launch_set", {
      key: button.getAttribute("data-key"),
      task_id: button.getAttribute("data-task-id"),
      target: ""
    }).then(function () { window.location.reload(); }).catch(fail);
  }

  function addLink(button) {
    var key = button.getAttribute("data-key");
    var title = document.querySelector('.link-title[data-key="' + key + '"]');
    var url = document.querySelector('.link-url[data-key="' + key + '"]');
    post("/api/links/add", {
      key: key,
      title: title ? title.value : "",
      url: url ? url.value : ""
    }).then(function () { window.location.reload(); }).catch(fail);
  }

  function supportEmail(button) {
    busy(button, true);
    post("/api/project/support-email", { key: button.getAttribute("data-key") }).then(function (data) {
      var missing = data.missing || [];
      toast("Draft opened in Outlook." + (missing.length ? " Fill in: " + missing.join("; ") : ""), missing.length ? 15000 : 4000);
    }).catch(fail).then(function () { busy(button, false); });
  }

  function removeLink(button) {
    post("/api/links/remove", {
      key: button.getAttribute("data-key"),
      url: button.getAttribute("data-url")
    }).then(function () { window.location.reload(); }).catch(fail);
  }

  function openTarget(button) {
    var taskId = button.getAttribute("data-task-id");
    if (taskId) {
      var body = { task_id: taskId, target: button.getAttribute("data-target") };
      var index = button.getAttribute("data-index");
      if (index !== null) { body.index = parseInt(index, 10); }
      post("/api/open", body).catch(fail);
      return;
    }
    post("/api/open", {
      key: button.getAttribute("data-key"),
      what: button.getAttribute("data-what")
    }).catch(fail);
  }

  function taskLinkAdd(button) {
    var taskId = button.getAttribute("data-task-id");
    var title = document.querySelector('.link-title[data-task-id="' + taskId + '"]');
    var url = document.querySelector('.link-url[data-task-id="' + taskId + '"]');
    post("/api/task/links/add", {
      task_id: taskId,
      title: title ? title.value : "",
      url: url ? url.value : ""
    }).then(function () { window.location.reload(); }).catch(fail);
  }

  function taskLinkRemove(button) {
    post("/api/task/links/remove", {
      task_id: button.getAttribute("data-task-id"),
      index: parseInt(button.getAttribute("data-index"), 10)
    }).then(function () { window.location.reload(); }).catch(fail);
  }

  function taskFolderSet(button) {
    var taskId = button.getAttribute("data-task-id");
    var input = document.querySelector('.folder-input[data-task-id="' + taskId + '"]');
    post("/api/task/folder", {
      task_id: taskId,
      folder: input ? input.value : ""
    }).then(function () { window.location.reload(); }).catch(fail);
  }

  function removeTimeEntry(button) {
    post("/api/task/time/remove", {
      key: button.getAttribute("data-key") || "",
      id: button.getAttribute("data-task-id"),
      entry_index: parseInt(button.getAttribute("data-index"), 10)
    }).then(function () { window.location.reload(); }).catch(fail);
  }

  function openAll(button) {
    var links = document.querySelectorAll("a.link-item[href]");
    if (!links.length) { toast("No links to open"); return; }
    toast("Opening " + links.length + " tabs - allow popups for this page if nothing appears");
    links.forEach(function (link) { window.open(link.href, "_blank", "noopener"); });
  }

  function addTask(button) {
    var form = button.closest(".task-form");
    if (!form) { return; }
    var key = form.getAttribute("data-key") || "";
    var select = form.querySelector(".task-key");
    if (select) { key = select.value; }
    var desc = form.querySelector(".task-desc");
    var due = form.querySelector(".task-due");
    var notes = form.querySelector(".task-notes");
    post("/api/tasks/add", {
      key: key,
      description: desc ? desc.value : "",
      due: due ? due.value : "",
      notes: notes ? notes.value : ""
    }).then(function () { window.location.reload(); }).catch(fail);
  }

  function editTask(button) {
    var row = button.closest(".task-row");
    var form = row ? row.querySelector(".task-edit") : null;
    if (!form) { return; }
    form.hidden = !form.hidden;
    if (!form.hidden) {
      wireEditForm(form);
      var desc = form.querySelector(".edit-desc");
      if (desc) { desc.focus(); }
    }
  }

  function wireEditForm(form) {
    if (form.dataset.wired === "1") { return; }
    form.dataset.wired = "1";
    var repeat = form.querySelector(".edit-repeat");
    var weekday = form.querySelector(".edit-weekday");
    if (!repeat) { return; }
    repeat.addEventListener("change", function () {
      repeat.dataset.dirty = "1";
      if (weekday) { weekday.hidden = !(repeat.value === "week" || repeat.value === "2weeks"); }
    });
    if (weekday) {
      weekday.addEventListener("change", function () { repeat.dataset.dirty = "1"; });
    }
  }

  function saveTask(button) {
    var row = button.closest(".task-row");
    var form = row ? row.querySelector(".task-edit") : null;
    if (!form) { return; }
    var desc = form.querySelector(".edit-desc");
    var due = form.querySelector(".edit-due");
    var notes = form.querySelector(".edit-notes");
    var category = form.querySelector(".edit-category");
    var repeat = form.querySelector(".edit-repeat");
    var weekday = form.querySelector(".edit-weekday");
    var ref = form.querySelector(".edit-ref");
    var body = {
      key: button.getAttribute("data-key") || "",
      id: button.getAttribute("data-id"),
      description: desc ? desc.value : "",
      due: due ? due.value : "",
      notes: notes ? notes.value : ""
    };
    if (category) { body.category = category.value; }
    if (ref) { body.ref = ref.value; }
    if (repeat && repeat.dataset.dirty === "1") {
      body.repeat = buildRule(repeat.value, weekday ? weekday.value : "", due ? due.value : "");
    }
    post("/api/tasks/update", body).then(function () { window.location.reload(); }).catch(fail);
  }

  function cancelTask(button) {
    var row = button.closest(".task-row");
    var form = row ? row.querySelector(".task-edit") : null;
    if (form) { form.hidden = true; }
  }

  function deleteTask(button) {
    if (!window.confirm("Delete this task?")) { return; }
    post("/api/tasks/delete", {
      key: button.getAttribute("data-key") || "",
      id: button.getAttribute("data-id")
    }).then(function () { window.location.reload(); }).catch(fail);
  }

  function archiveTask(button) {
    busy(button, true);
    post("/api/tasks/archive", {
      key: button.getAttribute("data-key") || "",
      id: button.getAttribute("data-id")
    }).then(function () {
      toast("Archived");
      if (window.location.pathname.indexOf("/t/") === 0) { window.location.href = "/"; }
      else { window.location.reload(); }
    }).catch(function (error) { busy(button, false); fail(error); });
  }

  function unarchiveTask(button) {
    busy(button, true);
    post("/api/tasks/unarchive", {
      key: button.getAttribute("data-key") || "",
      id: button.getAttribute("data-id")
    }).then(function () {
      toast("Restored");
      if (window.location.pathname.indexOf("/t/") === 0) { window.location.href = "/"; }
      else { window.location.reload(); }
    }).catch(function (error) { busy(button, false); fail(error); });
  }

  function quickDue(button) {
    var uid = button.getAttribute("data-uid");
    var input = document.querySelector('.quick-date[data-uid="' + uid + '"]');
    var due = input ? input.value : "";
    var id = button.getAttribute("data-id") || "";
    var kind = button.getAttribute("data-kind") || "";
    if (kind === "manual") {
      post("/api/tasks/update", { key: button.getAttribute("data-key") || "", id: id, due: due })
        .then(function () { window.location.reload(); })
        .catch(fail);
      return;
    }
    if (!id) { toast("This task has no id to override"); return; }
    post("/api/due_override", { task_id: id, due: due, note: "" })
      .then(function () { window.location.reload(); })
      .catch(fail);
  }

  function expandCard(button) {
    var card = button.closest(".card");
    var list = card ? card.querySelector(".card-tasks") : null;
    if (!list) { return; }
    list.hidden = !list.hidden;
    button.textContent = list.hidden ? "Tasks" : "Hide tasks";
  }

  function saveNote(button) {
    var key = button.getAttribute("data-key");
    var taskId = button.getAttribute("data-task-id");
    var area = document.getElementById("note-text");
    var hash = button.getAttribute("data-hash");
    var warning = document.getElementById("note-conflict");
    if (!area) { return; }
    var body = { text: area.value, base_hash: hash };
    if (taskId) { body.task_id = taskId; } else { body.key = key; }
    post("/api/note/save", body).then(function (data) {
      button.setAttribute("data-hash", data.hash);
      if (warning) { warning.hidden = true; }
      document.dispatchEvent(new CustomEvent("hub:notesaved"));
      toast("Saved");
    }).catch(function (error) {
      if (error.status === 409 && error.data) {
        if (warning) {
          warning.hidden = false;
          warning.setAttribute("data-current-hash", error.data.current_hash);
          warning.setAttribute("data-current-text", error.data.current_text);
        }
        toast("Conflict: the note changed on disk");
        return;
      }
      fail(error);
    });
  }

  document.addEventListener("click", function (event) {
    var reload = event.target.closest("#note-reload");
    if (reload) {
      event.preventDefault();
      window.location.reload();
      return;
    }
    var force = event.target.closest("#note-force");
    if (force) {
      event.preventDefault();
      var area = document.getElementById("note-text");
      var warning = document.getElementById("note-conflict");
      var save = document.getElementById("note-save");
      if (!area || !save) { return; }
      var forceBody = {
        text: area.value,
        base_hash: warning ? warning.getAttribute("data-current-hash") : "",
        force: true
      };
      var forceTask = save.getAttribute("data-task-id");
      if (forceTask) { forceBody.task_id = forceTask; } else { forceBody.key = save.getAttribute("data-key"); }
      post("/api/note/save", forceBody).then(function (data) {
        save.setAttribute("data-hash", data.hash);
        if (warning) { warning.hidden = true; }
        document.dispatchEvent(new CustomEvent("hub:notesaved"));
        toast("Saved (overwritten)");
      }).catch(fail);
      return;
    }
    var tab = event.target.closest("[data-tab]");
    if (tab) {
      event.preventDefault();
      var name = tab.getAttribute("data-tab");
      document.querySelectorAll("[data-tab]").forEach(function (other) {
        other.classList.toggle("active", other === tab);
      });
      document.querySelectorAll("[data-tab-body]").forEach(function (body) {
        body.hidden = body.getAttribute("data-tab-body") !== name;
      });
      var savebar = document.getElementById("note-savebar");
      if (savebar) { savebar.hidden = name !== "edit" && name !== "live"; }
      document.dispatchEvent(new CustomEvent("hub:tab", { detail: name }));
    }
  });

  function cssVar(name) {
    return window.getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function isDark() {
    var theme = document.documentElement.getAttribute("data-theme") || "";
    if (theme === "dark") { return true; }
    if (theme === "light") { return false; }
    return !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
  }

  function esc(text) {
    return String(text).replace(/[&<>"']/g, function (char) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char];
    });
  }

  function shiftDate(iso, days) {
    var base = Date.parse(iso + "T00:00:00");
    if (isNaN(base)) { return iso; }
    return new Date(base + days * 86400000).toISOString().slice(0, 10);
  }

  function renderGantt(el, rows, opts) {
    if (!el || !window.Plotly || !rows || !rows.length) { return; }
    opts = opts || {};
    var colors = {
      overdue: cssVar("--st-overdue"),
      "due-soon": cssVar("--st-due-soon"),
      upcoming: cssVar("--st-upcoming"),
      done: cssVar("--st-done"),
      "not-needed": cssVar("--st-not-needed"),
      open: cssVar("--st-open")
    };
    var groups = {};
    var order = [];
    rows.forEach(function (row) {
      var name = row.project || "Personal";
      if (!groups[name]) {
        groups[name] = [];
        order.push(name);
      }
      groups[name].push(row);
    });
    order.sort(function (a, b) {
      var first = groups[a].reduce(function (min, row) { return row.due < min ? row.due : min; }, groups[a][0].due);
      var second = groups[b].reduce(function (min, row) { return row.due < min ? row.due : min; }, groups[b][0].due);
      return first < second ? -1 : first > second ? 1 : 0;
    });
    var traces = [];
    var seen = {};
    order.forEach(function (name) {
      groups[name].forEach(function (row) {
        var color = colors[row.status] || colors.open;
        var single = row.start === row.due;
        var label = STATUS_LABEL[row.status] || row.status;
        traces.push({
          x: single ? [row.due] : [row.start, row.due],
          y: [name, name],
          mode: single ? "markers" : "lines+markers",
          line: { color: color, width: 10 },
          marker: { color: color, size: single ? 11 : 7 },
          name: label,
          legendgroup: row.status,
          showlegend: !seen[row.status],
          customdata: single ? [row.key || ""] : [row.key || "", row.key || ""],
          hovertemplate:
            "<b>" + esc(name) + "</b><br>" + esc(row.title) +
            "<br>start " + row.start + "<br>due " + row.due +
            "<br>" + label + "<extra></extra>"
        });
        seen[row.status] = true;
      });
    });
    var layout = {
      height: Math.max(260, 60 + 38 * order.length),
      margin: { l: 10, r: 20, t: 44, b: 40 },
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
      font: { color: cssVar("--text"), family: '"Segoe UI Variable", "Segoe UI", system-ui, sans-serif', size: 12 },
      legend: { orientation: "h", yanchor: "bottom", y: 1.02, x: 0 },
      xaxis: { type: "date", showgrid: true, gridcolor: cssVar("--line"), zeroline: false },
      yaxis: { autorange: "reversed", tickfont: { size: 11 }, automargin: true },
      shapes: [{
        type: "line",
        x0: today(),
        x1: today(),
        y0: 0,
        y1: 1,
        yref: "paper",
        line: { dash: "dash", color: cssVar("--muted"), width: 1 }
      }],
      annotations: [{
        x: today(),
        y: 1,
        yref: "paper",
        text: "Today",
        showarrow: false,
        yanchor: "bottom",
        font: { size: 11, color: cssVar("--muted") }
      }]
    };
    if (opts.windowDays) {
      layout.xaxis.range = [shiftDate(today(), -7), shiftDate(today(), opts.windowDays)];
    }
    window.Plotly.newPlot(el, traces, layout, { displaylogo: false, responsive: true }).then(function () {
      el.on("plotly_click", function (data) {
        var point = data && data.points && data.points[0];
        var key = point ? point.customdata : "";
        if (key) { window.location.href = "/p/" + encodeURIComponent(key); }
      });
    });
  }

  function initGantt() {
    var el = document.getElementById("gantt");
    var data = document.getElementById("gantt-data");
    if (!el || !data) { return; }
    var rows;
    try { rows = JSON.parse(data.textContent || "[]"); } catch (error) { rows = []; }
    var windowDays = parseInt(el.getAttribute("data-window"), 10) || 0;
    renderGantt(el, rows, { windowDays: windowDays });
    if (window.matchMedia) {
      var query = window.matchMedia("(prefers-color-scheme: dark)");
      var rerender = function () {
        if (isDark() === query.matches) { renderGantt(el, rows, { windowDays: windowDays }); }
      };
      if (query.addEventListener) { query.addEventListener("change", rerender); }
    }
  }

  function initTaskTable() {
    var table = document.getElementById("tasks-table");
    if (!table) { return; }
    var search = document.getElementById("task-search");
    var category = document.getElementById("task-category");
    var chips = Array.prototype.slice.call(document.querySelectorAll("#task-filters .chip-btn"));
    var filter = "all";
    var categoryValue = "all";
    function apply() {
      var query = search && search.value ? search.value.toLowerCase() : "";
      table.querySelectorAll("tbody tr").forEach(function (row) {
        var status = row.getAttribute("data-status") || "";
        var open = status !== "done" && status !== "not-needed";
        var matchFilter = filter === "all" || status === filter || (filter === "open" && open);
        var matchCategory = categoryValue === "all" || (row.getAttribute("data-category") || "") === categoryValue;
        var matchText = !query || row.textContent.toLowerCase().indexOf(query) !== -1;
        row.hidden = !(matchFilter && matchCategory && matchText);
      });
    }
    chips.forEach(function (chip) {
      chip.addEventListener("click", function () {
        filter = chip.getAttribute("data-filter") || "all";
        chips.forEach(function (other) { other.classList.toggle("active", other === chip); });
        apply();
      });
    });
    if (category) {
      category.addEventListener("change", function () {
        categoryValue = category.value || "all";
        apply();
      });
    }
    if (search) { search.addEventListener("input", apply); }
  }

  function openNewTask() {
    var modal = document.getElementById("new-task-modal");
    if (!modal) { return; }
    modal.hidden = false;
    var desc = document.getElementById("nt-desc");
    if (desc) { desc.focus(); }
  }

  function closeNewTask() {
    var modal = document.getElementById("new-task-modal");
    if (modal) { modal.hidden = true; }
  }

  function initNewTask() {
    var modal = document.getElementById("new-task-modal");
    if (!modal) { return; }
    var open = document.getElementById("new-task-open");
    var close = document.getElementById("new-task-close");
    var cancel = document.getElementById("new-task-cancel");
    var submit = document.getElementById("new-task-submit");
    var category = document.getElementById("nt-category");
    var keyField = document.getElementById("nt-key-field");
    var dueInput = document.getElementById("nt-due");
    var repeat = document.getElementById("nt-repeat");
    var weekdayField = document.getElementById("nt-weekday-field");
    var weekday = document.getElementById("nt-weekday");
    function syncCategory() {
      if (!category || !keyField) { return; }
      keyField.hidden = category.value !== "project";
    }
    function syncRepeat() {
      if (!repeat || !weekdayField) { return; }
      weekdayField.hidden = !(repeat.value === "week" || repeat.value === "2weeks");
    }
    if (category) { category.addEventListener("change", syncCategory); syncCategory(); }
    if (repeat) { repeat.addEventListener("change", syncRepeat); syncRepeat(); }
    if (dueInput && weekday) {
      dueInput.addEventListener("change", function () {
        var name = weekdayName(dueInput.value);
        if (name) { weekday.value = name; }
      });
    }
    var refInput = document.getElementById("nt-ref");
    var descInput = document.getElementById("nt-desc");
    if (refInput && descInput) {
      descInput.addEventListener("input", function () {
        if (refInput.value.trim()) { return; }
        var match = descInput.value.match(/\b(SO|QTE)-\d+\b/i);
        refInput.placeholder = match ? match[0].toUpperCase() : "SO-123456 or QTE-123456";
      });
    }
    if (open) { open.addEventListener("click", openNewTask); }
    if (close) { close.addEventListener("click", closeNewTask); }
    if (cancel) { cancel.addEventListener("click", closeNewTask); }
    modal.addEventListener("click", function (event) {
      if (event.target === modal) { closeNewTask(); }
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && !modal.hidden) { closeNewTask(); }
    });
    if (!submit) { return; }
    submit.addEventListener("click", function () {
      var desc = document.getElementById("nt-desc");
      var notes = document.getElementById("nt-notes");
      var key = document.getElementById("nt-key");
      var ref = document.getElementById("nt-ref");
      var isProject = !!category && category.value === "project";
      post("/api/tasks/add", {
        category: category ? category.value : "",
        key: isProject && key ? key.value : "",
        description: desc ? desc.value : "",
        due: dueInput ? dueInput.value : "",
        notes: notes ? notes.value : "",
        ref: ref ? ref.value : "",
        repeat: repeat ? buildRule(repeat.value, weekday ? weekday.value : "", dueInput ? dueInput.value : "") : ""
      }).then(function () {
        toast("Task added");
        window.location.reload();
      }).catch(fail);
    });
  }

  function inline(text) {
    var html = esc(text);
    html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/https?:\/\/[^\s<>"')]+/g, function (url) {
      return '<a href="' + url + '" target="_blank" rel="noopener">' + url + "</a>";
    });
    return html;
  }

  function renderMarkdown(text) {
    var out = [];
    String(text).replace(/\r\n/g, "\n").split("\n").forEach(function (line, index) {
      var check = /^(\s*[-*]\s+\[)([ xX-])(\]\s.*)$/.exec(line);
      if (check) {
        var mark = check[2].toLowerCase();
        var cls = mark === "x" ? "done" : mark === "-" ? "na" : "";
        out.push(
          '<div class="preview-line"><label class="preview-check">' +
          '<input type="checkbox" class="preview-tick" data-index="' + index + '"' + (mark === "x" ? " checked" : "") + ">" +
          '<span class="' + cls + '">' + inline(check[3].slice(1).trim()) + "</span></label></div>"
        );
        return;
      }
      var heading = /^(#{1,6})\s+(.*)$/.exec(line);
      if (heading) {
        out.push('<div class="preview-heading h' + heading[1].length + '">' + inline(heading[2]) + "</div>");
        return;
      }
      out.push('<div class="preview-line">' + (line.trim() ? inline(line) : "&nbsp;") + "</div>");
    });
    return out.join("");
  }

  function initNoteEditor() {
    var area = document.getElementById("note-text");
    var preview = document.getElementById("note-preview");
    if (!area || !preview) { return; }
    var dirty = document.getElementById("note-dirty");
    var initial = area.value;
    function render() { preview.innerHTML = renderMarkdown(area.value); }
    function markDirty() {
      var changed = area.value !== initial;
      area.dataset.dirty = changed ? "1" : "0";
      if (dirty) { dirty.hidden = !changed; }
    }
    area.addEventListener("input", function () { render(); markDirty(); });
    preview.addEventListener("click", function (event) {
      var box = event.target.closest(".preview-tick");
      if (!box) { return; }
      var index = parseInt(box.getAttribute("data-index"), 10);
      var lines = area.value.split("\n");
      if (isNaN(index) || index < 0 || index >= lines.length) { return; }
      var match = /^(\s*[-*]\s+\[)([ xX-])(\]\s.*)$/.exec(lines[index]);
      if (!match) { return; }
      lines[index] = match[1] + (box.checked ? "x" : " ") + match[3];
      area.value = lines.join("\n");
      render();
      markDirty();
    });
    document.addEventListener("hub:notesaved", function () {
      initial = area.value;
      markDirty();
    });
    window.addEventListener("beforeunload", function (event) {
      if (area.dataset.dirty === "1") {
        event.preventDefault();
        event.returnValue = "";
      }
    });
    render();
    markDirty();
  }

  var CHECK_RE = /^(\s*[-*]\s+\[)([ xX-])(\]\s.*)$/;

  function liveInline(text) {
    var html = esc(text);
    html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
    html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    html = html.replace(/(^|[^"'>=])(https?:\/\/[^\s<>"')]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
    html = html.replace(/\[\[([^\]]+)\]\]/g, '<span class="live-wiki">$1</span>');
    html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/(^|[^*\w])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>");
    html = html.replace(/~~(.+?)~~/g, "<s>$1</s>");
    return html;
  }

  function liveLineHtml(line, index, state) {
    var open = '<div class="live-line';
    var attrs = '" data-index="' + index + '"';
    if (state.frontmatter || state.fence || /^\s*```/.test(line)) {
      return open + ' live-raw' + attrs + ">" + (line ? esc(line) : "&nbsp;") + "</div>";
    }
    var indent = /^\s*/.exec(line)[0].replace(/\t/g, "  ").length;
    var pad = indent ? ' style="padding-left:' + (indent * 0.5) + 'em"' : "";
    var check = CHECK_RE.exec(line);
    if (check) {
      var mark = check[2].toLowerCase();
      var cls = mark === "x" ? "done" : mark === "-" ? "na" : "";
      return open + ' live-check' + attrs + pad + '><input type="checkbox" class="live-tick" data-index="' + index + '"' +
        (mark === "x" ? " checked" : "") + '><span class="' + cls + '">' + liveInline(check[3].slice(1).trim()) + "</span></div>";
    }
    var heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      return open + " preview-heading h" + heading[1].length + attrs + ">" + liveInline(heading[2]) + "</div>";
    }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) {
      return open + ' live-hr' + attrs + "><hr></div>";
    }
    var bullet = /^\s*[-*+]\s+(.*)$/.exec(line);
    if (bullet) {
      return open + ' live-bullet' + attrs + pad + '><span class="live-mark">&bull;</span>' + liveInline(bullet[1]) + "</div>";
    }
    var number = /^\s*(\d+[.)])\s+(.*)$/.exec(line);
    if (number) {
      return open + ' live-bullet' + attrs + pad + '><span class="live-mark">' + esc(number[1]) + "</span>" + liveInline(number[2]) + "</div>";
    }
    var quote = /^\s*>\s?(.*)$/.exec(line);
    if (quote) {
      return open + ' live-quote' + attrs + ">" + (quote[1] ? liveInline(quote[1]) : "&nbsp;") + "</div>";
    }
    return open + attrs + pad + ">" + (line.trim() ? liveInline(line.trim()) : "&nbsp;") + "</div>";
  }

  function initLiveEditor() {
    var area = document.getElementById("note-text");
    var editor = document.getElementById("live-editor");
    if (!area || !editor) { return; }
    var editing = -1;
    var box = null;

    function lines() { return area.value.replace(/\r\n/g, "\n").split("\n"); }

    function store(list) {
      area.value = list.join("\n");
      area.dispatchEvent(new Event("input"));
    }

    function fit() {
      if (!box) { return; }
      box.style.height = "auto";
      box.style.height = box.scrollHeight + "px";
    }

    function draw(focusIndex, caret) {
      var list = lines();
      var state = { frontmatter: false, fence: false };
      var html = [];
      list.forEach(function (line, index) {
        if (index === 0 && line.trim() === "---") {
          state.frontmatter = true;
          html.push(liveLineHtml(line, index, state));
          return;
        }
        if (state.frontmatter) {
          html.push(liveLineHtml(line, index, state));
          if (line.trim() === "---") { state.frontmatter = false; }
          return;
        }
        var fenceLine = /^\s*```/.test(line);
        html.push(liveLineHtml(line, index, state));
        if (fenceLine) { state.fence = !state.fence; }
      });
      editor.innerHTML = html.join("");
      editing = -1;
      box = null;
      if (focusIndex === undefined || focusIndex < 0 || focusIndex >= list.length) { return; }
      var target = editor.querySelector('.live-line[data-index="' + focusIndex + '"]');
      if (!target) { return; }
      editing = focusIndex;
      box = document.createElement("textarea");
      box.className = "live-input";
      box.rows = 1;
      box.spellcheck = false;
      box.value = list[focusIndex];
      target.replaceWith(box);
      fit();
      box.focus();
      var pos = caret === undefined || caret < 0 ? box.value.length : Math.min(caret, box.value.length);
      box.setSelectionRange(pos, pos);
      box.addEventListener("input", onInput);
      box.addEventListener("keydown", onKey);
      box.addEventListener("blur", onBlur);
    }

    function onInput() {
      if (!box || editing < 0) { return; }
      if (box.value.indexOf("\n") >= 0) { box.value = box.value.replace(/\r?\n/g, " "); }
      var list = lines();
      list[editing] = box.value;
      store(list);
      fit();
    }

    function singleRow() {
      var lh = parseFloat(window.getComputedStyle(box).lineHeight) || 20;
      return box.scrollHeight < lh * 1.8;
    }

    function onKey(event) {
      if (!box) { return; }
      var list = lines();
      var start = box.selectionStart;
      var end = box.selectionEnd;
      var value = box.value;
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        var prefixMatch = /^(\s*(?:[-*+]\s+\[[ xX-]\]\s|[-*+]\s|\d+[.)]\s|>\s?))/.exec(value);
        var prefix = prefixMatch ? prefixMatch[1].replace(/\[[xX-]\]/, "[ ]") : "";
        if (prefix && value.trim() === prefixMatch[1].trim() && start === value.length) {
          list[editing] = "";
          store(list);
          draw(editing, 0);
          return;
        }
        var before = value.slice(0, start);
        var after = value.slice(end);
        var numbered = /^(\s*)(\d+)([.)]\s)/.exec(prefix);
        if (numbered) { prefix = numbered[1] + (parseInt(numbered[2], 10) + 1) + numbered[3]; }
        if (start < (prefixMatch ? prefixMatch[1].length : 0)) { prefix = ""; }
        list.splice(editing, 1, before, prefix + after);
        store(list);
        draw(editing + 1, prefix.length);
        return;
      }
      if (event.key === "Backspace" && start === 0 && end === 0 && editing > 0) {
        event.preventDefault();
        var prev = list[editing - 1];
        list.splice(editing - 1, 2, prev + value);
        store(list);
        draw(editing - 1, prev.length);
        return;
      }
      if (event.key === "Delete" && start === value.length && end === value.length && editing < list.length - 1) {
        event.preventDefault();
        list.splice(editing, 2, value + list[editing + 1]);
        store(list);
        draw(editing, value.length);
        return;
      }
      if (event.key === "Tab") {
        event.preventDefault();
        if (event.shiftKey) {
          var removed = /^( {1,2}|\t)/.exec(value);
          if (!removed) { return; }
          list[editing] = value.slice(removed[1].length);
          store(list);
          draw(editing, Math.max(0, start - removed[1].length));
        } else {
          list[editing] = "  " + value;
          store(list);
          draw(editing, start + 2);
        }
        return;
      }
      if (event.key === "ArrowUp" && editing > 0 && (start === 0 || singleRow())) {
        event.preventDefault();
        draw(editing - 1, start);
        return;
      }
      if (event.key === "ArrowDown" && editing < list.length - 1 && (start === value.length || singleRow())) {
        event.preventDefault();
        draw(editing + 1, start);
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        draw();
      }
    }

    function onBlur() {
      var current = box;
      window.setTimeout(function () {
        if (box === current && document.activeElement !== current) { draw(); }
      }, 0);
    }

    editor.addEventListener("mousedown", function (event) {
      if (event.target.closest("a") || event.target.closest(".live-tick") || event.target === box) { return; }
      var line = event.target.closest(".live-line");
      var index;
      if (line) {
        index = parseInt(line.getAttribute("data-index"), 10);
      } else if (event.target === editor) {
        index = lines().length - 1;
      } else {
        return;
      }
      if (isNaN(index)) { return; }
      event.preventDefault();
      draw(index);
    });

    editor.addEventListener("change", function (event) {
      var tick = event.target.closest(".live-tick");
      if (!tick) { return; }
      var index = parseInt(tick.getAttribute("data-index"), 10);
      var list = lines();
      var match = CHECK_RE.exec(list[index] || "");
      if (!match) { return; }
      list[index] = match[1] + (tick.checked ? "x" : " ") + match[3];
      store(list);
      draw();
    });

    document.addEventListener("hub:tab", function (event) {
      if (event.detail === "live") { draw(); }
    });

    draw();
    var wanted = new URLSearchParams(window.location.search).get("tab");
    if (wanted) {
      var button = document.querySelector('[data-tab="' + wanted.replace(/[^a-z]/g, "") + '"]');
      if (button) { button.click(); }
    }
  }

  function initTaskView() {
    var flat = document.querySelector(".task-list-flat");
    if (!flat) { return; }
    var panel = flat.closest(".panel");
    var groups = panel.querySelectorAll(".stage-group");
    var rows = [];
    panel.querySelectorAll(".stage-group .task-row").forEach(function (row) {
      rows.push({ row: row, home: row.parentNode });
    });
    function sortKey(row) {
      var open = row.getAttribute("data-mark") === " " ? "0" : "1";
      return open + (row.getAttribute("data-due") || "9999-99-99");
    }
    function show(view) {
      panel.querySelectorAll("[data-task-view]").forEach(function (button) {
        button.classList.toggle("active", button.getAttribute("data-task-view") === view);
      });
      panel.classList.toggle("by-due", view === "due");
      if (view === "due") {
        rows.slice().sort(function (a, b) {
          var ka = sortKey(a.row), kb = sortKey(b.row);
          return ka < kb ? -1 : ka > kb ? 1 : 0;
        }).forEach(function (item) { flat.appendChild(item.row); });
      } else {
        rows.forEach(function (item) { item.home.appendChild(item.row); });
      }
      flat.hidden = view !== "due";
      groups.forEach(function (group) { group.hidden = view === "due"; });
    }
    panel.addEventListener("click", function (event) {
      var button = event.target.closest("[data-task-view]");
      if (!button) { return; }
      var view = button.getAttribute("data-task-view");
      try { window.localStorage.setItem("hub.taskView", view); } catch (error) { }
      show(view);
    });
    var saved = "phase";
    try { saved = window.localStorage.getItem("hub.taskView") || "phase"; } catch (error) { }
    saved = new URLSearchParams(window.location.search).get("view") || saved;
    show(saved === "due" ? "due" : "phase");
  }

  function applyZoom(value) {
    var main = document.querySelector(".content");
    if (main) { main.style.zoom = String(value / 100); }
    document.body.setAttribute("data-zoom", String(value));
    window.dispatchEvent(new Event("resize"));
  }

  function initZoom() {
    var select = document.getElementById("zoom-select");
    if (select) {
      select.addEventListener("change", function () {
        var value = parseInt(select.value, 10);
        if (isNaN(value)) { return; }
        post("/api/ui", { zoom: value }).then(function () {
          applyZoom(value);
        }).catch(function (error) {
          select.value = document.body.getAttribute("data-zoom") || "100";
          fail(error);
        });
      });
    }
    var toggle = document.getElementById("fit-toggle");
    if (toggle) {
      toggle.addEventListener("click", function () {
        var next = toggle.getAttribute("aria-pressed") !== "true";
        post("/api/ui", { fit: next }).then(function () {
          document.body.classList.toggle("fit-board", next);
          toggle.setAttribute("aria-pressed", next ? "true" : "false");
          toggle.classList.toggle("active", next);
        }).catch(fail);
      });
    }
    var archivedToggle = document.getElementById("archived-toggle");
    if (archivedToggle) {
      archivedToggle.addEventListener("click", function () {
        var next = archivedToggle.getAttribute("aria-pressed") !== "true";
        post("/api/ui", { show_archived: next }).then(function () {
          window.location.reload();
        }).catch(fail);
      });
    }
  }

  function recalcTimesheet() {
    var table = document.querySelector(".ts-table");
    if (!table) { return; }
    var columnTotals = {};
    Array.prototype.forEach.call(table.querySelectorAll("tbody tr.ts-row"), function (row) {
      var sum = 0;
      Array.prototype.forEach.call(row.querySelectorAll(".ts-cell"), function (cell) {
        var value = parseFloat(cell.value);
        if (!isFinite(value)) { value = 0; }
        sum += value;
        var day = cell.getAttribute("data-day");
        columnTotals[day] = (columnTotals[day] || 0) + value;
      });
      var total = row.querySelector(".ts-total");
      if (total) { total.textContent = sum ? String(Math.round(sum * 100) / 100) : ""; }
    });
    Array.prototype.forEach.call(table.querySelectorAll("tfoot .ts-day-total"), function (cell) {
      var value = columnTotals[cell.getAttribute("data-day")] || 0;
      cell.textContent = value ? String(Math.round(value * 100) / 100) : "";
      cell.classList.remove("under", "over");
      if (!value || !cell.classList.contains("ts-weekday")) { return; }
      var target = parseFloat(table.getAttribute("data-target")) || 0;
      if (value < target) { cell.classList.add("under"); }
      if (value > target) { cell.classList.add("over"); }
    });
  }

  function spreadRow(button) {
    var rowId = button.getAttribute("data-row");
    var row = button.closest("tr");
    var table = button.closest(".ts-table");
    var input = row ? row.querySelector(".ts-spread-input") : null;
    var total = input ? parseFloat(input.value) : NaN;
    if (!isFinite(total)) {
      toast("Enter a weekly total first");
      return;
    }
    busy(button, true);
    post("/api/timesheet/spread", {
      row_id: rowId,
      start: table ? table.getAttribute("data-start") : "",
      end: table ? table.getAttribute("data-end") : "",
      total: total
    }).then(function () {
      window.location.reload();
    }).catch(function (error) {
      busy(button, false);
      fail(error);
    });
  }

  function copyWeek() {
    var area = document.getElementById("ts-copy-text");
    if (!area) { return; }
    copyText(area.value, "Copied week");
  }

  function pageIdle() {
    if (document.querySelector(".modal:not([hidden])")) { return false; }
    if (document.querySelector(".task-edit:not([hidden])")) { return false; }
    var note = document.getElementById("note-text");
    if (note && note.dataset.dirty === "1") { return false; }
    var active = document.activeElement;
    if (active && /^(INPUT|TEXTAREA|SELECT)$/.test(active.tagName)) { return false; }
    return true;
  }

  function initAutoReload() {
    if (document.body.getAttribute("data-auto-reload") !== "1") { return; }
    window.setInterval(function () {
      fetch("/api/status").then(function (r) { return r.json(); }).then(function (data) {
        if (data.running) { return; }
        if ((data.generated || "") === (document.body.getAttribute("data-generated") || "")) { return; }
        if (!pageIdle()) { return; }
        window.location.reload();
      }).catch(function () {});
    }, 60000);
  }

  function initSettings() {
    var save = document.getElementById("settings-save");
    if (!save) { return; }
    save.addEventListener("click", function () {
      var refresh = document.getElementById("set-refresh");
      var dueSoon = document.getElementById("set-due-soon");
      var theme = document.getElementById("set-theme");
      var zoom = document.getElementById("set-zoom");
      var layout = document.getElementById("set-layout");
      var fit = document.getElementById("set-fit");
      var autoReload = document.getElementById("set-auto-reload");
      var vault = document.getElementById("set-vault");
      var personal = document.getElementById("set-personal");
      var template = document.getElementById("set-template");
      var apeSheet = document.getElementById("set-ape-sheet");
      var msProject = document.getElementById("set-ms-project");
      var backupTime = document.getElementById("set-backup-time");
      var offlineAuto = document.getElementById("set-offline-auto");
      var workOffline = document.getElementById("set-work-offline");
      var payload = {
        refresh_minutes: refresh ? parseInt(refresh.value, 10) : 15,
        due_soon_days: dueSoon ? parseInt(dueSoon.value, 10) : 7,
        theme: theme ? theme.value : "system",
        zoom: zoom ? parseInt(zoom.value, 10) : 100,
        layout: layout ? layout.value : "board",
        fit: !!(fit && fit.checked),
        auto_reload_pages: !!(autoReload && autoReload.checked),
        auto_create_projects: !!(document.getElementById("set-auto-create") && document.getElementById("set-auto-create").checked),
        vault: vault ? vault.value : "",
        personal_tasks_path: personal ? personal.value : "",
        time_estimate_template: template ? template.value : "",
        ape_sheet_path: apeSheet ? apeSheet.value : "",
        ms_project_path: msProject ? msProject.value : ""
      };
      [["vault", vault], ["personal_tasks_path", personal], ["time_estimate_template", template], ["ape_sheet_path", apeSheet], ["ms_project_path", msProject]].forEach(function (pair) {
        if (pair[1] && pair[1].disabled) { delete payload[pair[0]]; }
      });
      if (backupTime) {
        payload.backup = { daily_time: backupTime.value, offline_fallback: !!(offlineAuto && offlineAuto.checked), work_offline: !!(workOffline && workOffline.checked) };
      }
      var checklistItems = document.querySelectorAll(".checklist-item");
      if (checklistItems.length) {
        payload.checklist_optional = Array.prototype.filter.call(checklistItems, function (input) {
          return input.checked && !input.disabled;
        }).map(function (input) { return input.getAttribute("data-id"); });
      }
      post("/api/settings", payload).then(function (data) {
        toast("Settings saved" + (data.rerun ? " - refreshing projects" : ""));
        window.setTimeout(function () { window.location.reload(); }, 900);
      }).catch(fail);
    });
    function setOptional(checked) {
      Array.prototype.forEach.call(document.querySelectorAll(".checklist-item"), function (input) {
        if (!input.disabled) { input.checked = checked; }
      });
    }
    var requiredOnly = document.getElementById("checklist-required-only");
    var enableAll = document.getElementById("checklist-enable-all");
    if (requiredOnly) { requiredOnly.addEventListener("click", function () { setOptional(false); }); }
    if (enableAll) { enableAll.addEventListener("click", function () { setOptional(true); }); }
  }

  initGantt();
  initTaskTable();
  initNewTask();
  initNoteEditor();
  initLiveEditor();
  initTaskView();
  initZoom();
  initAutoReload();
  initSettings();
  var stageFilter = document.getElementById("stage-filter");
  if (stageFilter && stageFilter.value) { filterStage(stageFilter.value); }
})();
