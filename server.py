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
    outputs["index.html"] = build_public_index(stats, errors, len(combined_items))
    if errors:
        print("Skipped feeds:")
        for error in errors:
            print(f"- {error}")
    return outputs


def weak_abstract(item):
    abstract = item.get("abstract") or item.get("fallback_description") or ""
    return not abstract or "No abstract found" in abstract or bool(re.match(r"^Volume \d+", abstract))


def build_public_index(stats, errors, item_count):
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    weak_total = sum(stat["weak_abstracts"] for stat in stats)
    translated_total = sum(stat["translated"] for stat in stats)
    journal_rows = []
    for stat in stats:
        quality_class = "good" if stat["weak_abstracts"] == 0 else "watch"
        quality_label = "完整" if stat["weak_abstracts"] == 0 else f"{stat['weak_abstracts']} 条待补"
        journal_rows.append(
            f"""
            <tr>
              <td>
                <strong>{html.escape(stat["title"])}</strong>
                <span>{html.escape(stat["publisher"])}</span>
              </td>
              <td><span class="number">{stat["items"]}</span></td>
              <td><span class="number">{stat["translated"]}</span></td>
              <td><span class="quality {quality_class}">{quality_label}</span></td>
              <td class="actions-cell">
                <div class="actions">
                  <a class="icon-button" title="打开单刊 RSS" href="{html.escape(stat["feed"])}" aria-label="打开单刊 RSS">RSS</a>
                  <button class="icon-button" title="复制单刊订阅地址" data-copy="{html.escape(stat["feed"])}" aria-label="复制单刊订阅地址">Copy</button>
                  <a class="icon-button" title="打开期刊网站" href="{html.escape(stat["homepage"])}" aria-label="打开期刊网站">Site</a>
                </div>
              </td>
            </tr>
            """
        )
    errors_html = ""
    if errors:
        errors_html = (
            '<section class="notice"><h2>生成提示</h2><ul>'
            + "".join(f"<li>{html.escape(error)}</li>" for error in errors)
            + "</ul></section>"
        )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>翻译学期刊 RSS 订阅</title>
  <meta name="description" content="翻译学与口译研究期刊的增强 RSS 订阅源，自动汇总最新文章并补全摘要、作者、DOI 和页码。">
  <style>
    :root {{
      color-scheme: light;
      --ink: #243036;
      --muted: #68757b;
      --line: #d9d2c2;
      --panel: #fffdf7;
      --panel-strong: #ffffff;
      --bg: #f4efe4;
      --accent: #087f73;
      --accent-ink: #ffffff;
      --ruby: #a6384b;
      --blue: #315f8f;
      --gold: #b57919;
      --soft: #e9f4f1;
      --paper: #fbf8ef;
      --shadow: 0 18px 48px rgba(63, 52, 34, .12);
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      background:
        linear-gradient(90deg, rgba(36, 48, 54, .035) 1px, transparent 1px),
        linear-gradient(rgba(36, 48, 54, .025) 1px, transparent 1px),
        var(--bg);
      background-size: 34px 34px;
      color: var(--ink);
      font: 15px/1.55 "Iowan Old Style", "Palatino Linotype", Palatino, "Songti SC", serif;
    }}
    a {{ color: inherit; }}
    code {{ font: 13px/1.4 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
    header.hero {{
      background:
        linear-gradient(135deg, rgba(8, 127, 115, .12), transparent 42%),
        linear-gradient(315deg, rgba(166, 56, 75, .11), transparent 38%),
        var(--paper);
      border-bottom: 1px solid var(--line);
    }}
    .wrap {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 26px;
    }}
    h1 {{
      margin: 0;
      font-size: clamp(36px, 6vw, 76px);
      line-height: .96;
      letter-spacing: 0;
    }}
    h2 {{
      margin: 0 0 14px;
      font-size: 22px;
      line-height: 1.15;
      letter-spacing: 0;
    }}
    h3 {{
      margin: 0 0 6px;
      font-size: 16px;
      letter-spacing: 0;
    }}
    p {{ margin: 0; color: var(--muted); }}
    .eyebrow {{
      display: inline-flex;
      gap: 8px;
      align-items: center;
      margin-bottom: 16px;
      color: var(--accent);
      font: 700 13px/1.2 ui-sans-serif, system-ui, sans-serif;
      text-transform: uppercase;
      letter-spacing: .08em;
    }}
    .rss-mark {{
      width: 13px;
      height: 13px;
      border: 2px solid var(--accent);
      border-left-color: transparent;
      border-bottom-color: transparent;
      border-radius: 50%;
      position: relative;
      display: inline-block;
    }}
    .rss-mark::after {{
      content: "";
      position: absolute;
      left: -2px;
      bottom: -2px;
      width: 4px;
      height: 4px;
      background: var(--accent);
      border-radius: 50%;
    }}
    .topline {{
      display: grid;
      grid-template-columns: minmax(0, 1.15fr) minmax(320px, .85fr);
      gap: 28px;
      align-items: center;
      min-height: 420px;
      padding-top: 18px;
      padding-bottom: 22px;
    }}
    .hero-copy {{ max-width: 720px; }}
    .hero-copy p {{
      max-width: 680px;
      margin-top: 18px;
      color: #46545a;
      font-size: 18px;
    }}
    .primary-actions {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 24px;
    }}
    .button, .icon-button {{
      appearance: none;
      border: 1px solid var(--line);
      background: var(--panel-strong);
      color: var(--ink);
      border-radius: 6px;
      padding: 9px 12px;
      text-decoration: none;
      font: 15px/1.2 ui-sans-serif, system-ui, sans-serif;
      cursor: pointer;
      min-height: 38px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      white-space: nowrap;
    }}
    .button.primary {{
      border-color: var(--accent);
      background: var(--accent);
      color: var(--accent-ink);
      box-shadow: 0 10px 22px rgba(8, 127, 115, .18);
    }}
    .button:hover, .icon-button:hover, .client-link:hover, .manage-link:hover {{
      transform: translateY(-1px);
      transition: transform .14s ease, border-color .14s ease;
      border-color: rgba(8, 127, 115, .5);
    }}
    .subscribe-panel {{
      background: var(--panel-strong);
      border: 1px solid var(--line);
      box-shadow: var(--shadow);
      border-radius: 8px;
      padding: 18px;
    }}
    .subscribe-panel h2 {{
      font-size: 18px;
      margin-bottom: 8px;
    }}
    .feed-url {{
      display: flex;
      gap: 8px;
      margin-top: 14px;
    }}
    .feed-url code {{
      flex: 1;
      min-width: 0;
      display: block;
      border: 1px solid var(--line);
      background: #f8f3e9;
      border-radius: 6px;
      padding: 10px 11px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .mini-note {{
      margin-top: 10px;
      color: var(--muted);
      font-size: 13px;
      font-family: ui-sans-serif, system-ui, sans-serif;
    }}
    main.wrap {{
      display: grid;
      gap: 22px;
    }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
    }}
    .metric {{
      background: var(--panel-strong);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
      min-height: 92px;
    }}
    .metric span {{
      display: block;
      color: var(--muted);
      font: 12px/1.2 ui-sans-serif, system-ui, sans-serif;
      text-transform: uppercase;
      letter-spacing: .08em;
    }}
    .metric strong {{
      display: block;
      font-size: 30px;
      line-height: 1.2;
      margin-top: 8px;
    }}
    section {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 22px;
    }}
    .section-head {{
      display: flex;
      align-items: flex-end;
      justify-content: space-between;
      gap: 18px;
      margin-bottom: 16px;
    }}
    .section-head p {{ max-width: 560px; }}
    .steps, .clients {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
    }}
    .steps {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
    .step, .client-link, .source-note {{
      border: 1px solid var(--line);
      background: var(--panel-strong);
      border-radius: 8px;
      padding: 16px;
    }}
    .step strong {{
      width: 30px;
      height: 30px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      margin-bottom: 12px;
      border-radius: 50%;
      background: var(--ink);
      color: #fff;
      font-family: ui-sans-serif, system-ui, sans-serif;
    }}
    .client-link {{
      min-height: 138px;
      color: var(--ink);
      text-decoration: none;
      display: grid;
      align-content: start;
      gap: 8px;
    }}
    .client-link b {{
      color: var(--accent);
      font-family: ui-sans-serif, system-ui, sans-serif;
      font-size: 13px;
    }}
    .client-link span {{ color: var(--muted); }}
    .source-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
      font-family: ui-sans-serif, system-ui, sans-serif;
    }}
    th:nth-child(1), td:nth-child(1) {{ width: 53%; }}
    th:nth-child(2), td:nth-child(2),
    th:nth-child(3), td:nth-child(3) {{
      width: 7%;
      text-align: center;
    }}
    th:nth-child(4), td:nth-child(4) {{ width: 12%; }}
    th:nth-child(5), td:nth-child(5) {{ width: 21%; }}
    th, td {{
      border-bottom: 1px solid var(--line);
      padding: 12px 8px;
      text-align: left;
      vertical-align: middle;
    }}
    th {{
      color: var(--muted);
      font-weight: 600;
      font-size: 13px;
    }}
    td strong {{ font-size: 14px; }}
    td span {{
      display: block;
      color: var(--muted);
      font-size: 13px;
    }}
    .number {{
      color: var(--ink);
      font-weight: 700;
    }}
    .quality {{
      display: inline-flex;
      width: fit-content;
      border-radius: 999px;
      padding: 4px 9px;
      font-size: 12px;
      font-weight: 700;
    }}
    .quality.good {{
      background: var(--soft);
      color: var(--accent);
    }}
    .quality.watch {{
      background: #fff0d2;
      color: #8c5b00;
    }}
    tr:last-child td {{ border-bottom: 0; }}
    .actions-cell {{
      min-width: 248px;
    }}
    .actions {{
      display: flex;
      gap: 6px;
      flex-wrap: nowrap;
      align-items: center;
      justify-content: flex-start;
      min-width: max-content;
    }}
    .actions .icon-button {{
      min-width: 62px;
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
      background: var(--panel-strong);
    }}
    .manage-link strong {{ display: block; }}
    .manage-link span {{ color: var(--muted); font-size: 13px; }}
    .notice {{ border-color: #d6a53b; }}
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
      font-family: ui-sans-serif, system-ui, sans-serif;
    }}
    .toast.show {{
      opacity: 1;
      transform: translateY(0);
    }}
    @media (max-width: 900px) {{
      .topline {{ grid-template-columns: 1fr; min-height: auto; }}
      .stats, .clients, .source-grid {{ grid-template-columns: 1fr 1fr; }}
    }}
    @media (max-width: 760px) {{
      .wrap {{ padding: 18px; }}
      .steps, .manage-grid {{ grid-template-columns: 1fr; }}
      .section-head {{ display: block; }}
      .section-head p {{ margin-top: 8px; }}
      table, thead, tbody, tr, th, td {{ display: block; }}
      thead {{ display: none; }}
      tr {{ border-bottom: 1px solid var(--line); padding: 10px 0; }}
      tr:last-child {{ border-bottom: 0; }}
      td {{ border-bottom: 0; padding: 4px 0; }}
      .actions-cell {{ min-width: 0; }}
      .actions {{
        padding-top: 8px;
        flex-wrap: wrap;
        min-width: 0;
      }}
    }}
    @media (max-width: 520px) {{
      .stats, .clients, .source-grid {{ grid-template-columns: 1fr; }}
      .feed-url {{ flex-direction: column; }}
      h1 {{ font-size: 40px; }}
    }}
  </style>
