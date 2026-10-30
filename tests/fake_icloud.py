"""A fake iCloud Photos web service for offline tests. Nothing here uses the network.

FakeICloud answers the four read-only CloudKit calls that icloud.py makes, with
records shaped like Apple's. Any other call is recorded as a violation and
fails the test. FakeFetcher serves file contents for the download links and can
inject failures per file.
"""

import base64
import datetime as dt
import hashlib
import threading
import time
from urllib.parse import parse_qs, urlsplit

import icloud

ALLOWED_PATHS = set(icloud.CK_READ_PATHS)
UTI = {".HEIC": "public.heic", ".JPG": "public.jpeg", ".PNG": "public.png",
       ".MOV": "com.apple.quicktime-movie", ".MP4": "public.mpeg-4", ".DNG": "com.adobe.raw-image"}


def ms(day):
    """Milliseconds since 1970 for an aware datetime."""
    return int((day - icloud.EPOCH).total_seconds() * 1000)


class FakePhoto(object):
    def __init__(self, asset_id, filename, taken, video=False, live=False, edited=False,
                 edited_live=False, alt=False, favorite=False, hidden=False, library="personal",
                 offset=7200, size=None, albums=()):
        self.asset_id = asset_id
        self.master_id = "M-" + asset_id
        self.filename = filename
        self.taken = taken
        self.video = video
        self.live = live
        self.edited = edited
        self.edited_live = edited_live
        self.alt = alt
        self.favorite = favorite
        self.hidden = hidden
        self.library = library
        self.offset = offset
        self.size = size
        self.albums = list(albums)

    @property
    def ext(self):
        return "." + self.filename.rsplit(".", 1)[-1].upper()

    def fields(self):
        """(holder, field prefix, uti) for every file this item has."""
        out = [("master", "resOriginal", UTI.get(self.ext, "public.jpeg"))]
        if self.alt:
            out.append(("master", "resOriginalAlt", "com.adobe.raw-image"))
        if self.video:
            if self.edited:
                out.append(("asset", "resVidFull", "com.apple.quicktime-movie"))
        else:
            if self.live:
                out.append(("master", "resOriginalVidCompl", "com.apple.quicktime-movie"))
            if self.edited:
                out.append(("asset", "resJPEGFull", "public.jpeg"))
            if self.edited_live:
                out.append(("asset", "resVidCompl", "com.apple.quicktime-movie"))
        return out


