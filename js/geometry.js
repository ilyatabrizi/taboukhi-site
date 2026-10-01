// TABOUKHI — film geometry (C1, C6).
// Maps points of the master frame (fractions of the 2880² source) to the screen, through the
// encode's crop and object-fit: cover, and solves the camera for each station.
// Reads assets/media/media.json and assets/media/k-track.json (SPEC §7); both fall back to
// the documented values, so the page works (with a still lens) if either is missing.

const DEFAULT_MEDIA = {
  sq: { w: 1920, h: 1920, crop: [0, 0, 1, 1] },
  pt: { w: 1080, h: 1920, crop: [0.23681, 0.1, 0.50625, 0.9] },
  fps: 24, frames: 384,
};
// Frame-0 engraving box from the audit (x 42.4–59.2%, y 46.3–51.4%): centre and width.
const DEFAULT_ENGRAVING = [0.508, 0.4885, 0.168];

// Stations (SPEC §4 C1). Frame point in master fractions; zoom desktop / phone.
export const STATIONS = {
  home: null,
  // Phone z raised from the table's 1.0 to the 1.1 cap: at 1.0 the pt encode exactly covers
  // the screen's height, so the camera cannot lift the engraving out from under the Peek sheet.
  house: { pt: 'track-mean', z: [1.2, 1.1] },
  collection: { pt: [0.30, 0.44], z: [1.12, 1.05] },
  commission: { pt: [0.505, 0.475], z: [1.35, 1.1] },
  contact: { pt: [0.72, 0.80], z: [1.0, 1.1] },          // phone 1.1: see house
  notices: { pt: [0.72, 0.80], z: [1.0, 1.1] },
};
const ZOOM_CAP = { desktop: 1.35, phone: 1.1 };

export const geo = {
  media: DEFAULT_MEDIA,
  track: null,            // { fps, frames, pts: [[cx, cy, w], …] }
  enc: 'sq',
  W: innerWidth, H: innerHeight,
  mean: [0.505, 0.475],
};

let filmEl = null;
export function initGeometry(film) { filmEl = film; measure(); }

export function measure() {
  geo.W = filmEl ? filmEl.clientWidth : innerWidth;
  geo.H = filmEl ? filmEl.clientHeight : innerHeight;
  return geo;
}

export function setEncode(enc) { if (enc === 'pt' || enc === 'sq') geo.enc = enc; }

export async function loadGeometry(root) {
  const get = (p) => fetch(new URL(p, root)).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  const [media, track] = await Promise.all([get('assets/media/media.json'), get('assets/media/k-track.json')]);
  if (media && media.sq && media.pt) geo.media = { ...DEFAULT_MEDIA, ...media };
  if (track && Array.isArray(track.pts) && track.pts.length) {
    geo.track = { fps: track.fps || 24, frames: track.frames || track.pts.length, pts: track.pts };
    let sx = 0, sy = 0;
    for (const p of track.pts) { sx += p[0]; sy += p[1]; }
    geo.mean = [sx / track.pts.length, sy / track.pts.length];
  }
  return geo;
}

// The cover box of the current encode in the current viewport.
export function cover() {
  const e = geo.media[geo.enc];
  const s = Math.max(geo.W / e.w, geo.H / e.h);
  const dw = e.w * s, dh = e.h * s;
  return { s, dw, dh, ox: (geo.W - dw) / 2, oy: (geo.H - dh) / 2 };
}

// Master-frame point → screen point with the camera at rest.
export function toScreen(fx, fy) {
  const e = geo.media[geo.enc];
  const [x0, y0, cw, ch] = e.crop;
  const c = cover();
  return [c.ox + ((fx - x0) / cw) * e.w * c.s, c.oy + ((fy - y0) / ch) * e.h * c.s];
}
export function widthToScreen(fw) {
  const e = geo.media[geo.enc];
  return (fw / e.crop[2]) * e.w * cover().s;
}

