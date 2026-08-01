#!/usr/bin/env python3
import argparse
import concurrent.futures
import hashlib
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
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


BASE_DIR = os.path.dirname(__file__)
CONFIG_PATH = os.path.join(BASE_DIR, "journals.json")
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X) Translation Studies RSS enhancer"
CACHE_PATH = os.environ.get("ABSTRACT_CACHE_PATH", os.path.join(BASE_DIR, "work", "metadata-cache.json"))
TRANSLATE_TO_ZH = os.environ.get("TRANSLATE_TO_ZH", "").lower() in ("1", "true", "yes")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_TRANSLATION_MODEL = os.environ.get("DEEPSEEK_TRANSLATION_MODEL", "deepseek-v4-flash")
TRANSLATION_PROMPT = (
    "作为一名精通简体中文、口笔译学科知识、统计学、心理学的专业翻译家，"
    "请将所提供的文本准确地翻译为简体中文。请仅回复翻译后的文本，不要任何其他内容。"
    "【待翻译文本】如下"
)
METADATA_TTL_SECONDS = 60 * 60 * 24 * 30
FEED_TTL_SECONDS = 60 * 30
MAX_WORKERS = int(os.environ.get("RSS_MAX_WORKERS", "8"))
MAX_ITEMS_PER_SOURCE = int(os.environ.get("RSS_MAX_ITEMS_PER_SOURCE", "8"))

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "prism": "http://prismstandard.org/namespaces/basic/2.0/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rss1": "http://purl.org/rss/1.0/",
}


def fetch_text(url, accept="*/*", timeout=5, attempts=3, use_curl=True):
    request = urllib.request.Request(
        url,
        headers={
            "Accept": accept,
            "User-Agent": USER_AGENT,
        },
    )
    last_error = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = response.read()
                return data.decode(response.headers.get_content_charset() or "utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError) as error:
            last_error = error
            time.sleep(1 + attempt)

    if use_curl and shutil.which("curl"):
        try:
            completed = subprocess.run(
                ["curl", "-fsSL", "--max-time", str(timeout), "-A", USER_AGENT, "-H", f"Accept: {accept}", url],
                check=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return completed.stdout
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            last_error = error
    raise last_error


def load_json(path, fallback):
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return fallback


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2, sort_keys=True)


def load_journals():
    return load_json(CONFIG_PATH, [])


class MetaTagParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = {}

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "meta":
            return
        values = {name.lower(): value for name, value in attrs if name and value is not None}
        name = (values.get("name") or values.get("property") or "").lower()
        content = (values.get("content") or "").strip()
        if name and content:
            self.meta.setdefault(name, []).append(html.unescape(content))


def local_name(tag):
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def find_child(element, name):
    for child in list(element):
        if local_name(child.tag) == name:
            return child
    return None


def text_child(element, *names):
    for name in names:
        child = find_child(element, name)
        if child is not None and child.text:
            return child.text.strip()
    return ""


def inner_xml(element):
    if element is None:
        return ""
    parts = []
    if element.text:
        parts.append(element.text)
    for child in list(element):
        parts.append(ET.tostring(child, encoding="unicode", method="xml"))
    return "".join(parts).strip()


def strip_html(value):
    value = html.unescape(value or "")
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", "", value)
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"^Abstract\s+", "", value, flags=re.I)
    return value.strip()


def extract_doi(*values):
    for value in values:
        match = re.search(r"10\.\d{4,9}/[^\s\"'<>]+", value or "", flags=re.I)
        if match:
            return match.group(0).rstrip(").,;").replace("?TRACK=RSS", "")
    return ""


def parse_date(value):
    if not value:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return datetime.now(timezone.utc)


def reconstruct_openalex_abstract(inverted_index):
    if not inverted_index:
        return ""
    words = []
    for word, positions in inverted_index.items():
        for position in positions:
            words.append((position, word))
    return " ".join(word for _, word in sorted(words)).strip()


def api_json(url):
    if shutil.which("curl"):
        try:
            completed = subprocess.run(
                ["curl", "-fsSL", "--max-time", "5", "-A", USER_AGENT, "-H", "Accept: application/json", url],
                check=True,
                capture_output=True,
                text=True,
                timeout=7,
            )
            return json.loads(completed.stdout)
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            json.JSONDecodeError,
        ):
            return {}
    try:
        return json.loads(fetch_text(url, accept="application/json", attempts=1, use_curl=False))
    except (
        urllib.error.URLError,
        TimeoutError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ):
        return {}