class FakeICloud(object):
    def __init__(self, photos, deleted=2, url_lifetime=3600, split_masters=False, storage=None,
                 lookup_lifetime=None, url_base="https://cvws.icloud-content.com"):
        self.url_base = url_base
        self.photos = list(photos)
        self.deleted = deleted
        self.url_lifetime = url_lifetime
        self.lookup_lifetime = lookup_lifetime  # lifetime of links from records/lookup, if different
        self.split_masters = split_masters
        self.storage_info = storage
        self.calls = []
        self.violations = []
        self.generation = {}
        self.fail_on = []          # [(path, CloudError)], raised once each on the next matching call
        self.content_version = {}  # (asset_id, field) -> int, bump to change a file in iCloud
        self.lock = threading.Lock()

    # ---- content ----------------------------------------------------------
    def content(self, asset_id, field):
        photo = self.by_id(asset_id)
        version = self.content_version.get((asset_id, field), 0)
        seed = ("%s|%s|%d" % (asset_id, field, version)).encode()
        size = photo.size if (photo.size is not None and field == "resOriginal") else \
            200 + int(hashlib.sha256(seed).hexdigest()[:3], 16) % 300
        block = hashlib.sha256(seed).digest()
        data = (block * (size // len(block) + 1))[:size]
        return data

    def by_id(self, asset_id):
        for photo in self.photos:
            if photo.asset_id == asset_id:
                return photo
        raise KeyError(asset_id)

    def url(self, photo, field, expired=False):
        key = (photo.asset_id, field)
        gen = self.generation.get(key, 0)
        expiry = int(time.time() + (-120 if expired else self.url_lifetime))
        return ("%s/B/%s~%s~%d/${f}?o=AbC&v=1&e=%d"
                % (self.url_base, photo.asset_id, field, gen, expiry))

    # ---- records ----------------------------------------------------------
    def zone_name(self, photo):
        return "PrimarySync" if photo.library == "personal" else "SharedSync-0F1E2D"

    def res(self, photo, field, uti, expired=False):
        return {
            field + "Res": {"type": "ASSETID", "value": {
                "size": len(self.content(photo.asset_id, field)),
                "downloadURL": self.url(photo, field, expired),
                "fileChecksum": "AZ" + photo.asset_id,
            }},
            field + "FileType": {"type": "STRING", "value": uti},
        }

    def master_record(self, photo, expired=False):
        fields = {
            "filenameEnc": {"type": "ENCRYPTED_BYTES",
                            "value": base64.b64encode(photo.filename.encode("utf-8")).decode()},
            "itemType": {"type": "STRING", "value": UTI.get(photo.ext, "public.jpeg")},
        }
        for holder, field, uti in photo.fields():
            if holder == "master":
                fields.update(self.res(photo, field, uti, expired))
        return {"recordName": photo.master_id, "recordType": "CPLMaster", "fields": fields,
                "recordChangeTag": "1"}

    def asset_record(self, photo, expired=False):
        fields = {
            "masterRef": {"type": "REFERENCE", "value": {
                "recordName": photo.master_id,
                "zoneID": {"zoneName": self.zone_name(photo)}}},
            "assetDate": {"type": "TIMESTAMP", "value": ms(photo.taken)},
            "addedDate": {"type": "TIMESTAMP", "value": ms(photo.taken)},
            "timeZoneOffset": {"type": "INT64", "value": photo.offset},
            "isFavorite": {"type": "INT64", "value": 1 if photo.favorite else 0},
            "isHidden": {"type": "INT64", "value": 1 if photo.hidden else 0},
        }
        for holder, field, uti in photo.fields():
            if holder == "asset":
                fields.update(self.res(photo, field, uti, expired))
        return {"recordName": photo.asset_id, "recordType": "CPLAsset", "fields": fields,
                "recordChangeTag": "1"}

    def listed(self, zone, hidden):
        items = [p for p in self.photos if self.zone_name(p) == zone and p.hidden == hidden]
        return sorted(items, key=lambda p: (p.taken, p.asset_id))

    # ---- the CloudKit endpoints ------------------------------------------------
    def post_json(self, db, path, payload):
        with self.lock:
            self.calls.append((db, path))
            if path not in ALLOWED_PATHS:
                self.violations.append(path)
                raise AssertionError("forbidden CloudKit call: " + path)
            for index, (fail_path, error) in enumerate(self.fail_on):
                if fail_path == path:
                    del self.fail_on[index]
                    raise error
        if path == "zones/list":
            if db == "shared":
                return {"zones": []}
            zones = [{"zoneID": dict(icloud.PRIMARY_ZONE)}]
            if any(p.library == "shared" for p in self.photos):
                zones.append({"zoneID": {"zoneName": "SharedSync-0F1E2D",
                                         "zoneType": "REGULAR_CUSTOM_ZONE",
                                         "ownerRecordName": "_owner"}})
            return {"zones": zones}
        if path == "internal/records/query/batch":
            out = []
            for entry in payload["batch"]:
                zone = entry["zoneID"]["zoneName"]
                cid = entry["query"]["filterBy"]["fieldValue"]["value"][0]
                if cid == icloud.COUNT_ALL:
                    value = len(self.listed(zone, False))
                elif cid == icloud.COUNT_HIDDEN:
                    value = len(self.listed(zone, True))
                else:
                    value = self.deleted if zone == "PrimarySync" else 0
                out.append({"records": [{"recordName": cid, "recordType": "HyperionIndexCountLookup",
                                         "fields": {"itemCount": {"type": "INT64", "value": value}}}]})
            return {"batch": out}
        if path == "records/query":
            query = payload["query"]
            filters = dict((f["fieldName"], f["fieldValue"]["value"]) for f in query["filterBy"])
            hidden = query["recordType"] == icloud.LIST_HIDDEN
            items = self.listed(payload["zoneID"]["zoneName"], hidden)
            per_page = max(1, payload["resultsLimit"] // 2)
            rank = int(filters["startRank"])
            if filters["direction"] == "ASCENDING":
                page = items[rank:rank + per_page]
            else:
                page = list(reversed(items[max(0, rank - per_page + 1):rank + 1]))
            records = []
            for index, photo in enumerate(page):
                records.append(self.asset_record(photo))
                last = index == len(page) - 1
                if not (self.split_masters and last and len(page) == per_page):
                    records.append(self.master_record(photo))
            return {"records": records}
        if path == "records/lookup":
            return self._lookup(payload)
        raise AssertionError("unhandled path " + path)

    def _lookup(self, payload):
        saved = self.url_lifetime
        if self.lookup_lifetime is not None:
            self.url_lifetime = self.lookup_lifetime
        try:
            records = []
            for item in payload["records"]:
                name = item["recordName"]
                photo = None
                for p in self.photos:
                    if name in (p.asset_id, p.master_id):
                        photo = p
                if photo is None:
                    records.append({"recordName": name, "serverErrorCode": "NOT_FOUND",
                                    "reason": "Record not found"})
                    continue
                for holder, field, uti in photo.fields():
                    key = (photo.asset_id, field)
                    self.generation[key] = self.generation.get(key, 0) + 1
                if name == photo.master_id:
                    records.append(self.master_record(photo))
                else:
                    records.append(self.asset_record(photo))
            return {"records": records}
        finally:
            self.url_lifetime = saved

    def albums(self, membership, out):
        names = []
        members = {}
        for photo in self.photos:
            for name in photo.albums:
                if name not in names:
                    names.append(name)
                if membership:
                    members.setdefault(photo.asset_id, []).append(name)
        return names, members, []


class FakeResponse(object):
    def __init__(self, status, body=b"", headers=None, chunk=64, after=None):
        self.status = status
        self.headers = headers or {}
        self._body = body
        self._chunk = chunk
        self._after = after
        self.closed = False

    def iter_chunks(self, size):
        step = min(size, self._chunk)
        for start in range(0, len(self._body), step):
            yield self._body[start:start + step]
        if self._after is not None:
            raise self._after

    def close(self):
        self.closed = True


class FakeFetcher(object):
    """Serves file bytes for fake download links.

    plans maps (asset_id, field) to a list of actions used one per request:
    "ok", "500", "429", "403", "410", "truncate", "interrupt", "ignore_range".
    """

    def __init__(self, cloud):
        self.cloud = cloud
        self.requests = []
        self.plans = {}
        self.lock = threading.Lock()

    def get(self, url, headers):
        icloud.check_request("GET", url)
        parts = urlsplit(url)
        token = parts.path.split("/")[2]
        asset_id, field, _ = token.split("~")
        expiry = int(parse_qs(parts.query)["e"][0])
        start = 0
        if headers and headers.get("Range"):
            start = int(headers["Range"].split("=")[1].rstrip("-"))
        with self.lock:
            self.requests.append((asset_id, field, start))
            queue = self.plans.get((asset_id, field)) or []
            action = queue.pop(0) if queue else "ok"
        if expiry < time.time() or action == "410":
            return FakeResponse(410)
        if action in ("500", "403"):
            return FakeResponse(int(action))
        if action == "429":
            return FakeResponse(429, headers={"Retry-After": "0"})
        data = self.cloud.content(asset_id, field)
        if action == "ignore_range":
            return FakeResponse(200, data)
        status = 206 if start else 200
        body = data[start:]
        if action == "truncate":
            return FakeResponse(status, body[: len(body) // 2],
                                after=icloud.FetchError("retry", "the connection dropped"))
        if action == "interrupt":
            return FakeResponse(status, body[: len(body) // 2], after=KeyboardInterrupt())
        return FakeResponse(status, body)


def make_bundle(cloud, recover_auth=None, recover_access=None, adp=False, fetcher=None):
    client = icloud.CloudClient(cloud, recover_auth=recover_auth, recover_access=recover_access,
                                sleep=lambda seconds: None, retry_base=0.001)
    return icloud.CloudBundle(client, fetcher=fetcher or FakeFetcher(cloud), albums=cloud.albums,
                              adp=adp, storage=cloud.storage_info)


def day(year, month, dayn, hour=12):
    return dt.datetime(year, month, dayn, hour, 0, 0, tzinfo=dt.timezone.utc)


def sample_library():
    """A small library with every kind of item."""
    return [
        FakePhoto("A1", "IMG_0001.HEIC", day(2019, 5, 1), live=True, favorite=True, albums=["Trip"]),
        FakePhoto("A2", "IMG_0002.HEIC", day(2019, 5, 2), edited=True, albums=["Trip", "Best"]),
        FakePhoto("A3", "IMG_0003.MOV", day(2020, 1, 3), video=True, edited=True),
        FakePhoto("A4", "IMG_0004.JPG", day(2020, 1, 4), hidden=True),
        FakePhoto("A5", "IMG_0005.HEIC", day(2021, 7, 5), live=True, edited=True, edited_live=True),
        FakePhoto("A6", "IMG_0006.JPG", day(2021, 7, 6), alt=True),
        FakePhoto("S1", "IMG_7001.HEIC", day(2022, 3, 1), library="shared"),
        FakePhoto("S2", "IMG_7002.MOV", day(2022, 3, 2), video=True, library="shared"),
    ]
