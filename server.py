#!/usr/bin/env python3
import argparse
import concurrent.futures
import html
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


SOURCE_FEED = "https://www.tandfonline.com/feed/rss/ritt20"
FEED_TITLE = "The Interpreter and Translator Trainer: Table of Contents with Abstracts"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X) NetNewsWire RSS enhancer"
CACHE_PATH = os.environ.get(
    "ABSTRACT_CACHE_PATH",
    os.path.join(os.path.dirname(__file__), "work", "abstract-cache.json"),
)
ABSTRACT_TTL_SECONDS = 60 * 60 * 24 * 30
FEED_TTL_SECONDS = 60 * 30

NS = {
    "rss1": "http://purl.org/rss/1.0/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "prism": "http://prismstandard.org/namespaces/basic/2.0/",
}


def fetch_text(url, accept="*/*", timeout=20):
    request = urllib.request.Request(
        url,
        headers={
            "Accept": accept,
            "User-Agent": USER_AGENT,
        },
    )
    last_error = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read().decode(response.headers.get_content_charset() or "utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError) as error:
            last_error = error
            time.sleep(1 + attempt)

    if shutil.which("curl"):
        try:
            completed = subprocess.run(
                [
                    "curl",
                    "-fsSL",
                    "-A",
                    USER_AGENT,
                    "-H",
                    f"Accept: {accept}",
                    url,
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return completed.stdout
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            last_error = error
    raise last_error


def text_of(element, path, namespaces=None):
    found = element.find(path, namespaces or NS)
    return (found.text or "").strip() if found is not None else ""


def load_cache():
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_cache(cache):
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as file:
        json.dump(cache, file, ensure_ascii=False, indent=2, sort_keys=True)


def reconstruct_openalex_abstract(inverted_index):
    if not inverted_index:
        return ""
    words = []
    for word, positions in inverted_index.items():
        for position in positions:
            words.append((position, word))
    return " ".join(word for _, word in sorted(words)).strip()


def api_json(url):
    try:
        return json.loads(fetch_text(url, accept="application/json"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return {}


def abstract_from_semantic_scholar(doi):
    encoded = urllib.parse.quote(f"DOI:{doi}", safe="")
    url = f"https://api.semanticscholar.org/graph/v1/paper/{encoded}?fields=abstract"
    data = api_json(url)
    return (data.get("abstract") or "").strip()


def abstract_from_openalex(doi):
    encoded = urllib.parse.quote(f"https://doi.org/{doi}", safe="")
    url = f"https://api.openalex.org/works/{encoded}"
    data = api_json(url)
    return reconstruct_openalex_abstract(data.get("abstract_inverted_index"))


def fetch_abstract(doi, cache):
    if not doi:
        return ""
    cached = cache.get(doi)
    if cached and time.time() - cached.get("fetched_at", 0) < ABSTRACT_TTL_SECONDS:
        return cached.get("abstract", "")

    abstract = abstract_from_semantic_scholar(doi) or abstract_from_openalex(doi)
    cache[doi] = {"abstract": abstract, "fetched_at": time.time()}
    return abstract


def parse_date(value):
    if not value:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            return parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return datetime.now(timezone.utc)


def clean_html_description(value):
    value = html.unescape(value or "")
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"\s+", " ", value).strip()


def parse_tandf_feed(xml_text):
    root = ET.fromstring(xml_text)
    channel = root.find("rss1:channel", NS)
    items = []
    for item in root.findall("rss1:item", NS):
        doi = text_of(item, "prism:doi")
        if not doi:
            identifier = text_of(item, "dc:identifier")
            doi = identifier.replace("doi:", "", 1).strip()
        items.append(
            {
                "title": text_of(item, "rss1:title"),
                "link": text_of(item, "rss1:link"),
                "doi": doi,
                "creator": text_of(item, "dc:creator"),
                "date": text_of(item, "dc:date"),
                "fallback_description": clean_html_description(text_of(item, "rss1:description")),
                "volume": text_of(item, "prism:volume"),
                "issue": text_of(item, "prism:number"),
                "pages": "-".join(
                    part
                    for part in [text_of(item, "prism:startingPage"), text_of(item, "prism:endingPage")]
                    if part
                ),
            }
        )
    return {
        "title": text_of(channel, "rss1:title") if channel is not None else FEED_TITLE,
        "link": text_of(channel, "rss1:link") if channel is not None else SOURCE_FEED,
        "description": text_of(channel, "rss1:description") if channel is not None else "",
        "items": items,
    }


def cdata(value):
    return "<![CDATA[" + (value or "").replace("]]>", "]]]]><![CDATA[>") + "]]>"


def item_body(item, abstract):
    meta = []
    if item["creator"]:
        meta.append(f"<p><strong>Authors:</strong> {html.escape(item['creator'])}</p>")
    issue_bits = []
    if item["volume"]:
        issue_bits.append(f"Volume {html.escape(item['volume'])}")
    if item["issue"]:
        issue_bits.append(f"Issue {html.escape(item['issue'])}")
    if item["pages"]:
        issue_bits.append(f"Pages {html.escape(item['pages'])}")
    if issue_bits:
        meta.append(f"<p><strong>Issue:</strong> {', '.join(issue_bits)}</p>")
    if item["doi"]:
        meta.append(f"<p><strong>DOI:</strong> {html.escape(item['doi'])}</p>")

    if abstract:
        meta.insert(0, f"<p>{html.escape(abstract)}</p>")
    elif item["fallback_description"]:
        meta.insert(0, f"<p>{html.escape(item['fallback_description'])}</p>")
    else:
        meta.insert(0, "<p>No abstract found in public metadata yet.</p>")
    return "\n".join(meta)


def generate_feed_xml():
    source_xml = fetch_text(SOURCE_FEED, accept="application/rss+xml, application/xml, text/xml")
    cache = load_cache()
    return build_rss(parse_tandf_feed(source_xml), cache)


def build_rss(feed, cache):
    def enrich(item):
        return item, fetch_abstract(item["doi"], cache)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        enriched = list(executor.map(enrich, feed["items"]))

    save_cache(cache)
    now = format_datetime(datetime.now(timezone.utc))
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:dc="http://purl.org/dc/elements/1.1/">',
        "<channel>",
        f"<title>{html.escape(FEED_TITLE)}</title>",
        f"<link>{html.escape(feed['link'])}</link>",
        f"<description>{html.escape(feed['description'])}</description>",
        f"<lastBuildDate>{now}</lastBuildDate>",
        "<ttl>30</ttl>",
    ]

    for item, abstract in enriched:
        body = item_body(item, abstract)
        pub_date = format_datetime(parse_date(item["date"]))
        guid = item["doi"] or item["link"]
        lines.extend(
            [
                "<item>",
                f"<title>{html.escape(item['title'])}</title>",
                f"<link>{html.escape(item['link'])}</link>",
                f"<guid isPermaLink=\"false\">{html.escape(guid)}</guid>",
                f"<pubDate>{pub_date}</pubDate>",
                f"<description>{cdata(body)}</description>",
                f"<content:encoded>{cdata(body)}</content:encoded>",
                f"<dc:creator>{html.escape(item['creator'])}</dc:creator>" if item["creator"] else "",
                "</item>",
            ]
        )
    lines.extend(["</channel>", "</rss>"])
    return "\n".join(line for line in lines if line)


class FeedHandler(BaseHTTPRequestHandler):
    rendered_feed = None
    rendered_at = 0

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args))

    def send_text(self, status, body, content_type):
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ("/", "/feed.xml"):
            try:
                force = urllib.parse.parse_qs(parsed.query).get("refresh") == ["1"]
                if force or not self.rendered_feed or time.time() - self.rendered_at > FEED_TTL_SECONDS:
                    self.__class__.rendered_feed = generate_feed_xml()
                    self.__class__.rendered_at = time.time()
                self.send_text(200, self.rendered_feed, "application/rss+xml")
            except Exception as error:
                self.send_text(502, f"Feed proxy error: {error}\n", "text/plain")
            return
        if parsed.path == "/health":
            self.send_text(200, "ok\n", "text/plain")
            return
        self.send_text(404, "Not found\n", "text/plain")


def main():
    parser = argparse.ArgumentParser(description="Taylor & Francis RSS abstract proxy for NetNewsWire")
    parser.add_argument("--once", metavar="PATH", help="write one static RSS file and exit")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    args = parser.parse_args()

    if args.once:
        os.makedirs(os.path.dirname(os.path.abspath(args.once)), exist_ok=True)
        with open(args.once, "w", encoding="utf-8") as file:
            file.write(generate_feed_xml())
            file.write("\n")
        print(f"Wrote {args.once}")
        return

    server = ThreadingHTTPServer((args.host, args.port), FeedHandler)
    print(f"RSS proxy running at http://{args.host}:{args.port}/feed.xml")
    server.serve_forever()


if __name__ == "__main__":
    main()
