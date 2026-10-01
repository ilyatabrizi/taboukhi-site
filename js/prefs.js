// TABOUKHI — the one remembered setting (the Film switch) and session conveniences.
// Every storage access is wrapped: storage can be absent, full or blocked.

const LS_FILM = 'tbk.film';

export const store = {
  get(area, key) { try { return window[area].getItem(key); } catch { return null; } },
  set(area, key, value) { try { window[area].setItem(key, value); return true; } catch { return false; } },
};
export const session = {
  get: (k) => store.get('sessionStorage', k),
  set: (k, v) => store.set('sessionStorage', k, v),
};

export const prefs = {
  get film() { return store.get('localStorage', LS_FILM) === 'held' ? 'held' : 'playing'; },
  set film(v) {
    if (v === 'held') store.set('localStorage', LS_FILM, 'held');
    else { try { localStorage.removeItem(LS_FILM); } catch { /* ignore */ } }
    document.dispatchEvent(new CustomEvent('tbk:film', { detail: v }));
  },
};