def post_json(url, payload, headers=None, timeout=20):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            **(headers or {}),
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def authors_from_crossref(authors):
    names = []
    for author in authors or []:
        name = " ".join(part for part in [author.get("given"), author.get("family")] if part).strip()
        if name:
            names.append(name)
    return ", ".join(names)


def crossref_date(message):
    for key in ("published-online", "published-print", "published", "created"):
        parts = (message.get(key) or {}).get("date-parts") or []
        if parts and parts[0]:
            year = parts[0][0]
            month = parts[0][1] if len(parts[0]) > 1 else 1
            day = parts[0][2] if len(parts[0]) > 2 else 1
            return datetime(year, month, day, tzinfo=timezone.utc).isoformat()
    return ""


def metadata_from_crossref(doi):
    data = api_json(f"https://api.crossref.org/works/{urllib.parse.quote(doi, safe='')}")
    message = data.get("message") or {}
    if not message:
        return {}
    return {
        "title": (message.get("title") or [""])[0],
        "creator": authors_from_crossref(message.get("author")),
        "date": crossref_date(message),
        "journal": (message.get("container-title") or [""])[0],
        "volume": message.get("volume") or "",
        "issue": message.get("issue") or "",
        "pages": message.get("page") or "",
        "abstract": strip_html(message.get("abstract") or ""),
    }


def metadata_from_semantic_scholar(doi):
    encoded = urllib.parse.quote(f"DOI:{doi}", safe="")
    data = api_json(f"https://api.semanticscholar.org/graph/v1/paper/{encoded}?fields=title,abstract,authors,year")
    if not data:
        return {}
    return {
        "title": data.get("title") or "",
        "creator": ", ".join(author.get("name", "") for author in data.get("authors") or [] if author.get("name")),
        "date": f"{data.get('year')}-01-01T00:00:00+00:00" if data.get("year") else "",
        "abstract": (data.get("abstract") or "").strip(),
    }


def metadata_from_openalex(doi):
    encoded = urllib.parse.quote(f"https://doi.org/{doi}", safe="")
    data = api_json(f"https://api.openalex.org/works/{encoded}")
    if not data:
        return {}
    source = ((data.get("primary_location") or {}).get("source") or {}).get("display_name") or ""
    authors = []
    for authorship in data.get("authorships") or []:
        display_name = (authorship.get("author") or {}).get("display_name")
        if display_name:
            authors.append(display_name)
    return {
        "title": data.get("title") or "",
        "creator": ", ".join(authors),
        "date": data.get("publication_date") or "",
        "journal": source,
        "abstract": reconstruct_openalex_abstract(data.get("abstract_inverted_index")),
    }


def first_meta(meta, *names):
    for name in names:
        values = meta.get(name.lower()) or []
        for value in values:
            if value:
                return value.strip()
    return ""


