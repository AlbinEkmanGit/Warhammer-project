(function () {
  var sheets = Array.prototype.slice.call(document.querySelectorAll("details.datasheet"));
  var search = document.getElementById("search");
  var hideLegends = document.getElementById("hide-legends");
  var count = document.getElementById("count");

  function applyFilter() {
    var q = search.value.trim().toLowerCase();
    var shown = 0;
    sheets.forEach(function (el) {
      var matches = !q || el.dataset.search.indexOf(q) !== -1;
      var legendsHidden = hideLegends.checked && el.dataset.legends === "1";
      var visible = matches && !legendsHidden;
      el.hidden = !visible;
      if (visible) shown++;
    });
    count.textContent = shown + " shown";
  }

  function setAll(open) {
    sheets.forEach(function (el) {
      if (!el.hidden) el.open = open;
    });
  }

  function openFromHash() {
    if (!location.hash) return;
    var el = document.getElementById(location.hash.slice(1));
    if (el && el.tagName === "DETAILS") {
      el.hidden = false;
      el.open = true;
      el.scrollIntoView();
    }
  }

  search.addEventListener("input", applyFilter);
  hideLegends.addEventListener("change", applyFilter);
  document.getElementById("expand").addEventListener("click", function () { setAll(true); });
  document.getElementById("collapse").addEventListener("click", function () { setAll(false); });

  // Clicking a bar updates the URL so a unit can be bookmarked / shared.
  sheets.forEach(function (el) {
    el.addEventListener("toggle", function () {
      if (el.open) history.replaceState(null, "", "#" + el.id);
    });
  });

  window.addEventListener("hashchange", openFromHash);
  applyFilter();
  openFromHash();
})();

const factionSelect = document.querySelector("#faction-select");

if (factionSelect) {
    factionSelect.addEventListener("change", () => {
        if (!factionSelect.value) return;

        const url = new URL(window.location.href);

        // Remove any previously selected run.
        url.searchParams.delete("run");

        // Select the new faction.
        url.searchParams.set("faction", factionSelect.value);

        window.location.href = url.toString();
    });
}
