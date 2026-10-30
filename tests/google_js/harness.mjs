// A small fake photos.google.com page for scripts/google/homecloud_google.js.
//
// It gives the script what the real page gives it: WIZ_global_data, fetch, a folder picker,
// navigator.wakeLock and navigator.locks, and a minimal DOM. The fake batchexecute answers in
// the format that GPTK's makeApiRequest and parser read (the wrb.fr envelope), checks every
// request against GPTK's request format, and records each request id. Media and zip
// addresses are served from memory. The folder handle writes into a real folder the way
// Chrome does: a .crswap file that close() moves into place.
//
// Usage: node harness.mjs --scenario NAME --dir DIR --script FILLED_SCRIPT [--run N]
// The last line on stdout is a JSON summary. Everything else goes to stderr.

import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import vm from 'node:vm';
import zlib from 'node:zlib';
import crypto from 'node:crypto';

// ----------------------------------------------------------------- arguments

const args = {};
for (let i = 2; i < process.argv.length; i += 2) args[process.argv[i].replace(/^--/, '')] = process.argv[i + 1];
const SCENARIO = args.scenario || 'full';
const DIR = path.resolve(args.dir);
const RUN = Number(args.run || 1);
const SCRIPT = path.resolve(args.script);
const KNOWN = ['full', 'nospace', 'cors', 'omit', 'broken', 'resume', 'wrongfolder', 'offline'];
if (!KNOWN.includes(SCENARIO)) throw new Error('unknown scenario ' + SCENARIO);

// ----------------------------------------------------------------- the fake library

const ME = 'ACTOR-ME';
const OTHER = 'ACTOR-OTHER';
const AT = 'AT-TOKEN-0123456789';
const FSID = '-4242424242424242424';
const BL = 'boq_photosuiserver_20261020.07_p0';
const PATH = '/_/PhotosUi/';
const READ_ONLY = ['lcxiM', 'Z5xsfc', 'snAcKc', 'EWgK9e', 'fDcn4b', 'VrseUb', 'yCLA7', 'dnv2s'];
// GPTK src/api/api.ts line 664, the second part of the EWgK9e request.
const EWGK9E_TAIL = [[null, null, null, null, null, null, null, null, null, null, null, null, null, null, null, null, null, null, null, null, null, null, null, null, [], null, null, null, null, null, null, null, null, null, null, []]];

const T = (iso) => Date.parse(iso);
const BASE = [
  { key: 'K09', name: 'IMG_0001.MOV', kind: 'video', taken: T('2024-07-20T10:00:00Z'), size: 50000, duration: 4000 },
  { key: 'K01', name: 'IMG_0001.HEIC', live: true, fav: true, taken: T('2024-07-15T09:00:00Z'), size: 6000, liveSize: 20000 },
  { key: 'K02', name: 'IMG_0001.HEIC', taken: T('2024-07-10T08:00:00Z'), size: 5000 },
  { key: 'K11', name: 'img_0001.heic', taken: T('2024-07-05T08:00:00Z'), size: 5100 },
  { key: 'K03', name: 'VID_20240630.mp4', kind: 'video', taken: T('2024-06-30T23:59:00Z'), size: 300000, duration: 12000 },
  { key: 'K04', name: 'archived.jpg', archived: true, taken: T('2023-12-31T23:30:00Z'), size: 4000 },
  { key: 'K06', name: 'a:b?.jpg', taken: T('2023-08-01T12:00:00Z'), size: 4100 },
  { key: 'K12', name: 'con.jpg', taken: T('2023-07-01T12:00:00Z'), size: 4200 },
  { key: 'K13', name: 'note‮txt~1.jpg', taken: T('2023-06-01T12:00:00Z'), size: 4300 },
  { key: 'K05', name: 'edited.jpg', taken: T('2023-05-01T12:00:00Z'), size: 3500, editedSize: 3300 },
  { key: 'K07', name: 'from-fdcn4b.png', taken: T('2023-04-01T12:00:00Z'), size: 4400, extOnly: true },
  { key: 'K08', name: 'saver.jpg', taken: T('2023-03-01T12:00:00Z'), size: 4500, quality: false },
  { key: 'K10', name: 'old.jpg', taken: T('1999-12-31T23:30:00Z'), size: 4600 },
];
const K20 = { key: 'K20', name: 'IMG_0001.HEIC', taken: T('2024-07-25T10:00:00Z'), size: 5300 };

function library() {
  let items = BASE.slice();
  if (SCENARIO === 'resume' && RUN >= 2) items = [K20].concat(items.filter((it) => it.key !== 'K02' && it.key !== 'K10'));
  return items.map((it) => Object.assign({ dedup: 'D' + it.key.slice(1), kind: 'photo' }, it));
}
const ITEMS = library();
const BY_KEY = new Map(ITEMS.map((it) => [it.key, it]));
// Google's size for a Live Photo: the photo alone, or the photo and its video together.
const LIVE_RULE = SCENARIO === 'resume' ? 'both' : 'exact';

