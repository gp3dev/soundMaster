// Applies saved theme before first paint to prevent a flash of wrong colors.
(function () {
  if (localStorage.getItem('sm_theme') === 'light')
    document.documentElement.dataset.theme = 'light';
}());
