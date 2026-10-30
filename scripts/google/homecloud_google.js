/*
 * Homecloud for Google Photos: the part that runs in your browser.
 *
 * Paste this whole script into the DevTools console of your own signed-in
 * https://photos.google.com tab and press Enter. A Homecloud panel appears at
 * the bottom right of the page. Click Start, pick the folder that Claude Code
 * made for you, and allow editing.
 *
 * What it does:
 *   1. Lists your whole library, archived items included, with the internal
 *      requests that the Google Photos web app itself uses. Trash and Locked
 *      Folder are not read.
 *   2. Reads your own albums, so each item keeps its album names.
 *   3. Reads file names and sizes in batches. It stops before any download if
 *      the files do not fit in the space that Claude Code measured.
 *   4. Downloads the first items to find a download method that works. Then it
 *      downloads every original file, and the video part of each Live Photo,
 *      3 at a time.
 *   5. Writes google-list.json, google-done.jsonl and google-progress.json in
 *      DIR/.homecloud, which scripts/google/google_photos.py reads.
 *
 * It only reads from Google Photos. Every internal request goes through
 * makeApiRequest(), which refuses any request id that is not in
 * READ_ONLY_RPCS, so nothing in Google Photos is changed. It talks only to
 * photos.google.com and to the media addresses that photos.google.com
 * returns. It never reads cookies. It reads the page's own request tokens from
 * WIZ_global_data, as the page does, sends them only to photos.google.com, and
 * never prints or stores them or any download link.
 *
 * Download methods, tried in this order on the first items:
 *   item-url  The item info request VrseUb returns downloadOriginalUrl,
 *             downloadUrl and livePhotoVideoDownloadUrl (GPTK
 *             src/api/parser.ts lines 310, 311 and 321). This page fetches
 *             that address with credentials "include", the way GPTK fetches
 *             media from Google's media host inside this page (GPTK
 *             src/filters.ts lines 366 to 377).
 *   zip       The web app's own Download route: yCLA7 asks for a download
 *             token, and dnv2s returns the address of a zip file when it is
 *             ready (GPTK src/api/api.ts lines 476 to 503, src/api/parser.ts
 *             lines 333 to 340). This page fetches the zip and saves the files
 *             inside it.
 * Each method is tried with credentials "include" first, then "omit". A file
 * counts only when its size matches the size Google reports for it. The method
 * that works is written into google-list.json as "method".
 *
 * Request formats and response parsing are copied from Google-Photos-Toolkit
 * (GPTK) by xob0t, https://github.com/xob0t/Google-Photos-Toolkit at commit
 * 6db9ab355e4797327e4078cf87a2b6994d36cf73: src/api/api.ts, src/api/parser.ts,
 * src/windowGlobalData.ts, src/api/api-utils-default-presets.ts, and the
 * paging loops in src/gptk-core.ts and src/api/api-utils.ts. GPTK's license:
 *
 *   MIT License
 *
 *   Copyright (c) 2024 xob0t
 *
 *   Permission is hereby granted, free of charge, to any person obtaining a copy
 *   of this software and associated documentation files (the "Software"), to deal
 *   in the Software without restriction, including without limitation the rights
 *   to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 *   copies of the Software, and to permit persons to whom the Software is
 *   furnished to do so, subject to the following conditions:
 *
 *   The above copyright notice and this permission notice shall be included in all
 *   copies or substantial portions of the Software.
 *
 *   THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 *   IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 *   FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 *   AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 *   LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 *   OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 *   SOFTWARE.
 *
 * The rest of this file is part of Homecloud (MIT License, Copyright (c) 2026
 * Bodila51).
 *
 * scripts/google/google_photos.py prepare fills in the settings line below by
 * exact text replacement. Keep that line as it is.
 */
{
const HOMECLOUD_CONFIG = {"version": 1, "freeBytes": null, "folderName": null, "lang": "en"};

(function homecloudGoogle(CONFIG) {
  'use strict';

  const w = window;
  const PANEL_ID = 'homecloud-google-panel';
  const META_DIR = '.homecloud';
  const LIST_FILE = 'google-list.json';
  const DONE_FILE = 'google-done.jsonl';
  const PROGRESS_FILE = 'google-progress.json';
  // GPTK's defaults (src/api/api-utils-default-presets.ts lines 3 to 9): 3 requests at a
  // time, and 5000 items in one info request.
  const WORKERS = 3;
  const INFO_CHUNK = 5000;
  const INFO_CHUNK_SMALL = 500;
  const LIVE_GUESS_BYTES = 3000000; // a Live Photo's video part, until one has arrived
  const MAX_FAILS_IN_A_ROW = 20;
  const DONE_FLUSH_MS = 10000;
  const PROGRESS_MS = 2000;

  // The only internal requests this script sends. All of them read.
  const READ_ONLY_RPCS = {
    lcxiM: 'list the library by taken date',
    Z5xsfc: 'list albums',
    snAcKc: 'list the items of one album',
    EWgK9e: 'file names and sizes of many items',
    fDcn4b: 'file name and size of one item',
    VrseUb: 'item info with its download addresses',
    yCLA7: 'ask for a download token',
    dnv2s: 'read the state of a download token',
  };

  const COMBOS = [
    { method: 'item-url', cred: 'include' },
    { method: 'item-url', cred: 'omit' },
    { method: 'zip', cred: 'include' },
    { method: 'zip', cred: 'omit' },
  ];
  const KINDS = [['photo', 'Photos'], ['video', 'Videos'], ['live', 'Live Photos']];
  const VIDEO_EXT = /\.(mp4|m4v|mov|qt|3gp|3g2|avi|mkv|webm|mts|m2ts|mpg|mpeg|wmv|flv)$/i;

  // Panel and progress text. English for now; CONFIG.lang is kept for later.
  const MSG = {
    intro: 'This panel copies your Google Photos library to a folder on this computer. It only reads from Google Photos. It never changes or deletes anything there.',
    pick: CONFIG.folderName
      ? 'Click Start. Pick the folder named "' + CONFIG.folderName + '" and allow editing.'
      : 'Click Start. Pick the folder that Claude Code made for you and allow editing.',
    keepOpen: 'Keep this tab open until the panel says Done. Keep the computer awake and plugged in.',
    wrongSite: 'Open https://photos.google.com in this tab, then paste the script again.',
    noPicker: 'This browser cannot save files into a folder. Use Chrome, Edge, Brave, Arc, Vivaldi or Opera.',
    notReady: 'This page is not ready. Sign in to Google Photos in this tab, reload the page, and paste the script again.',
    noFolder: 'No folder was picked. Click Start to try again.',
    pickFailed: 'The folder window did not open. Click Start to try again.',
    wrongFolder: (got, want) => 'You picked the folder "' + got + '". Click Start again and pick the folder named "' + want + '".',
    noPermission: 'The panel may not save files in that folder. Click Start again and allow editing.',
    otherTab: 'Homecloud is already running in another tab. Use that tab, or close it and click Start here.',
    starting: 'Starting.',
    listing: (n) => 'Reading your library. ' + fmtInt(n) + ' items so far.',
    albums: (i, n) => 'Reading your albums. ' + fmtInt(i) + ' of ' + fmtInt(n) + '.',
    info: (i, n) => 'Reading file names and sizes. ' + fmtInt(i) + ' of ' + fmtInt(n) + '.',
    checking: (i, n) => 'Checking the files from the last run. ' + fmtInt(i) + ' of ' + fmtInt(n) + '.',
    noSpace: (need, free, early) => 'Not enough free space. Your photos and videos need about ' + fmtBytes(need) +
      ', but only ' + fmtBytes(free) + ' may be used on this disk. The rest stays free as a safety margin. ' +
      (early ? 'Nothing was downloaded.' : 'Only the first few files were downloaded.') +
      ' Free some space or pick a folder on a bigger drive, then tell Claude Code.',
    firstFiles: 'Trying the first downloads.',
    done: (n) => 'Done. All ' + fmtInt(n) + ' items are in the folder. Go back to Claude Code.',
    doneSomeFailed: (k, n) => 'Finished. ' + fmtInt(n - k) + ' of ' + fmtInt(n) + ' items are in the folder. ' +
      fmtInt(k) + ' did not download. Click Start to try them again. If they still fail, tell Claude Code.',
    stopping: 'Stopping. Please wait.',
    stopped: 'Stopped. Click Start to continue. Files already saved stay in the folder.',
    listFailed: (why) => 'Google Photos did not answer the way this script expects (' + short(why, 80) +
      '). Nothing was downloaded, and nothing was changed in Google Photos. Tell Claude Code that the listing failed. It can offer Google Takeout instead.',
    downloadFailed: (what, why) => 'Downloads do not work from this page. ' + what + ' did not download (' +
      short(why, 80) + '). Nothing was changed in Google Photos. Tell Claude Code that the download failed. It can offer Google Takeout instead.',
    signedOut: 'Google Photos says you are not signed in. Sign in in this tab, reload the page, paste the script again, and click Start. It continues where it stopped.',
    empty: 'The script found no photos or videos in this library. If your library is not empty, Google may have changed its page. Tell Claude Code.',
    offline: 'This page cannot reach Google right now. Check your internet connection, then click Start. It continues where it stopped.',
    manyFailed: (why) => 'Many downloads failed in a row (' + short(why, 80) +
      '). Check your internet connection. Then reload this page, paste the script again, and click Start. It continues where it stopped.',
    diskFull: 'The disk is full. Free some space, then click Start to continue.',
    lostFolder: 'The panel can no longer save in the folder. Click Start again, pick the folder and allow editing.',
    unexpected: (why) => 'Something went wrong (' + short(why, 80) + '). Nothing was changed in Google Photos. Click Start to try again. If it happens again, tell Claude Code.',
    alreadyRunning: 'Homecloud is already running in this tab. Its panel is at the bottom right of the page.',
    open: 'The Homecloud panel is open at the bottom right of the page.',
  };

  const earlier = w.__homecloudGoogle;
  if (earlier && earlier.running) {
    try { earlier.show(); } catch (e) { /* the old panel is gone */ }
    return MSG.alreadyRunning;
  }
  if (earlier && typeof earlier.close === 'function') {
    try { earlier.close(); } catch (e) { /* nothing to close */ }
  }

  // ---------------------------------------------------------------- helpers

  function fmtInt(n) {
    return Number(n || 0).toLocaleString('en-US');
  }

  function fmtBytes(n) {
    n = Number(n) || 0;
    if (n >= 1e9) return (n / 1e9).toFixed(1) + ' GB';
    if (n >= 1e6) return (n / 1e6).toFixed(1) + ' MB';
    if (n >= 1e3) return Math.round(n / 1e3) + ' KB';
    return n + ' bytes';
  }

  function nowIso() {
    return new Date().toISOString();
  }

  function validSize(n) {
    return typeof n === 'number' && Number.isSafeInteger(n) && n >= 0;
  }

  function nonEmpty(s) {
    return typeof s === 'string' && s.length > 0 ? s : null;
  }

  function short(s, n) {
    s = String(s);
    return s.length > n ? s.slice(0, n - 3) + '...' : s;
  }

  // Short and safe to show: no links, because a download link works like a password until it expires.
  function errText(e) {
    const s = e && e.message ? String(e.message) : String(e);
    return short(s.replace(/\b(?:https?|blob|data):\S+/g, 'a link'), 200);
  }

  function sentence(s) {
    s = String(s || 'unknown problem');
    return s.charAt(0).toUpperCase() + s.slice(1) + (/[.!?]$/.test(s) ? '' : '.');
  }

  function stopErr() {
    const e = new Error('stopped');
    e.hc = 'stop';
    return e;
  }

  function runFail(message) {
    const e = new Error(message);
    e.hc = 'fail';
    e.userMessage = message;
    return e;
  }

  function itemErr(code, why, transient) {
    const e = new Error(why);
    e.hc = 'item';
    e.code = code;
    e.transient = !!transient;
    return e;
  }

  function apiErr(status, why) {
    const e = new Error(why);
    e.hc = 'api';
    e.status = status;
    return e;
  }

  function isAbort(e) {
    return !!e && (e.hc === 'stop' || e.name === 'AbortError');
  }

  function isNotFound(e) {
    return !!e && (e.name === 'NotFoundError' || e.name === 'TypeMismatchError');
  }

  function checkStop() {
    if (st.stopping) throw stopErr();
  }

  function sleep(ms, signal) {
    return new Promise((resolve, reject) => {
      if (signal && signal.aborted) {
        reject(stopErr());
        return;
      }
      const onAbort = () => {
        clearTimeout(timer);
        reject(stopErr());
      };
      const timer = setTimeout(() => {
        if (signal) signal.removeEventListener('abort', onAbort);
        resolve();
      }, ms);
      if (signal) signal.addEventListener('abort', onAbort, { once: true });
    });
  }

  // All workers wait together after Google asks to slow down (HTTP 429).
  function retryAfterMs(response) {
    const v = response && response.headers ? response.headers.get('retry-after') : null;
    if (!v) return 0;
    const s = Number(v);
    if (isFinite(s)) return Math.max(0, s * 1000);
    const t = Date.parse(v);
    return isFinite(t) ? Math.max(0, t - Date.now()) : 0;
  }

  function noteRateLimit(waitMs) {
    st.rateLevel = Math.min(st.rateLevel + 1, 6);
    const wait = Math.max(waitMs || 0, Math.min(120000, 5000 * Math.pow(2, st.rateLevel - 1)));
    st.pauseUntil = Math.max(st.pauseUntil, Date.now() + wait);
  }

  async function waitForPause(signal) {
    const ms = st.pauseUntil - Date.now();
    if (ms > 0) await sleep(ms, signal);
  }

  async function pool(list, n, fn) {
    let next = 0;
    let firstError = null;
    const worker = async () => {
      while (next < list.length && !firstError) {
        const x = list[next++];
        try {
          await fn(x);
        } catch (e) {
          firstError = firstError || e;
        }
      }
    };
    const workers = [];
    for (let i = 0; i < Math.min(n, list.length); i++) workers.push(worker());
    await Promise.all(workers);
    if (firstError) throw firstError;
  }

  // ------------------------------------------------- page data and requests

  // GPTK src/windowGlobalData.ts lines 3 to 10. The account (oPEP7c) is not read. rapt (Dbw5Ud)
  // is only for Locked Folder, which this script never opens, so it is never sent.
  function readPageData() {
    const wiz = w.WIZ_global_data;
    if (!wiz || typeof wiz !== 'object') return null;
    const data = { 'f.sid': wiz.FdrFJe, bl: wiz.cfb2h, path: wiz.eptZe, at: wiz.SNlM0e };
    if (!nonEmpty(data.at) || !nonEmpty(data.path) || !nonEmpty(data.bl) || !nonEmpty(data['f.sid'])) return null;
    return data;
  }

  // GPTK src/api/api.ts lines 43 to 112 (makeApiRequest): the same envelope, body, query and
  // reply parsing. The waits before a retry are longer after a 429 or 5xx answer.
  async function makeApiRequest(rpcid, requestData, signal) {
    if (!Object.prototype.hasOwnProperty.call(READ_ONLY_RPCS, rpcid)) {
      throw new Error('This script only reads. The request ' + rpcid + ' is not allowed.');
    }
    const page = readPageData() || st.page;
    const wrappedData = [[[rpcid, JSON.stringify(requestData), null, 'generic']]];
    const requestDataString = 'f.req=' + encodeURIComponent(JSON.stringify(wrappedData)) +
      '&at=' + encodeURIComponent(page.at) + '&';
    const params = {
      rpcids: rpcid,
      'source-path': w.location.pathname,
      'f.sid': page['f.sid'],
      bl: page.bl,
      pageId: 'none',
      rt: 'c',
    };
    const paramsString = Object.keys(params).map((key) => key + '=' + encodeURIComponent(params[key])).join('&');
    const url = 'https://photos.google.com' + page.path + 'data/batchexecute?' + paramsString;

    let lastError = null;
    for (let attempt = 1; ; attempt++) {
      await waitForPause(signal);
      let status = 0;
      try {
        const response = await fetch(url, {
          headers: { 'content-type': 'application/x-www-form-urlencoded;charset=UTF-8' },
          body: requestDataString,
          method: 'POST',
          credentials: 'include',
          signal: signal,
        });
        status = response.status;
        if (!response.ok) {
          if (status === 429) noteRateLimit(retryAfterMs(response));
          throw apiErr(status, 'Google answered ' + status);
        }
        const responseBody = await response.text();
        if (!responseBody) throw apiErr(status, 'the answer was empty');
        const jsonLines = responseBody.split('\n').filter((line) => line.includes('wrb.fr'));
        if (jsonLines.length === 0) throw apiErr(status, 'the answer had an unknown format');
        const parsedData = JSON.parse(jsonLines[0]);
        if (!parsedData?.[0]?.[2]) throw apiErr(status, 'the answer had no data');
        st.rateLevel = 0;
        return JSON.parse(parsedData[0][2]);
      } catch (error) {
        if (isAbort(error) || st.stopping) throw stopErr();
        lastError = error && error.hc === 'api' ? error
          : apiErr(status, status ? 'the answer could not be read' : 'the request did not reach Google');
        if (!status) lastError.network = true;
        if (status === 401 || status === 403) break;
        const busy = status === 429 || status >= 500 || status === 0;
        if (attempt >= (busy ? 6 : 3)) break;
        await sleep(busy ? Math.min(60000, 2000 * Math.pow(2, attempt - 1)) : 2000 * attempt, signal);
      }
    }
    throw lastError;
  }

  function isSignedOut(e) {
    return !!e && e.hc === 'api' && (e.status === 401 || e.status === 403);
  }

  // ------------------------------------------ parsers (GPTK src/api/parser.ts)
  // Copied for the fields this script uses; the others are left out.

  function libraryItemParse(itemData) { // parser.ts lines 36 to 59
    return {
      mediaKey: itemData?.[0],
      timestamp: itemData?.[2],
      timezoneOffset: itemData?.[4],
      creationTimestamp: itemData?.[5],
      dedupKey: itemData?.[3],
      thumb: itemData?.[1]?.[0],
      isArchived: itemData?.[13],
      isFavorite: itemData?.at(-1)?.[163238866]?.[0],
      duration: itemData?.at(-1)?.[76647426]?.[0],
      isLivePhoto: itemData?.at(-1)?.[146008172] ? true : false,
      livePhotoDuration: itemData?.at(-1)?.[146008172]?.[1],
    };
  }

  function libraryTimelinePage(data) { // parser.ts lines 61 to 67
    return {
      items: data?.[0]?.map((itemData) => libraryItemParse(itemData)),
      nextPageId: data?.[1],
      lastItemTimestamp: parseInt(data?.[2]),
    };
  }

  function albumParse(itemData) { // parser.ts lines 108 to 121
    return {
      mediaKey: itemData?.[0],
      ownerActorId: itemData?.[6]?.[0],
      title: itemData?.at(-1)?.[72930366]?.[1],
      itemCount: itemData?.at(-1)?.[72930366]?.[3],
      isShared: itemData?.at(-1)?.[72930366]?.[4] ?? false,
      authKey: itemData?.at(-1)?.[72930366]?.[5] ?? undefined,
    };
  }

  function albumsPage(data) { // parser.ts lines 123 to 128
    return {
      items: data?.[0]?.map((itemData) => albumParse(itemData)),
      nextPageId: data?.[1],
    };
  }

  function albumItemParse(itemData) { // parser.ts lines 147 to 161
    return {
      mediaKey: itemData?.[0],
      timestamp: itemData?.[2],
      dedupKey: itemData?.[3],
    };
  }

  function albumItemsPage(data) { // parser.ts lines 197 to 213
    return {
      items: data?.[1]?.map((itemData) => albumItemParse(itemData)),
      nextPageId: data?.[2],
      mediaKey: data?.[3]?.[0],
      title: data?.[3]?.[1],
      itemCount: data?.[3]?.[21],
    };
  }

  function itemBulkMediaInfoParse(itemData) { // parser.ts lines 222 to 235
    return {
      mediaKey: itemData?.[0],
      fileName: itemData?.[1]?.[3],
      timestamp: itemData?.[1]?.[6],
      size: itemData?.[1]?.[9],
      takesUpSpace: itemData?.[1]?.at(-1)?.[0] === undefined ? null : itemData?.[1]?.at(-1)?.[0] === 1,
      isOriginalQuality: itemData?.[1]?.at(-1)?.[2] === undefined ? null : itemData?.[1]?.at(-1)?.[2] === 2,
    };
  }

  function bulkMediaInfo(data) { // parser.ts lines 329 to 331
    return data.map((itemData) => itemBulkMediaInfoParse(itemData));
  }

  function itemInfoExtParse(itemData) { // parser.ts lines 237 to 292
    return {
      mediaKey: itemData[0]?.[0],
      dedupKey: itemData[0]?.[11],
      fileName: itemData[0]?.[2],
      timestamp: itemData[0]?.[3],
      size: itemData[0]?.[5],
      takesUpSpace: itemData[0]?.[30]?.[0] === undefined ? null : itemData[0]?.[30]?.[0] === 1,
      isOriginalQuality: itemData[0]?.[30]?.[2] === undefined ? null : itemData[0][30][2] === 2,
    };
  }

  function itemInfoParse(itemData) { // parser.ts lines 294 to 327
    const knownKeys = ['15', '76647426', '146008172', '163238866', '225032867', '318563170', '525000000'];
    // extObj can be at position 9 or 15 depending on whether mediaKey comes from getAlbumPage or the library.
    const extObj = itemData[0]?.find((x) =>
      x && typeof x === 'object' && !Array.isArray(x) && Object.keys(x).some((k) => knownKeys.includes(k))
    );
    return {
      mediaKey: itemData[0]?.[0],
      dedupKey: itemData[0]?.[3],
      timestamp: itemData[0]?.[2],
      downloadUrl: itemData?.[1],
      downloadOriginalUrl: itemData?.[7],
      isArchived: itemData[0]?.[13],
      takesUpSpace: extObj?.[318563170]?.[0]?.[0] === undefined ? null : extObj?.[318563170]?.[0]?.[0] === 1,
      isOriginalQuality: extObj?.[318563170]?.[0]?.[2] === undefined ? null : extObj?.[318563170]?.[0]?.[2] === 2,
      isFavorite: extObj?.[163238866]?.[0],
      duration: extObj?.[76647426]?.[0],
      isLivePhoto: extObj?.[146008172] ? true : false,
      livePhotoDuration: extObj?.[146008172]?.[1],
      livePhotoVideoDownloadUrl: extObj?.[146008172]?.[3],
    };
  }

  function downloadTokenCheckParse(data) { // parser.ts lines 333 to 340
    return {
      fileName: data?.[0]?.[0]?.[0]?.[2]?.[0]?.[0],
      downloadUrl: data?.[0]?.[0]?.[0]?.[2]?.[0]?.[1],
      downloadSize: data?.[0]?.[0]?.[0]?.[2]?.[0]?.[2],
      unzippedSize: data?.[0]?.[0]?.[0]?.[2]?.[0]?.[3],
    };
  }

  const parserRegistry = {
    lcxiM: libraryTimelinePage,
    Z5xsfc: albumsPage,
    snAcKc: albumItemsPage,
    VrseUb: itemInfoParse,
    fDcn4b: itemInfoExtParse,
    EWgK9e: bulkMediaInfo,
    dnv2s: downloadTokenCheckParse,
  };

  function parser(data, rpcid) { // parser.ts lines 404 to 411
    if (!data?.length) return null;
    const parserFn = parserRegistry[rpcid];
    if (parserFn) return parserFn(data);
    return null;
  }

  // ------------------------------------------ requests (GPTK src/api/api.ts)

  const api = {
    // api.ts lines 114 to 136; source null means library and archive together (sourceCode 3)
    async getItemsByTakenDate(timestamp, source, pageId, signal) {
      let sourceCode;
      if (source === 'library') sourceCode = 1;
      else if (source === 'archive') sourceCode = 2;
      else sourceCode = 3; // both
      const requestData = [pageId, timestamp, 500, null, 1, sourceCode];
      return parser(await makeApiRequest('lcxiM', requestData, signal), 'lcxiM');
    },
    // api.ts lines 275 to 290
    async getAlbums(pageId, signal) {
      const requestData = [pageId, null, null, null, 1, null, null, 100, [2], 5];
      return parser(await makeApiRequest('Z5xsfc', requestData, signal), 'Z5xsfc');
    },
    // api.ts lines 292 to 308
    async getAlbumPage(albumMediaKey, pageId, signal) {
      const requestData = [albumMediaKey, pageId, null, null];
      return parser(await makeApiRequest('snAcKc', requestData, signal), 'snAcKc');
    },
    // api.ts lines 657 to 674
    async getBatchMediaInfo(mediaKeyArray, signal) {
      const mappedKeys = mediaKeyArray.map((id) => [id]);
      // prettier-ignore
      const requestData = [[[mappedKeys], [[null, null, null, null, null, null, null, null, null, null, null, null, null, null, null, null, null, null, null, null, null, null, null, null, [], null, null, null, null, null, null, null, null, null, null, []]]]];
      let response = await makeApiRequest('EWgK9e', requestData, signal);
      response = response?.[0]?.[1];
      return parser(response, 'EWgK9e');
    },
    // api.ts lines 639 to 655
    async getItemInfoExt(mediaKey, signal) {
      const requestData = [mediaKey, 1, null, null, 1];
      return parser(await makeApiRequest('fDcn4b', requestData, signal), 'fDcn4b');
    },
    // api.ts lines 621 to 637
    async getItemInfo(mediaKey, signal) {
      const requestData = [mediaKey, null, null, null, null];
      return parser(await makeApiRequest('VrseUb', requestData, signal), 'VrseUb');
    },
    // api.ts lines 476 to 487
    async getDownloadToken(mediaKeyArray, signal) {
      const mappedKeys = mediaKeyArray.map((id) => [id]);
      const response = await makeApiRequest('yCLA7', [mappedKeys], signal);
      return response?.[0];
    },
    // api.ts lines 489 to 503 with parseResponse false, so every zip of a download can be read
    async checkDownloadToken(dlToken, signal) {
      return makeApiRequest('dnv2s', [[dlToken]], signal);
    },
  };

  // ------------------------------------------------------------------- state

  function freshState() {
    return {
      running: false,
      stopping: false,
      halt: null,
      controller: null,
      page: null,
      root: null,
      hc: null,
      dirCache: new Map(),
      diskNames: new Map(),
      state: 'idle',
      listed: 0,
      done: 0,
      failed: 0,
      bytesDone: 0,
      bytesTotal: 0,
      message: '',
      doneKeys: new Set(),
      failedItems: new Map(),
      prefs: { photo: null, video: null, live: null },
      otherStreak: { photo: 0, video: 0, live: 0 },
      liveRule: null,
      liveSeen: { bytes: 0, n: 0 },
      failsInARow: 0,
      pauseUntil: 0,
      rateLevel: 0,
      doneBuf: [],
      lastDoneFlush: 0,
      doneChain: Promise.resolve(),
      progressChain: Promise.resolve(),
      lastProgress: 0,
      wakeLock: null,
    };
  }

  const st = freshState();

  function resetRun(dir, page) {
    Object.assign(st, freshState(), {
      running: true,
      controller: new AbortController(),
      page: page,
      root: dir,
      state: 'listing',
      lastDoneFlush: Date.now(),
    });
  }

  // ------------------------------------------------------------------- panel

  const ui = {};

  function el(tag, style, text) {
    const e = document.createElement(tag);
    if (style) Object.keys(style).forEach((k) => { e.style[k] = style[k]; });
    if (text != null) e.textContent = text;
    return e;
  }

  function makeButton(label, primary) {
    const b = el('button', {
      padding: '6px 16px',
      borderRadius: '4px',
      border: '1px solid ' + (primary ? '#1a73e8' : '#dadce0'),
      background: primary ? '#1a73e8' : '#ffffff',
      color: primary ? '#ffffff' : '#1a73e8',
      font: 'bold 14px Arial, Helvetica, sans-serif',
      cursor: 'pointer',
    }, label);
    b.type = 'button';
    return b;
  }

  function buildPanel() {
    const old = document.getElementById(PANEL_ID);
    if (old) old.remove();
    const box = el('div', {
      position: 'fixed',
      right: '16px',
      bottom: '16px',
      zIndex: '2147483647',
      width: '360px',
      maxWidth: 'calc(100vw - 32px)',
      boxSizing: 'border-box',
      background: '#ffffff',
      color: '#202124',
      border: '1px solid #dadce0',
      borderRadius: '8px',
      boxShadow: '0 4px 16px rgba(0, 0, 0, 0.25)',
      padding: '14px 16px',
      font: '14px/1.45 Arial, Helvetica, sans-serif',
      textAlign: 'left',
    });
    box.id = PANEL_ID;
    box.setAttribute('role', 'region');
    box.setAttribute('aria-label', 'Homecloud');
    const title = el('div', { fontWeight: 'bold', fontSize: '16px', marginBottom: '6px' }, 'Homecloud');
    const status = el('div', { marginBottom: '6px' }, MSG.intro + ' ' + MSG.pick);
    status.setAttribute('aria-live', 'polite');
    const note = el('div', { color: '#5f6368', fontSize: '12px', marginBottom: '10px' }, MSG.keepOpen);
    const row = el('div', { display: 'flex', gap: '8px' });
    const start = makeButton('Start', true);
    const stop = makeButton('Stop', false);
    stop.disabled = true;
    start.addEventListener('click', onStart);
    stop.addEventListener('click', onStop);
    row.appendChild(start);
    row.appendChild(stop);
    box.appendChild(title);
    box.appendChild(status);
    box.appendChild(note);
    box.appendChild(row);
    (document.body || document.documentElement).appendChild(box);
    Object.assign(ui, { box: box, status: status, start: start, stop: stop });
  }

  function setButtons() {
    if (!ui.start) return;
    ui.start.disabled = st.running;
    ui.stop.disabled = !st.running || st.stopping;
  }

  function say(text, force) {
    st.message = text;
    if (ui.status) ui.status.textContent = text;
    return writeProgress(!!force);
  }

  function downloadingText() {
    let s = 'Downloading. ' + fmtInt(st.done) + ' of ' + fmtInt(st.listed) + ' items.';
    if (st.bytesTotal) s += ' ' + fmtBytes(st.bytesDone) + ' of ' + fmtBytes(st.bytesTotal) + '.';
    if (st.failed) s += ' ' + fmtInt(st.failed) + ' did not download yet.';
    return s;
  }

  // ------------------------------------------------------------------- files

  function getDir(parts, create) {
    let chain = Promise.resolve(st.root);
    let key = '';
    for (const part of parts) {
      key = key ? key + '/' + part : part;
      const cached = st.dirCache.get(key);
      if (cached) {
        chain = cached;
        continue;
      }
      const k = key;
      const next = chain.then((parent) => parent.getDirectoryHandle(part, { create: create }));
      st.dirCache.set(k, next);
      next.catch(() => {
        if (st.dirCache.get(k) === next) st.dirCache.delete(k);
      });
      chain = next;
    }
    return chain;
  }

  function splitRel(rel) {
    const parts = rel.split('/');
    const name = parts.pop();
    return { parts: parts, name: name };
  }

  async function fileSize(rel) {
    const p = splitRel(rel);
    try {
      const dir = await getDir(p.parts, false);
      const fh = await dir.getFileHandle(p.name);
      return (await fh.getFile()).size;
    } catch (e) {
      if (isNotFound(e)) return -1;
      throw e;
    }
  }

  async function removeFile(rel) {
    if (!rel) return;
    const p = splitRel(rel);
    try {
      const dir = await getDir(p.parts, false);
      await dir.removeEntry(p.name);
    } catch (e) {
      /* already gone */
    }
  }

  async function readTextFile(dir, name) {
    try {
      const fh = await dir.getFileHandle(name);
      return await (await fh.getFile()).text();
    } catch (e) {
      if (isNotFound(e)) return null;
      throw e;
    }
  }

  async function writeTextFile(dir, name, text) {
    const fh = await dir.getFileHandle(name, { create: true });
    const out = await fh.createWritable();
    try {
      await out.write(text);
      await out.close();
    } catch (e) {
      try { await out.abort(); } catch (e2) { /* ignore */ }
      throw e;
    }
  }

  async function appendTextFile(dir, name, text) {
    const fh = await dir.getFileHandle(name, { create: true });
    const file = await fh.getFile();
    let data = text;
    if (file.size > 0 && (await file.slice(file.size - 1).text()) !== '\n') data = '\n' + text;
    const out = await fh.createWritable({ keepExistingData: true });
    try {
      await out.write({ type: 'write', position: file.size, data: data });
      await out.close();
    } catch (e) {
      try { await out.abort(); } catch (e2) { /* ignore */ }
      throw e;
    }
  }

  function writeError(e) {
    if (isAbort(e)) return stopErr();
    if (e && (e.hc === 'fail' || e.hc === 'item')) return e;
    const name = e && e.name;
    if (name === 'QuotaExceededError' || /no space|disk full|quota/i.test(errText(e))) return runFail(MSG.diskFull);
    if (name === 'NotAllowedError' || name === 'SecurityError' || name === 'NotFoundError' || name === 'InvalidStateError') {
      return runFail(MSG.lostFolder);
    }
    return itemErr('write', 'the file could not be saved (' + errText(e) + ')');
  }

  // Streams a download into DIR/rel. Chrome writes into a temporary file and moves it into
  // place on close(), so a file never appears half written under its own name.
  async function writeStream(rel, readable, signal) {
    const p = splitRel(rel);
    let fh;
    let out;
    try {
      const dir = await getDir(p.parts, true);
      fh = await dir.getFileHandle(p.name, { create: true });
      out = await fh.createWritable();
    } catch (e) {
      try { await readable.cancel(); } catch (e2) { /* ignore */ }
      throw writeError(e);
    }
    const reader = readable.getReader();
    try {
      for (;;) {
        if (signal && signal.aborted) throw stopErr();
        let r;
        try {
          r = await reader.read();
        } catch (e) {
          if (isAbort(e) || st.stopping) throw stopErr();
          if (e && e.hc) throw e;
          throw itemErr('network', 'the download broke off', true);
        }
        if (r.done) break;
        try {
          await out.write(r.value);
        } catch (e) {
          throw writeError(e);
        }
        heartbeat();
      }
      try {
        await out.close();
      } catch (e) {
        throw writeError(e);
      }
    } catch (e) {
      try { await out.abort(); } catch (e2) { /* ignore */ }
      try { await reader.cancel(); } catch (e2) { /* ignore */ }
      await removeFile(rel);
      throw e;
    }
    return (await fh.getFile()).size;
  }

  // ------------------------------------------------------- progress and done

  function writeProgress(force) {
    if (!st.hc) return st.progressChain;
    const now = Date.now();
    if (!force && now - st.lastProgress < PROGRESS_MS) return st.progressChain;
    st.lastProgress = now;
    const text = JSON.stringify({
      state: st.state,
      listed: st.listed,
      done: st.done,
      failed: st.failed,
      bytesDone: st.bytesDone,
      bytesTotal: st.bytesTotal,
      message: st.message,
      updated: nowIso(),
    }, null, 2) + '\n';
    const hc = st.hc;
    st.progressChain = st.progressChain
      .then(() => writeTextFile(hc, PROGRESS_FILE, text))
      .catch((e) => { console.warn('Homecloud: could not save ' + PROGRESS_FILE + ': ' + errText(e)); });
    return st.progressChain;
  }

  // Keeps the progress file fresh while a big file downloads.
  function heartbeat() {
    if (Date.now() - st.lastProgress > 10000) writeProgress(true);
  }

  // google-done.jsonl gets its lines in batches: each append copies the file once, so one
  // append per item would cost too much on a big library.
  function queueDoneLine(obj) {
    st.doneBuf.push(JSON.stringify(obj));
    if (st.doneBuf.length >= 100 || Date.now() - st.lastDoneFlush > DONE_FLUSH_MS) flushDone();
  }

  function flushDone() {
    if (!st.doneBuf.length || !st.hc) return st.doneChain;
    const lines = st.doneBuf;
    st.doneBuf = [];
    st.lastDoneFlush = Date.now();
    const hc = st.hc;
    st.doneChain = st.doneChain
      .catch(() => { /* an earlier batch failed and went back into the buffer */ })
      .then(() => appendTextFile(hc, DONE_FILE, lines.join('\n') + '\n'))
      .catch((e) => {
        st.doneBuf = lines.concat(st.doneBuf);
        console.warn('Homecloud: could not save ' + DONE_FILE + ': ' + errText(e));
        throw writeError(e);
      });
    st.doneChain.catch(() => { /* finishDone reports it */ });
    return st.doneChain;
  }

  // Waits for the appends under way, then writes what is left.
  async function finishDone() {
    try {
      await st.doneChain;
    } catch (e) {
      /* its lines are back in the buffer */
    }
    if (st.doneBuf.length) await flushDone();
  }

  function markDone(item, size, liveSize, writeLine) {
    if (st.doneKeys.has(item.mediaKey)) return;
    st.doneKeys.add(item.mediaKey);
    st.failedItems.delete(item.mediaKey);
    st.done += 1;
    if (validSize(item.size)) st.bytesDone += item.size;
    if (item.live && validSize(liveSize)) {
      st.liveSeen.bytes += liveSize;
      st.liveSeen.n += 1;
    }
    if (writeLine) {
      queueDoneLine({
        mediaKey: item.mediaKey,
        path: item.path,
        size: size,
        livePath: item.live ? item.livePath : null,
        liveSize: item.live ? liveSize : null,
      });
    }
  }

  // --------------------------------------------------------- earlier runs

  // The same rules as safe_rel() in google_photos.py, and no colon, which these paths never hold.
  function validRel(p) {
    if (typeof p !== 'string' || !p || p !== p.trim() || /[\\:\0]/.test(p) || p.charAt(0) === '/') return false;
    const parts = p.split('/');
    return parts.length >= 2 && parts[0] !== META_DIR && parts.every((x) => x && x !== '.' && x !== '..');
  }

  async function readPrevious() {
    const prev = { items: new Map(), done: new Map(), method: null };
    let listText = null;
    try { listText = await readTextFile(st.hc, LIST_FILE); } catch (e) { listText = null; }
    if (listText) {
      try {
        const obj = JSON.parse(listText);
        if (obj && obj.format === 'homecloud-google-list' && Array.isArray(obj.items)) {
          if (typeof obj.method === 'string' && obj.method !== 'none') prev.method = obj.method;
          for (const it of obj.items) {
            if (it && typeof it.mediaKey === 'string' && validRel(it.path)) prev.items.set(it.mediaKey, it);
          }
        }
      } catch (e) {
        /* an unreadable list: this run writes a new one */
      }
    }
    let doneText = null;
    try { doneText = await readTextFile(st.hc, DONE_FILE); } catch (e) { doneText = null; }
    if (doneText) {
      for (const line of doneText.split('\n')) {
        if (!line.trim()) continue;
        let rec;
        try { rec = JSON.parse(line); } catch (e) { continue; }
        if (!rec || typeof rec.mediaKey !== 'string') continue;
        // The last line for an item counts, as in google_photos.py verify.
        if (validRel(rec.path) && validSize(rec.size) && !('error' in rec)) prev.done.set(rec.mediaKey, rec);
        else prev.done.delete(rec.mediaKey);
      }
    }
    return prev;
  }

  // --------------------------------------------------------------- listing

  // The full-library loop of GPTK src/gptk-core.ts lines 342 to 352.
  async function listLibrary(signal) {
    const media = [];
    const seen = new Set();
    let nextPageId = null;
    let nextPageTimestamp = null;
    let lastPageId = null;
    do {
      checkStop();
      const mediaPage = await api.getItemsByTakenDate(nextPageTimestamp, null, nextPageId, signal);
      nextPageId = mediaPage?.nextPageId ?? null;
      if (nextPageId && nextPageId === lastPageId) throw apiErr(0, 'the library list repeats itself');
      lastPageId = nextPageId;
      if (!mediaPage) continue;
      nextPageTimestamp = mediaPage.lastItemTimestamp - 1;
      if (!mediaPage.items || mediaPage.items.length === 0) continue;
      for (const it of mediaPage.items) {
        if (typeof it.mediaKey !== 'string' || !it.mediaKey || seen.has(it.mediaKey)) continue;
        seen.add(it.mediaKey);
        media.push(it);
      }
      st.listed = media.length;
      say(MSG.listing(media.length));
    } while (nextPageId);
    return media;
  }

  // Your own albums: an album that is not shared can only be yours, so the owner of those
  // albums is you. Shared albums of other owners are left out.
  function ownAlbums(albums) {
    const owners = new Map();
    for (const a of albums) {
      if (!a.isShared && typeof a.ownerActorId === 'string') owners.set(a.ownerActorId, (owners.get(a.ownerActorId) || 0) + 1);
    }
    let me = null;
    let best = 0;
    owners.forEach((n, id) => {
      if (n > best) {
        best = n;
        me = id;
      }
    });
    return albums.filter((a) => typeof a.mediaKey === 'string' &&
      (!a.isShared || !me || typeof a.ownerActorId !== 'string' || a.ownerActorId === me));
  }

  // The paging of GPTK src/api/api-utils.ts lines 114 to 136 and 150 to 152. Album items
  // are matched to library items by dedupKey, as GPTK does in src/gptk-core.ts lines 246 to 259.
  async function readAlbums(signal) {
    const albums = [];
    let nextPageId = null;
    let lastPageId = null;
    do {
      checkStop();
      const page = await api.getAlbums(nextPageId, signal);
      if (page?.items?.length) albums.push(...page.items);
      nextPageId = page?.nextPageId || null;
      if (nextPageId && nextPageId === lastPageId) throw apiErr(0, 'the album list repeats itself');
      lastPageId = nextPageId;
    } while (nextPageId);

    const own = ownAlbums(albums);
    const titlesByDedup = new Map();
    const list = [];
    for (let i = 0; i < own.length; i++) {
      const album = own[i];
      say(MSG.albums(i + 1, own.length));
      let title = typeof album.title === 'string' ? album.title.trim() : '';
      const dedups = [];
      let read = 0;
      let pageId = null;
      let lastId = null;
      do {
        checkStop();
        const page = await api.getAlbumPage(album.mediaKey, pageId, signal);
        if (!title && typeof page?.title === 'string') title = page.title.trim();
        for (const it of page?.items || []) {
          read += 1;
          if (typeof it.dedupKey === 'string' && it.dedupKey) dedups.push(it.dedupKey);
        }
        pageId = page?.nextPageId || null;
        if (pageId && pageId === lastId) throw apiErr(0, 'an album list repeats itself');
        lastId = pageId;
      } while (pageId);
      if (!title) title = 'Untitled album';
      for (const d of dedups) {
        if (!titlesByDedup.has(d)) titlesByDedup.set(d, new Set());
        titlesByDedup.get(d).add(title);
      }
      // Google's own count, so that verify can see items that the list does not hold.
      list.push({ title: title, count: validSize(album.itemCount) ? album.itemCount : read });
    }
    list.sort((a, b) => (a.title < b.title ? -1 : a.title > b.title ? 1 : 0));
    return { titlesByDedup: titlesByDedup, albums: list };
  }

  // File names and sizes in batches of INFO_CHUNK (EWgK9e). Items still without a name or a
  // size get one fDcn4b request each.
  async function readInfo(media, signal) {
    const info = new Map();
    const keys = media.map((m) => m.mediaKey);
    let seenCount = 0;
    const take = (rows) => {
      for (const r of rows || []) if (r && typeof r.mediaKey === 'string') info.set(r.mediaKey, r);
    };
    for (let i = 0; i < keys.length; i += INFO_CHUNK) {
      checkStop();
      const chunk = keys.slice(i, i + INFO_CHUNK);
      try {
        take(await api.getBatchMediaInfo(chunk, signal));
      } catch (e) {
        if (isAbort(e) || isSignedOut(e) || chunk.length <= INFO_CHUNK_SMALL) throw e;
        // Once more in smaller parts, in case the batch was too big.
        for (let j = 0; j < chunk.length; j += INFO_CHUNK_SMALL) {
          take(await api.getBatchMediaInfo(chunk.slice(j, j + INFO_CHUNK_SMALL), signal));
        }
      }
      seenCount += chunk.length;
      say(MSG.info(seenCount, keys.length));
    }
    const lacking = media.filter((m) => {
      const r = info.get(m.mediaKey);
      return !r || !nonEmpty(r.fileName) || !validSize(r.size);
    });
    if (lacking.length > Math.max(20, Math.ceil(media.length * 0.02))) {
      throw apiErr(0, 'file names and sizes are missing for ' + fmtInt(lacking.length) + ' items');
    }
    await pool(lacking, WORKERS, async (m) => {
      checkStop();
      let x = null;
      try {
        x = await api.getItemInfoExt(m.mediaKey, signal);
      } catch (e) {
        if (isAbort(e) || isSignedOut(e)) throw e;
        return;
      }
      if (!x) return;
      const r = info.get(m.mediaKey) || {};
      info.set(m.mediaKey, {
        mediaKey: m.mediaKey,
        fileName: nonEmpty(r.fileName) || x.fileName,
        size: validSize(r.size) ? r.size : x.size,
        isOriginalQuality: typeof r.isOriginalQuality === 'boolean' ? r.isOriginalQuality : x.isOriginalQuality,
      });
    });
    return info;
  }

  // ------------------------------------------------------------ the items

  function kindOf(item) {
    return item.live ? 'live' : item.kind;
  }

  function buildItems(media, info, titlesByDedup, prev) {
    const items = [];
    for (const m of media) {
      const inf = info.get(m.mediaKey) || {};
      const live = m.isLivePhoto === true;
      let name = nonEmpty(inf.fileName) && inf.fileName.trim() ? inf.fileName : null;
      const kind = !live && (m.duration != null || (name !== null && VIDEO_EXT.test(name))) ? 'video' : 'photo';
      if (!name) name = 'google-' + m.mediaKey.slice(0, 40) + (kind === 'video' ? '.mp4' : '.jpg');
      const taken = typeof m.timestamp === 'number' && isFinite(m.timestamp) ? Math.round(m.timestamp) : 0;
      const titles = titlesByDedup.get(m.dedupKey);
      items.push({
        mediaKey: m.mediaKey,
        takenTime: taken,
        fileName: name,
        size: validSize(inf.size) ? inf.size : null,
        kind: kind,
        live: live,
        originalQuality: typeof inf.isOriginalQuality === 'boolean' ? inf.isOriginalQuality : null,
        favorite: m.isFavorite === true,
        archived: m.isArchived === true,
        albums: titles ? Array.from(titles).sort() : [],
        path: null,
        livePath: null,
        kept: false,
      });
    }
    // Items finished in an earlier run that this listing did not return stay in the list
    // while their files are on disk (checkExisting drops them otherwise).
    const listed = new Set(items.map((it) => it.mediaKey));
    prev.done.forEach((rec, key) => {
      const old = prev.items.get(key);
      if (listed.has(key) || !old || typeof old.fileName !== 'string') return;
      items.push({
        mediaKey: key,
        takenTime: typeof old.takenTime === 'number' && isFinite(old.takenTime) ? Math.round(old.takenTime) : 0,
        fileName: old.fileName,
        size: validSize(old.size) ? old.size : null,
        kind: old.kind === 'video' ? 'video' : 'photo',
        live: old.live === true,
        originalQuality: typeof old.originalQuality === 'boolean' ? old.originalQuality : null,
        favorite: old.favorite === true,
        archived: old.archived === true,
        albums: Array.isArray(old.albums) ? old.albums.filter((a) => typeof a === 'string') : [],
        path: null,
        livePath: null,
        kept: true,
      });
    });
    return items;
  }

  const encoder = new TextEncoder();

  // A name that Chrome's File System Access API and Windows accept: Chrome refuses the
  // characters "~*/:<>?\| and control and format characters, a dot or space at either end,
  // device names such as CON, and the extensions .lnk, .scf and .url.
  function safeFileName(raw) {
    let s = String(raw);
    if (typeof s.toWellFormed === 'function') s = s.toWellFormed();
    s = s.normalize('NFC');
    s = s.replace(/["~*/:<>?\\|\p{Cc}\p{Cf}]/gu, '_');
    s = Array.from(s).map((ch) => {
      const cp = ch.codePointAt(0);
      return (cp & 0xfffe) === 0xfffe || (cp >= 0xfdd0 && cp <= 0xfdef) ? '_' : ch;
    }).join('');
    s = s.replace(/^\s+/, '').replace(/[\s.]+$/, '');
    if (!s) return '';
    if (s.charAt(0) === '.') s = '_' + s;
    const lower = s.toLowerCase();
    if (/^(con|prn|aux|nul|com[0-9]|lpt[0-9]|clock\$|conin\$|conout\$)$/.test(lower.split('.')[0]) ||
      lower === 'desktop.ini' || lower === 'thumbs.db') s = '_' + s;
    if (/\.(lnk|scf|url|\{[^.]*\})$/i.test(s)) s += '_';
    // At most 200 bytes, which leaves room for " (99)" within the 255 that disks allow.
    const parts = splitName(s);
    const stem = Array.from(parts.stem);
    while (stem.length > 1 && encoder.encode(stem.join('') + parts.ext).length > 200) stem.pop();
    s = stem.join('') + parts.ext;
    return parts.ext ? s : s.replace(/[\s.]+$/, '');
  }

  function splitName(s) {
    const dot = s.lastIndexOf('.');
    if (dot <= 0 || dot === s.length - 1 || s.length - dot > 12) return { stem: s, ext: '' };
    return { stem: s.slice(0, dot), ext: s.slice(dot) };
  }

  // The video part of a Live Photo: .MOV for Apple files, .MP4 for others, in the case of the photo's extension.
  function liveExtFor(name, photoExt) {
    const p = splitName(name);
    const ext = p.ext.slice(1);
    const apple = /^(heic|heif)$/i.test(ext) || /^IMG_\d{4}/i.test(p.stem);
    let e = apple ? 'mov' : 'mp4';
    if (('.' + e) === photoExt.toLowerCase()) e = apple ? 'mp4' : 'mov';
    const upper = ext !== '' && ext === ext.toUpperCase() && ext !== ext.toLowerCase();
    return '.' + (upper ? e.toUpperCase() : e);
  }

  // DIR/<YYYY>/<YYYY-MM> from the taken time in UTC.
  function folderFor(ms) {
    let d = new Date(typeof ms === 'number' && isFinite(ms) ? ms : 0);
    if (isNaN(d.getTime()) || d.getUTCFullYear() < 1 || d.getUTCFullYear() > 9999) d = new Date(0);
    const y = String(d.getUTCFullYear()).padStart(4, '0');
    return y + '/' + y + '-' + String(d.getUTCMonth() + 1).padStart(2, '0');
  }

  // Mac and Windows disks ignore case, so names are compared folded (like path_key in google_photos.py).
  function fold(name) {
    return name.normalize('NFC').toUpperCase().toLowerCase();
  }

  async function namesOnDisk(folder) {
    if (st.diskNames.has(folder)) return st.diskNames.get(folder);
    const names = new Set();
    try {
      const dir = await getDir(folder.split('/'), false);
      for await (const name of dir.keys()) names.add(fold(name));
    } catch (e) {
      if (!isNotFound(e)) throw e;
    }
    st.diskNames.set(folder, names);
    return names;
  }

  // Paths stay the same from run to run: an item keeps the path of an earlier run, and new
  // items get names that no other item and no file on disk uses.
  async function allocatePaths(items, prev) {
    const reserved = new Map();
    const owner = (rel) => reserved.get(fold(rel));
    const reserve = (rel, key) => reserved.set(fold(rel), key);
    for (const it of items) {
      const d = prev.done.get(it.mediaKey);
      const p = prev.items.get(it.mediaKey);
      const path = (d && validRel(d.path) && d.path) || (p && validRel(p.path) && p.path) || null;
      if (!path) continue;
      let livePath = null;
      if (it.live) {
        livePath = (d && validRel(d.livePath) && d.livePath) || (p && validRel(p.livePath) && p.livePath) || null;
        if (!livePath) continue;
      }
      if (owner(path) || (livePath && owner(livePath))) continue;
      it.path = path;
      it.livePath = livePath;
      reserve(path, it.mediaKey);
      if (livePath) reserve(livePath, it.mediaKey);
    }
    for (const it of items) {
      if (it.path) continue;
      const folder = folderFor(it.takenTime);
      const disk = await namesOnDisk(folder);
      let safe = safeFileName(it.fileName);
      if (!safe) safe = safeFileName('google-' + it.mediaKey.slice(0, 40) + (it.kind === 'video' ? '.mp4' : '.jpg'));
      const parts = splitName(safe);
      const liveExt = it.live ? liveExtFor(it.fileName, parts.ext) : null;
      const taken = (name) => !!owner(folder + '/' + name) || disk.has(fold(name));
      let name;
      let liveName = null;
      for (let n = 1; ; n++) {
        const stem = n === 1 ? parts.stem : parts.stem + ' (' + n + ')';
        name = stem + parts.ext;
        liveName = liveExt ? stem + liveExt : null;
        if (!taken(name) && !(liveName && taken(liveName))) break;
      }
      it.path = folder + '/' + name;
      it.livePath = liveName ? folder + '/' + liveName : null;
      reserve(it.path, it.mediaKey);
      if (it.livePath) reserve(it.livePath, it.mediaKey);
    }
  }

  // A file counts when its size is the one Google reports. For a Live Photo, Google's size may
  // be the photo alone or the photo and its video together: the first Live Photo that arrives
  // decides. If it is neither, Live Photos count with the sizes that arrived.
  function sizeOk(item, size, liveSize) {
    if (!(size > 0)) return false;
    if (item.live && !(liveSize > 0)) return false;
    if (!validSize(item.size)) return true;
    if (!item.live) return size === item.size;
    const exact = size === item.size;
    const both = size + liveSize === item.size;
    if (st.liveRule === null) st.liveRule = exact ? 'exact' : both ? 'both' : 'loose';
    if (st.liveRule === 'exact') return exact;
    if (st.liveRule === 'both') return both || exact;
    return true;
  }

  // Items finished in an earlier run are skipped when their files are on disk with the
  // recorded sizes. A file without a done line counts when its size is Google's size.
  async function checkExisting(items, prev) {
    const todo = [];
    const keep = [];
    let n = 0;
    for (const it of items) {
      n += 1;
      if (n % 200 === 0) say(MSG.checking(n, items.length));
      const d = prev.done.get(it.mediaKey);
      let ok = false;
      if (d && d.path === it.path && (!it.live || (d.livePath === it.livePath && validSize(d.liveSize)))) {
        ok = (await fileSize(it.path)) === d.size &&
          (!it.live || (await fileSize(it.livePath)) === d.liveSize) &&
          sizeOk(it, d.size, it.live ? d.liveSize : null);
        if (ok) markDone(it, d.size, it.live ? d.liveSize : null, false);
      } else if (!d && !it.kept && !it.live && validSize(it.size) && it.size > 0 && prev.items.has(it.mediaKey)) {
        ok = (await fileSize(it.path)) === it.size;
        if (ok) markDone(it, it.size, null, true);
      }
      if (ok) keep.push(it);
      else if (!it.kept) {
        keep.push(it);
        todo.push(it);
      }
    }
    return { items: keep, todo: todo };
  }

  function itemOut(it) {
    return {
      mediaKey: it.mediaKey,
      takenTime: it.takenTime,
      fileName: it.fileName,
      // A Live Photo's size goes into the list only when it is the photo's own size.
      size: it.live && st.liveRule !== 'exact' ? null : it.size,
      kind: it.kind,
      live: it.live,
      originalQuality: it.originalQuality,
      favorite: it.favorite,
      archived: it.archived,
      albums: it.albums,
      path: it.path,
      livePath: it.live ? it.livePath : null,
    };
  }

  // google-list.json, written once per run, in pieces so a big library needs no huge string.
  async function writeList(items, albums, method) {
    const fh = await st.hc.getFileHandle(LIST_FILE, { create: true });
    const out = await fh.createWritable();
    try {
      await out.write('{"format": "homecloud-google-list", "version": 1, "created": ' + JSON.stringify(nowIso()) +
        ', "method": ' + JSON.stringify(method) + ', "items": [\n');
      let buf = [];
      for (let i = 0; i < items.length; i++) {
        buf.push((i ? ',\n' : '') + JSON.stringify(itemOut(items[i])));
        if (buf.length >= 2000) {
          await out.write(buf.join(''));
          buf = [];
        }
      }
      if (buf.length) await out.write(buf.join(''));
      await out.write('\n], "albums": ' + JSON.stringify(albums) + ', "totals": ' +
        JSON.stringify({ count: items.length, bytes: st.bytesTotal }) + '}\n');
      await out.close();
    } catch (e) {
      try { await out.abort(); } catch (e2) { /* ignore */ }
      throw writeError(e);
    }
  }

  function checkSpace(need, early) {
    const free = CONFIG.freeBytes;
    if (typeof free === 'number' && isFinite(free) && need > free) throw runFail(MSG.noSpace(need, free, early));
  }

  // --------------------------------------------------------------- downloads

  function allowedMediaUrl(u) {
    let url;
    try {
      url = new URL(u);
    } catch (e) {
      return false;
    }
    if (url.protocol !== 'https:') return false;
    const h = url.hostname.toLowerCase();
    return h === 'photos.google.com' || /\.(google|googleusercontent|googlevideo)\.com$/.test(h);
  }

  async function fetchMedia(url, cred, signal) {
    if (!allowedMediaUrl(url)) throw itemErr('url', 'Google gave an address this script does not use');
    let resp;
    try {
      resp = await fetch(url, { method: 'GET', credentials: cred, cache: 'no-store', signal: signal });
    } catch (e) {
      if (isAbort(e) || st.stopping) throw stopErr();
      throw itemErr('network', 'the browser could not fetch the file', true);
    }
    if (!resp.ok) {
      try { if (resp.body) await resp.body.cancel(); } catch (e) { /* ignore */ }
      if (resp.status === 429) {
        noteRateLimit(retryAfterMs(resp));
        throw itemErr('busy', 'Google asked to slow down', true);
      }
      if (resp.status >= 500) throw itemErr('server', 'Google answered ' + resp.status, true);
      throw itemErr('http', 'Google answered ' + resp.status);
    }
    const type = (resp.headers.get('content-type') || '').toLowerCase();
    if (type.indexOf('text/html') === 0) {
      try { if (resp.body) await resp.body.cancel(); } catch (e) { /* ignore */ }
      throw itemErr('html', 'Google sent a web page instead of the file');
    }
    return resp;
  }

  // Retries one fetch. A fetch that the browser blocks looks like a network error, so it is
  // retried more only with a method that already worked for this kind of item.
  async function withRetries(fn, known, signal) {
    for (let attempt = 1; ; attempt++) {
      await waitForPause(signal);
      try {
        return await fn();
      } catch (e) {
        if (isAbort(e) || st.stopping) throw stopErr();
        if (!e || e.hc !== 'item' || !e.transient) throw e;
        const max = e.code === 'network' && !known ? 2 : 5;
        if (attempt >= max) throw e;
        await sleep(Math.min(60000, 2000 * Math.pow(2, attempt - 1)), signal);
      }
    }
  }

  async function fetchToPath(url, cred, rel, signal) {
    const resp = await fetchMedia(url, cred, signal);
    const body = resp.body || (await resp.blob()).stream();
    return writeStream(rel, body, signal);
  }

  function infoError(e) {
    if (isAbort(e) || st.stopping) return stopErr();
    if (isSignedOut(e)) return runFail(MSG.signedOut);
    if (e && e.hc === 'fail') return e;
    return itemErr('info', 'the item info request failed: ' + errText(e));
  }

  async function viaItemUrl(item, cred, ctx, known, signal) {
    if (!ctx.info) {
      try {
        ctx.info = await api.getItemInfo(item.mediaKey, signal);
      } catch (e) {
        throw infoError(e);
      }
      if (!ctx.info) throw itemErr('info', 'Google gave no details for this item');
    }
    const info = ctx.info;
    const mainUrl = nonEmpty(info.downloadOriginalUrl) || nonEmpty(info.downloadUrl);
    if (!mainUrl) throw itemErr('url', 'Google gave no download address for this item');
    let liveUrl = null;
    if (item.live) {
      liveUrl = nonEmpty(info.livePhotoVideoDownloadUrl);
      if (!liveUrl) throw itemErr('url', 'Google gave no address for the video part of this Live Photo');
    }
    const size = await withRetries(() => fetchToPath(mainUrl, cred, item.path, signal), known, signal);
    let liveSize = null;
    if (liveUrl) liveSize = await withRetries(() => fetchToPath(liveUrl, cred, item.livePath, signal), known, signal);
    return { size: size, liveSize: liveSize };
  }

  // Every zip file of a ready download token. GPTK parser.ts lines 333 to 340 read the first one.
  function zipParts(raw) {
    const files = raw?.[0]?.[0]?.[0]?.[2];
    const out = [];
    if (Array.isArray(files)) {
      for (const f of files) {
        if (Array.isArray(f) && nonEmpty(f[1])) out.push({ name: nonEmpty(f[0]) || '', url: f[1], size: f[2] });
      }
    }
    return out;
  }

  function u64(dv, off) {
    return dv.getUint32(off, true) + dv.getUint32(off + 4, true) * 4294967296;
  }

  // The entries of a zip from its central directory (ZIP64 too), or the file itself when
  // Google sent a plain file.
  async function zipEntries(blob, partName) {
    const head = new Uint8Array(await blob.slice(0, 4).arrayBuffer());
    if (!(head.length === 4 && head[0] === 0x50 && head[1] === 0x4b && head[2] === 0x03 && head[3] === 0x04)) {
      return [{ name: partName || 'file', plain: true, blob: blob, size: blob.size }];
    }
    const bad = () => itemErr('zip', 'the zip from Google could not be read');
    const tailStart = Math.max(0, blob.size - (65535 + 22));
    const tail = new DataView(await blob.slice(tailStart).arrayBuffer());
    let eocd = -1;
    for (let i = tail.byteLength - 22; i >= 0; i--) {
      if (tail.getUint32(i, true) === 0x06054b50) {
        eocd = i;
        break;
      }
    }
    if (eocd < 0) throw bad();
    let count = tail.getUint16(eocd + 10, true);
    let cdSize = tail.getUint32(eocd + 12, true);
    let cdOffset = tail.getUint32(eocd + 16, true);
    if (count === 0xffff || cdSize === 0xffffffff || cdOffset === 0xffffffff) {
      const loc = eocd - 20;
      if (loc < 0 || tail.getUint32(loc, true) !== 0x07064b50) throw bad();
      const z64 = u64(tail, loc + 8);
      const z = new DataView(await blob.slice(z64, z64 + 56).arrayBuffer());
      if (z.byteLength < 56 || z.getUint32(0, true) !== 0x06064b50) throw bad();
      count = u64(z, 32);
      cdSize = u64(z, 40);
      cdOffset = u64(z, 48);
    }
    const cd = new DataView(await blob.slice(cdOffset, cdOffset + cdSize).arrayBuffer());
    const decoder = new TextDecoder('utf-8');
    const entries = [];
    let p = 0;
    for (let n = 0; n < count; n++) {
      if (p + 46 > cd.byteLength || cd.getUint32(p, true) !== 0x02014b50) throw bad();
      const method = cd.getUint16(p + 10, true);
      let csize = cd.getUint32(p + 20, true);
      let size = cd.getUint32(p + 24, true);
      const nameLen = cd.getUint16(p + 28, true);
      const extraLen = cd.getUint16(p + 30, true);
      const commentLen = cd.getUint16(p + 32, true);
      let local = cd.getUint32(p + 42, true);
      if (p + 46 + nameLen + extraLen > cd.byteLength) throw bad();
      const name = decoder.decode(new Uint8Array(cd.buffer, cd.byteOffset + p + 46, nameLen));
      let q = p + 46 + nameLen;
      const end = q + extraLen;
      while (q + 4 <= end) {
        const id = cd.getUint16(q, true);
        const len = cd.getUint16(q + 2, true);
        if (id === 0x0001) {
          let r = q + 4;
          if (size === 0xffffffff && r + 8 <= end) { size = u64(cd, r); r += 8; }
          if (csize === 0xffffffff && r + 8 <= end) { csize = u64(cd, r); r += 8; }
          if (local === 0xffffffff && r + 8 <= end) { local = u64(cd, r); r += 8; }
        }
        q += 4 + len;
      }
      p = end + commentLen;
      if (!name || name.charAt(name.length - 1) === '/') continue;
      entries.push({ name: name.split('/').pop(), method: method, csize: csize, size: size, local: local, blob: blob });
    }
    return entries;
  }

  async function entryStream(entry) {
    if (entry.plain) return entry.blob.stream();
    const lh = new DataView(await entry.blob.slice(entry.local, entry.local + 30).arrayBuffer());
    if (lh.byteLength < 30 || lh.getUint32(0, true) !== 0x04034b50) throw itemErr('zip', 'the zip from Google could not be read');
    const start = entry.local + 30 + lh.getUint16(26, true) + lh.getUint16(28, true);
    const data = entry.blob.slice(start, start + entry.csize);
    if (entry.method === 0) return data.stream();
    if (entry.method === 8 && typeof DecompressionStream === 'function') {
      return data.stream().pipeThrough(new DecompressionStream('deflate-raw'));
    }
    throw itemErr('zip', 'the zip from Google uses a format this script cannot read');
  }

  function pickEntries(entries, item) {
    const videos = entries.filter((e) => VIDEO_EXT.test(e.name));
    const others = entries.filter((e) => !VIDEO_EXT.test(e.name));
    const bySize = (list) => (validSize(item.size) ? list.find((e) => e.size === item.size) : null) || null;
    if (item.kind === 'video') return { main: bySize(videos) || videos[0] || entries[0] || null, live: null };
    if (item.live) return { main: bySize(others) || others[0] || null, live: videos[0] || null };
    return { main: bySize(others) || bySize(entries) || others[0] || entries[0] || null, live: null };
  }

  async function writeEntry(entry, rel, signal) {
    const size = await writeStream(rel, await entryStream(entry), signal);
    if (!entry.plain && size !== entry.size) {
      await removeFile(rel);
      throw itemErr('zip', 'a file in the zip from Google was incomplete');
    }
    return size;
  }

  async function viaZip(item, cred, known, signal) {
    let token;
    try {
      token = await api.getDownloadToken([item.mediaKey], signal);
    } catch (e) {
      throw infoError(e);
    }
    if (Array.isArray(token)) token = token[0];
    if (!nonEmpty(token)) throw itemErr('zip', 'Google gave no download token');
    let parts = [];
    for (let i = 0; i < 90 && parts.length === 0; i++) {
      if (i) await sleep(Math.min(5000, 500 * (i + 1)), signal);
      let raw;
      try {
        raw = await api.checkDownloadToken(token, signal);
      } catch (e) {
        throw infoError(e);
      }
      parts = zipParts(raw);
    }
    if (!parts.length) throw itemErr('zip', 'Google did not prepare the download in time', true);
    const entries = [];
    for (const part of parts) {
      const resp = await withRetries(() => fetchMedia(part.url, cred, signal), known, signal);
      let blob;
      try {
        blob = await resp.blob();
      } catch (e) {
        if (isAbort(e) || st.stopping) throw stopErr();
        throw itemErr('network', 'the download broke off', true);
      }
      if (validSize(part.size) && blob.size !== part.size) throw itemErr('zip', 'the zip from Google was incomplete');
      entries.push(...(await zipEntries(blob, part.name)));
    }
    const pick = pickEntries(entries, item);
    if (!pick.main) throw itemErr('zip', 'the zip from Google did not hold the file');
    if (item.live && !pick.live) throw itemErr('zip', 'the zip from Google did not hold the video part of this Live Photo');
    const size = await writeEntry(pick.main, item.path, signal);
    const liveSize = item.live ? await writeEntry(pick.live, item.livePath, signal) : null;
    return { size: size, liveSize: liveSize };
  }

  // A method that keeps failing for a kind of item while another one works gives way to it.
  function learn(kind, idx) {
    if (st.prefs[kind] === null) {
      st.prefs[kind] = idx;
    } else if (st.prefs[kind] === idx) {
      st.otherStreak[kind] = 0;
    } else if (++st.otherStreak[kind] >= 3) {
      st.prefs[kind] = idx;
      st.otherStreak[kind] = 0;
    }
  }

  async function downloadItem(item, signal) {
    const kind = kindOf(item);
    const pref = st.prefs[kind];
    const order = pref === null ? [0, 1, 2, 3] : [pref].concat([0, 1, 2, 3].filter((i) => i !== pref));
    const ctx = { info: null };
    const skip = new Set();
    let last = null;
    for (const idx of order) {
      const combo = COMBOS[idx];
      if (skip.has(combo.method)) continue;
      checkStop();
      try {
        const r = combo.method === 'item-url'
          ? await viaItemUrl(item, combo.cred, ctx, pref === idx, signal)
          : await viaZip(item, combo.cred, pref === idx, signal);
        if (!sizeOk(item, r.size, r.liveSize)) {
          await removeFile(item.path);
          if (item.live) await removeFile(item.livePath);
          throw itemErr('size', 'the file size did not match: Google reports ' + fmtInt(item.size) + ' bytes, ' +
            fmtInt(r.size) + ' arrived');
        }
        learn(kind, idx);
        return r;
      } catch (e) {
        if (isAbort(e) || st.stopping) throw stopErr();
        if (e && e.hc === 'fail') throw e;
        last = e;
        // Google answered, so the other credentials mode of the same method would not help.
        if (!(e && e.code === 'network')) skip.add(combo.method);
      }
    }
    throw last || itemErr('none', 'no download method worked');
  }

  function methodText() {
    const byCombo = new Map();
    for (const pair of KINDS) {
      const idx = st.prefs[pair[0]];
      if (idx === null) continue;
      if (!byCombo.has(idx)) byCombo.set(idx, []);
      byCombo.get(idx).push(pair[1].toLowerCase());
    }
    const text = (idx) => COMBOS[idx].method + ', credentials ' + COMBOS[idx].cred;
    if (byCombo.size === 0) return 'none';
    if (byCombo.size === 1) return text(byCombo.keys().next().value);
    return Array.from(byCombo.entries()).map((e) => text(e[0]) + ' (' + e[1].join(', ') + ')').join('; ');
  }

  // The first item of each kind (photo, video, Live Photo) shows whether downloads work. If one
  // fails, the second of that kind gets a chance before the run stops.
  async function probe(queue, signal) {
    const finished = new Set();
    for (const pair of KINDS) {
      const candidates = [];
      for (const it of queue) {
        if (kindOf(it) === pair[0]) candidates.push(it);
        if (candidates.length === 2) break;
      }
      if (!candidates.length) continue;
      let ok = false;
      let last = null;
      for (const it of candidates) {
        checkStop();
        try {
          const r = await downloadItem(it, signal);
          markDone(it, r.size, r.liveSize, true);
          finished.add(it);
          ok = true;
          break;
        } catch (e) {
          if (isAbort(e) || st.stopping) throw stopErr();
          if (e && e.hc === 'fail') throw e;
          last = e;
        }
      }
      if (!ok) throw runFail(MSG.downloadFailed(pair[1], errText(last)));
      say(downloadingText());
    }
    return finished;
  }

  function haltRun(message) {
    if (st.halt) return;
    st.halt = message;
    st.stopping = true;
    if (st.controller) st.controller.abort();
  }

  async function downloadAll(queue, signal, lastPass) {
    let next = 0;
    st.failsInARow = 0;
    const worker = async () => {
      while (!st.stopping && next < queue.length) {
        const item = queue[next++];
        try {
          const r = await downloadItem(item, signal);
          markDone(item, r.size, r.liveSize, true);
          st.failsInARow = 0;
        } catch (e) {
          if (st.halt || isAbort(e) || st.stopping) break;
          if (e && e.hc === 'fail') {
            haltRun(e.userMessage);
            break;
          }
          st.failedItems.set(item.mediaKey, sentence(errText(e)));
          st.failsInARow += 1;
          if (!lastPass && st.failsInARow >= MAX_FAILS_IN_A_ROW) {
            haltRun(MSG.manyFailed(errText(e)));
            break;
          }
        }
        st.failed = st.failedItems.size;
        say(downloadingText());
        if (st.doneBuf.length && Date.now() - st.lastDoneFlush > DONE_FLUSH_MS) flushDone();
      }
    };
    const workers = [];
    for (let i = 0; i < Math.min(WORKERS, queue.length); i++) workers.push(worker());
    await Promise.all(workers);
    st.failed = st.failedItems.size;
    if (st.halt) throw runFail(st.halt);
    if (st.stopping) throw stopErr();
  }

  // ------------------------------------------------------------ keep awake

  async function keepAwake() {
    try {
      if (navigator.wakeLock && document.visibilityState === 'visible' && !st.wakeLock) {
        const lock = await navigator.wakeLock.request('screen');
        st.wakeLock = lock;
        if (lock && typeof lock.addEventListener === 'function') {
          lock.addEventListener('release', () => {
            if (st.wakeLock === lock) st.wakeLock = null;
          });
        }
      }
    } catch (e) {
      st.wakeLock = null;
    }
  }

  function releaseAwake() {
    const lock = st.wakeLock;
    st.wakeLock = null;
    if (lock && typeof lock.release === 'function') lock.release().catch(() => { /* already released */ });
  }

  function onVisibility() {
    if (st.running && document.visibilityState === 'visible') keepAwake();
  }

  function onBeforeUnload(ev) {
    if (!st.running) return undefined;
    ev.preventDefault();
    ev.returnValue = '';
    return '';
  }

  // ------------------------------------------------------------------- a run

  async function runInFolder(dir, page) {
    resetRun(dir, page);
    setButtons();
    const signal = st.controller.signal;
    let items = null;
    let albums = [];
    let listWritten = false;
    try {
      try {
        st.hc = await dir.getDirectoryHandle(META_DIR, { create: true });
      } catch (e) {
        throw runFail(MSG.noPermission);
      }
      await say(MSG.starting, true);
      await keepAwake();
      const prev = await readPrevious();

      let media;
      let albumInfo;
      let info;
      try {
        media = await listLibrary(signal);
        albumInfo = await readAlbums(signal);
        info = await readInfo(media, signal);
      } catch (e) {
        if (isAbort(e) || st.stopping) throw stopErr();
        if (isSignedOut(e)) throw runFail(MSG.signedOut);
        if (e && e.hc === 'fail') throw e;
        if (e && e.network) throw runFail(MSG.offline);
        throw runFail(MSG.listFailed(errText(e)));
      }
      if (!media.length && !prev.done.size) throw runFail(MSG.empty);

      const all = buildItems(media, info, albumInfo.titlesByDedup, prev);
      await allocatePaths(all, prev);
      const checked = await checkExisting(all, prev);
      items = checked.items;
      albums = albumInfo.albums;
      const todo = checked.todo;
      st.listed = items.length;
      st.bytesTotal = items.reduce((s, it) => s + (validSize(it.size) ? it.size : 0), 0);

      // Google's sizes first. The video parts of Live Photos are added after the first
      // Live Photo arrives, when their size is known.
      const need = todo.reduce((s, it) => s + (validSize(it.size) ? it.size : 0), 0);
      checkSpace(need, true);

      st.state = 'downloading';
      let queue = todo;
      let method = prev.method || 'none';
      if (queue.length) {
        await say(MSG.firstFiles, true);
        const probed = await probe(queue, signal);
        queue = queue.filter((it) => !probed.has(it));
        method = methodText();
        const lives = todo.filter((it) => it.live).length;
        if (lives && st.liveRule !== 'both') {
          const each = st.liveSeen.n ? Math.ceil(st.liveSeen.bytes / st.liveSeen.n) : LIVE_GUESS_BYTES;
          checkSpace(need + lives * each, false);
        }
      }
      await writeList(items, albums, method);
      listWritten = true;
      await say(downloadingText(), true);
      await downloadAll(queue, signal, false);
      const again = queue.filter((it) => st.failedItems.has(it.mediaKey));
      if (again.length) await downloadAll(again, signal, true);
      st.state = 'done';
      st.message = st.failed ? MSG.doneSomeFailed(st.failed, st.listed) : MSG.done(st.listed);
    } catch (e) {
      if (st.halt) {
        st.state = 'failed';
        st.message = st.halt;
      } else if (isAbort(e) || (st.stopping && !(e && e.hc === 'fail'))) {
        st.state = 'stopped';
        st.message = MSG.stopped;
      } else if (e && e.hc === 'fail') {
        st.state = 'failed';
        st.message = e.userMessage;
      } else {
        st.state = 'failed';
        st.message = MSG.unexpected(errText(e));
        console.error('Homecloud: ' + errText(e));
      }
      if (st.state === 'failed' && items && items.length && !listWritten) {
        try { await writeList(items, albums, methodText()); } catch (e2) { /* the message says what to do */ }
      }
    } finally {
      st.failedItems.forEach((why, key) => queueDoneLine({ mediaKey: key, error: why }));
      try {
        await finishDone();
      } catch (e) {
        if (st.state !== 'failed') {
          st.state = 'failed';
          st.message = e && e.userMessage ? e.userMessage : MSG.unexpected(errText(e));
        }
      }
      st.failed = st.failedItems.size;
      st.running = false;
      st.stopping = false;
      releaseAwake();
      if (ui.status) ui.status.textContent = st.message;
      await writeProgress(true);
      setButtons();
    }
  }

  async function startRun() {
    if (w.location.hostname !== 'photos.google.com') {
      say(MSG.wrongSite);
      return;
    }
    if (typeof w.showDirectoryPicker !== 'function') {
      say(MSG.noPicker);
      return;
    }
    const page = readPageData();
    if (!page) {
      say(MSG.notReady);
      return;
    }
    let dir;
    try {
      // The first call after the click, while the click still allows a folder window.
      dir = await w.showDirectoryPicker({ id: 'homecloud-google', mode: 'readwrite' });
    } catch (e) {
      say(e && e.name === 'AbortError' ? MSG.noFolder : MSG.pickFailed);
      return;
    }
    if (CONFIG.folderName && String(dir.name).normalize('NFC') !== String(CONFIG.folderName).normalize('NFC')) {
      say(MSG.wrongFolder(dir.name, CONFIG.folderName));
      return;
    }
    try {
      let perm = 'granted';
      if (typeof dir.queryPermission === 'function') perm = await dir.queryPermission({ mode: 'readwrite' });
      if (perm !== 'granted' && typeof dir.requestPermission === 'function') perm = await dir.requestPermission({ mode: 'readwrite' });
      if (perm !== 'granted') {
        say(MSG.noPermission);
        return;
      }
    } catch (e) {
      say(MSG.noPermission);
      return;
    }
    if (navigator.locks && typeof navigator.locks.request === 'function') {
      // One run at a time across tabs. Chrome also does not freeze a tab that holds a lock.
      let got = false;
      await navigator.locks.request('homecloud-google', { ifAvailable: true }, async (lock) => {
        if (!lock) return;
        got = true;
        await runInFolder(dir, page);
      });
      if (!got) say(MSG.otherTab);
    } else {
      await runInFolder(dir, page);
    }
  }

  function onStart() {
    if (st.running) return;
    instance.lastRun = startRun().catch((e) => {
      console.error('Homecloud: ' + errText(e));
      say(MSG.unexpected(errText(e)));
    });
  }

  function onStop() {
    if (!st.running || st.stopping) return;
    st.stopping = true;
    if (st.controller) st.controller.abort();
    say(MSG.stopping, true);
    setButtons();
  }

  const instance = {
    lastRun: null,
    get running() {
      return st.running;
    },
    show() {
      if (ui.box && !ui.box.parentNode) (document.body || document.documentElement).appendChild(ui.box);
    },
    close() {
      if (st.running) return;
      document.removeEventListener('visibilitychange', onVisibility);
      w.removeEventListener('beforeunload', onBeforeUnload);
      if (ui.box) ui.box.remove();
    },
  };

  buildPanel();
  document.addEventListener('visibilitychange', onVisibility);
  w.addEventListener('beforeunload', onBeforeUnload);
  w.__homecloudGoogle = instance;
  return MSG.open;
})(HOMECLOUD_CONFIG);
}