// The engraving at a media time: [cx, cy, w] in master fractions, interpolated between
// k-track frames (frame = t·fps mod frames).
export function engravingAt(t) {
  const tr = geo.track;
  if (!tr) return DEFAULT_ENGRAVING;
  const f = Math.max(0, t) * tr.fps;
  const n = tr.pts.length;
  const i0 = Math.floor(f) % n, i1 = (i0 + 1) % n, a = f - Math.floor(f);
  const p = tr.pts[i0], q = tr.pts[i1];
  return [p[0] + (q[0] - p[0]) * a, p[1] + (q[1] - p[1]) * a, p[2] + (q[2] - p[2]) * a];
}

export function layoutKind() {
  if (geo.W < 720) return 'phone';
  return geo.W < 1080 ? 'tablet' : 'desktop';
}

// Solve the camera for a station: the frame point lands on the centre of the free area,
// clamped so the film always covers the viewport. Returns { tx, ty, z, point, free }.
export function solveStation(name, free) {
  const st = STATIONS[name];
  if (!st || !free) return { tx: 0, ty: 0, z: 1, point: null, free: null };
  const phone = geo.W < 720;
  let z = st.z[phone ? 1 : 0];
  z = Math.min(z, phone ? ZOOM_CAP.phone : ZOOM_CAP.desktop);
  let pt = st.pt === 'track-mean' ? geo.mean : st.pt;
  // Wide desktop: Collection's point moves right so the name's last letter clears the panel.
  if (name === 'collection' && layoutKind() === 'desktop') pt = [0.335, 0.44];
  let sol = solveAt(pt, z, free);
  // The engraved name is shown whole or not at all, never cut by the panel: where the free
  // area beside the panel is narrower than the name (tablets), House and Commission frame
  // the arm the way Collection does, zoomed in just enough (up to the cap) that the name,
  // wherever it travels in the loop, stands fully behind the glass.
  if (!phone && geo.track && (name === 'house' || name === 'commission')) {
    const ew = widthToScreen(Math.max(...geo.track.pts.map((q) => q[2]))) * z;
    if (free.x1 - free.x0 < ew * 1.15) {
      const panelLeft = free.x1 + 24;
      const cp = STATIONS.collection.pt;
      for (let zz = Math.min(STATIONS.collection.z[0], ZOOM_CAP.desktop); ; zz = Math.min(ZOOM_CAP.desktop, zz + 0.01)) {
        sol = solveAt(cp, zz, free);
        if (nameLeft(sol) >= panelLeft || zz >= ZOOM_CAP.desktop) break;
      }
    }
  }
  return sol;
}
function solveAt(pt, z, free) {
  const [fx, fy] = pt;
  const [x, y] = toScreen(fx, fy);
  const c = cover();
  const cx = geo.W / 2, cy = geo.H / 2;
  const px = (free.x0 + free.x1) / 2, py = (free.y0 + free.y1) / 2;
  const mx = Math.max(0, (c.dw * z - geo.W) / 2), my = Math.max(0, (c.dh * z - geo.H) / 2);
  const tx = Math.min(mx, Math.max(-mx, px - cx - (x - cx) * z));
  const ty = Math.min(my, Math.max(-my, py - cy - (y - cy) * z));
  return { tx, ty, z, point: [cx + (x - cx) * z + tx, cy + (y - cy) * z + ty], target: [px, py], free };
}
// The leftmost screen x the engraved name reaches over the whole loop, under a camera.
function nameLeft({ tx, z }) {
  const cx = geo.W / 2;
  let l = Infinity;
  for (const [fx, fy, fw] of geo.track.pts) l = Math.min(l, cx + (toScreen(fx, fy)[0] - widthToScreen(fw) / 2 - cx) * z + tx);
  return l;
}

export function camTransform({ tx, ty, z }) {
  return `translate3d(${tx.toFixed(2)}px, ${ty.toFixed(2)}px, 0) scale(${z.toFixed(4)})`;
}