const ALBUMS = [
  { key: 'ALB-1', title: 'Trip', owner: ME, shared: false, members: ['D01', 'D03'] },
  { key: 'ALB-2', title: 'Family', owner: ME, shared: true, authKey: 'AUTH-2', members: ['D01', 'D04', 'D99'] },
  { key: 'ALB-3', title: 'Joined', owner: OTHER, shared: true, authKey: 'AUTH-3', members: ['D05'] },
];

function content(key, variant, size) {
  const out = Buffer.alloc(size);
  let x = 2166136261;
  for (const ch of key + '/' + variant) x = Math.imul(x ^ ch.charCodeAt(0), 16777619) >>> 0;
  const head = Buffer.from('HCFAKE ' + key + ' ' + variant + '\n');
  head.copy(out, 0, 0, Math.min(head.length, size));
  for (let i = head.length; i < size; i++) {
    x = (Math.imul(x, 1103515245) + 12345) >>> 0;
    out[i] = x >>> 24;
  }
  return out;
}

const BLOBS = new Map(); // "key/variant" -> Buffer
for (const it of ITEMS) {
  BLOBS.set(it.key + '/orig', content(it.key, 'orig', it.size));
  if (it.editedSize) BLOBS.set(it.key + '/edited', content(it.key, 'edited', it.editedSize));
  if (it.live) BLOBS.set(it.key + '/live', content(it.key, 'live', it.liveSize));
}
const sha = (buf) => crypto.createHash('sha256').update(buf).digest('hex');

function googleSize(it) {
  return it.live && LIVE_RULE === 'both' ? it.size + it.liveSize : it.size;
}

const isVideo = (it) => it.kind === 'video';
function mediaUrl(it, variant) {
  const host = isVideo(it) || variant === 'live' ? 'video-downloads.googleusercontent.com' : 'lh3.googleusercontent.com';
  return 'https://' + host + '/hcfake/' + it.key + '/' + variant + '?sig=SECRET-LINK-' + it.key;
}
const thumbUrl = (it) => 'https://photos.fife.usercontent.google.com/pw/THUMB-' + it.key;

// ----------------------------------------------------------------- records

const rec = {
  rpc: {},
  protocolErrors: [],
  media: [], // {key, variant, cred, outcome}
  blocked: 0,
  nonGoogle: [],
  sinks: [],
  joinedAlbumRead: false,
  accountRead: false,
  raptRead: false,
  unhandled: [],
  picker: [],
  wake: { requested: 0, released: 0 },
  locks: 0,
};
const perr = (msg) => {
  rec.protocolErrors.push(msg);
  process.stderr.write('PROTOCOL: ' + msg + '\n');
};
process.on('unhandledRejection', (e) => rec.unhandled.push(String(e && e.stack ? e.stack : e)));

// ----------------------------------------------------------------- batchexecute

function libItem(it) {
  const ext = {};
  if (it.fav) ext['163238866'] = [true];
  if (it.duration) ext['76647426'] = [it.duration, null, 1440, 1080];
  if (it.live) ext['146008172'] = [null, 2500];
  ext['129168200'] = [null, [[0, 0]]];
  return [it.key, [thumbUrl(it), 1440, 1080, null, null, null, null, null, [1440, 1080, 1], [1]], it.taken, it.dedup,
    3600000, it.taken + 1000, null, [[1], [2], [3]], 2, null, null, null, null, !!it.archived, 53246, ext];
}

function bulkEntry(it) {
  const info = new Array(35).fill(null);
  info[2] = '';
  info[3] = it.extOnly ? null : it.name;
  info[6] = it.taken;
  info[7] = 3600000;
  info[8] = it.taken + 1000;
  info[9] = it.extOnly ? null : googleSize(it);
  info[10] = 100;
  info[34] = [2, 0, it.quality === false ? 1 : 2, 0, null, 1];
  return [it.key, info, null, null, null, [it.key, ['100000000001', null, '100000000002']]];
}

function extInfo(it) {
  const row = new Array(33).fill(null);
  Object.assign(row, { 0: it.key, 1: '', 2: it.name, 3: it.taken, 4: 3600000, 5: googleSize(it), 6: 1440, 7: 1080,
    11: it.dedup, 12: [[1], [2]], 14: 2, 30: [2, 0, it.quality === false ? 1 : 2, 0, null, 1] });
  return [row];
}

function itemInfo(it) {
  const ext = { '163238866': [!!it.fav], '318563170': [[2, 0, it.quality === false ? 1 : 2, 0, null, 1]], '525000000': [[1]] };
  if (it.duration) ext['76647426'] = [it.duration, null, 1440, 1080];
  if (it.live) ext['146008172'] = [null, 2500, null, mediaUrl(it, 'live')];
  const item0 = [it.key, [thumbUrl(it), 1440, 1080], it.taken, it.dedup, 3600000, it.taken + 1000, [ME],
    [[1], [2], [3]], 2, null, null, null, null, !!it.archived, 53578, ext];
  const out = [item0, mediaUrl(it, it.editedSize ? 'edited' : 'orig'), [[1], [2]], [ME, '100000000001'], { 11: '', 13: thumbUrl(it) }];
  if (it.editedSize) out.push(null, null, mediaUrl(it, 'orig'));
  return out;
}

