#!/usr/bin/env python3
"""Read an MP4's box tree without ffmpeg: top-level order, tracks, samples, keyframes, B-frames.

    python3 scripts/mp4probe.py a.mp4 b.mp4          # one summary line per file
    python3 scripts/mp4probe.py --check a.mp4 ...    # exit 1 unless every file passes check()

check() is what SPEC §7 asks of every MP4 on the site: moov before mdat (fast start),
exactly one track, a video track, no audio track.
"""
import struct
import sys


def boxes(b, off, end):
    while off < end:
        size, typ = struct.unpack('>I4s', b[off:off + 8]); hdr = 8
        if size == 1: size = struct.unpack('>Q', b[off + 8:off + 16])[0]; hdr = 16
        if size == 0: size = end - off
        yield typ.decode('latin1'), off, off + hdr, off + size
        off += size


def walk(b, off, end, path=''):
    out = {}
    for t, o, s, e in boxes(b, off, end):
        p = path + '/' + t
        out.setdefault(p, []).append((s, e))
        if t in ('moov', 'trak', 'mdia', 'minf', 'stbl', 'edts', 'udta'):
            for k, v in walk(b, s, e, p).items(): out.setdefault(k, []).extend(v)
    return out


def probe(fn):
    b = open(fn, 'rb').read(); t = walk(b, 0, len(b))
    top = [k for k in t if k.count('/') == 1]
    info = {'top_order': top, 'size_kb': round(len(b) / 1024), 'bytes': len(b)}
    handlers = []
    for s, e in t.get('/moov/trak/mdia/hdlr', []): handlers.append(b[s + 8:s + 12].decode())
    info['tracks'] = handlers
    info['trak_count'] = len(t.get('/moov/trak', []))
    stsz = t.get('/moov/trak/mdia/minf/stbl/stsz', [])
    if stsz:
        s, e = stsz[0]; info['samples'] = struct.unpack('>I', b[s + 8:s + 12])[0]
    mdhd = t.get('/moov/trak/mdia/mdhd', [])
    if mdhd:
        s, e = mdhd[0]
        if b[s] == 1: ts, dur = struct.unpack('>IQ', b[s + 20:s + 32])
        else: ts, dur = struct.unpack('>II', b[s + 12:s + 20])
        info['timescale'] = ts; info['duration_s'] = round(dur / ts, 4) if ts else None
    stts = t.get('/moov/trak/mdia/minf/stbl/stts', [])
    if stts:
        s, e = stts[0]; n = struct.unpack('>I', b[s + 4:s + 8])[0]
        info['stts'] = [struct.unpack('>II', b[s + 8 + 8 * i:s + 16 + 8 * i]) for i in range(n)]
        c, d = max(info['stts'])            # the frame rate is the delta most samples use
        if info.get('timescale'): info['fps'] = round(info['timescale'] / d, 4)
    stss = t.get('/moov/trak/mdia/minf/stbl/stss', [])
    if stss:
        s, e = stss[0]; n = struct.unpack('>I', b[s + 4:s + 8])[0]
        info['keyframes'] = [struct.unpack('>I', b[s + 8 + 4 * i:s + 12 + 4 * i])[0] for i in range(n)]
    else: info['keyframes'] = 'all (no stss)'
    info['b_frames(ctts)'] = bool(t.get('/moov/trak/mdia/minf/stbl/ctts'))
    return info


def check(fn):
    """(ok, info, problems): moov precedes mdat, exactly one track, it is video, no audio."""
    i = probe(fn); order = i['top_order']; problems = []
    if '/moov' not in order or '/mdat' not in order: problems.append(f'missing moov/mdat {order}')
    elif order.index('/moov') > order.index('/mdat'): problems.append(f'mdat before moov {order}')
    if i['trak_count'] != 1: problems.append(f"{i['trak_count']} tracks")
    if i['tracks'] != ['vide']: problems.append(f"handlers {i['tracks']}")
    if 'soun' in i['tracks']: problems.append('audio track present')
    i['moov_before_mdat'] = not any('before' in p or 'missing' in p for p in problems)
    i['audio'] = 'soun' in i['tracks']
    return (not problems), i, problems


def line(fn, i):
    kf = i['keyframes']
    return (f"{fn.split('/')[-1]} {i['bytes']} B ({i['size_kb']} KB) order {i['top_order']} tracks {i['tracks']}"
            f" samples {i.get('samples')} stts {i.get('stts')} fps {i.get('fps')} mdhd {i.get('duration_s')} s keyframes "
            f"{kf if isinstance(kf, str) or len(kf) < 20 else f'{len(kf)} kf, first {kf[:6]}'} ctts {i['b_frames(ctts)']}")


if __name__ == '__main__':
    args = sys.argv[1:]; strict = '--check' in args
    bad = 0
    for f in [a for a in args if a != '--check']:
        ok, i, problems = check(f)
        print(line(f, i), '' if not strict else ('PASS' if ok else 'FAIL ' + '; '.join(problems)))
        bad += (not ok)
    sys.exit(1 if strict and bad else 0)
