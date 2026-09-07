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
from datetime import datetime, timedelta, timezone
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
JINA_READER_API_KEY = os.environ.get("JINA_READER_API_KEY", "")
TRANSLATION_PROMPT = (
    "作为一名精通简体中文、口笔译学科知识、统计学、心理学的专业翻译家，"
    "请将所提供的文本准确地翻译为简体中文。请仅回复翻译后的文本，不要任何其他内容。"
    "【待翻译文本】如下"
)
METADATA_TTL_SECONDS = 60 * 60 * 24 * 30
NEGATIVE_METADATA_TTL_SECONDS = 60 * 60 * 24
FEED_TTL_SECONDS = 60 * 30
MAX_WORKERS = int(os.environ.get("RSS_MAX_WORKERS", "8"))
MAX_ITEMS_PER_SOURCE = int(os.environ.get("RSS_MAX_ITEMS_PER_SOURCE", "8"))
WEEKLY_DAYS = int(os.environ.get("RSS_WEEKLY_DAYS", "7"))
WEEKLY_FALLBACK_ITEMS = int(os.environ.get("RSS_WEEKLY_FALLBACK_ITEMS", "20"))
NEW_ITEM_HISTORY_WINDOWS = (7, 30)
OBSIDIAN_WEEKLY_DIR = os.environ.get(
    "RSS_OBSIDIAN_WEEKLY_DIR",
    "/Users/lingsongxiong/Nutstore Files/Obsidian/claudesidian-0.13.1/01_Projects/译了么/公众号/RSS",
)

TOPICS = [
    {
        "slug": "general-translation-studies",
        "label": "综合翻译学",
        "description": "翻译理论、翻译实践、翻译史与跨学科翻译研究。",
    },
    {
        "slug": "interpreting",
        "label": "口译研究",
        "description": "会议口译、社区口译、口译实践与口译社会研究。",
    },
    {
        "slug": "translator-education",
        "label": "译者教育",
        "description": "翻译与口译教学、课程设计、训练方法与评估。",
    },
    {
        "slug": "society-culture",
        "label": "社会文化",
        "description": "翻译社会学、跨文化传播、多语环境与区域研究。",
    },
    {
        "slug": "cognition-process",
        "label": "认知过程",
        "description": "翻译认知、行为实验、过程研究与译者决策。",
    },
    {
        "slug": "digital-ai-translation",
        "label": "数字与 AI 翻译",
        "description": "机器翻译、本地化、数字媒介与技术驱动的翻译实践。",
    },
    {
        "slug": "audiovisual-translation",
        "label": "视听翻译",
        "description": "字幕、配音、无障碍传播、屏幕翻译与多模态翻译。",
    },
    {
        "slug": "terminology-specialized-translation",
        "label": "术语与专门用途翻译",
        "description": "术语学、专门用途语言、专门翻译与知识传播。",
    },
]
TOPIC_BY_SLUG = {topic["slug"]: topic for topic in TOPICS}

ARTICLE_TYPES = [
    {
        "slug": "article",
        "label": "Article",
        "description": "研究论文、案例研究和理论论文。",
    },
    {
        "slug": "review",
        "label": "Review",
        "description": "综述、评论文章、论坛短论和回顾性文章。",
    },
    {
        "slug": "book-review",
        "label": "Book Review",
        "description": "书评、新书评论和相关评介。",
    },
]
ARTICLE_TYPE_BY_SLUG = {item_type["slug"]: item_type for item_type in ARTICLE_TYPES}

KEYWORD_ALERTS = [
    {
        "slug": "machine-translation-ai",
        "label": "机器翻译与 AI",
        "description": "机器翻译、神经网络、大语言模型与 AI 辅助翻译。",
        "terms": ("machine translation", "neural translation", "large language model", "generative ai", "ai-assisted", "translation technology", "机器翻译", "大语言模型", "人工智能"),
    },
    {
        "slug": "interpreting",
        "label": "口译",
        "description": "会议、社区、司法、医疗与手语口译。",
        "terms": ("interpreting", "interpreter", "conference interpreting", "community interpreting", "court interpreting", "medical interpreting", "sign language", "口译", "同声传译", "交替传译"),
    },
    {
        "slug": "audiovisual-translation",
        "label": "视听翻译",
        "description": "字幕、配音、无障碍传播和多模态翻译。",
        "terms": ("audiovisual translation", "subtitling", "dubbing", "audio description", "captioning", "screen translation", "视听翻译", "字幕", "配音"),
    },
    {
        "slug": "localization",
        "label": "本地化",
        "description": "软件、游戏、产品与国际化本地化研究。",
        "terms": ("localization", "localisation", "internationalization", "internationalisation", "game localization", "software localization", "本地化", "国际化"),
    },
]

TOPIC_KEYWORDS = {
    "interpreting": (
        "interpreting",
        "interpreter",
        "conference interpreting",
        "simultaneous",
        "consecutive",
        "dialogue interpreting",
        "community interpreting",
        "public service interpreting",
        "court interpreting",
        "healthcare interpreting",
        "medical interpreting",
        "signed language",
        "sign language",
        "口译",
        "传译",
        "同声传译",
        "交替传译",
    ),
    "translator-education": (
        "training",
        "trainer",
        "education",
        "teaching",
        "learning",
        "pedagogy",
        "curriculum",
        "classroom",
        "student",
        "trainee",
        "assessment",
        "competence",
        "didactic",
        "译者教育",
        "翻译教学",
        "口译教学",
        "课程",
        "能力",
    ),
    "society-culture": (
        "culture",
        "cultural",
        "sociology",
        "social",
        "society",
        "ideology",
        "gender",
        "race",
        "migration",
        "activism",
        "colonial",
        "postcolonial",
        "minority",
        "multilingual",
        "translanguaging",
        "intercultural",
        "diplomacy",
        "policy",
        "文化",
        "社会",
        "意识形态",
        "跨文化",
        "多语",
    ),
    "cognition-process": (
        "cognition",
        "cognitive",
        "process",
        "eye-tracking",
        "eye tracking",
        "keystroke",
        "think-aloud",
        "think aloud",
        "experiment",
        "experimental",
        "effort",
        "working memory",
        "attention",
        "decision-making",
        "reception",
        "认知",
        "眼动",
        "实验",
        "过程",
    ),
    "digital-ai-translation": (
        "machine translation",
        "neural machine translation",
        "automatic translation",
        "artificial intelligence",
        "generative ai",
        "large language model",
        "chatgpt",
        "post-editing",
        "postediting",
        "localization",
        "localisation",
        "corpus",
        "digital",
        "technology",
        "automatic dubbing",
        "机器翻译",
        "人工智能",
        "大语言模型",
        "本地化",
        "译后编辑",
        "语料库",
    ),
    "audiovisual-translation": (
        "audiovisual",
        "subtitling",
        "subtitle",
        "caption",
        "dubbing",
        "voice-over",
        "audio description",
        "media accessibility",
        "fansubbing",
        "screen translation",
        "multimodal",
        "视听翻译",
        "字幕",
        "配音",
        "无障碍",
        "多模态",
    ),
    "terminology-specialized-translation": (
        "terminology",
        "term",
        "specialised",
        "specialized",
        "legal translation",
        "medical translation",
        "technical translation",
        "scientific translation",
        "institutional translation",
        "domain-specific",
        "lsp",
        "专门用途",
        "术语",
        "法律翻译",
        "医学翻译",
        "科技翻译",
        "机构翻译",
    ),
    "general-translation-studies": (
        "translation",
        "translator",
        "translating",
        "translation studies",
        "theory",
        "history",
        "literary translation",
        "translation theory",
        "翻译",
        "译者",
        "翻译学",
        "翻译理论",
        "翻译史",
    ),
}

TYPE_KEYWORDS = {
    "book-review": (
        "book review",
        "book reviews",
        "reviews of ",
        "books received",
        "new books",
        "书评",
    ),
    "review": (
        "review article",
        "literature review",
        "systematic review",
        "scoping review",
        "state of the art",
        "state-of-the-art",
        "review essay",
        "survey of ",
        "a review of ",
        "综述",
        "述评",
    ),
}

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "prism": "http://prismstandard.org/namespaces/basic/2.0/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rss1": "http://purl.org/rss/1.0/",
}