function albumRow(a) {
  const meta = [a.shared ? 4 : 1, a.title, [1, 2, null, null, 3, [1, 0], [2, 0], null, null, 4], a.members.length,
    a.shared ? true : null, a.shared ? a.authKey : null, [19999]];
  return [a.key, [thumbUrl({ key: a.key }), 1440, 1080], null, null, null, null, [a.owner], [[1], [2]], { 72930366: meta }];
}

function albumItem(a, d, i) {
  const it = ITEMS.find((x) => x.dedup === d);
  const taken = it ? it.taken : T('2020-01-01T00:00:00Z');
  return ['AK-' + a.key + '-' + i, [thumbUrl({ key: d }), 1440, 1080], taken, d, 0, taken + 1000, [a.owner], [[1]], 2, { 15: 53578 }];
}

const tokens = new Map(); // token -> {key, polls}
let lcxiCalls = 0;
let tokenCount = 0;

function pageOf(list, size, pageId, prefix) {
  const start = pageId ? Number(String(pageId).slice(prefix.length)) : 0;
  if (!Number.isInteger(start) || start < 0 || start > list.length) {
    perr('bad page id ' + pageId);
    return { part: [], next: null };
  }
  const part = list.slice(start, start + size);
  return { part, next: start + size < list.length ? prefix + (start + size) : null };
}

function answer(rpcid, data) {
  const isNullOrStr = (v) => v === null || typeof v === 'string';
  switch (rpcid) {
    case 'lcxiM': {
      lcxiCalls += 1;
      if (!(Array.isArray(data) && data.length === 6 && isNullOrStr(data[0]) && (data[1] === null || typeof data[1] === 'number') &&
        data[2] === 500 && data[3] === null && data[4] === 1 && data[5] === 3)) perr('lcxiM request ' + JSON.stringify(data));
      const pg = pageOf(ITEMS, 4, data[0], 'LCX-');
      const last = pg.part.length ? pg.part[pg.part.length - 1].taken : 0;
      return [pg.part.map(libItem), pg.next, String(last)];
    }
    case 'Z5xsfc': {
      if (!(Array.isArray(data) && data.length === 10 && isNullOrStr(data[0]) && data[4] === 1 && data[7] === 100 &&
        JSON.stringify(data[8]) === '[2]' && data[9] === 5 && [1, 2, 3, 5, 6].every((i) => data[i] === null))) perr('Z5xsfc request ' + JSON.stringify(data));
      const pg = pageOf(ALBUMS, 2, data[0], 'ALBP-');
      return [pg.part.map(albumRow), pg.next, [1], [1]];
    }
    case 'snAcKc': {
      if (!(Array.isArray(data) && data.length === 4 && typeof data[0] === 'string' && isNullOrStr(data[1]) && data[2] === null && data[3] === null)) {
        perr('snAcKc request ' + JSON.stringify(data));
      }
      const a = ALBUMS.find((x) => x.key === data[0]);
      if (!a) { perr('snAcKc unknown album'); return [null, [], '']; }
      if (a.owner !== ME) rec.joinedAlbumRead = true;
      const pg = pageOf(a.members, 1, data[1], 'AIP-');
      const start = data[1] ? Number(String(data[1]).slice(4)) : 0;
      const meta = [a.key, a.title, [1, 2, null, null, 3, [1, 0], [2, 0], null, null, 4]];
      meta[21] = a.members.length;
      return [null, pg.part.map((d, i) => albumItem(a, d, start + i)), pg.next || '', meta, null, 0];
    }
    case 'EWgK9e': {
      // [[[mappedKeys], [[null, ...]]]] as in GPTK src/api/api.ts line 664
      const ok = Array.isArray(data) && data.length === 1 && Array.isArray(data[0]) && data[0].length === 2 &&
        Array.isArray(data[0][0]) && data[0][0].length === 1 && Array.isArray(data[0][0][0]) &&
        JSON.stringify(data[0][1]) === JSON.stringify(EWGK9E_TAIL);
      if (!ok) { perr('EWgK9e request shape'); return [[null, []]]; }
      const keys = data[0][0][0];
      if (!keys.every((k) => Array.isArray(k) && k.length === 1 && typeof k[0] === 'string')) perr('EWgK9e keys');
      return [[null, keys.map((k) => BY_KEY.get(k[0])).filter(Boolean).map(bulkEntry)]];
    }
    case 'fDcn4b': {
      if (!(Array.isArray(data) && data.length === 5 && data[1] === 1 && data[2] === null && data[3] === null && data[4] === 1)) perr('fDcn4b request');
      const it = BY_KEY.get(data[0]);
      return it ? extInfo(it) : [];
    }
    case 'VrseUb': {
      if (!(Array.isArray(data) && data.length === 5 && typeof data[0] === 'string' && data.slice(1).every((v) => v === null))) perr('VrseUb request');
      const it = BY_KEY.get(data[0]);
      return it ? itemInfo(it) : [];
    }
    case 'yCLA7': {
      if (!(Array.isArray(data) && data.length === 1 && Array.isArray(data[0]) && data[0].length === 1 &&
        Array.isArray(data[0][0]) && data[0][0].length === 1)) perr('yCLA7 request');
      const it = BY_KEY.get(data[0][0][0]);
      if (!it) return [];
      tokenCount += 1;
      const token = 'TOKEN-' + it.key + '-' + tokenCount;
      tokens.set(token, { key: it.key, polls: 0 });
      return [token];
    }
    case 'dnv2s': {
      if (!(Array.isArray(data) && data.length === 1 && Array.isArray(data[0]) && data[0].length === 1 && typeof data[0][0] === 'string')) perr('dnv2s request');
      const t = tokens.get(data[0][0]);
      if (!t) { perr('dnv2s unknown token'); return []; }
      t.polls += 1;
      if (t.polls < 2) return [[[[data[0][0], 1], 0]]];
      const z = zipFor(BY_KEY.get(t.key));
      return [[[[data[0][0], 4, [[z.name, 'https://doc-export.googleusercontent.com/download/' + data[0][0], z.buf.length, z.unzipped]], 0], 100, 2]]];
    }
    default:
      perr('request id that is not read-only: ' + rpcid);
      return null;
  }
}

