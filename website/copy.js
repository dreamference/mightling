/* The page's only script besides init.js: copy buttons, the language menu and the theme button. */
document.addEventListener("DOMContentLoaded", function () {
  var root = document.documentElement;

  document.querySelectorAll("button.copy").forEach(function (button) {
    var label = button.textContent;
    button.addEventListener("click", function () {
      var text = button.getAttribute("data-copy");
      if (!navigator.clipboard) { return; }
      navigator.clipboard.writeText(text).then(function () {
        button.textContent = button.getAttribute("data-done") || label;
        setTimeout(function () { button.textContent = label; }, 1600);
      });
    });
  });

  var menu = document.querySelector("details.lang");
  if (menu) {
    menu.querySelectorAll("a[data-lang]").forEach(function (link) {
      link.addEventListener("click", function () {
        try { localStorage.setItem("lang", link.getAttribute("data-lang")); } catch (e) { /* not saved */ }
      });
    });
    document.addEventListener("click", function (event) {
      if (!menu.contains(event.target)) { menu.removeAttribute("open"); }
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && menu.hasAttribute("open")) {
        menu.removeAttribute("open");
        menu.querySelector("summary").focus();
      }
    });
  }

  var button = document.querySelector("button.theme");
  if (button) {
    var order = ["system", "light", "dark"];
    var show = function (mode) {
      button.setAttribute("data-mode", mode);
      var name = button.getAttribute("data-label-" + mode);
      button.querySelector(".tl").textContent = name;
      var label = button.getAttribute("data-theme-label") + ": " + name;
      button.setAttribute("aria-label", label);
      button.setAttribute("title", label);
    };
    var saved = "system";
    try { saved = localStorage.getItem("theme") || "system"; } catch (e) { /* none saved */ }
    if (order.indexOf(saved) === -1) { saved = "system"; }
    show(saved);
    button.hidden = false;
    button.addEventListener("click", function () {
      var next = order[(order.indexOf(button.getAttribute("data-mode")) + 1) % order.length];
      if (next === "system") { root.removeAttribute("data-theme"); } else { root.setAttribute("data-theme", next); }
      try {
        if (next === "system") { localStorage.removeItem("theme"); } else { localStorage.setItem("theme", next); }
      } catch (e) { /* not saved */ }
      show(next);
    });
  }
});
