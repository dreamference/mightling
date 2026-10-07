// The install line's Copy button; the page works without it.
document.querySelectorAll("button.copy").forEach(function (button) {
  button.addEventListener("click", function () {
    var text = button.getAttribute("data-copy");
    if (!navigator.clipboard) { return; }
    navigator.clipboard.writeText(text).then(function () {
      button.textContent = "Copied";
      setTimeout(function () { button.textContent = "Copy"; }, 1600);
    });
  });
});
