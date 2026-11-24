(function () {
  "use strict";
  var DAY = 86400000;

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function parse(text) {
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(text || "");
    return m ? Date.UTC(+m[1], +m[2] - 1, +m[3]) : null;
  }
  function fmt(ms) {
    var d = new Date(ms);
    return (d.getUTCMonth() + 1) + "/" + d.getUTCDate();
  }
  function ident(p) {
    return (p.so || []).concat(p.qte || []).join(", ") || p.project_no || p.key;
  }

  function rowsFrom(statuses) {
    var rows = [];
    statuses.forEach(function (person) {
      (person.projects || []).forEach(function (p) {
        var copy = {};
        for (var k in p) copy[k] = p[k];
        copy.ae = person.name || "";
        copy.generated = person.generated || "";
        rows.push(copy);
      });
    });
    return rows;
  }

  function options(values, label) {
    return '<option value="">All ' + esc(label) + "</option>" + values.map(function (v) {
      return '<option value="' + esc(v) + '">' + esc(v) + "</option>";
    }).join("");
  }

  function uniq(list) {
    var seen = {}, out = [];
    list.forEach(function (v) { if (v && !seen[v]) { seen[v] = 1; out.push(v); } });
    return out.sort();
  }

  function gantt(rows, today, opts) {
    var dated = rows.filter(function (r) { return parse(r.start) || parse(r.finish); });
    if (!dated.length) return '<p class="tv-muted">No project has start or finish dates yet.</p>';
    var lo = today - 30 * DAY, hi = today + 120 * DAY;
    dated.forEach(function (r) {
      var s = parse(r.start), f = parse(r.finish);
      if (s && s < lo && f && f > lo) lo = Math.max(s, today - 180 * DAY);
      if (f && f > hi) hi = Math.min(f, today + 365 * DAY);
    });
    var span = hi - lo || DAY;
    function pct(ms) { return Math.max(0, Math.min(100, (ms - lo) / span * 100)); }
    var ticks = "";
    for (var t = lo - (new Date(lo).getUTCDate() - 1) * DAY; t <= hi; ) {
      var d = new Date(t);
      if (t >= lo) ticks += '<span class="tv-tick" style="left:' + pct(t) + '%">' + (d.getUTCMonth() + 1) + "/" + d.getUTCFullYear().toString().slice(2) + "</span>";
      t = Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1);
    }
    var body = dated.sort(function (a, b) {
      return (a.ae + a.finish).localeCompare(b.ae + b.finish);
    }).map(function (r) {
      var s = parse(r.start) || parse(r.finish), f = parse(r.finish) || s;
      var late = (r.overdue || []).length ? " tv-late" : "";
      var left = pct(s), width = Math.max(0.8, pct(f) - left);
      var title = esc(r.customer + " " + ident(r) + ": " + (r.start || "?") + " to " + (r.finish || "?") + " (" + (r.dates_from || "") + ")");
      var action = "";
      if (opts.reassign) {
        action = '<button type="button" class="btn small" data-action="team-reassign" data-key="' + esc(r.key) + '" data-from="' + esc(r.ae) + '" data-label="' + esc(r.customer + " " + ident(r)) + '">Reassign</button>';
      }
      return '<div class="tv-row"><div class="tv-label" title="' + esc(r.name) + '"><b>' + esc(r.customer || r.name) + "</b> " + esc(ident(r)) +
        '<span class="tv-muted"> ' + esc(r.ae) + (r.pm ? " / PM " + esc(r.pm) : "") + (r.level ? " / " + esc(r.level) : "") + "</span>" +
        (r.reassigned_to ? ' <span class="tv-flag">to ' + esc(r.reassigned_to) + "</span>" : "") + action + "</div>" +
        '<div class="tv-track"><span class="tv-today" style="left:' + pct(today) + '%"></span><span class="tv-bar' + late + '" style="left:' + left + "%;width:" + width + '%" title="' + title + '"></span></div></div>';
    }).join("");
    return '<div class="tv-gantt"><div class="tv-row tv-head"><div class="tv-label">' + dated.length + ' projects</div><div class="tv-track">' + ticks + "</div></div>" + body + "</div>";
  }

  function lists(rows, statuses) {
    var late = rows.filter(function (r) { return (r.overdue || []).length; });
    var flagged = rows.filter(function (r) { return (r.flags || []).length; });
    var html = "<h3>At risk (" + late.length + ")</h3>";
    html += late.length ? "<ul>" + late.map(function (r) {
      return "<li><b>" + esc(r.customer) + " " + esc(ident(r)) + "</b> (" + esc(r.ae) + "): " + r.overdue.map(function (t) {
        return esc(t.title) + " due " + esc(t.due);
      }).join("; ") + "</li>";
    }).join("") + "</ul>" : '<p class="tv-muted">Nothing overdue.</p>';
    html += "<h3>Owner mismatch (" + flagged.length + ")</h3>";
    html += flagged.length ? "<ul>" + flagged.map(function (r) {
      return "<li><b>" + esc(r.customer) + " " + esc(ident(r)) + "</b> (" + esc(r.ae) + "): " + esc(r.flags.join("; ")) + "</li>";
    }).join("") + "</ul>" : '<p class="tv-muted">None.</p>';
    html += "<h3>Workload</h3><table class=\"tv-table\"><tr><th>Engineer</th><th>Projects</th><th>Overdue tasks</th><th>Data as of</th></tr>" +
      statuses.map(function (s) {
        var mine = rows.filter(function (r) { return r.ae === s.name; });
        var over = mine.reduce(function (n, r) { return n + (r.overdue || []).length; }, 0);
        return "<tr><td>" + esc(s.name) + (s.role ? " (" + esc(s.role) + ")" : "") + "</td><td>" + mine.length + "</td><td>" + over + "</td><td>" +
          (s.missing ? "not published yet" : esc((s.generated || "").slice(0, 16).replace("T", " "))) + "</td></tr>";
      }).join("") + "</table>";
    return html;
  }

  function render(root, data) {
    var statuses = data.statuses || [];
    var all = rowsFrom(statuses);
    var today = parse(data.today) || Date.UTC(new Date().getFullYear(), new Date().getMonth(), new Date().getDate());
    root.innerHTML = '<div class="tv-filters">' +
      '<select data-tv="pm">' + options(uniq(all.map(function (r) { return r.pm; })), "PMs") + "</select> " +
      '<select data-tv="ae">' + options(uniq(all.map(function (r) { return r.ae; })), "AEs") + "</select> " +
      '<input data-tv="q" type="search" placeholder="Customer, SO, QTE"> ' +
      '<label><input data-tv="late" type="checkbox"> Overdue only</label></div>' +
      '<div data-tv="gantt"></div><div data-tv="lists"></div>';
    function pick(name) { return root.querySelector('[data-tv="' + name + '"]'); }
    function update() {
      var pm = pick("pm").value, ae = pick("ae").value, q = pick("q").value.toLowerCase(), late = pick("late").checked;
      var rows = all.filter(function (r) {
        if (pm && r.pm !== pm) return false;
        if (ae && r.ae !== ae) return false;
        if (late && !(r.overdue || []).length) return false;
        if (q && (r.customer + " " + ident(r) + " " + r.name).toLowerCase().indexOf(q) < 0) return false;
        return true;
      });
      pick("gantt").innerHTML = gantt(rows, today, data);
      pick("lists").innerHTML = lists(rows, statuses.filter(function (s) { return !ae || s.name === ae; }));
    }
    root.addEventListener("change", update);
    root.addEventListener("input", update);
    update();
  }

  window.TeamView = { render: render, rowsFrom: rowsFrom };
})();