def fetch_text(url, accept="*/*", timeout=5, attempts=3, use_curl=True, headers=None):
    request_headers = {
        "Accept": accept,
        "User-Agent": USER_AGENT,
        **(headers or {}),
    }
    request = urllib.request.Request(
        url,
        headers=request_headers,
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
            curl_headers = []
            for name, value in request_headers.items():
                curl_headers.extend(["-H", f"{name}: {value}"])
            completed = subprocess.run(
                ["curl", "-fsSL", "--max-time", str(timeout), "-A", USER_AGENT, *curl_headers, url],
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
    journals = load_json(CONFIG_PATH, [])
    for journal in journals:
        tags = journal.get("tags", [])
        if not tags:
            raise ValueError(f"Missing topic tags for {journal.get('slug', 'unknown')}")
        unknown_tags = [tag for tag in tags if tag not in TOPIC_BY_SLUG]
        if unknown_tags:
            raise ValueError(f"Unknown topic tags for {journal.get('slug', 'unknown')}: {', '.join(unknown_tags)}")
    return journals


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


def clean_abstract_text(value):
    value = re.sub(r"\[[^\]]+\]\([^)]+\)", "", value or "")
    value = strip_html(value)
    value = re.split(
        r"\b(?:KEYWORDS?|Keywords?|Introduction|Disclosure statement|Acknowledgements|References)\s*:",
        value,
        maxsplit=1,
    )[0]
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def is_truncated_abstract(value):
    return bool(re.search(r"(\.\.\.|…)\s*$", value or ""))


def is_boilerplate_abstract(value):
    lowered = (value or "").lower()
    boilerplate_terms = (
        "citation & references download citations",
        "your download is now in progress",
        "with a free taylor & francis online account",
        "save your searches and schedule alerts",
        "copyright ©",
    )
    return any(term in lowered for term in boilerplate_terms)


def usable_abstract(value):
    return bool(value) and not is_truncated_abstract(value) and not is_boilerplate_abstract(value)


def source_type_label(source_type):
    labels = {
        "crossref": "Crossref",
        "rss": "官方 RSS",
    }
    return labels.get(source_type or "rss", source_type or "RSS")


def topic_feed_name(slug):
    return f"topic-{slug}.xml"


def type_feed_name(slug):
    return f"type-{slug}.xml"


def keyword_alert_feed_name(slug):
    return f"alert-{slug}.xml"


def new_topic_feed_name(slug):
    return f"new-topic-{slug}.xml"


def topic_labels(tags):
    return [TOPIC_BY_SLUG[tag]["label"] for tag in tags if tag in TOPIC_BY_SLUG]


def article_type_label(slug):
    return ARTICLE_TYPE_BY_SLUG.get(slug, ARTICLE_TYPE_BY_SLUG["article"])["label"]


def normalized_item_text(item):
    values = [
        item.get("title", ""),
        item.get("abstract", ""),
        "" if weak_abstract(item) else item.get("fallback_description", ""),
    ]
    return " ".join(values).lower()


def keyword_alert_matches(item, terms):
    searchable = " ".join(
        [
            normalized_item_text(item),
            item.get("creator", ""),
            item.get("source_title", ""),
            item.get("journal", ""),
            item.get("doi", ""),
        ]
    ).lower()
    return any(term.lower() in searchable for term in terms)


RELATED_TERM_STOPWORDS = frozenset(
    {
        "about", "across", "analysis", "approach", "article", "between", "case", "cases", "context",
        "discourse", "english", "evidence", "exploring", "from", "implications", "language", "languages",
        "research", "results", "social", "studies", "study", "theory", "translation", "translations",
        "translating", "translator", "translators", "using", "within", "with",
    }
)


def related_terms(item):
    text = " ".join([item.get("title", ""), item.get("abstract", ""), item.get("fallback_description", "")]).lower()
    return {
        term
        for term in re.findall(r"[a-z][a-z-]{4,}", text)
        if term not in RELATED_TERM_STOPWORDS
    }


def related_item_score(item, candidate, item_terms=None, candidate_terms=None):
    item_terms = related_terms(item) if item_terms is None else item_terms
    candidate_terms = related_terms(candidate) if candidate_terms is None else candidate_terms
    shared_terms = item_terms & candidate_terms
    shared_topics = set(item.get("source_tags", [])) & set(candidate.get("source_tags", []))
    same_author = bool(item.get("creator") and item.get("creator").strip().lower() == candidate.get("creator", "").strip().lower())
    same_source = item.get("source_slug") and item.get("source_slug") == candidate.get("source_slug")
    score = min(len(shared_terms), 4) * 2 + len(shared_topics) * 2 + (5 if same_author else 0) + (1 if same_source else 0)
    reasons = []
    if same_author:
        reasons.append("同一作者")
    if shared_topics:
        reasons.append("共同方向：" + "、".join(topic_labels(sorted(shared_topics))))
    if shared_terms:
        reasons.append("共同关键词：" + "、".join(sorted(shared_terms)[:3]))
    return score, reasons


def find_related_papers(items, limit=3):
    related = {}
    item_terms = [related_terms(item) for item in items]
    for index, item in enumerate(items):
        recommendations = []
        for candidate_index, candidate in enumerate(items):
            if index == candidate_index:
                continue
            score, reasons = related_item_score(item, candidate, item_terms[index], item_terms[candidate_index])
            if score >= 3:
                recommendations.append((score, parse_date(candidate.get("date")), candidate, reasons))
        recommendations.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
        related[index] = [{"item": candidate, "reasons": reasons} for _, _, candidate, reasons in recommendations[:limit]]
    return related


def score_topic(text, keywords):
    score = 0
    for keyword in keywords:
        keyword_lower = keyword.lower()
        if " " in keyword_lower or "-" in keyword_lower:
            if keyword_lower in text:
                score += 3
        else:
            score += len(re.findall(rf"(?<![a-z0-9]){re.escape(keyword_lower)}(?![a-z0-9])", text))
    return score


def classify_item_topics(item, fallback_tags):
    text = normalized_item_text(item)
    scored = []
    for slug, keywords in TOPIC_KEYWORDS.items():
        score = score_topic(text, keywords)
        if score > 0:
            scored.append((score, slug))
    if not scored:
        return list(fallback_tags or ["general-translation-studies"])
    scored.sort(key=lambda pair: (-pair[0], TOPICS.index(TOPIC_BY_SLUG[pair[1]])))
    topics = [slug for _, slug in scored[:3]]
    if "general-translation-studies" in topics and len(topics) > 1:
        topics = [slug for slug in topics if slug != "general-translation-studies"]
    return topics or ["general-translation-studies"]


def classify_item_type(item):
    title = (item.get("title") or "").lower()
    doi = (item.get("doi") or "").lower()
    if re.search(r"\.r\d+$", doi):
        return "book-review"
    if title.startswith("review of "):
        return "book-review"
    for keyword in TYPE_KEYWORDS["book-review"]:
        if keyword in title:
            return "book-review"
    for keyword in TYPE_KEYWORDS["review"]:
        if keyword in title:
            return "review"
    return "article"


def classify_item(item, fallback_tags):
    topics = classify_item_topics(item, fallback_tags)
    item["article_topics"] = topics
    item["source_tags"] = topics
    item["topic_labels"] = topic_labels(topics)
    item["journal_tags"] = list(fallback_tags or [])
    item["item_type"] = classify_item_type(item)
    item["item_type_label"] = article_type_label(item["item_type"])
    return item


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


def text_from_element(element):
    parts = []
    for node in element.iter():
        if local_name(node.tag).lower() == "title":
            continue
        if node.text:
            parts.append(node.text)
        if node.tail:
            parts.append(node.tail)
    return strip_html(" ".join(parts))


def abstract_from_xml(xml_text):
    try:
        root = ET.fromstring(xml_text.lstrip("\ufeff"))
    except ET.ParseError:
        return ""
    abstracts = []
    for element in root.iter():
        if local_name(element.tag).lower() == "abstract":
            text = clean_abstract_text(text_from_element(element))
            if text and len(text.split()) > 20:
                abstracts.append(text)
    return max(abstracts, key=len) if abstracts else ""


def markdown_lines_to_abstract(lines, start_index):
    section = []
    for raw_line in lines[start_index + 1 :]:
        line = raw_line.strip()
        if not line:
            if section:
                continue
            continue
        lowered = re.sub(r"^[#*\s]+", "", line).strip().lower()
        if lowered in ("keywords", "keyword", "introduction", "disclosure statement", "acknowledgements", "references"):
            break
        if re.match(r"^#{1,4}\s+\S", line) and section:
            break
        if line.startswith(("!", "|")) or line.lower().startswith(("image ", "table ")):
            continue
        section.append(line)
    abstract = clean_abstract_text(" ".join(section))
    abstract = re.sub(r"\s+", " ", abstract).strip()
    return abstract if len(abstract.split()) > 20 else ""


def abstract_from_reader_markdown(markdown):
    lines = markdown.splitlines()
    for index, raw_line in enumerate(lines):
        normalized = re.sub(r"^[#*\s]+", "", raw_line).strip().lower()
        if normalized == "abstract":
            abstract = markdown_lines_to_abstract(lines, index)
            if abstract:
                return abstract
    return ""


def cached_remote_metadata(cache, bucket, url, fetcher):
    page_cache = cache.setdefault(bucket, {})
    cached = page_cache.get(url)
    cached_metadata = (cached or {}).get("metadata") or {}
    cached_ttl = METADATA_TTL_SECONDS if cached_metadata else NEGATIVE_METADATA_TTL_SECONDS
    if cached and time.time() - cached.get("fetched_at", 0) < cached_ttl:
        return cached.get("metadata") or {}
    metadata = fetcher()
    page_cache[url] = {"metadata": metadata, "fetched_at": time.time()}
    return metadata


def xml_page_metadata(url, cache):
    def fetcher():
        try:
            xml_text = fetch_text(url, accept="application/xml,text/xml,*/*", timeout=5, attempts=1)
        except Exception:
            return {}
        abstract = abstract_from_xml(xml_text)
        return {"abstract": abstract, "abstract_source": "tandfonline-xml"} if usable_abstract(abstract) else {}

    return cached_remote_metadata(cache, "_xml_pages", url, fetcher)


def reader_page_metadata(url, cache):
    reader_url = f"http://r.jina.ai/{url}"

    def fetcher():
        headers = {"X-Return-Format": "markdown"}
        if JINA_READER_API_KEY:
            headers["Authorization"] = f"Bearer {JINA_READER_API_KEY}"
        try:
            markdown = fetch_text(reader_url, accept="text/plain,*/*", timeout=8, attempts=1, headers=headers)
        except Exception:
            return {}
        abstract = abstract_from_reader_markdown(markdown)
        return {"abstract": abstract, "abstract_source": "tandfonline-reader"} if usable_abstract(abstract) else {}

    return cached_remote_metadata(cache, "_reader_pages", reader_url, fetcher)


def tandfonline_urls(item):
    doi = item.get("doi") or extract_doi(item.get("link", ""))
    urls = []
    for url in item.get("metadata_links", []):
        if "tandfonline.com/doi/full-xml/" in url and url not in urls:
            urls.append(url)
    if doi:
        encoded_doi = urllib.parse.quote(doi, safe="/")
        xml_url = f"https://www.tandfonline.com/doi/full-xml/{encoded_doi}"
        page_url = f"https://www.tandfonline.com/doi/full/{encoded_doi}"
        for url in (xml_url, page_url):
            if url not in urls:
                urls.append(url)
    if item.get("link") and item["link"] not in urls:
        urls.append(item["link"])
    return urls


def enrich_tandfonline_abstract(item, cache):
    if not weak_abstract(item):
        return item
    urls = tandfonline_urls(item)
    for url in urls:
        if "/doi/full-xml/" not in url:
            continue
        metadata = xml_page_metadata(url, cache)
        if usable_abstract(metadata.get("abstract", "")):
            merge_missing(item, metadata)
            return item
    for url in urls:
        if "/doi/full-xml/" in url:
            continue
        metadata = reader_page_metadata(url, cache)
        if usable_abstract(metadata.get("abstract", "")):
            merge_missing(item, metadata)
            return item
    return item


def safe_enrich_tandfonline_abstract(item, cache):
    try:
        return enrich_tandfonline_abstract(item, cache)
    except Exception:
        return item


def merge_missing(item, metadata):
    for key in ("title", "date", "journal", "volume", "issue", "pages"):
        if not item.get(key) and metadata.get(key):
            item[key] = metadata[key]
    if not item.get("doi") and metadata.get("doi"):
        item["doi"] = metadata["doi"]
    if metadata.get("creator") and should_replace_creator(item.get("creator", "")):
        item["creator"] = metadata["creator"]
    if usable_abstract(metadata.get("abstract", "")):
        item["abstract"] = metadata["abstract"]
        if metadata.get("abstract_source"):
            item["abstract_source"] = metadata["abstract_source"]


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


def parse_feed_xml(xml_text, source):
    root = ET.fromstring(xml_text.lstrip("\ufeff"))
    root_name = local_name(root.tag)
    if root_name == "RDF":
        return parse_rss1(root, source)
    if root_name == "rss":
        return parse_rss2(root, source)
    if root_name == "feed":
        return parse_atom(root, source)
    raise ValueError(f"Unsupported feed format for {source['slug']}: {root.tag}")


def parse_source_feed(source):
    if source.get("source_type") == "crossref":
        return parse_crossref_source(source)
    xml_text = fetch_text(source["source_feed"], accept="application/rss+xml, application/atom+xml, application/xml, text/xml")
    return parse_feed_xml(xml_text, source)


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
        if is_truncated_abstract(abstract):
            abstract = ""
        link = work.get("URL") or (f"https://doi.org/{doi}" if doi else source["homepage"])
        metadata_links = [
            link_data.get("URL")
            for link_data in work.get("link") or []
            if link_data.get("URL") and "tandfonline.com/doi/full-xml/" in link_data.get("URL")
        ]
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
                "metadata_links": metadata_links,
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
    if item.get("discovered_at"):
        meta.append(f"<p><strong>First discovered:</strong> {html.escape(item['discovered_at'])}</p>")
    if item.get("publication_date"):
        meta.append(f"<p><strong>Published:</strong> {html.escape(item['publication_date'])}</p>")
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
    if item.get("topic_labels"):
        meta.append(f"<p><strong>Topics:</strong> {html.escape(', '.join(item['topic_labels']))}</p>")
    if item.get("item_type_label"):
        meta.append(f"<p><strong>Type:</strong> {html.escape(item['item_type_label'])}</p>")
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
        categories = []
        if item.get("item_type_label"):
            categories.append(item["item_type_label"])
        categories.extend(item.get("topic_labels") or [])
        category_lines = [f"<category>{html.escape(category)}</category>" for category in categories]
        lines.extend(
            [
                "<item>",
                f"<title>{html.escape(title_text)}</title>",
                f"<link>{html.escape(item.get('link') or '')}</link>",
                f"<guid isPermaLink=\"false\">{html.escape(guid)}</guid>",
                f"<pubDate>{pub_date}</pubDate>",
                *category_lines,
                f"<description>{cdata(body)}</description>",
                f"<content:encoded>{cdata(body)}</content:encoded>",
                f"<dc:creator>{html.escape(item['creator'])}</dc:creator>" if item.get("creator") else "",
                "</item>",
            ]
        )
    lines.extend(["</channel>", "</rss>"])
    return "\n".join(line for line in lines if line)


def markdown_escape(value):
    return (value or "").replace("\n", " ").strip()


def markdown_summary(item):
    abstract = item.get("abstract_zh") or item.get("abstract") or item.get("fallback_description") or ""
    if weak_abstract(item):
        return "公开元数据中暂未获取到完整摘要。"
    return markdown_escape(abstract)


def truncate_text(value, limit=220):
    value = re.sub(r"\s+", " ", value or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "..."


def item_date_label(item):
    return parse_date(item.get("date")).strftime("%Y-%m-%d")


def item_identity(item):
    return item.get("doi") or item.get("link") or item.get("title")


def track_new_items(cache, items, generated_at):
    seen_items = cache.setdefault("_seen_items", {})
    recent_items = cache.setdefault("_recent_items", {})
    tracking_active = bool(seen_items)
    retention_cutoff = generated_at - timedelta(days=max(NEW_ITEM_HISTORY_WINDOWS))
    for key, entry in list(recent_items.items()):
        try:
            first_seen_at = datetime.fromisoformat(entry.get("first_seen_at", ""))
        except ValueError:
            first_seen_at = datetime.min.replace(tzinfo=timezone.utc)
        if first_seen_at < retention_cutoff:
            recent_items.pop(key, None)

    new_items = []
    for item in items:
        identity = item_identity(item)
        if not identity:
            continue
        key = hashlib.sha256(f"{item.get('source_slug', '')}\0{identity}".encode("utf-8")).hexdigest()
        entry = seen_items.get(key)
        if entry is None:
            entry = {"first_seen_at": generated_at.isoformat()}
            seen_items[key] = entry
            if tracking_active:
                new_items.append(item)
                recent_items[key] = {"first_seen_at": entry["first_seen_at"], "item": item}
        item["first_seen_at"] = entry["first_seen_at"]
    return new_items, tracking_active


def recent_discovery_items(cache, generated_at, days):
    cutoff = generated_at - timedelta(days=days)
    recent_items = cache.get("_recent_items", {})
    items = []
    for entry in recent_items.values():
        try:
            first_seen_at = datetime.fromisoformat(entry.get("first_seen_at", ""))
        except ValueError:
            continue
        item = entry.get("item")
        if not isinstance(item, dict) or first_seen_at < cutoff:
            continue
        history_item = dict(item)
        history_item["publication_date"] = history_item.get("date")
        history_item["date"] = entry["first_seen_at"]
        history_item["discovered_at"] = entry["first_seen_at"]
        items.append(history_item)
    return items


def item_primary_topic(item):
    for tag in item.get("source_tags", []):
        if tag in TOPIC_BY_SLUG:
            return TOPIC_BY_SLUG[tag]["label"]
    return "最新文章"


def weekly_items(combined_items, generated_at):
    sorted_items = sorted(combined_items, key=lambda item: parse_date(item.get("date")), reverse=True)
    cutoff = generated_at - timedelta(days=WEEKLY_DAYS)
    recent_items = [item for item in sorted_items if parse_date(item.get("date")) >= cutoff]
    if recent_items:
        return recent_items, f"最近 {WEEKLY_DAYS} 天"
    return sorted_items[:WEEKLY_FALLBACK_ITEMS], f"最近 {WEEKLY_FALLBACK_ITEMS} 条"


def build_weekly_markdown(combined_items, topic_stats, generated_at):
    selected_items, scope_label = weekly_items(combined_items, generated_at)
    generated_label = generated_at.strftime("%Y-%m-%d %H:%M UTC")
    date_slug = generated_at.strftime("%Y-%m-%d")
    lines = [
        f"# 翻译学新论文速递：{date_slug}",
        "",
        f"> 由 Translation Studies RSS 自动生成。范围：{scope_label}；生成时间：{generated_label}。",
        "",
        "订阅入口：https://xionglingsong.github.io/rss-translation-studies/",
        "",
        f"本期共收录 {len(selected_items)} 条新近条目，来自 {len({item.get('source_slug') for item in selected_items})} 本期刊。",
        "",
    ]
    if not selected_items:
        lines.extend(["本期暂未检测到新条目。", ""])
        return "\n".join(lines).strip() + "\n"

    used_ids = set()
    for topic in topic_stats:
        topic_items = [
            item
            for item in selected_items
            if topic["slug"] in item.get("source_tags", []) and item_identity(item) not in used_ids
        ]
        if not topic_items:
            continue
        lines.extend([f"## {topic['label']}", ""])
        for item in topic_items:
            used_ids.add(item_identity(item))
            title = markdown_escape(item.get("title") or "Untitled")
            lines.extend(
                [
                    f"### {title}",
                    "",
                    f"- 期刊：{markdown_escape(item.get('source_title') or item.get('journal'))}",
                    f"- 日期：{item_date_label(item)}",
                ]
            )
            if item.get("creator"):
                lines.append(f"- 作者：{markdown_escape(item['creator'])}")
            if item.get("item_type_label"):
                lines.append(f"- 类型：{markdown_escape(item['item_type_label'])}")
            if item.get("topic_labels"):
                lines.append(f"- 研究方向：{markdown_escape('、'.join(item['topic_labels']))}")
            if item.get("doi"):
                lines.append(f"- DOI：{markdown_escape(item['doi'])}")
            if item.get("link"):
                lines.append(f"- 原文链接：{markdown_escape(item['link'])}")
            lines.extend(["", markdown_summary(item), ""])

    uncategorized = [
        item
        for item in selected_items
        if item_identity(item) not in used_ids
    ]
    if uncategorized:
        lines.extend(["## 其他更新", ""])
        for item in uncategorized:
            lines.extend(
                [
                    f"### {markdown_escape(item.get('title') or 'Untitled')}",
                    "",
                    f"- 期刊：{markdown_escape(item.get('source_title') or item.get('journal'))}",
                    f"- 日期：{item_date_label(item)}",
                    f"- 类型：{markdown_escape(item.get('item_type_label') or article_type_label(item.get('item_type')))}",
                    f"- 研究方向：{markdown_escape('、'.join(item.get('topic_labels') or topic_labels(item.get('source_tags', []))))}",
                    "",
                    markdown_summary(item),
                    "",
                ]
            )
    return "\n".join(lines).strip() + "\n"


def build_weekly_html(combined_items, topic_stats, generated_at, markdown_path):
    selected_items, scope_label = weekly_items(combined_items, generated_at)
    generated_label = generated_at.strftime("%Y-%m-%d %H:%M UTC")
    date_slug = generated_at.strftime("%Y-%m-%d")
    used_ids = set()
    topic_sections = []
    for topic in topic_stats:
        cards = []
        for item in selected_items:
            item_id = item_identity(item)
            if topic["slug"] not in item.get("source_tags", []) or item_id in used_ids:
                continue
            used_ids.add(item_id)
            title = html.escape(item.get("title") or "Untitled")
            link = html.escape(item.get("link") or "#")
            journal = html.escape(item.get("source_title") or item.get("journal") or "")
            creator = html.escape(markdown_escape(item.get("creator") or ""))
            creator_html = f"<p class=\"byline\">{creator}</p>" if creator else ""
            doi_html = f"<span>DOI: {html.escape(item['doi'])}</span>" if item.get("doi") else ""
            type_html = f"<span>{html.escape(item.get('item_type_label') or article_type_label(item.get('item_type')))}</span>"
            topics_html = "".join(
                f"<span>{html.escape(label)}</span>"
                for label in item.get("topic_labels", [])
            )
            summary = html.escape(markdown_summary(item))
            cards.append(
                f"""
                <article class="paper-card">
                  <div class="meta">
                    <span>{html.escape(item_date_label(item))}</span>
                    <span>{journal}</span>
                    {type_html}
                    {topics_html}
                    {doi_html}
                  </div>
                  <h3><a href="{link}">{title}</a></h3>
                  {creator_html}
                  <p>{summary}</p>
                </article>
                """
            )
        if cards:
            topic_sections.append(
                f"""
                <section class="topic-section">
                  <h2>{html.escape(topic["label"])}</h2>
                  <div class="paper-list">{"".join(cards)}</div>
                </section>
                """
            )
    body = "".join(topic_sections) or '<p class="empty">本周暂未检测到新条目。</p>'
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>翻译学新论文速递：{date_slug}</title>
  <meta name="description" content="翻译学期刊本周新论文摘要合集，按研究方向分组。">
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
      --blue: #315f8f;
      --paper: #fbf8ef;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background:
        linear-gradient(90deg, rgba(36, 48, 54, .035) 1px, transparent 1px),
        linear-gradient(rgba(36, 48, 54, .025) 1px, transparent 1px),
        var(--bg);
      background-size: 34px 34px;
      color: var(--ink);
      font: 16px/1.62 "Iowan Old Style", "Palatino Linotype", Palatino, "Songti SC", serif;
    }}
    a {{ color: inherit; }}
    .wrap {{ max-width: 980px; margin: 0 auto; padding: 28px; }}
    header {{
      border-bottom: 1px solid var(--line);
      background: var(--paper);
    }}
    h1 {{
      margin: 0;
      font-size: clamp(34px, 6vw, 64px);
      line-height: 1;
      letter-spacing: 0;
    }}
    .lede {{ max-width: 720px; margin-top: 16px; color: var(--muted); font-size: 18px; }}
    .actions {{ display: flex; gap: 10px; flex-wrap: wrap; margin-top: 22px; }}
    .button {{
      border: 1px solid var(--line);
      background: var(--panel-strong);
      border-radius: 6px;
      padding: 9px 12px;
      text-decoration: none;
      font: 15px/1.2 ui-sans-serif, system-ui, sans-serif;
    }}
    .button.primary {{ background: var(--accent); border-color: var(--accent); color: #fff; }}
    .summary {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin-top: 18px;
    }}
    .summary span, .meta span {{
      display: inline-flex;
      width: fit-content;
      border-radius: 999px;
      padding: 4px 9px;
      background: #eef4f8;
      color: var(--blue);
      font: 700 12px/1.2 ui-sans-serif, system-ui, sans-serif;
    }}
    main.wrap {{ display: grid; gap: 20px; }}
    .topic-section {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      padding: 22px;
    }}
    .topic-section h2 {{ margin: 0 0 14px; color: var(--accent); letter-spacing: 0; }}
    .paper-list {{ display: grid; gap: 12px; }}
    .paper-card {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel-strong);
      padding: 16px;
    }}
    .paper-card h3 {{
      margin: 10px 0 8px;
      font: 700 20px/1.28 ui-sans-serif, system-ui, sans-serif;
      letter-spacing: 0;
    }}
    .paper-card h3 a {{ text-decoration: none; }}
    .paper-card p {{ margin: 0; color: #46545a; }}
    .paper-card .byline {{
      margin-bottom: 10px;
      color: var(--muted);
      font: 13px/1.35 ui-sans-serif, system-ui, sans-serif;
    }}
    .meta {{ display: flex; gap: 6px; flex-wrap: wrap; }}
    .empty {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      padding: 18px;
    }}
    @media (max-width: 560px) {{
      .wrap {{ padding: 18px; }}
      .paper-card h3 {{ font-size: 17px; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="wrap">
      <h1>翻译学新论文速递</h1>
      <p class="lede">按研究方向整理的新近论文摘要合集，适合快速扫读、文献跟踪和转发分享。</p>
      <div class="summary">
        <span>{html.escape(scope_label)}</span>
        <span>{date_slug}</span>
        <span>{len(selected_items)} 条更新</span>
        <span>{len({item.get("source_slug") for item in selected_items})} 本期刊</span>
        <span>生成时间：{generated_label}</span>
      </div>
      <div class="actions">
        <a class="button primary" href="../index.html">返回首页</a>
        <a class="button" href="{html.escape(os.path.basename(markdown_path))}">Markdown 草稿</a>
        <a class="button" href="../feed.xml">订阅总 RSS</a>
      </div>
    </div>
  </header>
  <main class="wrap">
    {body}
  </main>
</body>
</html>
"""


def build_papers_page(combined_items, generated_at):
    generated_label = generated_at.strftime("%Y-%m-%d %H:%M UTC")
    topic_options = "".join(
        f'<option value="{html.escape(topic["slug"])}">{html.escape(topic["label"])}</option>'
        for topic in TOPICS
    )
    type_options = "".join(
        f'<option value="{html.escape(item_type["slug"])}">{html.escape(item_type["label"])}</option>'
        for item_type in ARTICLE_TYPES
    )
    sorted_items = sorted(combined_items, key=lambda item: parse_date(item.get("date")), reverse=True)
    related_by_index = find_related_papers(sorted_items)
    cards = []
    for index, item in enumerate(sorted_items):
        search_text = " ".join(
            [
                item.get("title", ""),
                item.get("creator", ""),
                item.get("source_title", ""),
                item.get("doi", ""),
                item.get("abstract", ""),
                item.get("fallback_description", ""),
                *item.get("topic_labels", []),
                item.get("item_type_label", ""),
            ]
        ).lower()
        topics = " ".join(item.get("source_tags", []))
        topic_html = "".join(f"<span>{html.escape(label)}</span>" for label in item.get("topic_labels", []))
        creator = html.escape(item.get("creator") or "")
        creator_html = f'<p class="byline">{creator}</p>' if creator else ""
        doi = item.get("doi") or ""
        doi_html = f'<span>DOI: {html.escape(doi)}</span>' if doi else ""
        reference_lines = [
            f"Title: {item.get('title') or 'Untitled'}",
            f"Authors: {item.get('creator')}" if item.get("creator") else "",
            f"Journal: {item.get('source_title') or item.get('journal')}" if item.get("source_title") or item.get("journal") else "",
            f"Published: {item_date_label(item)}",
            f"DOI: {doi}" if doi else "",
            f"URL: {item.get('link')}" if item.get("link") else "",
        ]
        reference_text = "\n".join(line for line in reference_lines if line)
        related_papers = related_by_index[index]
        related_html = ""
        if related_papers:
            related_items_html = "".join(
                f'<li><a href="{html.escape(related["item"].get("link") or "#")}">{html.escape(related["item"].get("title") or "Untitled")}</a><span>{html.escape("；".join(related["reasons"]))}</span></li>'
                for related in related_papers
            )
            related_html = f'<aside class="related-papers"><h3>关联阅读</h3><ul>{related_items_html}</ul></aside>'
        cards.append(
            f"""
            <article class="paper-card" data-paper data-search="{html.escape(search_text)}" data-topics="{html.escape(topics)}" data-type="{html.escape(item.get('item_type', 'article'))}" data-date="{int(parse_date(item.get('date')).timestamp())}" data-title="{html.escape((item.get('title') or '').lower())}">
              <div class="meta">
                <span>{html.escape(item_date_label(item))}</span>
                <span>{html.escape(item.get("source_title") or item.get("journal") or "")}</span>
                <span>{html.escape(item.get("item_type_label") or article_type_label(item.get("item_type")))}</span>
                {topic_html}
                {doi_html}
              </div>
              <h2><a href="{html.escape(item.get('link') or '#')}">{html.escape(item.get("title") or "Untitled")}</a></h2>
              {creator_html}
              <p>{html.escape(truncate_text(markdown_summary(item), 360))}</p>
              {related_html}
              <div class="paper-actions"><a href="{html.escape(item.get('link') or '#')}">打开原文</a><button type="button" data-copy-reference="{html.escape(reference_text, quote=True)}">复制引文信息</button></div>
            </article>
            """
        )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>论文索引 · Translation Studies RSS</title>
  <meta name="description" content="可按关键词、研究方向和文章类型检索的翻译学新近论文索引。">
  <style>
    :root {{ --ink:#243036; --muted:#68757b; --line:#d9d2c2; --panel:#fffdf7; --bg:#f4efe4; --accent:#087f73; --blue:#315f8f; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; color:var(--ink); background:var(--bg); font:16px/1.58 "Iowan Old Style", "Palatino Linotype", Palatino, "Songti SC", serif; }}
    a {{ color:inherit; }}
    .wrap {{ max-width:1080px; margin:0 auto; padding:28px; }}
    header {{ border-bottom:1px solid var(--line); background:var(--panel); }}
    h1 {{ margin:0; font-size:clamp(36px,6vw,68px); line-height:1; }}
    .lede {{ max-width:680px; margin:16px 0 0; color:var(--muted); font-size:18px; }}
    .meta, .summary {{ display:flex; gap:6px; flex-wrap:wrap; }}
    .summary {{ margin-top:18px; }}
    .meta span, .summary span {{ display:inline-flex; width:fit-content; border-radius:999px; padding:4px 8px; background:#eef4f8; color:var(--blue); font:700 12px/1.2 ui-sans-serif,system-ui,sans-serif; }}
    .actions {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:20px; }}
    .button {{ min-height:40px; border:1px solid var(--line); border-radius:6px; background:#fff; padding:9px 12px; color:var(--ink); text-decoration:none; font:14px/1.2 ui-sans-serif,system-ui,sans-serif; cursor:pointer; }}
    .button.primary {{ border-color:var(--accent); background:var(--accent); color:#fff; }}
    main {{ display:grid; gap:18px; padding-top:24px !important; }}
    .filters {{ display:grid; grid-template-columns:minmax(220px,1.7fr) repeat(4,minmax(128px,1fr)) auto; gap:10px; align-items:end; padding:14px; border:1px solid var(--line); border-radius:8px; background:var(--panel); font-family:ui-sans-serif,system-ui,sans-serif; }}
    .field {{ display:grid; gap:5px; }}
    .filter-actions {{ display:flex; gap:8px; flex-wrap:wrap; }}
    label {{ color:var(--muted); font-size:12px; font-weight:700; }}
    input, select {{ min-height:40px; width:100%; border:1px solid var(--line); border-radius:6px; padding:8px 10px; color:var(--ink); background:#fff; font:14px/1.2 ui-sans-serif,system-ui,sans-serif; }}
    input:focus-visible, select:focus-visible, .button:focus-visible, a:focus-visible {{ outline:3px solid rgba(8,127,115,.45); outline-offset:2px; }}
    .result {{ margin:0; color:var(--muted); font:13px/1.3 ui-sans-serif,system-ui,sans-serif; }}
    .paper-list {{ display:grid; gap:12px; }}
    .paper-card {{ border:1px solid var(--line); border-radius:8px; padding:18px; background:#fff; }}
    .paper-card h2 {{ margin:11px 0 8px; font:700 20px/1.28 ui-sans-serif,system-ui,sans-serif; }}
    .paper-card h2 a {{ text-decoration:none; }}
    .paper-card p {{ margin:0; color:#46545a; }}
    .byline {{ margin:0 0 10px !important; color:var(--muted) !important; font:13px/1.3 ui-sans-serif,system-ui,sans-serif; }}
    .related-papers {{ margin-top:16px; border-left:3px solid #b7d7d2; padding-left:12px; }}
    .related-papers h3 {{ margin:0 0 7px; color:var(--muted); font:700 12px/1.2 ui-sans-serif,system-ui,sans-serif; }}
    .related-papers ul {{ display:grid; gap:6px; margin:0; padding:0; list-style:none; }}
    .related-papers li {{ display:grid; gap:2px; font:13px/1.35 ui-sans-serif,system-ui,sans-serif; }}
    .related-papers li span {{ color:var(--muted); font-size:12px; }}
    .paper-actions {{ display:flex; gap:12px; flex-wrap:wrap; margin-top:14px; font:700 13px/1.2 ui-sans-serif,system-ui,sans-serif; }}
    .paper-actions a, .paper-actions button {{ border:0; padding:0; background:none; color:var(--accent); font:inherit; text-decoration:underline; text-underline-offset:3px; cursor:pointer; }}
    [data-paper][hidden] {{ display:none; }}
    @media (max-width:720px) {{ .wrap {{ padding:18px; }} .filters {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  <header><div class="wrap">
    <h1>论文索引</h1>
    <p class="lede">搜索当前收录的论文标题、作者、期刊、DOI 和摘要；再按研究方向或文章类型缩小范围。</p>
    <div class="summary"><span>{len(combined_items)} 条论文</span><span>生成时间：{generated_label}</span></div>
    <div class="actions"><a class="button primary" href="index.html">返回首页</a><a class="button" href="feed.xml">订阅总 RSS</a></div>
  </div></header>
  <main class="wrap">
    <form class="filters" id="paper-filters" role="search">
      <div class="field"><label for="paper-search">检索论文</label><input id="paper-search" type="search" autocomplete="off" placeholder="标题、作者、期刊、DOI 或摘要"></div>
      <div class="field"><label for="paper-topic">研究方向</label><select id="paper-topic"><option value="">全部方向</option>{topic_options}</select></div>
      <div class="field"><label for="paper-type">文章类型</label><select id="paper-type"><option value="">全部类型</option>{type_options}</select></div>
      <div class="field"><label for="paper-age">发表时间</label><select id="paper-age"><option value="">不限时间</option><option value="30">近 30 天</option><option value="90">近 90 天</option><option value="365">近 1 年</option></select></div>
      <div class="field"><label for="paper-sort">排序方式</label><select id="paper-sort"><option value="newest">最新发表</option><option value="oldest">最早发表</option><option value="title">标题 A–Z</option></select></div>
      <div class="filter-actions"><button class="button" type="reset">清除筛选</button><button class="button" type="button" id="copy-paper-link">复制筛选链接</button></div>
    </form>
    <p class="result" id="paper-result" role="status">显示全部 {len(combined_items)} 条论文。</p>
    <div class="paper-list">{"".join(cards)}</div>
  </main>
  <script>
    const form = document.getElementById("paper-filters");
    const search = document.getElementById("paper-search");
    const topic = document.getElementById("paper-topic");
    const type = document.getElementById("paper-type");
    const age = document.getElementById("paper-age");
    const sort = document.getElementById("paper-sort");
    const copyLink = document.getElementById("copy-paper-link");
    const result = document.getElementById("paper-result");
    const paperList = document.querySelector(".paper-list");
    const papers = Array.from(document.querySelectorAll("[data-paper]"));
    const setFromParams = () => {{
      const params = new URLSearchParams(window.location.search);
      search.value = params.get("q") || "";
      topic.value = params.get("topic") || "";
      type.value = params.get("type") || "";
      age.value = params.get("age") || "";
      sort.value = params.get("sort") || "newest";
    }};
    const updateUrl = () => {{
      const params = new URLSearchParams();
      if (search.value.trim()) params.set("q", search.value.trim());
      if (topic.value) params.set("topic", topic.value);
      if (type.value) params.set("type", type.value);
      if (age.value) params.set("age", age.value);
      if (sort.value !== "newest") params.set("sort", sort.value);
      const query = params.toString();
      window.history.replaceState(null, "", query ? `${{window.location.pathname}}?${{query}}` : window.location.pathname);
    }};
    const applyFilters = () => {{
      const query = search.value.trim().toLowerCase();
      const days = Number(age.value || 0);
      const cutoff = days ? Date.now() / 1000 - days * 86400 : 0;
      let visible = 0;
      papers.forEach((paper) => {{
        const matches = (!query || paper.dataset.search.includes(query)) && (!topic.value || paper.dataset.topics.split(" ").includes(topic.value)) && (!type.value || paper.dataset.type === type.value) && (!cutoff || Number(paper.dataset.date) >= cutoff);
        paper.hidden = !matches;
        if (matches) visible += 1;
      }});
      papers.sort((left, right) => {{
        if (sort.value === "title") return left.dataset.title.localeCompare(right.dataset.title);
        const direction = sort.value === "oldest" ? 1 : -1;
        return direction * (Number(left.dataset.date) - Number(right.dataset.date));
      }}).forEach((paper) => paperList.appendChild(paper));
      updateUrl();
      result.textContent = visible ? `显示 ${{visible}} / ${{papers.length}} 条论文。` : "没有匹配的论文。请调整或清除筛选条件。";
    }};
    const copyText = async (text) => {{
      try {{
        await navigator.clipboard.writeText(text);
      }} catch (error) {{
        const helper = document.createElement("textarea");
        helper.value = text;
        document.body.appendChild(helper);
        helper.select();
        document.execCommand("copy");
        helper.remove();
      }}
    }};
    const confirmCopy = async (button, text) => {{
      const label = button.textContent;
      await copyText(text);
      button.textContent = "已复制";
      setTimeout(() => {{ button.textContent = label; }}, 1400);
    }};
    copyLink.addEventListener("click", () => confirmCopy(copyLink, window.location.href));
    document.querySelectorAll("[data-copy-reference]").forEach((button) => {{
      button.addEventListener("click", () => confirmCopy(button, button.dataset.copyReference));
    }});
    setFromParams();
    applyFilters();
    form.addEventListener("submit", (event) => event.preventDefault());
    form.addEventListener("input", applyFilters);
    form.addEventListener("change", applyFilters);
    form.addEventListener("reset", () => window.requestAnimationFrame(applyFilters));
  </script>
</body>
</html>
"""


def build_subscriptions_opml(stats, topic_stats, type_stats, new_feed_stats, keyword_alert_stats, generated_at):
    base_url = "https://xionglingsong.github.io/rss-translation-studies/"

    def feed_outline(label, filename, description=""):
        return (
            f'<outline text="{html.escape(label, quote=True)}" title="{html.escape(label, quote=True)}" '
            f'type="rss" xmlUrl="{html.escape(base_url + filename, quote=True)}" '
            f'htmlUrl="{html.escape(base_url, quote=True)}" '
            f'description="{html.escape(description, quote=True)}" />'
        )

    def group_outline(label, outlines):
        return f'<outline text="{html.escape(label, quote=True)}" title="{html.escape(label, quote=True)}">' + "".join(outlines) + "</outline>"

    topic_outlines = [feed_outline(topic["label"], topic["feed"], topic["description"]) for topic in topic_stats]
    type_outlines = [feed_outline(item_type["label"], item_type["feed"], item_type["description"]) for item_type in type_stats]
    journal_outlines = [
        feed_outline(stat["title"], stat["feed"], f"{stat['source_label']} · {', '.join(stat['tag_labels'])}")
        for stat in sorted(stats, key=lambda stat: stat["title"].lower())
    ]
    new_item_outlines = [
        feed_outline("全部首次收录论文", new_feed_stats["feed"], "每次刷新首次识别到的论文。")
    ] + [
        feed_outline(window["label"], window["feed"], f"近 {window['days']} 天首次收录的论文。")
        for window in new_feed_stats["windows"]
    ] + [
        feed_outline(topic["label"], topic["feed"], f"首次收录的{topic['label']}论文。")
        for topic in new_feed_stats["topics"]
    ]
    keyword_outlines = [
        feed_outline(alert["label"], alert["feed"], alert["description"])
        for alert in keyword_alert_stats
    ]
    body = "".join(
        [
            feed_outline("全部翻译学期刊更新", "feed.xml", "所有收录期刊的最新文章"),
            group_outline("首次收录论文", new_item_outlines),
            group_outline("关键词提醒", keyword_outlines),
            group_outline("按研究方向订阅", topic_outlines),
            group_outline("按文章类型订阅", type_outlines),
            group_outline("按期刊订阅", journal_outlines),
        ]
    )
    created = generated_at.strftime("%a, %d %b %Y %H:%M:%S GMT")
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
  <head><title>Translation Studies RSS subscriptions</title><dateCreated>{created}</dateCreated></head>
  <body>{body}</body>
</opml>
'''


def generate_source(source, cache):
    source_snapshots = cache.setdefault("_source_snapshots", {})
    try:
        feed = parse_source_feed(source)
        source_snapshots[source["slug"]] = {"feed": feed, "fetched_at": time.time()}
    except Exception as source_error:
        snapshot = source_snapshots.get(source["slug"], {}).get("feed")
        if snapshot:
            feed = snapshot
            feed["fallback_reason"] = f"沿用缓存：{source_error}"
        else:
            published_url = f"https://xionglingsong.github.io/rss-translation-studies/{source['slug']}.xml"
            try:
                published_xml = fetch_text(
                    published_url,
                    accept="application/rss+xml, application/atom+xml, application/xml, text/xml",
                    timeout=10,
                    attempts=1,
                )
                feed = parse_feed_xml(published_xml, source)
                feed["fallback_reason"] = f"沿用已发布版本：{source_error}"
                source_snapshots[source["slug"]] = {"feed": feed, "fetched_at": time.time()}
            except Exception as fallback_error:
                raise RuntimeError(f"{source_error}; published-feed fallback failed: {fallback_error}") from source_error
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
    if source.get("enrich_tandfonline_abstracts"):
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, MAX_WORKERS)) as executor:
            items = list(executor.map(lambda item: safe_enrich_tandfonline_abstract(item, cache), items))
    if TRANSLATE_TO_ZH and DEEPSEEK_API_KEY:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, MAX_WORKERS)) as executor:
            items = list(executor.map(lambda item: translate_item(item, cache), items))
    return feed, items


def generate_all_feeds():
    cache = load_json(CACHE_PATH, {})
    if TRANSLATE_TO_ZH and not DEEPSEEK_API_KEY:
        print("TRANSLATE_TO_ZH is enabled, but DEEPSEEK_API_KEY is not set. Skipping Chinese translations.")
    sources = load_journals()
    generated_at = datetime.now(timezone.utc)
    generated_at_iso = generated_at.isoformat()
    outputs = {}
    combined_items = []
    errors = []
    stats = []
    source_health = cache.setdefault("_source_health", {})
    for source in sources:
        try:
            feed, items = generate_source(source, cache)
            tags = source.get("tags", [])
            for item in items:
                classify_item(item, tags)
            combined_items.extend(items)
            weak_abstracts = sum(1 for item in items if weak_abstract(item))
            article_topic_slugs = []
            for item in items:
                for tag in item.get("source_tags", []):
                    if tag not in article_topic_slugs:
                        article_topic_slugs.append(tag)
            source_type = source.get("source_type", "rss")
            fallback_reason = feed.get("fallback_reason", "")
            previous_health = source_health.get(source["slug"], {})
            if fallback_reason:
                health = {
                    **previous_health,
                    "last_checked_at": generated_at_iso,
                    "status": "stale",
                    "status_detail": fallback_reason,
                }
            else:
                health = {
                    "last_success_at": generated_at_iso,
                    "last_checked_at": generated_at_iso,
                    "status": "ok",
                    "status_detail": "",
                }
            source_health[source["slug"]] = health
            stats.append(
                {
                    "slug": source["slug"],
                    "title": source["title"],
                    "publisher": source.get("publisher", ""),
                    "homepage": source.get("homepage") or feed["link"],
                    "feed": f"{source['slug']}.xml",
                    "source_feed": source.get("source_feed", ""),
                    "source_type": source_type,
                    "source_label": source_type_label(source_type),
                    "tags": tags,
                    "tag_labels": topic_labels(tags),
                    "article_tags": article_topic_slugs,
                    "article_tag_labels": topic_labels(article_topic_slugs),
                    "status": health["status"],
                    "status_label": "沿用最近版本" if fallback_reason else "本次成功",
                    "status_detail": health["status_detail"],
                    "last_success_at": health.get("last_success_at", ""),
                    "last_checked_at": health["last_checked_at"],
                    "items": len(items),
                    "translated": sum(1 for item in items if item.get("abstract_zh")),
                    "weak_abstracts": weak_abstracts,
                    "abstract_quality": "complete" if weak_abstracts == 0 else "needs_review",
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
    new_items, update_tracking_active = track_new_items(cache, combined_items, generated_at)
    save_json(CACHE_PATH, cache)
    outputs["feed.xml"] = build_rss(
        "Translation Studies Journals with Abstracts",
        "https://xionglingsong.github.io/rss-translation-studies/",
        "Combined enhanced RSS feed for translation and interpreting studies journals.",
        combined_items,
    )
    topic_stats = []
    for topic in TOPICS:
        topic_items = [item for item in combined_items if topic["slug"] in item.get("source_tags", [])]
        journal_count = len({item.get("source_slug") for item in topic_items})
        feed_name = topic_feed_name(topic["slug"])
        outputs[feed_name] = build_rss(
            f"{topic['label']} - Translation Studies Articles",
            f"https://xionglingsong.github.io/rss-translation-studies/{feed_name}",
            topic["description"],
            topic_items,
        )
        topic_stats.append(
            {
                "slug": topic["slug"],
                "label": topic["label"],
                "description": topic["description"],
                "feed": feed_name,
                "journal_count": journal_count,
                "item_count": len(topic_items),
            }
        )
    new_feed_stats = {"feed": "new.xml", "count": len(new_items), "tracking_active": update_tracking_active, "windows": [], "topics": []}
    outputs[new_feed_stats["feed"]] = build_rss(
        "Newly Discovered Translation Studies Articles",
        "https://xionglingsong.github.io/rss-translation-studies/new.xml",
        "Articles first discovered during the most recent successful refresh.",
        new_items,
    )
    for days in NEW_ITEM_HISTORY_WINDOWS:
        window_items = recent_discovery_items(cache, generated_at, days)
        feed_name = f"new-{days}d.xml"
        outputs[feed_name] = build_rss(
            f"Translation Studies Articles Newly Discovered in {days} Days",
            f"https://xionglingsong.github.io/rss-translation-studies/{feed_name}",
            f"Articles first discovered by this service in the last {days} days.",
            window_items,
        )
        new_feed_stats["windows"].append({"days": days, "label": f"近 {days} 天新增", "feed": feed_name, "item_count": len(window_items)})

    for topic in TOPICS:
        topic_new_items = [item for item in new_items if topic["slug"] in item.get("source_tags", [])]
        feed_name = new_topic_feed_name(topic["slug"])
        outputs[feed_name] = build_rss(
            f"Newly Discovered {topic['label']} Articles",
            f"https://xionglingsong.github.io/rss-translation-studies/{feed_name}",
            f"{topic['label']} articles first discovered during the most recent successful refresh.",
            topic_new_items,
        )
        new_feed_stats["topics"].append({"slug": topic["slug"], "label": topic["label"], "feed": feed_name, "item_count": len(topic_new_items)})

    type_stats = []
    for item_type in ARTICLE_TYPES:
        type_items = [item for item in combined_items if item.get("item_type") == item_type["slug"]]
        feed_name = type_feed_name(item_type["slug"])
        outputs[feed_name] = build_rss(
            f"{item_type['label']} - Translation Studies Articles",
            f"https://xionglingsong.github.io/rss-translation-studies/{feed_name}",
            item_type["description"],
            type_items,
        )
        type_stats.append(
            {
                "slug": item_type["slug"],
                "label": item_type["label"],
                "description": item_type["description"],
                "feed": feed_name,
                "journal_count": len({item.get("source_slug") for item in type_items}),
                "item_count": len(type_items),
            }
        )
    keyword_alert_stats = []
    for alert in KEYWORD_ALERTS:
        alert_items = [item for item in combined_items if keyword_alert_matches(item, alert["terms"])]
        feed_name = keyword_alert_feed_name(alert["slug"])
        outputs[feed_name] = build_rss(
            f"{alert['label']} Keyword Alert - Translation Studies",
            f"https://xionglingsong.github.io/rss-translation-studies/{feed_name}",
            alert["description"],
            alert_items,
        )
        keyword_alert_stats.append({"slug": alert["slug"], "label": alert["label"], "description": alert["description"], "feed": feed_name, "item_count": len(alert_items)})

    outputs["subscriptions.opml"] = build_subscriptions_opml(stats, topic_stats, type_stats, new_feed_stats, keyword_alert_stats, generated_at)
    weekly_markdown = build_weekly_markdown(combined_items, topic_stats, generated_at)
    weekly_date = generated_at.strftime("%Y-%m-%d")
    weekly_md_path = f"weekly/{weekly_date}.md"
    weekly_html_path = f"weekly/{weekly_date}.html"
    weekly_html = build_weekly_html(combined_items, topic_stats, generated_at, weekly_md_path)
    outputs[weekly_md_path] = weekly_markdown
    outputs["weekly/latest.md"] = weekly_markdown
    outputs[weekly_html_path] = weekly_html
    outputs["weekly/latest.html"] = weekly_html
    outputs["papers.html"] = build_papers_page(combined_items, generated_at)
    outputs["manifest.json"] = json.dumps(
        {
            "title": "Translation Studies RSS",
            "generated_at": generated_at_iso,
            "combined_feed": "feed.xml",
            "subscriptions_opml": "subscriptions.opml",
            "weekly": {
                "latest": "weekly/latest.html",
                "latest_markdown": "weekly/latest.md",
                "dated": weekly_html_path,
                "dated_markdown": weekly_md_path,
            },
            "expected_journal_count": len(sources),
            "journal_count": len(stats),
            "item_count": len(combined_items),
            "papers": {"index": "papers.html", "count": len(combined_items)},
            "new_item_tracking_active": update_tracking_active,
            "new_items_count": len(new_items),
            "new_items": new_feed_stats,
            "keyword_alerts": keyword_alert_stats,
            "translated_count": sum(stat["translated"] for stat in stats),
            "weak_abstract_count": sum(stat["weak_abstracts"] for stat in stats),
            "topics": topic_stats,
            "types": type_stats,
            "journals": stats,
            "errors": errors,
        },
        ensure_ascii=False,
        indent=2,
    )
    outputs["index.html"] = build_public_index(
        stats,
        topic_stats,
        type_stats,
        errors,
        len(combined_items),
        generated_at,
        weekly_html_path,
        weekly_md_path,
        combined_items,
        new_items,
        update_tracking_active,
        new_feed_stats,
        keyword_alert_stats,
    )
    if errors:
        print("Skipped feeds:")
        for error in errors:
            print(f"- {error}")
    return outputs


def validate_static_outputs(outputs, sources):
    manifest = json.loads(outputs["manifest.json"])
    expected_count = len(sources)
    actual_count = manifest.get("journal_count", 0)
    errors = manifest.get("errors") or []
    missing_feeds = [
        source["slug"]
        for source in sources
        if f"{source['slug']}.xml" not in outputs
    ]
    missing_topic_feeds = [
        topic["slug"]
        for topic in TOPICS
        if topic_feed_name(topic["slug"]) not in outputs
    ]
    missing_type_feeds = [
        item_type["slug"]
        for item_type in ARTICLE_TYPES
        if type_feed_name(item_type["slug"]) not in outputs
    ]
    weekly_manifest = manifest.get("weekly", {})
    expected_weekly_files = [
        "weekly/latest.html",
        "weekly/latest.md",
        weekly_manifest.get("dated", ""),
        weekly_manifest.get("dated_markdown", ""),
    ]
    missing_weekly = [name for name in expected_weekly_files if name and name not in outputs]
    missing_pages = [name for name in (manifest.get("papers") or {}).values() if isinstance(name, str) and name not in outputs]
    subscriptions_opml = manifest.get("subscriptions_opml", "")
    missing_subscriptions = [subscriptions_opml] if subscriptions_opml and subscriptions_opml not in outputs else []
    new_items_manifest = manifest.get("new_items") or {}
    new_item_feeds = [new_items_manifest.get("feed", "")] + [window.get("feed", "") for window in new_items_manifest.get("windows", [])] + [topic.get("feed", "") for topic in new_items_manifest.get("topics", [])]
    missing_new_item_feeds = [feed for feed in new_item_feeds if feed and feed not in outputs]
    missing_keyword_feeds = [alert.get("feed", "") for alert in manifest.get("keyword_alerts", []) if alert.get("feed") not in outputs]
    if errors or actual_count != expected_count or missing_feeds or missing_topic_feeds or missing_type_feeds or missing_weekly or missing_pages or missing_subscriptions or missing_new_item_feeds or missing_keyword_feeds:
        details = [
            f"expected {expected_count} journals, generated {actual_count}",
            f"errors: {len(errors)}",
            f"missing feeds: {', '.join(missing_feeds) if missing_feeds else 'none'}",
            f"missing topic feeds: {', '.join(missing_topic_feeds) if missing_topic_feeds else 'none'}",
            f"missing type feeds: {', '.join(missing_type_feeds) if missing_type_feeds else 'none'}",
            f"missing weekly files: {', '.join(missing_weekly) if missing_weekly else 'none'}",
            f"missing index pages: {', '.join(missing_pages) if missing_pages else 'none'}",
            f"missing subscription exports: {', '.join(missing_subscriptions) if missing_subscriptions else 'none'}",
            f"missing new-item feeds: {', '.join(missing_new_item_feeds) if missing_new_item_feeds else 'none'}",
            f"missing keyword-alert feeds: {', '.join(missing_keyword_feeds) if missing_keyword_feeds else 'none'}",
        ]
        if errors:
            details.extend(errors)
        raise RuntimeError("Static feed validation failed: " + "; ".join(details))


def weak_abstract(item):
    abstract = item.get("abstract") or item.get("fallback_description") or ""
    return (
        not abstract
        or "No abstract found" in abstract
        or bool(re.match(r"^Volume \d+", abstract))
        or is_truncated_abstract(abstract)
        or is_boilerplate_abstract(abstract)
    )


def build_public_index(stats, topic_stats, type_stats, errors, item_count, generated_at, weekly_path, weekly_md_path, combined_items, new_items, update_tracking_active, new_feed_stats, keyword_alert_stats):
    generated_label = generated_at.strftime("%Y-%m-%d %H:%M UTC")
    weak_total = sum(stat["weak_abstracts"] for stat in stats)
    translated_total = sum(stat["translated"] for stat in stats)
    selected_weekly_items, weekly_scope = weekly_items(combined_items, generated_at)
    weekly_count = len(selected_weekly_items)
    weekly_journal_count = len({item.get("source_slug") for item in selected_weekly_items})
    ticker_items = sorted(combined_items, key=lambda item: parse_date(item.get("date")), reverse=True)[:12]
    ticker_cards = []
    for item in ticker_items:
        title = html.escape(truncate_text(item.get("title") or "Untitled", 90))
        link = html.escape(item.get("link") or "#")
        topic_label = html.escape(item_primary_topic(item))
        journal = html.escape(item.get("source_title") or item.get("journal") or "")
        abstract_label = "有摘要" if not weak_abstract(item) else "待补摘要"
        abstract_class = "ok" if not weak_abstract(item) else "watch"
        ticker_cards.append(
            f"""
            <a class="ticker-card" href="{link}">
              <span class="ticker-topic">{topic_label}</span>
              <strong>{title}</strong>
              <span>{journal} · {html.escape(item_date_label(item))}</span>
              <span class="ticker-quality {abstract_class}">{abstract_label}</span>
            </a>
            """
        )
    ticker_html = "".join(ticker_cards + ticker_cards)
    if new_items:
        discovery_cards = []
        for item in sorted(new_items, key=lambda item: parse_date(item.get("date")), reverse=True):
            discovery_cards.append(
                f"""
                <article class="update-card">
                  <div class="update-meta"><span>{html.escape(item_date_label(item))}</span><span>{html.escape(item.get("source_title") or item.get("journal") or "")}</span></div>
                  <h4><a href="{html.escape(item.get('link') or '#')}">{html.escape(item.get("title") or "Untitled")}</a></h4>
                  <p>{html.escape(truncate_text(markdown_summary(item), 180))}</p>
                </article>
                """
            )
        discovery_html = '<div class="update-list">' + "".join(discovery_cards) + "</div>"
        discovery_note = f"本次刷新识别到 {len(new_items)} 条首次收录论文。"
    elif update_tracking_active:
        discovery_html = '<p class="empty-note">本次刷新没有识别到首次收录的论文。</p>'
        discovery_note = "新条目追踪已开启，会在后续刷新时标出首次收录的论文。"
    else:
        discovery_html = '<p class="empty-note">首次运行正在建立追踪基线；下一次刷新开始识别新收录论文。</p>'
        discovery_note = "追踪基线已建立，不会将已有论文误报为新增。"
    new_feed_note = "追踪基线已建立；后续每次刷新会更新该订阅。" if update_tracking_active else "当前正在建立追踪基线；下一次刷新开始会推送首次收录论文。"
    new_feed_cards = [
        f'''
        <article class="topic-card">
          <div><h3>全部新发现</h3><p>{html.escape(new_feed_note)}</p></div>
          <div class="topic-meta"><span>{new_feed_stats["count"]} 条本轮新增</span></div>
          <div class="topic-actions"><a class="icon-button" href="{html.escape(new_feed_stats["feed"])}">RSS</a><button class="icon-button" data-copy="{html.escape(new_feed_stats["feed"])}">Copy</button></div>
        </article>
        '''
    ]
    for window in new_feed_stats["windows"]:
        new_feed_cards.append(
            f'''
            <article class="topic-card">
              <div><h3>{html.escape(window["label"])}</h3><p>保留近 {window["days"]} 天首次收录的论文；不会因错过一次刷新而遗漏。</p></div>
              <div class="topic-meta"><span>{window["item_count"]} 条论文</span></div>
              <div class="topic-actions"><a class="icon-button" href="{html.escape(window["feed"])}">RSS</a><button class="icon-button" data-copy="{html.escape(window["feed"])}">Copy</button></div>
            </article>
            '''
        )
    for topic in new_feed_stats["topics"]:
        new_feed_cards.append(
            f'''
            <article class="topic-card">
              <div><h3>{html.escape(topic["label"])}</h3><p>仅推送首次收录的{html.escape(topic["label"])}论文。</p></div>
              <div class="topic-meta"><span>{topic["item_count"]} 条本轮新增</span></div>
              <div class="topic-actions"><a class="icon-button" href="{html.escape(topic["feed"])}">RSS</a><button class="icon-button" data-copy="{html.escape(topic["feed"])}">Copy</button></div>
            </article>
            '''
        )
    keyword_alert_cards = []
    for alert in keyword_alert_stats:
        keyword_alert_cards.append(
            f'''
            <article class="topic-card">
              <div><h3>{html.escape(alert["label"])}</h3><p>{html.escape(alert["description"])}</p></div>
              <div class="topic-meta"><span>{alert["item_count"]} 条匹配论文</span></div>
              <div class="topic-actions"><a class="icon-button" href="{html.escape(alert["feed"])}">RSS</a><button class="icon-button" data-copy="{html.escape(alert["feed"])}">Copy</button></div>
            </article>
            '''
        )
    weekly_topic_sections = []
    used_weekly_ids = set()
    for topic in topic_stats:
        topic_items = []
        for item in selected_weekly_items:
            item_id = item.get("doi") or item.get("link") or item.get("title")
            if topic["slug"] in item.get("source_tags", []) and item_id not in used_weekly_ids:
                topic_items.append(item)
                used_weekly_ids.add(item_id)
        if not topic_items:
            continue
        cards = []
        for item in topic_items:
            title = html.escape(item.get("title") or "Untitled")
            journal = html.escape(item.get("source_title") or item.get("journal") or "")
            creator = html.escape(truncate_text(item.get("creator") or "", 120))
            summary = html.escape(truncate_text(markdown_summary(item), 180))
            link = html.escape(item.get("link") or "#")
            doi_html = f'<span>DOI: {html.escape(item["doi"])}</span>' if item.get("doi") else ""
            type_html = f'<span>{html.escape(item.get("item_type_label") or article_type_label(item.get("item_type")))}</span>'
            creator_html = f"<span>{creator}</span>" if creator else ""
            cards.append(
                f"""
                <article class="update-card">
                  <div class="update-meta">
                    <span>{html.escape(item_date_label(item))}</span>
                    <span>{journal}</span>
                    {type_html}
                    {doi_html}
                  </div>
                  <h4><a href="{link}">{title}</a></h4>
                  <p>{summary}</p>
                  <div class="update-byline">{creator_html}</div>
                </article>
                """
            )
        weekly_topic_sections.append(
            f"""
            <div class="update-group">
              <h3>{html.escape(topic["label"])}</h3>
              <div class="update-list">{"".join(cards)}</div>
            </div>
            """
        )
    weekly_updates_html = "".join(weekly_topic_sections) or '<p class="empty-note">本周暂未检测到新条目。</p>'
    topic_cards = []
    for topic in topic_stats:
        topic_cards.append(
            f"""
            <article class="topic-card">
              <div>
                <h3>{html.escape(topic["label"])}</h3>
                <p>{html.escape(topic["description"])}</p>
              </div>
              <div class="topic-meta">
                <span>{topic["journal_count"]} 本期刊</span>
                <span>{topic["item_count"]} 条</span>
              </div>
              <div class="topic-actions">
                <a class="icon-button" href="{html.escape(topic["feed"])}">RSS</a>
                <button class="icon-button" data-copy="{html.escape(topic["feed"])}">Copy</button>
              </div>
            </article>
            """
        )
    type_cards = []
    for item_type in type_stats:
        type_cards.append(
            f"""
            <article class="topic-card">
              <div>
                <h3>{html.escape(item_type["label"])}</h3>
                <p>{html.escape(item_type["description"])}</p>
              </div>
              <div class="topic-meta">
                <span>{item_type["journal_count"]} 本来源期刊</span>
                <span>{item_type["item_count"]} 条</span>
              </div>
              <div class="topic-actions">
                <a class="icon-button" href="{html.escape(item_type["feed"])}">RSS</a>
                <button class="icon-button" data-copy="{html.escape(item_type["feed"])}">Copy</button>
              </div>
            </article>
            """
        )
    topic_filter_options = "".join(
        f'<option value="{html.escape(topic["slug"])}">{html.escape(topic["label"])}</option>'
        for topic in TOPICS
    )
    source_filter_options = "".join(
        f'<option value="{html.escape(source_type)}">{html.escape(source_label)}</option>'
        for source_type, source_label in sorted({stat["source_type"]: stat["source_label"] for stat in stats}.items())
    )
    journal_rows = []
    for stat in stats:
        quality_class = "good" if stat["weak_abstracts"] == 0 else "watch"
        quality_label = "完整" if stat["weak_abstracts"] == 0 else f"{stat['weak_abstracts']} 条待补"
        last_success_at = stat.get("last_success_at", "")
        last_success_label = ""
        if last_success_at:
            last_success_label = datetime.fromisoformat(last_success_at).strftime("%Y-%m-%d %H:%M UTC")
        status_title = stat.get("status_detail") or f"最近成功：{last_success_label or generated_label}"
        status_text = stat["status_label"] + (f" · 最近成功 {last_success_label}" if last_success_label else "")
        tags_html = "".join(f'<span class="tag-chip">{html.escape(label)}</span>' for label in stat.get("article_tag_labels", []))
        filter_text = " ".join(
            [
                stat["title"],
                stat["publisher"],
                stat["source_label"],
                stat["status_label"],
                *stat.get("tag_labels", []),
                *stat.get("article_tag_labels", []),
            ]
        ).lower()
        journal_rows.append(
            f"""
            <tr data-journal-row data-search="{html.escape(filter_text)}" data-topics="{html.escape(' '.join(stat.get('tags', [])))}" data-source="{html.escape(stat['source_type'])}" data-status="{html.escape(stat['status'])}">
              <td data-label="期刊">
                <strong>{html.escape(stat["title"])}</strong>
                <span>{html.escape(stat["publisher"])}</span>
                <div class="tag-list">{tags_html}</div>
              </td>
              <td data-label="条目"><span class="number">{stat["items"]}</span></td>
              <td data-label="中文"><span class="number">{stat["translated"]}</span></td>
              <td data-label="来源">
                <span class="source-chip">{html.escape(stat["source_label"])}</span>
                <span class="status-line" title="{html.escape(status_title)}">{html.escape(status_text)}</span>
              </td>
              <td data-label="摘要状态"><span class="quality {quality_class}">{quality_label}</span></td>
              <td class="actions-cell" data-label="操作">
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
      max-width: 100%;
      overflow-wrap: anywhere;
      word-break: break-all;
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
    .hero-copy {{
      max-width: 720px;
      min-width: 0;
    }}
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
      min-width: 0;
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
    .ticker-section {{
      overflow: hidden;
      padding: 16px 0;
      border-top: 1px solid var(--line);
      border-bottom: 1px solid var(--line);
      background: rgba(255, 253, 247, .72);
    }}
    .ticker-head {{
      max-width: 1180px;
      margin: 0 auto 10px;
      padding: 0 26px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      font-family: ui-sans-serif, system-ui, sans-serif;
    }}
    .ticker-head strong {{ font-size: 14px; }}
    .ticker-head a {{
      color: var(--accent);
      text-decoration: none;
      font-size: 13px;
      font-weight: 700;
    }}
    .ticker-track {{
      display: flex;
      gap: 10px;
      width: max-content;
      animation: ticker-scroll 80s linear infinite;
      padding-inline: 26px;
    }}
    .ticker-track:hover {{
      animation-play-state: paused;
    }}
    .ticker-card {{
      width: 330px;
      min-height: 126px;
      border: 1px solid var(--line);
      background: var(--panel-strong);
      border-radius: 8px;
      padding: 12px;
      text-decoration: none;
      display: grid;
      gap: 7px;
      align-content: start;
      font-family: ui-sans-serif, system-ui, sans-serif;
    }}
    .ticker-card strong {{
      font-size: 14px;
      line-height: 1.28;
    }}
    .ticker-card span {{
      color: var(--muted);
      font-size: 12px;
      line-height: 1.25;
    }}
    .ticker-topic, .ticker-quality {{
      display: inline-flex;
      width: fit-content;
      border-radius: 999px;
      padding: 3px 7px;
      background: #eef4f8;
      color: var(--blue) !important;
      font-weight: 700;
    }}
    .ticker-quality.ok {{
      background: var(--soft);
      color: var(--accent) !important;
    }}
    .ticker-quality.watch {{
      background: #fff0d2;
      color: #8c5b00 !important;
    }}
    @keyframes ticker-scroll {{
      from {{ transform: translateX(0); }}
      to {{ transform: translateX(-50%); }}
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
    .steps, .clients, .topic-grid {{
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
    .topic-grid {{
      grid-template-columns: repeat(4, minmax(0, 1fr));
    }}
    .topic-card {{
      border: 1px solid var(--line);
      background: var(--panel-strong);
      border-radius: 8px;
      padding: 16px;
      min-height: 220px;
      display: grid;
      grid-template-rows: 1fr auto auto;
      gap: 12px;
    }}
    .topic-card p {{
      font-size: 14px;
    }}
    .topic-meta {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }}
    .topic-meta span, .tag-chip {{
      display: inline-flex;
      width: fit-content;
      border-radius: 999px;
      padding: 3px 8px;
      background: #eef4f8;
      color: var(--blue);
      font: 700 12px/1.2 ui-sans-serif, system-ui, sans-serif;
    }}
    .topic-actions {{
      display: flex;
      gap: 6px;
    }}
    .tag-list {{
      display: flex;
      gap: 5px;
      flex-wrap: wrap;
      margin-top: 8px;
    }}
    .tag-chip {{
      background: #f4efe4;
      color: var(--muted);
      font-size: 11px;
    }}
    .source-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
    }}
    .weekly-panel {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 16px;
      align-items: center;
    }}
    .weekly-actions {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }}
    .weekly-summary {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin-top: 12px;
    }}
    .weekly-summary span {{
      display: inline-flex;
      width: fit-content;
      border-radius: 999px;
      padding: 4px 9px;
      background: #eef4f8;
      color: var(--blue);
      font: 700 12px/1.2 ui-sans-serif, system-ui, sans-serif;
    }}
    .weekly-updates {{
      display: grid;
      gap: 16px;
      margin-top: 18px;
    }}
    .update-group h3 {{
      margin-bottom: 10px;
      color: var(--accent);
    }}
    .update-list {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }}
    .update-card {{
      border: 1px solid var(--line);
      background: var(--panel-strong);
      border-radius: 8px;
      padding: 14px;
      min-width: 0;
    }}
    .update-card h4 {{
      margin: 8px 0;
      font: 700 15px/1.28 ui-sans-serif, system-ui, sans-serif;
      letter-spacing: 0;
    }}
    .update-card h4 a {{
      text-decoration: none;
    }}
    .update-card p {{
      font-size: 13px;
    }}
    .update-meta, .update-byline {{
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
      color: var(--muted);
      font: 12px/1.25 ui-sans-serif, system-ui, sans-serif;
    }}
    .update-meta span {{
      border-radius: 999px;
      background: #f4efe4;
      padding: 3px 7px;
    }}
    .update-byline {{
      margin-top: 10px;
    }}
    .empty-note {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel-strong);
      padding: 14px;
    }}
    .journal-filters {{
      display: grid;
      grid-template-columns: minmax(220px, 1.55fr) repeat(3, minmax(140px, 1fr)) auto;
      gap: 10px;
      align-items: end;
      margin-bottom: 14px;
      padding: 14px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel-strong);
      font-family: ui-sans-serif, system-ui, sans-serif;
    }}
    .filter-field {{
      display: grid;
      gap: 5px;
      min-width: 0;
    }}
    .filter-field label {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }}
    .filter-field input, .filter-field select {{
      width: 100%;
      min-height: 40px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--ink);
      padding: 8px 10px;
      font: 14px/1.2 ui-sans-serif, system-ui, sans-serif;
    }}
    .filter-field input::placeholder {{ color: #7a8589; }}
    .journal-filter-status {{
      margin: 0 0 12px;
      color: var(--muted);
      font: 13px/1.3 ui-sans-serif, system-ui, sans-serif;
    }}
    .button:focus-visible, .icon-button:focus-visible, .filter-field input:focus-visible, .filter-field select:focus-visible {{
      outline: 3px solid rgba(8, 127, 115, .45);
      outline-offset: 2px;
    }}
    [data-journal-row][hidden] {{ display: none !important; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
      font-family: ui-sans-serif, system-ui, sans-serif;
    }}
    th:nth-child(1), td:nth-child(1) {{ width: 45%; }}
    th:nth-child(2), td:nth-child(2),
    th:nth-child(3), td:nth-child(3) {{
      width: 6%;
      text-align: center;
    }}
    th:nth-child(4), td:nth-child(4) {{ width: 12%; }}
    th:nth-child(5), td:nth-child(5) {{ width: 12%; }}
    th:nth-child(6), td:nth-child(6) {{ width: 19%; }}
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
    .source-chip {{
      display: inline-flex;
      width: fit-content;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 3px 8px;
      color: var(--ink);
      background: var(--panel-strong);
      font-size: 12px;
      font-weight: 700;
    }}
    .status-line {{
      margin-top: 4px;
      color: var(--accent);
      font-size: 12px;
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
      .stats, .clients, .topic-grid, .source-grid {{ grid-template-columns: 1fr 1fr; }}
    }}
    @media (max-width: 760px) {{
      .wrap {{ padding: 18px; }}
      .ticker-head {{ padding: 0 18px; }}
      .ticker-track {{
        width: auto;
        overflow-x: auto;
        animation: none;
        padding-inline: 18px;
        scroll-snap-type: x mandatory;
      }}
      .ticker-card {{
        width: 280px;
        flex: 0 0 280px;
        scroll-snap-align: start;
      }}
      .steps, .manage-grid {{ grid-template-columns: 1fr; }}
      .weekly-panel {{ grid-template-columns: 1fr; }}
      .journal-filters {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .journal-filters .button {{ grid-column: 1 / -1; }}
      .update-list {{ grid-template-columns: 1fr; }}
      .section-head {{ display: block; }}
      .section-head p {{ margin-top: 8px; }}
      table, thead, tbody, tr, th, td {{ display: block; }}
      thead {{ display: none; }}
      tr {{ border-bottom: 1px solid var(--line); padding: 10px 0; }}
      tr:last-child {{ border-bottom: 0; }}
      th, td {{ width: 100% !important; }}
      td {{
        border-bottom: 0;
        padding: 5px 0;
        text-align: left !important;
      }}
      td[data-label]:not(:first-child)::before {{
        content: attr(data-label);
        display: block;
        margin-bottom: 3px;
        color: var(--muted);
        font: 700 11px/1.2 ui-sans-serif, system-ui, sans-serif;
      }}
      .actions-cell {{ min-width: 0; }}
      .actions {{
        padding-top: 8px;
        flex-wrap: nowrap;
        min-width: 0;
      }}
      .actions .icon-button {{
        min-width: 0;
        flex: 1;
      }}
    }}
    @media (max-width: 520px) {{
      .stats, .clients, .topic-grid, .source-grid, .journal-filters {{ grid-template-columns: 1fr; }}
      .feed-url {{ flex-direction: column; }}
      h1 {{ font-size: 34px; }}
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
          <a class="button" href="subscriptions.opml">下载 OPML 订阅包</a>
          <a class="button" href="papers.html">论文索引</a>
          <a class="button" href="#alerts">关键词提醒</a>
          <a class="button" href="#weekly">本周更新</a>
          <a class="button" href="#topics">按方向订阅</a>
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
        <div class="mini-note">最近生成：{generated_label}。GitHub Actions 每 6 小时自动刷新。<a href="subscriptions.opml">需要按方向、类型或期刊导入？下载 OPML 订阅包。</a></div>
      </div>
    </div>
  </header>
  <div class="ticker-section" aria-label="最新文章推荐">
    <div class="ticker-head">
      <strong>最新文章推荐</strong>
      <a href="#weekly">查看本周更新</a>
    </div>
    <div class="ticker-track">
      {ticker_html}
    </div>
  </div>
  <main class="wrap">
    <div class="stats">
      <div class="metric"><span>收录期刊</span><strong>{len(stats)}</strong></div>
      <div class="metric"><span>当前论文</span><strong>{item_count}</strong></div>
      <div class="metric"><span>中文摘要</span><strong>{translated_total}</strong></div>
      <div class="metric"><span>待补摘要</span><strong>{weak_total}</strong></div>
    </div>

    <section id="discoveries">
      <div class="section-head">
        <div><h2>本轮新发现</h2><p>{html.escape(discovery_note)}</p></div>
        <div class="weekly-actions"><a class="button primary" href="papers.html">检索全部论文</a><a class="button" href="papers.html#paper-search">打开论文搜索</a></div>
      </div>
      {discovery_html}
    </section>

    <section id="new-feeds">
      <div class="section-head">
        <div><h2>只订阅新发现</h2><p>这组 Feed 只保留每次刷新时首次收录的论文，适合不想在阅读器里反复看到旧条目的读者。</p></div>
      </div>
      <div class="topic-grid">{"".join(new_feed_cards)}</div>
    </section>

    <section id="alerts">
      <div class="section-head">
        <div><h2>关键词提醒</h2><p>预设关键词会匹配论文标题、摘要、作者、期刊与 DOI。订阅后，只会收到匹配该主题的论文。</p></div>
      </div>
      <div class="topic-grid">{"".join(keyword_alert_cards)}</div>
    </section>

    <section id="weekly">
      <div class="weekly-panel">
        <div>
          <h2>本周更新</h2>
          <p>自动汇总新近论文，按研究方向分组。可以直接在这里扫读，也可以打开 Markdown 周报作为公众号、微信群和朋友圈素材。</p>
          <div class="weekly-summary">
            <span>{html.escape(weekly_scope)}</span>
            <span>{weekly_count} 条更新</span>
            <span>{weekly_journal_count} 本期刊</span>
          </div>
        </div>
        <div class="weekly-actions">
          <a class="button primary" href="weekly/latest.html">阅读完整周报</a>
          <button class="button" data-copy="weekly/latest.html">复制周报链接</button>
          <a class="button" href="weekly/latest.md">Markdown 草稿</a>
          <a class="button" href="{html.escape(weekly_path)}">日期版</a>
        </div>
      </div>
      <div class="weekly-updates">
        {weekly_updates_html}
      </div>
    </section>

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

    <section id="topics">
      <div class="section-head">
        <h2>按研究方向订阅</h2>
        <p>如果你不想订阅全部期刊，可以只订阅某个研究方向。分类 RSS 会按每篇文章的标题和摘要自动归类。</p>
      </div>
      <div class="topic-grid">
        {"".join(topic_cards)}
      </div>
    </section>

    <section id="types">
      <div class="section-head">
        <h2>按文章类型订阅</h2>
        <p>也可以只看研究论文、综述或书评。类型 RSS 和研究方向 RSS 可以搭配使用。</p>
      </div>
      <div class="topic-grid">
        {"".join(type_cards)}
      </div>
    </section>

    <section id="journals">
      <div class="section-head">
        <h2>期刊列表</h2>
        <p>可订阅总 RSS，也可只订阅单本期刊。用筛选器快速找到符合研究方向或来源偏好的订阅。</p>
      </div>
      <form class="journal-filters" id="journal-filters" role="search">
        <div class="filter-field">
          <label for="journal-search">检索期刊</label>
          <input id="journal-search" name="journal-search" type="search" autocomplete="off" placeholder="期刊、出版社或研究方向">
        </div>
        <div class="filter-field">
          <label for="topic-filter">研究方向</label>
          <select id="topic-filter" name="topic-filter">
            <option value="">全部方向</option>
            {topic_filter_options}
          </select>
        </div>
        <div class="filter-field">
          <label for="source-filter">数据来源</label>
          <select id="source-filter" name="source-filter">
            <option value="">全部来源</option>
            {source_filter_options}
          </select>
        </div>
        <div class="filter-field">
          <label for="status-filter">更新状态</label>
          <select id="status-filter" name="status-filter">
            <option value="">全部状态</option>
            <option value="ok">本次成功</option>
            <option value="stale">沿用最近版本</option>
          </select>
        </div>
        <button class="button" type="reset">清除筛选</button>
      </form>
      <p class="journal-filter-status" id="journal-filter-status" role="status">显示全部 {len(stats)} 本期刊。</p>
      <table>
        <thead>
          <tr>
            <th>期刊</th>
            <th>条目</th>
            <th>中文</th>
            <th>来源</th>
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

    const journalFilters = document.getElementById("journal-filters");
    const journalSearch = document.getElementById("journal-search");
    const topicFilter = document.getElementById("topic-filter");
    const sourceFilter = document.getElementById("source-filter");
    const statusFilter = document.getElementById("status-filter");
    const journalFilterStatus = document.getElementById("journal-filter-status");
    const journalRows = Array.from(document.querySelectorAll("[data-journal-row]"));

    const applyJournalFilters = () => {{
      const search = journalSearch.value.trim().toLowerCase();
      const topic = topicFilter.value;
      const source = sourceFilter.value;
      const status = statusFilter.value;
      let visible = 0;

      journalRows.forEach((row) => {{
        const matches = (
          (!search || row.dataset.search.includes(search)) &&
          (!topic || row.dataset.topics.split(" ").includes(topic)) &&
          (!source || row.dataset.source === source) &&
          (!status || row.dataset.status === status)
        );
        row.hidden = !matches;
        if (matches) visible += 1;
      }});

      journalFilterStatus.textContent = visible
        ? `显示 ${{visible}} / ${{journalRows.length}} 本期刊。`
        : "没有匹配的期刊。请调整或清除筛选条件。";
    }};

    journalFilters.addEventListener("submit", (event) => event.preventDefault());
    journalFilters.addEventListener("input", applyJournalFilters);
    journalFilters.addEventListener("change", applyJournalFilters);
    journalFilters.addEventListener("reset", () => window.requestAnimationFrame(applyJournalFilters));
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
        if name.endswith(".xml") or name.endswith(".md") or name.endswith(".html") or name == "manifest.json":
            try:
                force = urllib.parse.parse_qs(parsed.query).get("refresh") == ["1"]
                if force or not self.rendered or time.time() - self.rendered_at > FEED_TTL_SECONDS:
                    self.__class__.rendered = generate_all_feeds()
                    self.__class__.rendered_at = time.time()
                body = self.rendered.get(name)
                if body is None:
                    self.send_text(404, "Not found\n", "text/plain")
                    return
                if name.endswith(".html"):
                    content_type = "text/html"
                elif name == "manifest.json":
                    content_type = "application/json"
                elif name.endswith(".md"):
                    content_type = "text/markdown"
                else:
                    content_type = "application/rss+xml"
                self.send_text(200, body, content_type)
            except Exception as error:
                self.send_text(502, f"Feed proxy error: {error}\n", "text/plain")
            return
        self.send_text(404, "Not found\n", "text/plain")


def write_static_site(output_path):
    output_dir = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(output_dir, exist_ok=True)
    outputs = generate_all_feeds()
    validate_static_outputs(outputs, load_journals())
    for name, content in outputs.items():
        destination = os.path.join(output_dir, name)
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        with open(destination, "w", encoding="utf-8") as file:
            file.write(content)
            file.write("\n")
    with open(os.path.join(output_dir, ".nojekyll"), "w", encoding="utf-8") as file:
        file.write("")
    weekly = json.loads(outputs["manifest.json"]).get("weekly", {})
    if os.path.isdir(OBSIDIAN_WEEKLY_DIR) and weekly.get("dated_markdown"):
        obsidian_file = os.path.join(OBSIDIAN_WEEKLY_DIR, f"翻译学新论文速递-{os.path.basename(weekly['dated_markdown'])}")
        with open(obsidian_file, "w", encoding="utf-8") as file:
            file.write(outputs[weekly["dated_markdown"]])
        print(f"Synced weekly digest to {obsidian_file}")
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