def article_page_metadata(url, cache):
    page_cache = cache.setdefault("_article_pages", {})
    cached = page_cache.get(url)
    if cached and time.time() - cached.get("fetched_at", 0) < METADATA_TTL_SECONDS:
        return cached.get("metadata") or {}
    try:
        page_html = fetch_text(url, accept="text/html,application/xhtml+xml", timeout=8, attempts=2)
    except (urllib.error.URLError, TimeoutError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return {}

    parser = MetaTagParser()
    parser.feed(page_html)
    meta = parser.meta
    first_page = first_meta(meta, "citation_firstpage")
    last_page = first_meta(meta, "citation_lastpage")
    pages = first_meta(meta, "dc.identifier.pagenumber")
    if first_page and last_page and first_page != last_page:
        pages = f"{first_page}-{last_page}"
    elif first_page:
        pages = first_page

    metadata = {
        "title": first_meta(meta, "citation_title", "dc.title"),
        "creator": ", ".join(meta.get("citation_author") or meta.get("dc.creator.personalname") or []),
        "date": first_meta(meta, "dc.date.issued", "citation_date").replace("/", "-"),
        "journal": first_meta(meta, "citation_journal_title", "dc.source"),
        "issue": first_meta(meta, "citation_issue", "dc.source.issue"),
        "pages": pages,
        "doi": first_meta(meta, "citation_doi", "dc.identifier.doi"),
        "abstract": strip_html(first_meta(meta, "dc.description", "citation_abstract")),
    }
    metadata = {key: value for key, value in metadata.items() if value}
    page_cache[url] = {"metadata": metadata, "fetched_at": time.time()}
    return metadata


def merge_missing(item, metadata):
    for key in ("title", "date", "journal", "volume", "issue", "pages"):
        if not item.get(key) and metadata.get(key):
            item[key] = metadata[key]
    if not item.get("doi") and metadata.get("doi"):
        item["doi"] = metadata["doi"]
    if metadata.get("creator") and should_replace_creator(item.get("creator", "")):
        item["creator"] = metadata["creator"]
    if metadata.get("abstract"):
        item["abstract"] = metadata["abstract"]


def should_replace_creator(value):
    if not value:
        return True
    lowered = value.lower()
    affiliation_terms = (
        " university",
        " institute",
        " department",
        " faculty",
        " school of ",
        " centre ",
        " center ",
        " college",
        " academy",
        " laboratorio",
        " universidad",
    )
    return len(value) > 120 or any(term in lowered for term in affiliation_terms)


def normalize_creator(value):
    value = re.sub(r"\s+", " ", value or "").strip()
    value = re.sub(r"\s+[a-z]\s+[A-Z][A-Za-z .,'’-]+$", "", value)
    return value


def enrich_item(item, cache):
    doi = item.get("doi")
    if item.get("abstract") and item.get("creator") and item.get("date"):
        return item
    if not doi:
        return item
    cached = cache.get(doi)
    cached_metadata = (cached or {}).get("metadata") or {}
    needs_abstract = not item.get("abstract") and re.match(r"^Volume \d+", item.get("fallback_description") or "")
    has_usable_cache = cached and (not needs_abstract or cached_metadata.get("abstract"))
    if has_usable_cache and time.time() - cached.get("fetched_at", 0) < METADATA_TTL_SECONDS:
        merge_missing(item, cached.get("metadata") or {})
        return item

    metadata = {}
    for source in (metadata_from_openalex, metadata_from_semantic_scholar, metadata_from_crossref):
        data = source(doi)
        for key, value in data.items():
            if value and not metadata.get(key):
                metadata[key] = value
        if metadata.get("abstract") and metadata.get("creator") and metadata.get("date"):
            break
    cache[doi] = {"metadata": metadata, "fetched_at": time.time()}
    merge_missing(item, metadata)
    return item


def translate_to_zh(text):
    if not TRANSLATE_TO_ZH or not DEEPSEEK_API_KEY or not text:
        return ""
    payload = {
        "model": DEEPSEEK_TRANSLATION_MODEL,
        "messages": [
            {"role": "user", "content": f"{TRANSLATION_PROMPT}\n\n{text}"},
        ],
        "temperature": 0,
        "thinking": {"type": "disabled"},
        "stream": False,
    }
    try:
        data = post_json(
            "https://api.deepseek.com/chat/completions",
            payload,
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
            timeout=30,
        )
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return ""


def translate_item(item, cache):
    abstract = item.get("abstract") or item.get("fallback_description") or ""
    if not abstract or "No abstract found" in abstract or re.match(r"^Volume \d+", abstract):
        return item
    digest = hashlib.sha256(abstract.encode("utf-8")).hexdigest()
    translations = cache.setdefault("_translations", {})
    cached = translations.get(digest)
    if cached:
        item["abstract_zh"] = cached
        return item
    translated = translate_to_zh(abstract)
    if translated:
        translations[digest] = translated
        item["abstract_zh"] = translated
    return item


def absolute_url(base_url, value):
    if not value:
        return ""
    return urllib.parse.urljoin(base_url, value)


def parse_rss1(root, source):
    channel = root.find("rss1:channel", NS)
    items = []
    for item in root.findall("rss1:item", NS):
        description = strip_html(text_child(item, "description") or inner_xml(find_child(item, "encoded")))
        link = absolute_url(source["source_feed"], text_child(item, "link"))
        doi = text_child(item, "doi") or extract_doi(text_child(item, "identifier"), link)
        items.append(
            {
                "title": text_child(item, "title"),
                "link": link,
                "doi": doi,
                "creator": normalize_creator(text_child(item, "creator")),
                "date": text_child(item, "date"),
                "fallback_description": description,
                "abstract": "",
                "journal": source["title"],
                "source_slug": source["slug"],
                "source_title": source["title"],
                "volume": text_child(item, "volume"),
                "issue": text_child(item, "number"),
                "pages": "-".join(part for part in [text_child(item, "startingPage"), text_child(item, "endingPage")] if part),
            }
        )
    return {
        "title": text_child(channel, "title") if channel is not None else source["title"],
        "link": text_child(channel, "link") if channel is not None else source["homepage"],
        "description": text_child(channel, "description") if channel is not None else source.get("description", ""),
        "items": items,
    }


def parse_rss2(root, source):
    channel = find_child(root, "channel")
    items = []
    for item in list(channel or []):
        if local_name(item.tag) != "item":
            continue
        raw_description = text_child(item, "description") or inner_xml(find_child(item, "encoded"))
        description = strip_html(raw_description)
        creator = text_child(item, "creator")
        if source.get("split_description_author") and not creator and "<br" in raw_description.lower():
            author_part, abstract_part = re.split(r"<br\s*/?>", raw_description, maxsplit=1, flags=re.I)
            creator = strip_html(author_part)
            description = strip_html(abstract_part)
        link = text_child(item, "link")
        guid = text_child(item, "guid")
        doi = text_child(item, "doi") or extract_doi(text_child(item, "identifier"), guid, link, description)
        items.append(
            {
                "title": text_child(item, "title"),
                "link": absolute_url(source["source_feed"], link or guid),
                "doi": doi,
                "creator": creator,
                "date": text_child(item, "date") or text_child(item, "pubDate"),
                "fallback_description": description,
                "abstract": description if len(description.split()) > 20 else "",
                "journal": source["title"],
                "source_slug": source["slug"],
                "source_title": source["title"],
                "volume": text_child(item, "volume"),
                "issue": text_child(item, "number") or text_child(item, "issue"),
                "pages": text_child(item, "pages"),
            }
        )
    return {
        "title": text_child(channel, "title") if channel is not None else source["title"],
        "link": text_child(channel, "link") if channel is not None else source["homepage"],
        "description": text_child(channel, "description") if channel is not None else source.get("description", ""),
        "items": items,
    }


def parse_atom(root, source):
    items = []
    for entry in root.findall("atom:entry", NS):
        link = ""
        for link_element in entry.findall("atom:link", NS):
            if link_element.get("rel") in (None, "alternate"):
                link = link_element.get("href", "")
                break
        summary = strip_html(text_child(entry, "summary") or text_child(entry, "content"))
        doi = extract_doi(text_child(entry, "id"), link, summary)
        items.append(
            {
                "title": text_child(entry, "title"),
                "link": absolute_url(source["source_feed"], link),
                "doi": doi,
                "creator": text_child(find_child(entry, "author") or entry, "name"),
                "date": text_child(entry, "updated") or text_child(entry, "published"),
                "fallback_description": summary,
                "abstract": summary if len(summary.split()) > 20 else "",
                "journal": source["title"],
                "source_slug": source["slug"],
                "source_title": source["title"],
                "volume": "",
                "issue": "",
                "pages": "",
            }
        )
    return {
        "title": text_child(root, "title") or source["title"],
        "link": source["homepage"],
        "description": source.get("description", ""),
        "items": items,
    }


def parse_source_feed(source):
    if source.get("source_type") == "crossref":
        return parse_crossref_source(source)
    xml_text = fetch_text(source["source_feed"], accept="application/rss+xml, application/atom+xml, application/xml, text/xml")
    root = ET.fromstring(xml_text.lstrip("\ufeff"))
    root_name = local_name(root.tag)
    if root_name == "RDF":
        return parse_rss1(root, source)
    if root_name == "rss":
        return parse_rss2(root, source)
    if root_name == "feed":
        return parse_atom(root, source)
    raise ValueError(f"Unsupported feed format for {source['slug']}: {root.tag}")


def parse_crossref_source(source):
    rows = source.get("max_items", MAX_ITEMS_PER_SOURCE)
    issn = urllib.parse.quote(source["issn"], safe="")
    url = f"https://api.crossref.org/journals/{issn}/works?sort=published&order=desc&rows={rows}&filter=type:journal-article"
    data = api_json(url)
    items = []
    for work in (data.get("message") or {}).get("items") or []:
        doi = work.get("DOI") or ""
        title = strip_html((work.get("title") or [""])[0])
        abstract = strip_html(work.get("abstract") or "")
        link = work.get("URL") or (f"https://doi.org/{doi}" if doi else source["homepage"])
        items.append(
            {
                "title": title,
                "link": link,
                "doi": doi,
                "creator": authors_from_crossref(work.get("author")),
                "date": crossref_date(work),
                "fallback_description": abstract,
                "abstract": abstract,
                "journal": source["title"],
                "source_slug": source["slug"],
                "source_title": source["title"],
                "volume": work.get("volume") or "",
                "issue": work.get("issue") or "",
                "pages": work.get("page") or "",
            }
        )
    return {
        "title": source["title"],
        "link": source["homepage"],
        "description": source.get("description", ""),
        "items": items,
    }


def cdata(value):
    return "<![CDATA[" + (value or "").replace("]]>", "]]]]><![CDATA[>") + "]]>"


def item_body(item):
    meta = []
    if item.get("source_title"):
        meta.append(f"<p><strong>Journal:</strong> {html.escape(item['source_title'])}</p>")
    if item.get("creator"):
        meta.append(f"<p><strong>Authors:</strong> {html.escape(item['creator'])}</p>")
    issue_bits = []
    if item.get("volume"):
        issue_bits.append(f"Volume {html.escape(item['volume'])}")
    if item.get("issue"):
        issue_bits.append(f"Issue {html.escape(item['issue'])}")
    if item.get("pages"):
        issue_bits.append(f"Pages {html.escape(item['pages'])}")
    if issue_bits:
        meta.append(f"<p><strong>Issue:</strong> {', '.join(issue_bits)}</p>")
    if item.get("doi"):
        meta.append(f"<p><strong>DOI:</strong> {html.escape(item['doi'])}</p>")

    abstract = item.get("abstract") or item.get("fallback_description") or "No abstract found in public metadata yet."
    meta.insert(0, f"<p>{html.escape(abstract)}</p>")
    if item.get("abstract_zh"):
        meta.insert(1, f"<p><strong>中文摘要：</strong>{html.escape(item['abstract_zh'])}</p>")
    return "\n".join(meta)


def build_rss(title, link, description, items):
    now = format_datetime(datetime.now(timezone.utc))
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:dc="http://purl.org/dc/elements/1.1/">',
        "<channel>",
        f"<title>{html.escape(title)}</title>",
        f"<link>{html.escape(link)}</link>",
        f"<description>{html.escape(description)}</description>",
        f"<lastBuildDate>{now}</lastBuildDate>",
        "<ttl>360</ttl>",
    ]
    for item in sorted(items, key=lambda item: parse_date(item.get("date")), reverse=True):
        body = item_body(item)
        pub_date = format_datetime(parse_date(item.get("date")))
        guid = item.get("doi") or item.get("link") or item.get("title")
        title_text = item.get("title") or "Untitled"
        if item.get("source_title") and not title_text.startswith(item["source_title"]):
            title_text = f"[{item['source_title']}] {title_text}"
        lines.extend(
            [
                "<item>",
                f"<title>{html.escape(title_text)}</title>",
                f"<link>{html.escape(item.get('link') or '')}</link>",
                f"<guid isPermaLink=\"false\">{html.escape(guid)}</guid>",
                f"<pubDate>{pub_date}</pubDate>",
                f"<description>{cdata(body)}</description>",
                f"<content:encoded>{cdata(body)}</content:encoded>",
                f"<dc:creator>{html.escape(item['creator'])}</dc:creator>" if item.get("creator") else "",
                "</item>",
            ]
        )
    lines.extend(["</channel>", "</rss>"])
    return "\n".join(line for line in lines if line)


