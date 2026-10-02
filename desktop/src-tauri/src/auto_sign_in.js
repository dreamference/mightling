// Signs the window in with the web UI's default account, once, when it has no session
// (specs/DREAMFERENCE_PUFFIN_NODE.md §7). A fresh install has one account with a published
// default password, and "type `puffin-app` and it works" is the point of the client; an owner
// who changed the password gets the ordinary login page, because the attempt fails and is not
// repeated. Evaluated by main.rs after every page load; the two placeholders are filled in there.
(function () {
  "use strict";
  var FLAG = "puffin-auto-sign-in";
  try {
    if (window.sessionStorage.getItem(FLAG)) {
      return;
    }
    fetch("/api/me", { credentials: "same-origin" })
      .then(function (me) {
        if (me.status !== 401 && me.status !== 403) {
          return null; // Signed in already, or the server is not the web UI: nothing to do.
        }
        // Set before the attempt, so a wrong password is tried once per window, not per page.
        window.sessionStorage.setItem(FLAG, "tried");
        return fetch("/api/auth/login", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body:
            "username=" + encodeURIComponent("__PUFFIN_EMAIL__") +
            "&password=" + encodeURIComponent("__PUFFIN_PASSWORD__"),
        });
      })
      .then(function (login) {
        if (login && login.ok) {
          window.location.replace("/app");
        }
      })
      .catch(function () {});
  } catch (error) {
    // No sessionStorage or no fetch: leave the page as it is.
  }
})();
