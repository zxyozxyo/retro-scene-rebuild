(function () {
  var q = document.getElementById("q"), res = document.getElementById("res"), count = document.getElementById("count");
  var data = null, LIMIT = 300;
  function esc(s) { return s.replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function norm(s) { return s.toLowerCase().replace(/[._\-()\[\]]+/g, " "); }
  function run() {
    if (!data) return;
    var words = norm(q.value).split(/\s+/).filter(Boolean);
    if (!words.length) { res.innerHTML = ""; count.textContent = data.length.toLocaleString() + " releases — type to search."; return; }
    var hits = [], total = 0;
    for (var i = 0; i < data.length; i++) {
      var r = data[i], hay = r[4], ok = true;
      for (var w = 0; w < words.length; w++) if (hay.indexOf(words[w]) < 0) { ok = false; break; }
      if (ok) { total++; if (hits.length < LIMIT) hits.push(r); }
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
    history.replaceState(null, "", "?q=" + encodeURIComponent(q.value)); run(); }, 120); });
  q.value = new URLSearchParams(location.search).get("q") || "";
  count.textContent = "Loading…";
  fetch("assets/index.json").then(function (r) { return r.json(); }).then(function (d) {
    for (var i = 0; i < d.length; i++) d[i].push(norm(d[i][0] + " " + d[i][1] + " " + d[i][2]));
    data = d; run();
  }).catch(function () { count.textContent = "Search index could not be loaded (open the site through a web server, not as a file)."; });
})();
