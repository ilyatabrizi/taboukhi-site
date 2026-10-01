// TABOUKHI — installable as TABOUKHI. Registered late so it never competes with the first
// paint, only over https and never on a local host (A4.6).
export function initSW(root) {
  if (!('serviceWorker' in navigator) || location.protocol !== 'https:') return;
  if (/^(localhost|127\.|\[::1\]|0\.0\.0\.0)|\.localhost$|\.test$/.test(location.hostname)) return;
  const go = () => navigator.serviceWorker.register(new URL('sw.js', root).href, { scope: root.href }).catch(() => {});
  if (document.readyState === 'complete') setTimeout(go, 1500); else addEventListener('load', () => setTimeout(go, 1500), { once: true });
}
