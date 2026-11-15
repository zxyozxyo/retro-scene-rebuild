(function () {
  var q = document.getElementById("q"), res = document.getElementById("res"), count = document.getElementById("count");
  var data = null, LIMIT = 300;
  function esc(s) { return s.replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  // Names are matched with _ . - ( ) [ ] read as spaces, so "game and watch" finds Game_and_Watch.
  function norm(s) { return " " + s.toLowerCase().replace(/[._\-()\[\]]+/g, " ").replace(/\s+/g, " ").trim() + " "; }

  // One search term -> test function. * = any run of characters, ? = one character.
  function termTest(t, phrase) {
    var n = norm(t).trim();
    if (!n) return null;
    if (!/[*?]/.test(n)) return function (hay) { return hay.indexOf(n) >= 0; };
    var src = n.split("").map(function (c) {
      if (c === "*") return "[^]*";
      if (c === "?") return ".";
      return c.replace(/[.+^${}()|[\]\\\/]/g, "\\$&");
    }).join("");
    if (!phrase) src = src.replace(/ /g, "\\s");
    var re = new RegExp(src);
    return function (hay) { return re.test(hay); };
  }

  // Query syntax: words (all must match), "exact phrase", -exclude, wild*card, singl?, sys:nds, year:2004
  function parse(text) {
    var tests = [], filters = { sys: [], year: [] }, m, re = /(-?)(?:(sys|system|year):)?(?:"([^"]*)"?|(\S+))/gi;
    while ((m = re.exec(text))) {
      var neg = m[1] === "-", key = (m[2] || "").toLowerCase(), val = m[3] !== undefined ? m[3] : m[4];
      if (!val) continue;
      if (key === "sys" || key === "system") {
        var v = val.toLowerCase(), names = window.RSR_SYSTEMS || {}, exact = [];
        for (var code in names) if (code.toLowerCase() === v || names[code].toLowerCase() === v) exact.push(code.toLowerCase());
        filters.sys.push({ neg: neg, v: v, exact: exact }); continue;
      }
      if (key === "year") { filters.year.push({ neg: neg, t: termTest(val, false) }); continue; }
      var f = termTest(val, m[3] !== undefined);
      if (f) tests.push({ neg: neg, f: f });
    }
    return { tests: tests, filters: filters };
  }

  function matches(r, p) {
    for (var i = 0; i < p.tests.length; i++) if (p.tests[i].f(r[4]) === p.tests[i].neg) return false;
    var s = p.filters.sys, sysOk = !s.some(function (x) { return !x.neg; });
    for (var j = 0; j < s.length; j++) {
      var hit = s[j].exact.length ? s[j].exact.indexOf(r[1].toLowerCase()) >= 0 : r[5].indexOf(s[j].v) >= 0;
      if (hit && s[j].neg) return false;
      if (hit && !s[j].neg) sysOk = true;
    }
    if (!sysOk) return false;
    var y = p.filters.year, yOk = !y.some(function (x) { return !x.neg; });
    for (var k = 0; k < y.length; k++) {
      var yh = y[k].t && y[k].t(" " + r[2] + " ");
      if (yh && y[k].neg) return false;
      if (yh && !y[k].neg) yOk = true;
    }
    return yOk;
  }

  function run() {
    if (!data) return;
    var p = parse(q.value);
    if (!p.tests.length && !p.filters.sys.length && !p.filters.year.length) {
      res.innerHTML = ""; count.textContent = data.length.toLocaleString() + " releases — type to search."; return;
    }
    var hits = [], total = 0;
    for (var i = 0; i < data.length; i++) {
      if (matches(data[i], p)) { total++; if (hits.length < LIMIT) hits.push(data[i]); }
    }
    var html = "";
    for (var j = 0; j < hits.length; j++) {
      var h = hits[j], url = "r/" + encodeURIComponent(h[1]) + "/" + encodeURIComponent(h[0]) + ".html";
      html += "<tr><td><a href=\"" + url + "\">" + esc(h[0]) + "</a>" + (h[3] ? "" : " <span class=\"tag dim\">listed</span>") +
        "</td><td>" + esc(h[1]) + "</td><td>" + esc(h[2]) + "</td></tr>";
    }
    res.innerHTML = html;
    count.textContent = total.toLocaleString() + " match" + (total === 1 ? "" : "es") + (total > LIMIT ? " — showing the first " + LIMIT : "");
  }
  var t = null;
  q.addEventListener("input", function () { clearTimeout(t); t = setTimeout(function () {
    history.replaceState(null, "", "?q=" + encodeURIComponent(q.value)); run(); }, 150); });
  q.value = new URLSearchParams(location.search).get("q") || "";
  count.textContent = "Loading…";
  fetch("assets/index.json").then(function (r) { return r.json(); }).then(function (d) {
    var names = window.RSR_SYSTEMS || {};
    for (var i = 0; i < d.length; i++) {
      d[i].push(norm(d[i][0]));
      d[i].push((d[i][1] + " " + (names[d[i][1]] || "")).toLowerCase());
    }
    data = d; run();
  }).catch(function () { count.textContent = "Search index could not be loaded (open the site through a web server, not as a file)."; });
})();
