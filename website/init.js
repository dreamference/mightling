/* Runs in <head>, before first paint: applies the saved colour theme and, on the site root only,
   sends a visitor to their language. Nothing here leaves the browser. */
(function () {
  var root = document.documentElement;
  try {
    var theme = localStorage.getItem("theme");
    if (theme === "light" || theme === "dark") { root.setAttribute("data-theme", theme); }
  } catch (e) { /* storage blocked: follow the system */ }
  root.className += " js";

  if (root.getAttribute("data-root") !== "1") { return; }
  var langs = (root.getAttribute("data-langs") || "").split(",");
  function supported(tag) {
    tag = String(tag || "").toLowerCase().replace("_", "-");
    if (langs.indexOf(tag) !== -1) { return tag; }
    var primary = tag.split("-")[0];
    if (primary === "fil" || primary === "nb" || primary === "nn") { return ""; }
    return langs.indexOf(primary) !== -1 ? primary : "";
  }
  var target = "";
  try {
    var asked = new URLSearchParams(location.search).get("lang");
    if (asked) { target = supported(asked); }
  } catch (e) { /* old browser */ }
  if (!target) {
    try { target = supported(localStorage.getItem("lang")); } catch (e) { /* none saved */ }
  }
  if (!target) {
    var prefs = navigator.languages && navigator.languages.length ? navigator.languages : [navigator.language];
    for (var i = 0; i < prefs.length && !target; i += 1) { target = supported(prefs[i]); }
  }
  if (target && target !== "en") { location.replace("/" + target + "/" + location.hash); }
})();
