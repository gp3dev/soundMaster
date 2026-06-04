'use strict';
function navToggle() {
  document.getElementById('nav-menu').classList.toggle('open');
}
document.addEventListener('click', e => {
  if (!e.target.closest('.nav-wrap')) {
    const m = document.getElementById('nav-menu');
    if (m) m.classList.remove('open');
  }
});
