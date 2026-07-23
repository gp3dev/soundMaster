'use strict';

const SM_STATION_KEY = 'sm_station';

function smGetStationFromUrl() {
  return new URLSearchParams(window.location.search).get('station');
}

function smSetStationParam(slug) {
  localStorage.setItem(SM_STATION_KEY, slug);
  const url = new URL(window.location.href);
  url.searchParams.set('station', slug);
  window.history.replaceState({}, '', url);
}

async function smFetchStations() {
  try {
    const resp = await fetch('/api/stations');
    if (!resp.ok) return [];
    return await resp.json();
  } catch (_) {
    return [];
  }
}

function _smEscapeHtml(s) {
  const div = document.createElement('div');
  div.textContent = s;
  return div.innerHTML;
}

/**
 * Resolves the active station (URL param > localStorage > first active station),
 * populates #station-picker if present on the page, and reloads the page with the
 * new ?station= param when the user switches stations.
 *
 * Returns { slug, stations } — slug is null if no station exists yet.
 */
async function smInitPicker() {
  const stations = await smFetchStations();
  let slug = smGetStationFromUrl() || localStorage.getItem(SM_STATION_KEY);
  if (!slug || !stations.some(s => s.slug === slug)) {
    slug = stations.length ? stations[0].slug : null;
  }
  if (slug) smSetStationParam(slug);

  const picker = document.getElementById('station-picker');
  if (picker) {
    if (stations.length === 0) {
      picker.innerHTML = '<option value="">Keine Messstelle</option>';
    } else {
      picker.innerHTML = stations.map(s =>
        `<option value="${_smEscapeHtml(s.slug)}" ${s.slug === slug ? 'selected' : ''}>${_smEscapeHtml(s.name)}</option>`
      ).join('');
    }
    picker.addEventListener('change', () => {
      if (picker.value) {
        smSetStationParam(picker.value);
        window.location.reload();
      }
    });
  }

  return { slug, stations };
}

function smLastSeenLabel(lastSeenAtUnix) {
  if (!lastSeenAtUnix) return 'Keine Daten empfangen';
  const ageSec = Date.now() / 1000 - lastSeenAtUnix;
  if (ageSec < 120) return 'vor < 2 Min';
  if (ageSec < 3600) return `vor ${Math.round(ageSec / 60)} Min`;
  if (ageSec < 86400) return `vor ${Math.round(ageSec / 3600)} Std`;
  return `vor ${Math.round(ageSec / 86400)} Tagen`;
}

function smLastSeenClass(lastSeenAtUnix) {
  if (!lastSeenAtUnix) return 'err';
  const ageSec = Date.now() / 1000 - lastSeenAtUnix;
  if (ageSec < 300) return 'ok';
  if (ageSec < 3600) return 'warn';
  return 'err';
}
