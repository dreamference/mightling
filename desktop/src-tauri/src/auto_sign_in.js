// Signs the window in with the web UI's admin account, once, when it has no session
// (specs/DREAMFERENCE_MIGHTLING_NODE.md §7). The account's password is generated per install and
// stored in ~/.config/dreamference/chat-admin.json; main.rs fills the two placeholders with JSON
// string literals read from that file, and evaluates this after every page load. On a client
// machine there is no such file and this script is never run: the login page stays. An owner who
// changed the password gets the login page too, because the attempt fails and is not repeated.
(function () {
  "use strict";
  var FLAG = "mightling-auto-sign-in";
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
            "username=" + encodeURIComponent(__MIGHTLING_EMAIL_JSON__) +
            "&password=" + encodeURIComponent(__MIGHTLING_PASSWORD_JSON__),
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