def generate_source(source, cache):
    feed = parse_source_feed(source)
    items_to_enrich = feed["items"][: source.get("max_items", MAX_ITEMS_PER_SOURCE)]
    if source.get("enrich_from_article_page"):
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            page_metadata = list(
                executor.map(lambda item: article_page_metadata(item.get("link", ""), cache), items_to_enrich)
            )
        for item, metadata in zip(items_to_enrich, page_metadata):
            merge_missing(item, metadata)
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        items = list(executor.map(lambda item: enrich_item(item, cache), items_to_enrich))
    if TRANSLATE_TO_ZH and DEEPSEEK_API_KEY:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, MAX_WORKERS)) as executor:
            items = list(executor.map(lambda item: translate_item(item, cache), items))
    return feed, items


def generate_all_feeds():
    cache = load_json(CACHE_PATH, {})
    if TRANSLATE_TO_ZH and not DEEPSEEK_API_KEY:
        print("TRANSLATE_TO_ZH is enabled, but DEEPSEEK_API_KEY is not set. Skipping Chinese translations.")
    sources = load_journals()
    outputs = {}
    combined_items = []
    errors = []
    stats = []
    for source in sources:
        try:
            feed, items = generate_source(source, cache)
            combined_items.extend(items)
            stats.append(
                {
                    "slug": source["slug"],
                    "title": source["title"],
                    "publisher": source.get("publisher", ""),
                    "homepage": source.get("homepage") or feed["link"],
                    "feed": f"{source['slug']}.xml",
                    "source_feed": source.get("source_feed", ""),
                    "source_type": source.get("source_type", "rss"),
                    "items": len(items),
                    "translated": sum(1 for item in items if item.get("abstract_zh")),
                    "weak_abstracts": sum(1 for item in items if weak_abstract(item)),
                }
            )
            outputs[f"{source['slug']}.xml"] = build_rss(
                f"{source['title']} with Abstracts",
                source.get("homepage") or feed["link"],
                source.get("description") or feed["description"],
                items,
            )
        except Exception as error:
            errors.append(f"{source['slug']}: {error}")
    save_json(CACHE_PATH, cache)
    outputs["feed.xml"] = build_rss(
        "Translation Studies Journals with Abstracts",
        "https://xionglingsong.github.io/rss-translation-studies/",
        "Combined enhanced RSS feed for translation and interpreting studies journals.",
        combined_items,
    )
    outputs["manifest.json"] = json.dumps(
        {
            "title": "Translation Studies RSS",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "combined_feed": "feed.xml",
            "journal_count": len(stats),
            "item_count": len(combined_items),
            "translated_count": sum(stat["translated"] for stat in stats),
            "weak_abstract_count": sum(stat["weak_abstracts"] for stat in stats),
            "journals": stats,
            "errors": errors,
        },
        ensure_ascii=False,
        indent=2,
    )
    outputs["index.html"] = build_index(stats, errors, len(combined_items))
    if errors:
        print("Skipped feeds:")
        for error in errors:
            print(f"- {error}")
    return outputs