function envelope(rpcid, payload) {
  const line1 = JSON.stringify([['wrb.fr', rpcid, JSON.stringify(payload), null, null, null, 'generic'], ['di', 42], ['af.httprm', 41, '-5512345678901234567', 7]]);
  const line2 = JSON.stringify([['e', 4, null, null, line1.length + 25]]);
  return ")]}'\n\n" + line1.length + '\n' + line1 + '\n' + line2.length + '\n' + line2 + '\n';
}

const abortError = () => new DOMException('The operation was aborted.', 'AbortError');

async function batchexecute(url, init) {
  if (init.signal && init.signal.aborted) throw abortError();
  if (SCENARIO === 'offline') {
    rec.offline = (rec.offline || 0) + 1;
    throw new TypeError('Failed to fetch');
  }
  const u = new URL(url);
  if (u.origin + u.pathname !== 'https://photos.google.com' + PATH + 'data/batchexecute') perr('batchexecute path ' + u.pathname);
  if (init.method !== 'POST') perr('batchexecute method ' + init.method);
  if (init.credentials !== 'include') perr('batchexecute credentials ' + init.credentials);
  const headers = init.headers || {};
  if (JSON.stringify(headers) !== JSON.stringify({ 'content-type': 'application/x-www-form-urlencoded;charset=UTF-8' })) perr('batchexecute headers ' + JSON.stringify(headers));
  const rpcid = u.searchParams.get('rpcids');
  const want = { rpcids: rpcid, 'source-path': '/', 'f.sid': FSID, bl: BL, pageId: 'none', rt: 'c' };
  const wantQuery = Object.keys(want).map((k) => k + '=' + encodeURIComponent(want[k])).join('&');
  if (u.search !== '?' + wantQuery) perr('batchexecute query ' + u.search);
  const m = /^f\.req=([^&]*)&at=([^&]*)&$/.exec(String(init.body));
  if (!m) {
    perr('batchexecute body');
    return new Response('', { status: 400 });
  }
  if (decodeURIComponent(m[2]) !== AT) perr('batchexecute at token');
  const wrapped = JSON.parse(decodeURIComponent(m[1]));
  if (!(Array.isArray(wrapped) && wrapped.length === 1 && wrapped[0].length === 1 && wrapped[0][0].length === 4 &&
    wrapped[0][0][0] === rpcid && wrapped[0][0][2] === null && wrapped[0][0][3] === 'generic')) perr('batchexecute envelope');
  rec.rpc[rpcid] = (rec.rpc[rpcid] || 0) + 1;
  if (!READ_ONLY.includes(rpcid)) {
    perr('request id that is not read-only: ' + rpcid);
    return new Response('', { status: 400 });
  }
  const data = JSON.parse(wrapped[0][0][1]);
  // One "slow down" answer, on the second page of the library.
  if (SCENARIO === 'full' && rpcid === 'lcxiM' && data[0] === 'LCX-4' && !rec.rateLimited) {
    rec.rateLimited = true;
    return new Response('', { status: 429, headers: { 'retry-after': '1' } });
  }
  const payload = answer(rpcid, data);
  if (payload === null) return new Response('', { status: 400 });
  return new Response(envelope(rpcid, payload), { status: 200, headers: { 'content-type': 'application/json; charset=utf-8' } });
}

// ----------------------------------------------------------------- zips and media

