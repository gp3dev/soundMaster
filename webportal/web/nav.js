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

function themeToggle() {
  const toLight = document.documentElement.dataset.theme !== 'light';
  document.documentElement.dataset.theme = toLight ? 'light' : '';
  localStorage.setItem('sm_theme', toLight ? 'light' : '');
  _syncThemeBtn();
  window.dispatchEvent(new Event('themechange'));
}

function _syncThemeBtn() {
  const btn = document.getElementById('theme-btn');
  if (!btn) return;
  const light = document.documentElement.dataset.theme === 'light';
  btn.textContent = light ? '🌙' : '☀';
  btn.title = light ? 'Dark mode' : 'Light mode';
}

document.addEventListener('DOMContentLoaded', _syncThemeBtn);