def weak_abstract(item):
    abstract = item.get("abstract") or item.get("fallback_description") or ""
    return not abstract or "No abstract found" in abstract or bool(re.match(r"^Volume \d+", abstract))


def build_index(stats, errors, item_count):
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    journal_rows = "\n".join(
        f"""
        <tr>
          <td>
            <strong>{html.escape(stat["title"])}</strong>
            <span>{html.escape(stat["publisher"])}</span>
          </td>
          <td>{stat["items"]}</td>
          <td>{stat["translated"]}</td>
          <td>{stat["weak_abstracts"]}</td>
          <td class="actions">
            <a class="icon-button" title="Open RSS feed" href="{html.escape(stat["feed"])}">RSS</a>
            <button class="icon-button" title="Copy feed URL" data-copy="{html.escape(stat["feed"])}">Copy</button>
            <a class="icon-button" title="Open journal site" href="{html.escape(stat["homepage"])}">Site</a>
          </td>
        </tr>
        """
        for stat in stats
    )
    errors_html = ""
    if errors:
        errors_html = "<section><h2>Build Notes</h2><ul>" + "".join(f"<li>{html.escape(error)}</li>" for error in errors) + "</ul></section>"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Translation Studies RSS</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #1f2933;
      --muted: #667085;
      --line: #d8dee7;
      --panel: #ffffff;
      --bg: #f5f7fa;
      --accent: #0b6f6a;
      --accent-ink: #ffffff;
      --warn: #a35200;
      --soft: #e7f4f2;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    header {{
      background: #ffffff;
      border-bottom: 1px solid var(--line);
    }}
    .wrap {{
      max-width: 1120px;
      margin: 0 auto;
      padding: 24px;
    }}
    h1 {{
      margin: 0 0 6px;
      font-size: 28px;
      letter-spacing: 0;
    }}
    h2 {{
      margin: 0 0 12px;
      font-size: 17px;
      letter-spacing: 0;
    }}
    p {{ margin: 0; color: var(--muted); }}
    .topline {{
      display: flex;
      gap: 12px;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
    }}
    .primary-actions {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }}
    .button, .icon-button {{
      appearance: none;
      border: 1px solid var(--line);
      background: #ffffff;
      color: var(--ink);
      border-radius: 6px;
      padding: 8px 10px;
      text-decoration: none;
      font: inherit;
      cursor: pointer;
      min-height: 36px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      white-space: nowrap;
    }}
    .button.primary {{
      border-color: var(--accent);
      background: var(--accent);
      color: var(--accent-ink);
    }}
    main.wrap {{
      display: grid;
      gap: 18px;
    }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
    }}
    .metric {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      min-height: 84px;
    }}
    .metric span {{
      display: block;
      color: var(--muted);
      font-size: 13px;
    }}
    .metric strong {{
      display: block;
      font-size: 26px;
      line-height: 1.2;
      margin-top: 6px;
    }}
    section {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
    }}
    .feed-url {{
      display: flex;
      gap: 8px;
      margin-top: 12px;
    }}
    .feed-url code {{
      flex: 1;
      min-width: 0;
      display: block;
      border: 1px solid var(--line);
      background: #f8fafc;
      border-radius: 6px;
      padding: 8px 10px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
    }}
    th, td {{
      border-bottom: 1px solid var(--line);
      padding: 11px 8px;
      text-align: left;
      vertical-align: middle;
    }}
    th {{
      color: var(--muted);
      font-weight: 600;
      font-size: 13px;
    }}
    td span {{
      display: block;
      color: var(--muted);
      font-size: 13px;
    }}
    tr:last-child td {{ border-bottom: 0; }}
    .actions {{
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
    }}
    .manage-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
    }}
    .manage-link {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      color: var(--ink);
      text-decoration: none;
      background: #ffffff;
    }}
    .manage-link strong {{ display: block; }}
    .manage-link span {{ color: var(--muted); font-size: 13px; }}
    .toast {{
      position: fixed;
      right: 16px;
      bottom: 16px;
      background: var(--ink);
      color: #ffffff;
      border-radius: 6px;
      padding: 10px 12px;
      opacity: 0;
      transform: translateY(8px);
      transition: opacity .16s ease, transform .16s ease;
      pointer-events: none;
    }}
    .toast.show {{
      opacity: 1;
      transform: translateY(0);
    }}
    @media (max-width: 760px) {{
      .wrap {{ padding: 18px; }}
      .stats, .manage-grid {{ grid-template-columns: 1fr 1fr; }}
      table, thead, tbody, tr, th, td {{ display: block; }}
      thead {{ display: none; }}
      tr {{ border-bottom: 1px solid var(--line); padding: 10px 0; }}
      tr:last-child {{ border-bottom: 0; }}
      td {{ border-bottom: 0; padding: 4px 0; }}
      .actions {{ padding-top: 8px; }}
    }}
    @media (max-width: 520px) {{
      .stats, .manage-grid {{ grid-template-columns: 1fr; }}
      .feed-url {{ flex-direction: column; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="wrap topline">
      <div>
        <h1>Translation Studies RSS</h1>
        <p>Enhanced journal feeds with cleaned metadata and optional Chinese abstracts.</p>
      </div>
      <div class="primary-actions">
        <a class="button primary" href="feed.xml">Combined RSS</a>
        <button class="button" data-copy="feed.xml">Copy URL</button>
      </div>
    </div>
  </header>
  <main class="wrap">
    <div class="stats">
      <div class="metric"><span>Journals</span><strong>{len(stats)}</strong></div>
      <div class="metric"><span>Items</span><strong>{item_count}</strong></div>
      <div class="metric"><span>Chinese Abstracts</span><strong>{sum(stat["translated"] for stat in stats)}</strong></div>
      <div class="metric"><span>Needs Metadata</span><strong>{sum(stat["weak_abstracts"] for stat in stats)}</strong></div>
    </div>
    <section>
      <h2>Subscribe</h2>
      <p>Last generated: {generated_at}</p>
      <div class="feed-url">
        <code id="combined-url">https://xionglingsong.github.io/rss-translation-studies/feed.xml</code>
        <button class="button" data-copy="feed.xml">Copy</button>
      </div>
    </section>
    <section>
      <h2>Journal Feeds</h2>
      <table>
        <thead>
          <tr>
            <th>Journal</th>
            <th>Items</th>
            <th>ZH</th>
            <th>Weak</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {journal_rows}
        </tbody>
      </table>
    </section>
    <section>
      <h2>Manage</h2>
      <div class="manage-grid">
        <a class="manage-link" href="https://github.com/xionglingsong/rss-translation-studies/edit/main/journals.json">
          <strong>Edit journals</strong>
          <span>Update sources in journals.json</span>
        </a>
        <a class="manage-link" href="https://github.com/xionglingsong/rss-translation-studies/actions/workflows/publish-feed.yml">
          <strong>Refresh feeds</strong>
          <span>Run the publish workflow</span>
        </a>
        <a class="manage-link" href="https://github.com/xionglingsong/rss-translation-studies/settings/secrets/actions">
          <strong>Translation key</strong>
          <span>Set DEEPSEEK_API_KEY</span>
        </a>
      </div>
    </section>
    {errors_html}
  </main>
  <div class="toast" id="toast">Copied</div>
  <script>
    const baseUrl = "https://xionglingsong.github.io/rss-translation-studies/";
    const toast = document.getElementById("toast");
    document.querySelectorAll("[data-copy]").forEach((button) => {{
      button.addEventListener("click", async () => {{
        const value = new URL(button.dataset.copy, baseUrl).href;
        await navigator.clipboard.writeText(value);
        toast.classList.add("show");
        setTimeout(() => toast.classList.remove("show"), 1400);
      }});
    }});
  </script>
</body>
</html>
"""


class FeedHandler(BaseHTTPRequestHandler):
    rendered = {}
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
        if parsed.path == "/health":
            self.send_text(200, "ok\n", "text/plain")
            return
        name = parsed.path.strip("/") or "feed.xml"
        if name.endswith(".xml") or name == "index.html":
            try:
                force = urllib.parse.parse_qs(parsed.query).get("refresh") == ["1"]
                if force or not self.rendered or time.time() - self.rendered_at > FEED_TTL_SECONDS:
                    self.__class__.rendered = generate_all_feeds()
                    self.__class__.rendered_at = time.time()
                body = self.rendered.get(name)
                if body is None:
                    self.send_text(404, "Not found\n", "text/plain")
                    return
                content_type = "text/html" if name == "index.html" else "application/rss+xml"
                self.send_text(200, body, content_type)
            except Exception as error:
                self.send_text(502, f"Feed proxy error: {error}\n", "text/plain")
            return
        self.send_text(404, "Not found\n", "text/plain")


def write_static_site(output_path):
    output_dir = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(output_dir, exist_ok=True)
    outputs = generate_all_feeds()
    for name, content in outputs.items():
        with open(os.path.join(output_dir, name), "w", encoding="utf-8") as file:
            file.write(content)
            file.write("\n")
    with open(os.path.join(output_dir, ".nojekyll"), "w", encoding="utf-8") as file:
        file.write("")
    print(f"Wrote {len(outputs)} files to {output_dir}")


def main():
    parser = argparse.ArgumentParser(description="Enhanced RSS feeds for translation studies journals")
    parser.add_argument("--once", metavar="PATH", help="write static RSS files into PATH's directory and exit")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    args = parser.parse_args()

    if args.once:
        write_static_site(args.once)
        return

    server = ThreadingHTTPServer((args.host, args.port), FeedHandler)
    print(f"RSS proxy running at http://{args.host}:{args.port}/feed.xml")
    server.serve_forever()


if __name__ == "__main__":
    main()