</head>
<body>
  <header class="hero">
    <div class="wrap topline">
      <div class="hero-copy">
        <div class="eyebrow"><span class="rss-mark" aria-hidden="true"></span> Translation Studies RSS</div>
        <h1>翻译学期刊更新，一处订阅。</h1>
        <p>这个页面把翻译学、口译研究、本地化、视听翻译等相关期刊的最新文章汇总成一个增强 RSS。订阅后，新文章会自动出现在你的阅读器里，尽量附带作者、摘要、DOI、页码和中文摘要。</p>
        <div class="primary-actions">
          <a class="button primary" href="feed.xml">打开总 RSS</a>
          <button class="button" data-copy="feed.xml">复制订阅地址</button>
          <a class="button" href="#journals">查看期刊</a>
        </div>
      </div>
      <div class="subscribe-panel" id="subscribe">
        <h2>总订阅地址</h2>
        <p>把这个链接添加到任意 RSS 阅读器即可。已有订阅不用改地址，新增期刊会自动进入总 feed。</p>
        <div class="feed-url">
          <code id="combined-url">https://xionglingsong.github.io/rss-translation-studies/feed.xml</code>
          <button class="button primary" data-copy="feed.xml">复制</button>
        </div>
        <div class="mini-note">最近生成：{generated_at}。GitHub Actions 每 6 小时自动刷新。</div>
      </div>
    </div>
  </header>
  <main class="wrap">
    <div class="stats">
      <div class="metric"><span>收录期刊</span><strong>{len(stats)}</strong></div>
      <div class="metric"><span>最新条目</span><strong>{item_count}</strong></div>
      <div class="metric"><span>中文摘要</span><strong>{translated_total}</strong></div>
      <div class="metric"><span>待补摘要</span><strong>{weak_total}</strong></div>
    </div>

    <section>
      <div class="section-head">
        <h2>怎么订阅</h2>
        <p>RSS 的好处是不用每天打开十几个期刊网站，也不用等社交媒体推送。阅读器会替你定时检查更新。</p>
      </div>
      <div class="steps">
        <div class="step">
          <strong>1</strong>
          <h3>复制总订阅地址</h3>
          <p>点击页面上的“复制订阅地址”，或直接复制 <code>feed.xml</code> 的完整链接。</p>
        </div>
        <div class="step">
          <strong>2</strong>
          <h3>打开 RSS 阅读器</h3>
          <p>在阅读器里选择 Add Feed、New Subscription 或 Subscribe。</p>
        </div>
        <div class="step">
          <strong>3</strong>
          <h3>粘贴并确认</h3>
          <p>保存后就能在同一个列表里看到各期刊的新文章。想只看某本期刊，也可以订阅下方单刊 RSS。</p>
        </div>
      </div>
    </section>

    <section>
      <div class="section-head">
        <h2>推荐客户端</h2>
        <p>下面按使用场景推荐。它们都支持添加 RSS 链接；选一个你会长期打开的就好。</p>
      </div>
      <div class="clients">
        <a class="client-link" href="https://netnewswire.com/">
          <b>Mac / iPhone / iPad</b>
          <h3>NetNewsWire</h3>
          <span>免费、开源、轻量，苹果设备上最省心的选择。</span>
        </a>
        <a class="client-link" href="https://feedly.com/news-reader">
          <b>Web / iOS / Android</b>
          <h3>Feedly</h3>
          <span>跨平台同步方便，适合已经有较多信息源的用户。</span>
        </a>
        <a class="client-link" href="https://www.inoreader.com/">
          <b>Web / iOS / Android</b>
          <h3>Inoreader</h3>
          <span>过滤、规则和整理能力强，适合精细管理订阅。</span>
        </a>
        <a class="client-link" href="https://readwise.io/read">
          <b>深度阅读</b>
          <h3>Readwise Reader</h3>
          <span>把 RSS、稍后读、PDF、批注放在一起，适合做文献跟踪。</span>
        </a>
      </div>
    </section>

    <section>
      <div class="section-head">
        <h2>这个 RSS 做了什么</h2>
        <p>有些期刊官方 RSS 信息很少，所以这里会在公开数据范围内做增强。</p>
      </div>
      <div class="source-grid">
        <div class="source-note">
          <h3>合并和清洗</h3>
          <p>统一多个出版社和开放期刊系统的 RSS，按发布日期排序，保留每篇文章的原文链接。</p>
        </div>
        <div class="source-note">
          <h3>补全元数据</h3>
          <p>当官方 RSS 缺少摘要、DOI、页码时，会尝试从 DOI 数据库或文章页公开 meta 信息中补全。</p>
        </div>
        <div class="source-note">
          <h3>中文摘要</h3>
          <p>部署环境配置翻译 API 后，会为可用英文摘要生成中文摘要，便于快速扫读。</p>
        </div>
        <div class="source-note">
          <h3>透明状态</h3>
          <p>期刊表里显示每个源的条目数和摘要完整度。Editorial、书评等可能本来就没有摘要。</p>
        </div>
      </div>
    </section>

    <section id="journals">
      <div class="section-head">
        <h2>期刊列表</h2>
        <p>可订阅总 RSS，也可只订阅单本期刊。单刊按钮会复制对应 XML 地址。</p>
      </div>
      <table>
        <thead>
          <tr>
            <th>期刊</th>
            <th>条目</th>
            <th>中文</th>
            <th>摘要状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {"".join(journal_rows)}
        </tbody>
      </table>
    </section>

    <section>
      <div class="section-head">
        <h2>维护入口</h2>
        <p>这个项目托管在 GitHub Pages，上游源变化时可以在仓库里调整配置并手动刷新。</p>
      </div>
      <div class="manage-grid">
        <a class="manage-link" href="https://github.com/xionglingsong/rss-translation-studies/edit/main/journals.json">
          <strong>编辑期刊源</strong>
          <span>更新 journals.json</span>
        </a>
        <a class="manage-link" href="https://github.com/xionglingsong/rss-translation-studies/actions/workflows/publish-feed.yml">
          <strong>刷新 feed</strong>
          <span>运行发布 workflow</span>
        </a>
        <a class="manage-link" href="https://github.com/xionglingsong/rss-translation-studies/settings/secrets/actions">
          <strong>翻译密钥</strong>
          <span>设置 DEEPSEEK_API_KEY</span>
        </a>
      </div>
    </section>
    {errors_html}
  </main>
  <div class="toast" id="toast" role="status" aria-live="polite">已复制订阅地址</div>
  <script>
    const baseUrl = "https://xionglingsong.github.io/rss-translation-studies/";
    const toast = document.getElementById("toast");
    document.querySelectorAll("[data-copy]").forEach((button) => {{
      button.addEventListener("click", async () => {{
        const value = new URL(button.dataset.copy, baseUrl).href;
        try {{
          await navigator.clipboard.writeText(value);
        }} catch (error) {{
          const helper = document.createElement("textarea");
          helper.value = value;
          document.body.appendChild(helper);
          helper.select();
          document.execCommand("copy");
          helper.remove();
        }}
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
