import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import Request, urlopen

from calibre.ebooks.metadata.book.base import Metadata
from calibre.ebooks.metadata.sources.base import Option, Source
from calibre.utils.date import parse_date

BANGUMI_BASE_URL = "https://bangumi.tv/"
BANGUMI_API_URL = "https://api.bgm.tv/v0/"
BANGUMI_SUBJECT_URL = BANGUMI_BASE_URL + "subject/%s"
BANGUMI_API_SUBJECT_URL = BANGUMI_API_URL + "subjects/%s"
BANGUMI_API_SUBJECTS_URL = BANGUMI_API_URL + "subjects/%s/subjects"
BANGUMI_API_SEARCH_URL = BANGUMI_API_URL + "search/subjects"
PLUGIN_VERSION = (1, 0, 0)

CONFIG = {
    "tag_user_count": 5,
    "tag_count": 10,
}


class BangumiMetadata(Source):
    name = "Bangumi Metadata Source"
    description = "从 Bangumi 中获取书籍信息"
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

    def _get_headers(self):
        headers = {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Mobile Safari/537.36",
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
            if tag["count"] >= CONFIG["tag_count"]:
                result.append(tag["name"])

        return result[: CONFIG["tag_count"]]

    def _parse_data(self, data):
        book = {}

        book["title"] = data["name_cn"] or data["name"]
        book["authors"] = self._parse_infobox(
            data.get("infobox", []), ["作者", "原作", "作画", "插图", "插画"]
        )
        book["tags"] = self._parse_tags(data.get("tags", []))
        book["pubdate"] = parse_date(
            data["date"]
            or self._parse_infobox(data.get("infobox", []), ["发售日"], only_first=True)
        )
        book["publisher"] = self._parse_infobox(
            data.get("infobox", []), ["出版社"], only_first=True
        )
        book["comments"] = data.get("summary", "")
        book["rating"] = data.get("rating", {}).get("score", 0)
        book["identifier:isbn"] = self._parse_infobox(
            data.get("infobox", []), ["ISBN"], only_first=True
        )
        book["identifier:bgm"] = data["id"]

        return book

    def _to_metadata(self, book):
        mi = Metadata(book["title"], book["authors"])
        mi.tags = book["tags"]
        mi.pubdate = book["pubdate"]
        mi.publisher = book["publisher"]
        mi.comments = book["comments"]
        mi.rating = book["rating"]
        mi.set_identifier("bgm", str(book["identifier:bgm"]))
        if book["identifier:isbn"]:
            mi.set_identifier("isbn", str(book["identifier:isbn"]))

        return mi

    def _query_subject(self, log, bangumi_id):
        url = BANGUMI_API_SUBJECT_URL % bangumi_id
        headers = self._get_headers()

        res = urlopen(Request(url, headers=headers, method="GET"))
        if res.getcode() == 200:
            raw_data = res.read()
        else:
            return None

        data = json.loads(raw_data)

        book = self._parse_data(data)

        return book

    def _query_related_subjects(self, log, bangumi_id):
        url = BANGUMI_API_SUBJECTS_URL % bangumi_id
        headers = self._get_headers()

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

        url = BANGUMI_API_SEARCH_URL
        headers = self._get_headers()

        req_body = json.dumps(payload).encode("utf-8")
        res = urlopen(Request(url, headers=headers, method="POST", data=req_body))
        if res.getcode() == 200:
            raw_data = res.read()
        else:
            return []

        data = json.loads(raw_data)

        bangumi_ids = [item["id"] for item in data.get("data", [])]
        if not bangumi_ids:
            log.info("未找到匹配的 Bangumi 条目")
            return []

        expanded_ids = set(bangumi_ids)
        with ThreadPoolExecutor(max_workers=5) as executor:
            related_ids = executor.map(
                lambda id: self._query_related_subjects(log, id), bangumi_ids
            )

            expanded_ids.update(rel_id for sublist in related_ids for rel_id in sublist)

        log.info(f"找到{len(expanded_ids)}个匹配的 Bangumi 条目，正在查询详细信息...")

        with ThreadPoolExecutor(max_workers=5) as executor:
            books = executor.map(lambda id: self._query_subject(log, id), expanded_ids)

            return list(books)

        return []

    def get_book_url(self, identifiers):
        bangumi_id = identifiers.get("bgm", None)
        if bangumi_id is not None:
            return ("Bangumi", bangumi_id, BANGUMI_SUBJECT_URL % bangumi_id)

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

        bangumi_id = identifiers.get("bgm", None)
        if bangumi_id is not None:
            log.info("正在查询 Bangumi ID %s...", bangumi_id)

            book = self._query_subject(log, bangumi_id)

            log.info("查询 Bangumi ID %s: %s", bangumi_id, "成功" if book else "失败")

            books.append(book)
        else:
            log.info("未提供 Bangumi ID")
            log.info("通过 Title 进行查询...")

            books = self._search_by_title(log, title)

        for book in books:
            mi = self._to_metadata(book)

            result_queue.put(mi)


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
                    "title": "OVERLORD",
                },
                [],
            ),
        ],
    )
