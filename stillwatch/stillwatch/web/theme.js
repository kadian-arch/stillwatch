/* Which theme, settled before anything paints.

   Light unless this browser has asked for dark. A dark desktop says
   something about somebody's screen, not about what this page is for, and
   deciding from it meant half the people who opened Stillwatch were shown
   the quieter of the two themes on that basis alone.

   Its own file rather than a tag in the page, because the page is served
   under `default-src 'self'` and an inline script is refused without a
   word. */
(function () {
  var saved = null;
  try {
    saved = localStorage.getItem("stillwatch.theme");
  } catch (error) {
    saved = null;  /* private window, or storage turned off */
  }
  document.documentElement.dataset.theme = saved === "dark" ? "dark" : "light";
})();
