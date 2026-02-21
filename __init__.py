import difflib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import Request, urlopen

from calibre.ebooks.metadata.book.base import Metadata
from calibre.ebooks.metadata.sources.base import Option, Source
from calibre.utils.date import parse_date

BANGUMI_BASE_URL = "https://bangumi.tv"
BANGUMI_API_URL = "https://api.bgm.tv/v0"

PLUGIN_VERSION = (1, 0, 0)

CONFIG = {
    "tag_user_count": 5,
    "tag_count": 10,
}


class BangumiMetadata(Source):
    name = "Bangumi"
    description = "Fetch book metadata from Bangumi (https://bangumi.tv/)"
    supported_platforms = ["windows", "osx", "linux"]
    author = "Cusox"
    version = PLUGIN_VERSION

    capabilities = frozenset(["identify", "cover"])
    touched_fields = frozenset(
        [
            "title",
            "authors",
            "tags",
            "pubdate",
            "publisher",
            "comments",
            "rating",
            "identifier:isbn",
            "identifier:bgm",
        ]
    )

    options = [
        Option(
            "tag_user_count",
            "number",
            CONFIG["tag_user_count"],
            _("有效标签的最小打标签人数"),
            _("其他用户打标签时，至少有多少人打了这个标签，才会被认为是有效标签"),
        ),
        Option(
            "tag_count",
            "number",
            CONFIG["tag_count"],
            _("最大标签数量"),
            _("最多保留多少个标签"),
        ),
    ]

    def __init__(self, *args, **kwargs):
        Source.__init__(self, *args, **kwargs)

        global CONFIG
        for key in CONFIG:
            if key in self.prefs:
                CONFIG[key] = self.prefs[key]

    @property
    def user_agent(self):
        return "Cusox/calibre-bangumi"

    @property
    def headers(self):
        headers = {
            "Accept": "application/json",
            "User-Agent": self.user_agent,
        }

        return headers

    def _parse_infobox(self, infobox, keys, only_first=False):
        matches = {}

        if not infobox:
            return []

        for info in infobox:
            if info["key"] in keys:
                value = info["value"]
                if isinstance(value, list):
                    matches[info["key"]] = [v["v"] for v in value]
                else:
                    matches[info["key"]] = value

        result = []
        for key in keys:
            if key in matches:
                if isinstance(matches[key], list):
                    result.extend(matches[key])
                else:
                    result.append(matches[key])

        if result and only_first:
            return result[0]
        else:
            return result

    def _parse_tags(self, tags):
        if not tags:
            return []

        result = []
        for tag in tags:
            if tag["count"] >= CONFIG["tag_user_count"]:
                result.append(tag["name"])

        return result[: CONFIG["tag_count"]]

    def _parse_data(self, data):
        book = {}

        book["title"] = data.get("name", "")
        book["title_cn"] = data.get("name_cn", "")
        book["authors"] = self._parse_infobox(
            data.get("infobox", []), ["作者", "原作", "作画", "插图", "插画"]
        )
        book["cover"] = data.get("images", {}).get("large", None)
        book["tags"] = self._parse_tags(data.get("tags", []))
        book["pubdate"] = parse_date(
            data["date"]
            or self._parse_infobox(data.get("infobox", []), ["发售日"], only_first=True)
        )
        book["publisher"] = self._parse_infobox(
            data.get("infobox", []), ["出版社"], only_first=True
        )
        book["comments"] = data.get("summary", "")
        book["rating"] = float(data.get("rating", {}).get("score", 0)) / 2
        book["identifier:isbn"] = self._parse_infobox(
            data.get("infobox", []), ["ISBN"], only_first=True
        )
        book["identifier:bgm"] = data["id"]

        return book

    def _to_metadata(self, book):
        mi = Metadata(book["title_cn"] or book["title"], book["authors"])
        mi.cover = book["cover"]
        mi.tags = book["tags"]
        mi.pubdate = book["pubdate"]
        mi.publisher = book["publisher"]
        mi.comments = book["comments"]
        mi.rating = book["rating"]
        mi.set_identifier("bgm", str(book["identifier:bgm"]))
        if book["identifier:isbn"]:
            mi.isbn = book["identifier:isbn"]
            mi.set_identifier("isbn", str(book["identifier:isbn"]))

        return mi

    def _query_subject(self, log, bangumi_id):
        url = f"{BANGUMI_API_URL}/subjects/{bangumi_id}"
        headers = self.headers

        res = urlopen(Request(url, headers=headers, method="GET"))
        if res.getcode() == 200:
            raw_data = res.read()
        else:
            log.error(
                f"Failed to fetch data for Bangumi ID {bangumi_id}: HTTP {res.getcode()}"
            )
            return None

        data = json.loads(raw_data)

        book = self._parse_data(data)

        return book

    def _query_subject_relations(self, log, bangumi_id):
        url = f"{BANGUMI_API_URL}/subjects/{bangumi_id}/subjects"
        headers = self.headers

        res = urlopen(Request(url, headers=headers, method="GET"))
        if res.getcode() == 200:
            raw_data = res.read()
        else:
            return []

        bangumi_ids = []

        data = json.loads(raw_data)
        for item in data:
            if item["type"] == 1:
                bangumi_ids.append(item["id"])

        return bangumi_ids

    def _search_by_title(self, log, title):
        payload = {
            "keyword": title,
            "filter": {
                "type": [1],
                "nsfw": True,
            },
        }

        url = f"{BANGUMI_API_URL}/search/subjects?limit=3"
        headers = self.headers

        req_body = json.dumps(payload).encode("utf-8")
        res = urlopen(Request(url, headers=headers, method="POST", data=req_body))
        if res.getcode() == 200:
            raw_data = res.read()
        else:
            log.error(f"Failed to search for title '{title}': HTTP {res.getcode()}")
            return []

        data = json.loads(raw_data)

        bangumi_ids = [item["id"] for item in data.get("data", [])]
        if not bangumi_ids:
            log.info("Cannot find any Bangumi subject matching the title.")
            return []

        return bangumi_ids

    def get_book_url(self, identifiers):
        bangumi_id = identifiers.get("bgm", None)
        if bangumi_id is not None:
            return ("Bangumi", bangumi_id, f"{BANGUMI_BASE_URL}/subject/{bangumi_id}")

    def get_cached_cover_url(self, identifiers):
        url = None
        bangumi_id = identifiers.get("bgm", None)
        if bangumi_id is not None:
            url = self.cached_identifier_to_cover_url(bangumi_id)
        else:
            isbn = identifiers.get("isbn", None)
            if isbn is not None:
                bangumi_id = self.cached_isbn_to_identifier(isbn)
                url = self.cached_identifier_to_cover_url(bangumi_id)

        return url

    def identify(
        self,
        log,
        result_queue,
        abort,
        title=None,
        authors=None,
        identifiers={},
        timeout=30,
    ):
        books = []
        valid_books = []

        bangumi_id = identifiers.get("bgm", None)
        if bangumi_id is not None:
            log.info(f"Found book by Bangumi ID: {bangumi_id}")

            book = self._query_subject(log, bangumi_id)

            log.info("Success..." if book else "Failed!!!")

            books.append(book)
        else:
            log.info("No Bangumi ID Provided...")
            log.info(f"Found book by Title: {title}")

            bangumi_ids = self._search_by_title(log, title)
            # with ThreadPoolExecutor(max_workers=5) as executor:
            #     books.extend(
            #         executor.map(lambda id: self._query_subject(log, id), bangumi_ids)
            #     )

            child_ids = set(bangumi_ids)
            with ThreadPoolExecutor(max_workers=5) as executor:
                related_ids = executor.map(
                    lambda id: self._query_subject_relations(log, id), bangumi_ids
                )

                for sublist in related_ids:
                    if sublist:
                        child_ids.update(sublist)

            with ThreadPoolExecutor(max_workers=5) as executor:
                books.extend(
                    executor.map(lambda id: self._query_subject(log, id), child_ids)
                )

        log.info(f"Found {len(books)} books in total.")

        if title and books:
            search_title = title.lower()
            scored_books = []

            for book in books:
                if book is None:
                    continue

                title_orig = book.get("title", "").lower()
                title_cn = book.get("title_cn", "").lower()

                score_orig = difflib.SequenceMatcher(
                    None, search_title, title_orig
                ).ratio()
                score_cn = difflib.SequenceMatcher(None, search_title, title_cn).ratio()

                if search_title in title_orig or search_title in title_cn:
                    score = 1.0
                else:
                    score = max(score_orig, score_cn)

                scored_books.append((score, book))

            scored_books.sort(key=lambda x: x[0], reverse=True)

            valid_books = [book for score, book in scored_books[:10]]
        else:
            valid_books = [book for book in books if book is not None][:10]

        for book in valid_books:
            mi = self._to_metadata(book)

            bangumi_id = mi.identifiers["bgm"]
            if mi.isbn:
                self.cache_isbn_to_identifier(mi.isbn, bangumi_id)
            if mi.cover:
                self.cache_identifier_to_cover_url(bangumi_id, mi.cover)
            self.clean_downloaded_metadata(mi)

            result_queue.put(mi)

    def download_cover(
        self,
        log,
        result_queue,
        abort,
        title=None,
        authors=None,
        identifiers={},
        timeout=30,
        get_best_cover=False,
    ):
        cached_url = self.get_cached_cover_url(identifiers)
        if cached_url is None:
            log.info("No cached cover found for this book.")
            return

        log.info(f"Found cached cover URL: {cached_url}")
        try:
            br = self.browser
            cover_data = br.open_novisit(cached_url, timeout=timeout).read()
            if cover_data:
                result_queue.put((self, cover_data))
                log.info("Cover downloaded successfully.")

        except Exception:
            log.error(f"Failed to download cover from {cached_url}")


if __name__ == "__main__":
    from calibre.ebooks.metadata.sources.test import (
        authors_test,
        test_identify_plugin,
        title_test,
    )

    test_identify_plugin(
        BangumiMetadata.name,
        [
            # (
            #     {
            #         "identifiers": {"bgm": "136517"},
            #         "title": "OVERLORD",
            #     },
            #     [
            #         title_test("オーバーロード (1)", exact=False),
            #     ],
            # ),
            (
                {
                    "title": "欢迎来到实力至上主义的教室 0",
                },
                [],
            ),
        ],
    )