function crc32(buf) {
  let crc = 0xffffffff;
  for (let n = 0; n < buf.length; n++) {
    let c = (crc ^ buf[n]) & 0xff;
    for (let k = 0; k < 8; k++) c = c & 1 ? (c >>> 1) ^ 0xedb88320 : c >>> 1;
    crc = (crc >>> 8) ^ c;
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function makeZip(entries) {
  const parts = [];
  const central = [];
  let offset = 0;
  for (const e of entries) {
    const name = Buffer.from(e.name, 'utf8');
    const raw = e.data || Buffer.alloc(0);
    const data = e.deflate ? zlib.deflateRawSync(raw) : raw;
    const crc = crc32(raw);
    const lh = Buffer.alloc(30);
    lh.writeUInt32LE(0x04034b50, 0); lh.writeUInt16LE(20, 4); lh.writeUInt16LE(0x0800, 6);
    lh.writeUInt16LE(e.deflate ? 8 : 0, 8); lh.writeUInt32LE(crc, 14); lh.writeUInt32LE(data.length, 18);
    lh.writeUInt32LE(raw.length, 22); lh.writeUInt16LE(name.length, 26); lh.writeUInt16LE(0, 28);
    parts.push(lh, name, data);
    const ch = Buffer.alloc(46);
    ch.writeUInt32LE(0x02014b50, 0); ch.writeUInt16LE(20, 4); ch.writeUInt16LE(20, 6); ch.writeUInt16LE(0x0800, 8);
    ch.writeUInt16LE(e.deflate ? 8 : 0, 10); ch.writeUInt32LE(crc, 16); ch.writeUInt32LE(data.length, 20);
    ch.writeUInt32LE(raw.length, 24); ch.writeUInt16LE(name.length, 28); ch.writeUInt32LE(offset, 42);
    central.push(ch, name);
    offset += 30 + name.length + data.length;
  }
  const cd = Buffer.concat(central);
  const eocd = Buffer.alloc(22);
  eocd.writeUInt32LE(0x06054b50, 0); eocd.writeUInt16LE(entries.length, 8); eocd.writeUInt16LE(entries.length, 10);
  eocd.writeUInt32LE(cd.length, 12); eocd.writeUInt32LE(offset, 16);
  return Buffer.concat(parts.concat([cd, eocd]));
}

// Photos come as a zip, videos as the plain file, Live Photos as a zip with both parts.
const zipCache = new Map();
function zipFor(it) {
  if (zipCache.has(it.key)) return zipCache.get(it.key);
  const orig = BLOBS.get(it.key + '/orig');
  let z;
  if (isVideo(it)) {
    z = { name: it.name, buf: orig, unzipped: orig.length };
  } else if (it.live) {
    const live = BLOBS.get(it.key + '/live');
    const stem = it.name.replace(/\.[^.]+$/, '');
    z = { name: 'Photos-001.zip', buf: makeZip([{ name: 'Google Photos/' }, { name: 'Google Photos/' + it.name, data: orig },
      { name: 'Google Photos/' + stem + '.MOV', data: live, deflate: true }]), unzipped: orig.length + live.length };
  } else {
    z = { name: 'Photos-001.zip', buf: makeZip([{ name: it.name, data: orig }]), unzipped: orig.length };
  }
  zipCache.set(it.key, z);
  return z;
}

function bodyStream(buf, signal) {
  let pos = 0;
  return new ReadableStream({
    pull(controller) {
      if (signal && signal.aborted) {
        controller.error(abortError());
        return undefined;
      }
      if (pos >= buf.length) {
        controller.close();
        return undefined;
      }
      const end = Math.min(buf.length, pos + 65536);
      controller.enqueue(new Uint8Array(buf.subarray(pos, end)));
      pos = end;
      return new Promise((resolve) => setImmediate(resolve));
    },
  });
}

let mediaCount = 0;
let failedOnce = false;
const STOP_AT = SCENARIO === 'resume' && RUN === 1 ? 7 : 0;

function blockedFor(host, cred) {
  const itemHosts = ['lh3.googleusercontent.com', 'video-downloads.googleusercontent.com'];
  if (SCENARIO === 'cors') return itemHosts.includes(host);
  if (SCENARIO === 'omit') return itemHosts.includes(host) && cred === 'include';
  if (SCENARIO === 'broken') return itemHosts.includes(host) || host === 'doc-export.googleusercontent.com';
  return false;
}

async function media(url, init) {
  let u;
  try {
    u = new URL(url);
  } catch (e) {
    rec.nonGoogle.push(String(url));
    throw new TypeError('Failed to fetch');
  }
  const hosts = ['lh3.googleusercontent.com', 'video-downloads.googleusercontent.com', 'doc-export.googleusercontent.com'];
  if (!hosts.includes(u.hostname)) {
    rec.nonGoogle.push(u.origin + u.pathname);
    throw new TypeError('Failed to fetch');
  }
  const cred = init.credentials;
  if ((init.method || 'GET') !== 'GET' || init.body !== undefined) perr('media request is not a plain GET');
  if (cred !== 'include' && cred !== 'omit') perr('media credentials ' + cred);
  let key;
  let variant;
  let buf;
  let type;
  if (u.hostname === 'doc-export.googleusercontent.com') {
    const token = u.pathname.split('/').pop();
    const t = tokens.get(token);
    if (!t) {
      perr('unknown zip address');
      return new Response('', { status: 404 });
    }
    key = t.key;
    variant = 'zip';
    const z = zipFor(BY_KEY.get(key));
    buf = z.buf;
    type = z.name.endsWith('.zip') ? 'application/zip' : 'video/mp4';
  } else {
    const m = /^\/hcfake\/([^/]+)\/([^/]+)$/.exec(u.pathname);
    if (!m || !BLOBS.has(m[1] + '/' + m[2])) {
      perr('unknown media address ' + u.pathname);
      return new Response('', { status: 404 });
    }
    key = m[1];
    variant = m[2];
    buf = BLOBS.get(key + '/' + variant);
    type = isVideo(BY_KEY.get(key)) || variant === 'live' ? 'video/quicktime' : 'image/jpeg';
  }
  const entry = { key, variant, cred, outcome: 'ok' };
  rec.media.push(entry);
  if (init.signal && init.signal.aborted) {
    entry.outcome = 'aborted';
    throw abortError();
  }
  mediaCount += 1;
  if (STOP_AT && mediaCount === STOP_AT) {
    clickButton('Stop');
    entry.outcome = 'stopped';
    throw abortError();
  }
  if (blockedFor(u.hostname, cred)) {
    rec.blocked += 1;
    entry.outcome = 'blocked';
    throw new TypeError('Failed to fetch');
  }
  if (SCENARIO === 'full' && key === 'K03' && !failedOnce) {
    failedOnce = true;
    entry.outcome = '503';
    return new Response('busy', { status: 503, headers: { 'content-type': 'text/plain' } });
  }
  return new Response(bodyStream(buf, init.signal), { status: 200, headers: { 'content-type': type, 'content-length': String(buf.length) } });
}

async function fakeFetch(input, init = {}) {
  const url = String(input);
  if (url.startsWith('https://photos.google.com/')) return batchexecute(url, init);
  return media(url, init);
}

// ----------------------------------------------------------------- folder handles

const ILLEGAL = /["~*/:<>?\\|\p{Cc}\p{Cf}]/u;
// The name rules of Chrome's File System Access API (IsSafePathComponent).
function badName(name) {
  if (typeof name !== 'string' || name === '' || name === '.' || name === '..') return true;
  const rest = name.charAt(0) === '.' ? name.slice(1) : name;
  if (rest === '' || ILLEGAL.test(rest) || /^[\s.]|[\s.]$/u.test(rest)) return true;
  for (const ch of rest) {
    const cp = ch.codePointAt(0);
    if ((cp & 0xfffe) === 0xfffe || (cp >= 0xfdd0 && cp <= 0xfdef)) return true;
  }
  const lower = name.toLowerCase();
  const devices = ['con', 'prn', 'aux', 'nul', 'clock$'].concat([1, 2, 3, 4, 5, 6, 7, 8, 9].flatMap((n) => ['com' + n, 'lpt' + n]));
  if (devices.some((d) => lower === d || lower.startsWith(d + '.')) || lower === 'desktop.ini' || lower === 'thumbs.db') return true;
  const dot = lower.lastIndexOf('.');
  const ext = dot > 0 ? lower.slice(dot + 1) : '';
  return ext === 'lnk' || ext === 'scf' || ext === 'url' || (ext.length > 2 && ext[0] === '{' && ext.endsWith('}'));
}
const notFound = () => new DOMException('A requested file or directory could not be found.', 'NotFoundError');
const nameNotAllowed = (name) => new TypeError('Name is not allowed: ' + JSON.stringify(name));
const statOrNull = async (p) => {
  try {
    return await fsp.stat(p);
  } catch (e) {
    return null;
  }
};

class FakeWritable {
  constructor(target, swap) {
    this.target = target;
    this.swap = swap;
    this.pos = 0;
    this.closed = false;
  }
  async write(data) {
    if (this.closed) throw new TypeError('The stream is closed.');
    let pos = this.pos;
    if (data && typeof data === 'object' && !ArrayBuffer.isView(data) && !(data instanceof ArrayBuffer) && !(data instanceof Blob)) {
      if (data.type === 'seek') {
        this.pos = data.position;
        return;
      }
      if (data.type !== 'write') throw new TypeError('Unsupported write type ' + data.type);
      if (typeof data.position === 'number') pos = data.position;
      data = data.data;
    }
    let buf;
    if (typeof data === 'string') buf = Buffer.from(data, 'utf8');
    else if (data instanceof Blob) buf = Buffer.from(await data.arrayBuffer());
    else if (data instanceof ArrayBuffer) buf = Buffer.from(data);
    else if (ArrayBuffer.isView(data)) buf = Buffer.from(data.buffer, data.byteOffset, data.byteLength);
    else throw new TypeError('Unsupported data');
    const fh = await fsp.open(this.swap, 'r+');
    try {
      await fh.write(buf, 0, buf.length, pos);
    } finally {
      await fh.close();
    }
    this.pos = pos + buf.length;
  }
  async close() {
    if (this.closed) throw new TypeError('The stream is closed.');
    this.closed = true;
    await fsp.rename(this.swap, this.target);
  }
  async abort() {
    if (this.closed) return;
    this.closed = true;
    await fsp.rm(this.swap, { force: true });
  }
}

class FakeFileHandle {
  constructor(p, name) {
    this.kind = 'file';
    this.name = name;
    this.p = p;
  }
  async getFile() {
    let buf;
    try {
      buf = await fsp.readFile(this.p);
    } catch (e) {
      throw notFound();
    }
    return new File([buf], this.name);
  }
  async createWritable(opts = {}) {
    if (!(await statOrNull(this.p))) throw notFound();
    for (let n = 0; ; n++) {
      const swap = this.p + (n ? '.' + n : '') + '.crswap';
      try {
        if (opts.keepExistingData) await fsp.copyFile(this.p, swap, fs.constants.COPYFILE_EXCL);
        else await fsp.writeFile(swap, Buffer.alloc(0), { flag: 'wx' });
        return new FakeWritable(this.p, swap);
      } catch (e) {
        if (e.code !== 'EEXIST') throw e;
      }
    }
  }
}

class FakeDirHandle {
  constructor(p, name) {
    this.kind = 'directory';
    this.name = name;
    this.p = p;
  }
  async getDirectoryHandle(name, opts = {}) {
    if (badName(name)) throw nameNotAllowed(name);
    const p = path.join(this.p, name);
    const st = await statOrNull(p);
    if (st && !st.isDirectory()) throw new DOMException('A file is in the way.', 'TypeMismatchError');
    if (!st) {
      if (!opts.create) throw notFound();
      await fsp.mkdir(p, { recursive: true });
    }
    return new FakeDirHandle(p, name);
  }
  async getFileHandle(name, opts = {}) {
    if (badName(name)) throw nameNotAllowed(name);
    const p = path.join(this.p, name);
    const st = await statOrNull(p);
    if (st && st.isDirectory()) throw new DOMException('A folder is in the way.', 'TypeMismatchError');
    if (!st) {
      if (!opts.create) throw notFound();
      try {
        await fsp.writeFile(p, Buffer.alloc(0), { flag: 'wx' });
      } catch (e) {
        if (e.code !== 'EEXIST') throw e;
      }
    }
    return new FakeFileHandle(p, name);
  }
  async removeEntry(name, opts = {}) {
    if (badName(name)) throw nameNotAllowed(name);
    const p = path.join(this.p, name);
    const st = await statOrNull(p);
    if (!st) throw notFound();
    if (st.isDirectory()) await fsp.rm(p, { recursive: !!opts.recursive });
    else await fsp.unlink(p);
  }
  async *keys() {
    for (const name of await fsp.readdir(this.p)) yield name;
  }
  async *entries() {
    for (const name of await fsp.readdir(this.p)) {
      const st = await statOrNull(path.join(this.p, name));
      if (st) yield [name, st.isDirectory() ? new FakeDirHandle(path.join(this.p, name), name) : new FakeFileHandle(path.join(this.p, name), name)];
    }
  }
  async queryPermission() {
    return 'granted';
  }
  async requestPermission() {
    return 'granted';
  }
}

// ----------------------------------------------------------------- DOM

class FakeElement {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.style = {};
    this.children = [];
    this.parentNode = null;
    this.attributes = {};
    this.listeners = {};
    this.ownText = '';
    this.id = '';
    this.disabled = false;
    this.type = '';
  }
  get textContent() {
    return this.ownText + this.children.map((c) => c.textContent).join('');
  }
  set textContent(v) {
    this.ownText = String(v);
    this.children = [];
  }
  get innerHTML() {
    return this.textContent;
  }
  set innerHTML(v) {
    rec.sinks.push('innerHTML');
    throw new TypeError("This document requires 'TrustedHTML' assignment.");
  }
  get outerHTML() {
    return '';
  }
  set outerHTML(v) {
    rec.sinks.push('outerHTML');
    throw new TypeError("This document requires 'TrustedHTML' assignment.");
  }
  insertAdjacentHTML() {
    rec.sinks.push('insertAdjacentHTML');
    throw new TypeError("This document requires 'TrustedHTML' assignment.");
  }
  setAttribute(k, v) {
    this.attributes[k] = String(v);
    if (k === 'id') this.id = String(v);
  }
  appendChild(child) {
    if (child.parentNode) child.remove();
    child.parentNode = this;
    this.children.push(child);
    return child;
  }
  remove() {
    if (!this.parentNode) return;
    this.parentNode.children = this.parentNode.children.filter((c) => c !== this);
    this.parentNode = null;
  }
  addEventListener(type, fn) {
    (this.listeners[type] = this.listeners[type] || []).push(fn);
  }
  removeEventListener(type, fn) {
    this.listeners[type] = (this.listeners[type] || []).filter((f) => f !== fn);
  }
  click() {
    if (this.disabled) return;
    for (const fn of this.listeners.click || []) fn({ type: 'click', target: this, preventDefault() {} });
  }
}

const html = new FakeElement('html');
const body = new FakeElement('body');
html.appendChild(body);
const walk = (elx, fn) => {
  fn(elx);
  elx.children.forEach((c) => walk(c, fn));
};
const fakeDocument = {
  documentElement: html,
  body,
  visibilityState: 'visible',
  createElement: (tag) => new FakeElement(tag),
  getElementById(id) {
    let found = null;
    walk(html, (e) => {
      if (!found && e.id === id) found = e;
    });
    return found;
  },
  addEventListener() {},
  removeEventListener() {},
  write() {
    rec.sinks.push('document.write');
    throw new TypeError("This document requires 'TrustedHTML' assignment.");
  },
  writeln() {
    rec.sinks.push('document.writeln');
    throw new TypeError("This document requires 'TrustedHTML' assignment.");
  },
};

function panel() {
  return fakeDocument.getElementById('homecloud-google-panel');
}
function clickButton(label) {
  let button = null;
  walk(html, (e) => {
    if (e.tagName === 'BUTTON' && e.textContent === label) button = e;
  });
  if (!button) throw new Error('no ' + label + ' button');
  button.click();
}

// ----------------------------------------------------------------- the page

new Response('warm up'); // loads fetch's internals before the timers change
const realSetTimeout = globalThis.setTimeout;
// Waits of the script (retries, pauses, polls) take a thousandth of their time here.
globalThis.setTimeout = (fn, ms, ...rest) => realSetTimeout(fn, ms >= 200 ? Math.max(1, Math.round(ms / 1000)) : ms, ...rest);

const wiz = { SNlM0e: AT, FdrFJe: FSID, cfb2h: BL, eptZe: PATH };
Object.defineProperty(wiz, 'oPEP7c', { enumerable: true, get() { rec.accountRead = true; return 'person@example.com'; } });
Object.defineProperty(wiz, 'Dbw5Ud', { enumerable: true, get() { rec.raptRead = true; return 'RAPT-TOKEN'; } });

globalThis.window = globalThis;
globalThis.WIZ_global_data = wiz;
globalThis.location = { hostname: 'photos.google.com', pathname: '/', origin: 'https://photos.google.com', href: 'https://photos.google.com/' };
globalThis.document = fakeDocument;
globalThis.addEventListener = () => {};
globalThis.removeEventListener = () => {};
globalThis.fetch = fakeFetch;
globalThis.showDirectoryPicker = async (opts) => {
  rec.picker.push(opts);
  return new FakeDirHandle(DIR, path.basename(DIR));
};
Object.defineProperty(globalThis, 'navigator', {
  configurable: true,
  writable: true,
  value: {
    userAgent: 'Mozilla/5.0 (fake page) Chrome/141.0.0.0',
    wakeLock: {
      async request(type) {
        rec.wake.requested += 1;
        return { type, released: false, addEventListener() {}, async release() { rec.wake.released += 1; this.released = true; } };
      },
    },
    locks: {
      async request(name, opts, cb) {
        rec.locks += 1;
        return cb({ name, mode: 'exclusive' });
      },
    },
  },
});

const out = (obj) => process.stdout.write(JSON.stringify(obj) + '\n');

async function main() {
  const code = fs.readFileSync(SCRIPT, 'utf8');
  const returned = vm.runInThisContext(code, { filename: 'homecloud_google.js' });
  if (!panel()) throw new Error('no panel');
  const firstText = panel().textContent;
  clickButton('Start');
  const run = globalThis.__homecloudGoogle && globalThis.__homecloudGoogle.lastRun;
  if (!run) throw new Error('Start did not start a run');
  let timer;
  await Promise.race([run, new Promise((resolve, reject) => {
    timer = realSetTimeout(() => reject(new Error('the run did not finish in time')), 90000);
  })]);
  clearTimeout(timer);
  await new Promise((resolve) => setImmediate(resolve));

  let progress = null;
  try {
    progress = JSON.parse(fs.readFileSync(path.join(DIR, '.homecloud', 'google-progress.json'), 'utf8'));
  } catch (e) {
    progress = null;
  }
  const expected = {};
  for (const it of ITEMS) {
    expected[it.key] = { orig: { sha256: sha(BLOBS.get(it.key + '/orig')), size: it.size } };
    if (it.live) expected[it.key].live = { sha256: sha(BLOBS.get(it.key + '/live')), size: it.liveSize };
    if (it.editedSize) expected[it.key].edited = { sha256: sha(BLOBS.get(it.key + '/edited')), size: it.editedSize };
  }
  let statusText = null;
  walk(html, (e) => {
    if (e.attributes['aria-live'] === 'polite') statusText = e.textContent;
  });
  out({
    returned,
    firstText,
    statusText,
    running: globalThis.__homecloudGoogle.running,
    state: progress ? progress.state : null,
    message: progress ? progress.message : null,
    rpc: rec.rpc,
    protocolErrors: rec.protocolErrors,
    media: rec.media,
    blocked: rec.blocked,
    nonGoogle: rec.nonGoogle,
    sinks: rec.sinks,
    joinedAlbumRead: rec.joinedAlbumRead,
    accountRead: rec.accountRead,
    raptRead: rec.raptRead,
    unhandled: rec.unhandled,
    picker: rec.picker,
    wake: rec.wake,
    locks: rec.locks,
    offlineTries: rec.offline || 0,
    liveRule: LIVE_RULE,
    library: Object.fromEntries(ITEMS.map((it) => [it.key, {
      fileName: it.name,
      takenTime: it.taken,
      kind: it.kind,
      live: !!it.live,
      favorite: !!it.fav,
      archived: !!it.archived,
      originalQuality: it.quality !== false,
      googleSize: googleSize(it),
    }])),
    expected,
  });
}

main().catch((e) => {
  process.stderr.write('HARNESS ERROR: ' + (e && e.stack ? e.stack : e) + '\n');
  process.exit(3);
});
