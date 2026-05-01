from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import textwrap
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape, unescape
from pathlib import Path
from typing import Any

import feedparser
import httpx
import trafilatura

USER_AGENT = "rss-ai-summary-bot/0.1"
STATE_VERSION = 1
DEFAULT_LLM_PROVIDER = "openrouter"
DEFAULT_GEMINI_MODEL = "gemini-2.0-flash-lite"
DEFAULT_OPENROUTER_MODEL = "stepfun/step-3.5-flash:free"
DEFAULT_SUMMARY_LANGUAGE = "English"
LLM_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
LLM_MAX_ATTEMPTS = 4
SECRET_LIKE_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bxai-[A-Za-z0-9_-]{16,}\b"),
)
PRIORITY_PAPER_TOPICS = (
    ("AI", (re.compile(r"\bartificial intelligence\b", re.IGNORECASE), re.compile(r"\bai\b", re.IGNORECASE))),
    (
        "Deep Learning",
        (re.compile(r"\bdeep[- ]learning\b", re.IGNORECASE),),
    ),
    (
        "Machine Learning",
        (re.compile(r"\bmachine[- ]learning\b", re.IGNORECASE), re.compile(r"\bml\b", re.IGNORECASE)),
    ),
    (
        "Bacteria",
        (
            re.compile(r"\bbacteria\b", re.IGNORECASE),
            re.compile(r"\bbacterial\b", re.IGNORECASE),
            re.compile(r"\bbacterium\b", re.IGNORECASE),
        ),
    ),
    (
        "Microbiology",
        (
            re.compile(r"\bmicrobiology\b", re.IGNORECASE),
            re.compile(r"\bmicrobiological\b", re.IGNORECASE),
            re.compile(r"\bmicrobial\b", re.IGNORECASE),
            re.compile(r"\bmicrobe(?:s)?\b", re.IGNORECASE),
        ),
    ),
    (
        "Phages",
        (
            re.compile(r"\bphage(?:s)?\b", re.IGNORECASE),
            re.compile(r"\bbacteriophage(?:s)?\b", re.IGNORECASE),
        ),
    ),
    ("Plasmids", (re.compile(r"\bplasmid(?:s)?\b", re.IGNORECASE),)),
    (
        "DNA Modification/Methylation",
        (
            re.compile(r"\bdna methylation\b", re.IGNORECASE),
            re.compile(r"\bmethylation\b", re.IGNORECASE),
            re.compile(r"\bmethylome\b", re.IGNORECASE),
            re.compile(r"\bdna modification(?:s)?\b", re.IGNORECASE),
            re.compile(r"\brestriction[- ]modification\b", re.IGNORECASE),
            re.compile(r"\bepigen(?:etic|ome)\b", re.IGNORECASE),
        ),
    ),
    (
        "Computational Methods",
        (
            re.compile(
                r"\bcomputational (?:method|methods|framework|frameworks|approach|approaches|pipeline|pipelines|tool|tools|modeling)\b",
                re.IGNORECASE,
            ),
            re.compile(r"\bbioinformatics\b", re.IGNORECASE),
            re.compile(r"\bin silico\b", re.IGNORECASE),
            re.compile(r"\balgorithm(?:s|ic)?\b", re.IGNORECASE),
            re.compile(r"\bmethod development\b", re.IGNORECASE),
        ),
    ),
)


@dataclass
class FeedConfig:
    category: str
    title: str
    xml_url: str
    html_url: str | None = None


def main() -> int:
    args = parse_args()
    config = load_runtime_config(args)
    report = build_digest_report(config)
    write_outputs(config, report)
    print(
        f"Generated digest for {report['feeds_with_updates']} feed(s) "
        f"with {report['total_items']} item(s)."
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate an AI summarized RSS digest from an OPML file."
    )
    parser.add_argument(
        "--opml-path",
        default="subscriptions.opml",
        help="Path to the NetNewsWire OPML export.",
    )
    parser.add_argument(
        "--state-path",
        default="data/state.json",
        help="Path to the persisted backlog state JSON file.",
    )
    parser.add_argument(
        "--latest-path",
        default="data/latest.json",
        help="Path to the latest digest JSON output.",
    )
    parser.add_argument(
        "--site-dir",
        default="site",
        help="Directory where the generated static site is written.",
    )
    parser.add_argument(
        "--mock-summary",
        action="store_true",
        help="Skip the configured LLM and synthesize summaries from the feed titles for local testing.",
    )
    return parser.parse_args()


def load_runtime_config(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "opml_path": Path(args.opml_path),
        "state_path": Path(args.state_path),
        "latest_path": Path(args.latest_path),
        "site_dir": Path(args.site_dir),
        "mock_summary": args.mock_summary,
        "llm_provider": os.getenv("LLM_PROVIDER", DEFAULT_LLM_PROVIDER).strip().lower()
        or DEFAULT_LLM_PROVIDER,
        "gemini_api_key": os.getenv("GEMINI_API_KEY", "").strip(),
        "gemini_model": os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip()
        or DEFAULT_GEMINI_MODEL,
        "openrouter_api_key": os.getenv("OPENROUTER_API_KEY", "").strip(),
        "openrouter_model": os.getenv(
            "OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL
        ).strip()
        or DEFAULT_OPENROUTER_MODEL,
        "summary_language": os.getenv("SUMMARY_LANGUAGE", DEFAULT_SUMMARY_LANGUAGE).strip()
        or DEFAULT_SUMMARY_LANGUAGE,
        "site_title": os.getenv("SITE_TITLE", "Daily Feed TLDR").strip() or "Daily Feed TLDR",
        "min_feed_content_chars": positive_int_env("MIN_FEED_CONTENT_CHARS", 700),
        "max_item_chars": positive_int_env("MAX_ITEM_CHARS", 4000),
        "max_prompt_chars": positive_int_env("MAX_PROMPT_CHARS", 18000),
    }


def positive_int_env(name: str, default: int) -> int:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be an integer.") from exc
    if value <= 0:
        raise ValueError(f"Environment variable {name} must be greater than zero.")
    return value


def build_digest_report(config: dict[str, Any]) -> dict[str, Any]:
    if not config["opml_path"].exists():
        raise FileNotFoundError(f"OPML file not found: {config['opml_path']}")
    validate_llm_config(config)

    feeds = parse_opml(config["opml_path"])
    state = load_state(config["state_path"])
    migrate_state_from_latest(state, config["latest_path"])
    generated_at = utc_now_iso()

    report: dict[str, Any] = {
        "site_title": config["site_title"],
        "generated_at": generated_at,
        "generated_at_human": human_timestamp(generated_at),
        "model": active_model_name(config),
        "summary_language": config["summary_language"],
        "total_feeds": len(feeds),
        "feeds_with_updates": 0,
        "total_items": 0,
        "feeds": [],
        "errors": [],
    }

    transport = httpx.HTTPTransport(retries=2)
    timeout = httpx.Timeout(20.0, connect=10.0)
    with httpx.Client(
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
        transport=transport,
    ) as client:
        for feed in feeds:
            digest = process_feed(client, config, state, feed)
            if digest["error"]:
                report["errors"].append(
                    {
                        "feed": feed.title,
                        "category": feed.category,
                        "message": digest["error"],
                    }
                )
            if digest["summary"]:
                report["feeds"].append(digest["summary"])
                report["feeds_with_updates"] += 1
                report["total_items"] += digest["summary"]["item_count"]

    state["version"] = STATE_VERSION
    state["updated_at"] = generated_at
    state["feed_count"] = len(feeds)
    report["categories"] = ordered_categories(report["feeds"])
    report["state"] = state
    return report


def parse_opml(opml_path: Path) -> list[FeedConfig]:
    tree = ET.parse(opml_path)
    root = tree.getroot()
    body = root.find("body")
    if body is None:
        raise ValueError("The OPML file does not have a <body> section.")

    feeds: list[FeedConfig] = []

    def visit(node: ET.Element, category: str) -> None:
        for outline in node.findall("outline"):
            xml_url = outline.attrib.get("xmlUrl")
            title = (
                outline.attrib.get("title")
                or outline.attrib.get("text")
                or xml_url
                or "Untitled feed"
            )
            if xml_url:
                feeds.append(
                    FeedConfig(
                        category=category,
                        title=title,
                        xml_url=xml_url,
                        html_url=outline.attrib.get("htmlUrl"),
                    )
                )
                continue
            nested_category = outline.attrib.get("title") or outline.attrib.get("text") or category
            visit(outline, nested_category)

    visit(body, "Ungrouped")
    return feeds


def process_feed(
    client: httpx.Client,
    config: dict[str, Any],
    state: dict[str, Any],
    feed: FeedConfig,
) -> dict[str, Any]:
    feed_state = state.setdefault("feeds", {}).setdefault(feed.xml_url, {})
    pending_items = load_pending_items(feed_state, feed.xml_url)
    stored_title = feed_state.get("feed_title") or feed.title
    stored_site_url = feed_state.get("site_url") or feed.html_url or feed.xml_url
    stored_feed = FeedConfig(
        category=feed.category,
        title=stored_title,
        xml_url=feed.xml_url,
        html_url=feed.html_url,
    )
    try:
        parsed_feed = fetch_feed(client, feed.xml_url)
    except Exception as exc:
        if not pending_items:
            return {"summary": None, "error": f"Feed fetch failed: {safe_error_message(exc)}"}
        stored_summary = summary_from_state(feed_state, stored_feed, pending_items)
        return {
            "summary": build_feed_digest(
                feed=stored_feed,
                site_url=stored_site_url,
                items=pending_items,
                summary=stored_summary,
            ),
            "error": f"Feed fetch failed: {safe_error_message(exc)}",
        }

    seen_ids = list(feed_state.get("seen_ids", []))
    seen_lookup = set(seen_ids)
    normalized_entries = normalize_entries(parsed_feed, feed)
    pending_signatures = {item["signature"] for item in pending_items}

    new_entries = [
        entry
        for entry in normalized_entries
        if entry["id"] not in seen_lookup and entry["signature"] not in pending_signatures
    ]
    feed_title = parsed_feed.feed.get("title") or feed.title
    display_feed = FeedConfig(
        category=feed.category,
        title=feed_title,
        xml_url=feed.xml_url,
        html_url=feed.html_url,
    )
    site_url = feed.html_url or parsed_feed.feed.get("link") or feed.xml_url

    new_pending_items = []
    for entry in new_entries:
        source_text, source_kind = resolve_entry_text(
            client,
            entry,
            min_feed_content_chars=config["min_feed_content_chars"],
            max_item_chars=config["max_item_chars"],
        )
        new_pending_items.append(
            {
                "id": entry["id"],
                "signature": entry["signature"],
                "title": entry["title"],
                "link": entry["link"],
                "published": entry["published"],
                "sort_key": entry["sort_key"],
                "source_kind": source_kind,
                "text": source_text,
            }
        )

    pending_items = merge_pending_items(feed.xml_url, pending_items, new_pending_items)

    feed_state["pending_items"] = pending_items
    feed_state["seen_ids"] = unique_values([entry["id"] for entry in new_entries] + seen_ids)
    feed_state["last_checked_at"] = utc_now_iso()
    feed_state["feed_title"] = feed_title
    feed_state["site_url"] = site_url

    if not pending_items:
        return {"summary": None, "error": None}

    summary_inputs = [pending_item_summary_input(item) for item in pending_items]

    summary_error = None
    try:
        summary = summarize_feed(config, display_feed, summary_inputs)
    except Exception as exc:
        if is_llm_auth_error(exc):
            raise RuntimeError(
                f"{config['llm_provider']} authentication failed: {safe_error_message(exc)}"
            ) from exc
        summary = fallback_summary(display_feed, pending_items)
        summary_error = f"LLM summary failed, used fallback: {safe_error_message(exc)}"

    pending_items = apply_item_tldrs(pending_items, summary.get("item_tldrs"))
    feed_state["pending_items"] = pending_items
    feed_state["summary"] = summary

    return {
        "summary": build_feed_digest(
            feed=display_feed,
            site_url=site_url,
            items=pending_items,
            summary=summary,
        ),
        "error": summary_error,
    }


def fetch_feed(client: httpx.Client, url: str) -> feedparser.FeedParserDict:
    response = client.get(url)
    response.raise_for_status()
    parsed = feedparser.parse(response.content)
    if parsed.bozo and not parsed.entries:
        raise ValueError(str(parsed.bozo_exception))
    return parsed


def normalize_entries(
    parsed_feed: feedparser.FeedParserDict,
    feed: FeedConfig,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for entry in parsed_feed.entries:
        timestamp = entry_timestamp(entry)
        entries.append(
            {
                "id": entry_fingerprint(feed.xml_url, entry),
                "title": normalize_text(entry.get("title") or "Untitled item"),
                "link": entry.get("link"),
                "published": timestamp.strftime("%Y-%m-%d %H:%M UTC") if timestamp else None,
                "sort_key": timestamp.timestamp() if timestamp else 0,
                "feed_text": feed_entry_text(entry),
                "signature": item_signature(
                    feed.xml_url,
                    title=normalize_text(entry.get("title") or "Untitled item"),
                    link=entry.get("link"),
                    published=timestamp.strftime("%Y-%m-%d %H:%M UTC") if timestamp else None,
                ),
            }
        )
    entries.sort(key=lambda item: item["sort_key"], reverse=True)
    return entries


def entry_timestamp(entry: feedparser.FeedParserDict) -> datetime | None:
    for field_name in ("published_parsed", "updated_parsed", "created_parsed"):
        value = entry.get(field_name)
        if not value:
            continue
        try:
            return datetime(*value[:6], tzinfo=timezone.utc)
        except Exception:
            continue
    return None


def entry_fingerprint(feed_url: str, entry: feedparser.FeedParserDict) -> str:
    basis = "|".join(
        [
            feed_url,
            entry.get("id", ""),
            entry.get("link", ""),
            entry.get("title", ""),
            entry.get("published", ""),
        ]
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def item_signature(
    feed_url: str,
    *,
    title: str,
    link: str | None,
    published: str | None,
) -> str:
    basis = "|".join([feed_url, link or "", title or "", published or ""])
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def feed_entry_text(entry: feedparser.FeedParserDict) -> str:
    chunks: list[str] = []
    for item in entry.get("content", []):
        value = item.get("value")
        if value:
            chunks.append(value)
    if entry.get("summary"):
        chunks.append(entry["summary"])
    if entry.get("description"):
        chunks.append(entry["description"])
    return normalize_text("\n\n".join(chunks))


def resolve_entry_text(
    client: httpx.Client,
    entry: dict[str, Any],
    *,
    min_feed_content_chars: int,
    max_item_chars: int,
) -> tuple[str, str]:
    feed_text = clip_text(entry["feed_text"], max_item_chars)
    if len(feed_text) >= min_feed_content_chars or not entry.get("link"):
        return feed_text or entry["title"], "feed"

    try:
        article_text = extract_article_text(client, entry["link"])
    except Exception:
        article_text = ""

    article_text = clip_text(article_text, max_item_chars)
    if len(article_text) > len(feed_text):
        return article_text, "article"
    return feed_text or entry["title"], "feed"


def extract_article_text(client: httpx.Client, url: str) -> str:
    response = client.get(url)
    response.raise_for_status()
    extracted = trafilatura.extract(
        response.text,
        url=url,
        favor_precision=True,
        include_comments=False,
        include_tables=False,
        no_fallback=False,
    )
    return normalize_text(extracted or "")


def summarize_feed(
    config: dict[str, Any],
    feed: FeedConfig,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    if config["mock_summary"]:
        return fallback_summary(feed, items)

    payload_items = []
    remaining_chars = config["max_prompt_chars"]
    for item in items:
        clipped_text = clip_text(item["text"], min(len(item["text"]), remaining_chars))
        if not clipped_text:
            continue
        payload_items.append(
            {
                "id": item["id"],
                "title": item["title"],
                "published": item["published"],
                "link": item["link"],
                "text": clipped_text,
            }
        )
        remaining_chars -= len(clipped_text)
        if remaining_chars <= 0:
            break

    if not payload_items:
        return fallback_summary(feed, items)

    if is_papers_category(feed.category):
        return summarize_paper_items(config, feed, items, payload_items)

    prompt = textwrap.dedent(
        f"""
        You are creating a daily digest for a single RSS feed.

        Rules:
        - Reply with valid JSON only.
        - Write in {config['summary_language']}.
        - Use only the supplied source material.
        - Be concise and avoid hype.
        - Capture the common themes or the most important changes across the feed.
        - Do not fabricate details that are not present in the sources.

        Return this exact JSON shape:
        {{
          "tldr": "2-4 sentence feed-level summary",
          "highlights": ["short bullet", "short bullet", "short bullet"]
        }}

        Feed:
        {json.dumps({"title": feed.title, "category": feed.category}, ensure_ascii=False, indent=2)}

        Items:
        {json.dumps(payload_items, ensure_ascii=False, indent=2)}
        """
    ).strip()

    response_data = call_llm_json(config=config, prompt=prompt)
    tldr = normalize_text(str(response_data.get("tldr", "")))
    highlights = [
        normalize_text(str(item))
        for item in response_data.get("highlights", [])
        if normalize_text(str(item))
    ]
    if not tldr:
        raise ValueError("LLM response did not contain a TLDR.")
    if not highlights:
        highlights = [item["title"] for item in items[:3]]
    return {"tldr": tldr, "highlights": highlights[:5]}


def summarize_paper_items(
    config: dict[str, Any],
    feed: FeedConfig,
    items: list[dict[str, Any]],
    payload_items: list[dict[str, Any]],
) -> dict[str, Any]:
    numbered_items = []
    payload_lookup: dict[int, dict[str, Any]] = {}
    for number, item in enumerate(payload_items, start=1):
        numbered_items.append(
            {
                "number": number,
                "title": item["title"],
                "published": item["published"],
                "link": item["link"],
                "text": item["text"],
            }
        )
        payload_lookup[number] = item

    prompt = textwrap.dedent(
        f"""
        You are creating a daily digest for a single research paper feed.

        Rules:
        - Reply with valid JSON only.
        - Write in {config['summary_language']}.
        - Use only the supplied source material.
        - Be concise and avoid hype.
        - For each paper, write a brief 1-2 sentence TLDR focused on the main contribution, result, or method.
        - If the source is sparse, summarize only what is clearly supported by the title and text.
        - Do not fabricate details that are not present in the sources.

        Return this exact JSON shape:
        {{
          "tldr": "2-4 sentence feed-level summary",
          "highlights": ["short bullet", "short bullet", "short bullet"],
          "item_tldrs": [
            {{"number": 1, "tldr": "1-2 sentence paper summary"}}
          ]
        }}

        Feed:
        {json.dumps({"title": feed.title, "category": feed.category}, ensure_ascii=False, indent=2)}

        Papers:
        {json.dumps(numbered_items, ensure_ascii=False, indent=2)}
        """
    ).strip()

    response_data = call_llm_json(config=config, prompt=prompt)
    tldr = normalize_text(str(response_data.get("tldr", "")))
    highlights = [
        normalize_text(str(item))
        for item in response_data.get("highlights", [])
        if normalize_text(str(item))
    ]
    if not tldr:
        raise ValueError("LLM response did not contain a TLDR.")
    if not highlights:
        highlights = [item["title"] for item in items[:3]]

    item_tldrs: dict[str, str] = {}
    for item in response_data.get("item_tldrs", []):
        if not isinstance(item, dict):
            continue
        number = item.get("number")
        if not isinstance(number, int):
            continue
        source_item = payload_lookup.get(number)
        if not source_item:
            continue
        item_tldr = normalize_text(str(item.get("tldr", "")))
        if item_tldr:
            item_tldrs[source_item["id"]] = item_tldr

    if not item_tldrs:
        item_tldrs = fallback_item_tldrs(items)
    return {"tldr": tldr, "highlights": highlights[:5], "item_tldrs": item_tldrs}


def validate_llm_config(config: dict[str, Any]) -> None:
    if config["mock_summary"]:
        return

    provider = config["llm_provider"]
    if provider == "gemini":
        if not config["gemini_api_key"]:
            raise ValueError(
                "GEMINI_API_KEY is required when LLM_PROVIDER=gemini unless --mock-summary is enabled."
            )
        return
    if provider == "openrouter":
        if not config["openrouter_api_key"]:
            raise ValueError(
                "OPENROUTER_API_KEY is required when LLM_PROVIDER=openrouter unless --mock-summary is enabled."
            )
        return
    raise ValueError(
        f"LLM_PROVIDER must be one of: gemini, openrouter. Got: {provider!r}"
    )


def migrate_state_from_latest(state: dict[str, Any], latest_path: Path) -> None:
    if not latest_path.exists():
        return

    try:
        latest_report = json.loads(latest_path.read_text(encoding="utf-8"))
    except Exception:
        return

    for feed in latest_report.get("feeds", []):
        feed_url = feed.get("feed_url")
        if not feed_url:
            continue

        feed_state = state.setdefault("feeds", {}).setdefault(feed_url, {})
        if feed_state.get("pending_items"):
            continue

        pending_items = [
            migrated_pending_item(feed_url, item) for item in feed.get("items", [])
        ]
        if not pending_items:
            continue

        feed_state["pending_items"] = pending_items
        if feed.get("title"):
            feed_state["feed_title"] = feed["title"]
        if feed.get("site_url"):
            feed_state["site_url"] = feed["site_url"]

        tldr = normalize_text(str(feed.get("tldr", "")))
        highlights = [
            normalize_text(str(item))
            for item in feed.get("highlights", [])
            if normalize_text(str(item))
        ]
        if tldr:
            feed_state["summary"] = {
                "tldr": tldr,
                "highlights": highlights or [item["title"] for item in pending_items[:3]],
            }


def migrated_pending_item(feed_url: str, item: dict[str, Any]) -> dict[str, Any]:
    title = normalize_text(str(item.get("title") or "Untitled item"))
    published = item.get("published")
    signature = item_signature(
        feed_url,
        title=title,
        link=item.get("link"),
        published=published,
    )
    return {
        "id": f"migrated:{signature}",
        "signature": signature,
        "title": title,
        "link": item.get("link"),
        "published": published,
        "sort_key": rendered_timestamp_sort_key(published),
        "source_kind": item.get("source_kind"),
        "text": title,
        "tldr": normalize_text(str(item.get("tldr") or "")),
    }


def load_pending_items(feed_state: dict[str, Any], feed_url: str) -> list[dict[str, Any]]:
    pending_items = [
        normalize_pending_item(feed_url, item)
        for item in feed_state.get("pending_items", [])
        if isinstance(item, dict)
    ]
    pending_items.sort(key=lambda item: item["sort_key"], reverse=True)
    return pending_items


def normalize_pending_item(feed_url: str, item: dict[str, Any]) -> dict[str, Any]:
    title = normalize_text(str(item.get("title") or "Untitled item"))
    published = item.get("published")
    sort_key = item.get("sort_key")
    if not isinstance(sort_key, (int, float)):
        sort_key = rendered_timestamp_sort_key(published)

    signature = item.get("signature")
    if not signature:
        signature = item_signature(
            feed_url,
            title=title,
            link=item.get("link"),
            published=published,
        )

    item_id = item.get("id") or f"pending:{signature}"
    return {
        "id": str(item_id),
        "signature": str(signature),
        "title": title,
        "link": item.get("link"),
        "published": published,
        "sort_key": float(sort_key),
        "source_kind": item.get("source_kind"),
        "text": normalize_text(str(item.get("text") or title)),
        "tldr": normalize_text(str(item.get("tldr") or "")),
    }


def merge_pending_items(
    feed_url: str,
    existing_items: list[dict[str, Any]],
    new_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for item in new_items + existing_items:
        normalized = normalize_pending_item(feed_url, item)
        merged.setdefault(normalized["signature"], normalized)
    return sorted(merged.values(), key=lambda item: item["sort_key"], reverse=True)


def pending_item_summary_input(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "title": item["title"],
        "link": item.get("link"),
        "published": item.get("published"),
        "text": item.get("text") or item["title"],
    }


def pending_item_output(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": item["title"],
        "link": item.get("link"),
        "published": item.get("published"),
        "source_kind": item.get("source_kind"),
        "tldr": item.get("tldr"),
    }


def apply_item_tldrs(
    items: list[dict[str, Any]], item_tldrs: dict[str, str] | None
) -> list[dict[str, Any]]:
    if not item_tldrs:
        return items

    updated_items = []
    for item in items:
        updated_item = dict(item)
        item_tldr = normalize_text(str(item_tldrs.get(item["id"], item.get("tldr") or "")))
        if item_tldr:
            updated_item["tldr"] = item_tldr
        updated_items.append(updated_item)
    return updated_items


def summary_from_state(
    feed_state: dict[str, Any],
    feed: FeedConfig,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    stored_summary = feed_state.get("summary", {})
    tldr = normalize_text(str(stored_summary.get("tldr", "")))
    highlights = [
        normalize_text(str(item))
        for item in stored_summary.get("highlights", [])
        if normalize_text(str(item))
    ]
    if tldr:
        return {
            "tldr": tldr,
            "highlights": highlights or [item["title"] for item in items[:3]],
        }
    return fallback_summary(feed, items)


def build_feed_digest(
    *,
    feed: FeedConfig,
    site_url: str,
    items: list[dict[str, Any]],
    summary: dict[str, Any],
) -> dict[str, Any]:
    output_items = []
    for item in items:
        output_item = pending_item_output(item)
        if is_papers_category(feed.category):
            topic_matches = paper_topic_matches(item)
            if topic_matches:
                output_item["topic_matches"] = topic_matches
        output_items.append(output_item)
    return {
        "category": feed.category,
        "title": feed.title,
        "site_url": site_url,
        "feed_url": feed.xml_url,
        "item_count": len(output_items),
        "skipped_count": 0,
        "tldr": summary["tldr"],
        "highlights": summary["highlights"],
        "items": output_items,
    }


def is_papers_category(category: str) -> bool:
    return category.strip().lower() == "papers"


def paper_topic_matches(item: dict[str, Any]) -> list[str]:
    search_text = " ".join(
        normalize_text(str(item.get(field) or ""))
        for field in ("title", "tldr", "text")
        if item.get(field)
    )
    if not search_text:
        return []

    matches = []
    for label, patterns in PRIORITY_PAPER_TOPICS:
        if any(pattern.search(search_text) for pattern in patterns):
            matches.append(label)
    return matches


def active_model_name(config: dict[str, Any]) -> str:
    if config["mock_summary"]:
        return "mock-summary"
    if config["llm_provider"] == "gemini":
        return config["gemini_model"]
    return config["openrouter_model"]


def call_llm_json(*, config: dict[str, Any], prompt: str) -> dict[str, Any]:
    if config["llm_provider"] == "gemini":
        return call_gemini_json(
            api_key=config["gemini_api_key"],
            model=config["gemini_model"],
            prompt=prompt,
        )
    if config["llm_provider"] == "openrouter":
        return call_openrouter_json(
            api_key=config["openrouter_api_key"],
            model=config["openrouter_model"],
            prompt=prompt,
        )
    raise ValueError(f"Unsupported LLM provider: {config['llm_provider']}")


def call_gemini_json(*, api_key: str, model: str, prompt: str) -> dict[str, Any]:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
        },
    }
    for attempt in range(1, LLM_MAX_ATTEMPTS + 1):
        try:
            response = httpx.post(
                url,
                params={"key": api_key},
                json=payload,
                headers={"User-Agent": USER_AGENT},
                timeout=60.0,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if (
                status_code not in LLM_RETRYABLE_STATUS_CODES
                or attempt == LLM_MAX_ATTEMPTS
            ):
                raise
        except httpx.HTTPError:
            if attempt == LLM_MAX_ATTEMPTS:
                raise
        else:
            data = response.json()
            candidates = data.get("candidates") or []
            if not candidates:
                raise ValueError("Gemini returned no candidates.")
            parts = candidates[0].get("content", {}).get("parts", [])
            if not parts:
                raise ValueError("Gemini returned no content parts.")
            text = parts[0].get("text", "")
            if not text:
                raise ValueError("Gemini returned an empty response body.")
            return json.loads(strip_code_fences(text))

        # Back off on rate limits and transient upstream errors.
        time.sleep(min(20, 5 * attempt))

    raise RuntimeError("Gemini request retries exhausted unexpectedly.")


def call_openrouter_json(*, api_key: str, model: str, prompt: str) -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "https://example.com/rss-ai-summary"),
        "X-OpenRouter-Title": "rss-ai-summary",
        "User-Agent": USER_AGENT,
    }
    for attempt in range(1, LLM_MAX_ATTEMPTS + 1):
        try:
            response = httpx.post(
                "https://openrouter.ai/api/v1/chat/completions",
                json=payload,
                headers=headers,
                timeout=60.0,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if (
                status_code not in LLM_RETRYABLE_STATUS_CODES
                or attempt == LLM_MAX_ATTEMPTS
            ):
                raise
        except httpx.HTTPError:
            if attempt == LLM_MAX_ATTEMPTS:
                raise
        else:
            data = response.json()
            choices = data.get("choices") or []
            if not choices:
                raise ValueError("OpenRouter returned no choices.")
            text = openrouter_message_text(choices[0].get("message", {}).get("content"))
            if not text:
                raise ValueError("OpenRouter returned an empty response body.")
            return json.loads(strip_code_fences(text))

        time.sleep(min(20, 5 * attempt))

    raise RuntimeError("OpenRouter request retries exhausted unexpectedly.")


def openrouter_message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        content = [content]
    if isinstance(content, list):
        chunks = []
        for part in content:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if not text:
                continue
            if part.get("type") in {None, "text", "output_text"}:
                chunks.append(str(text))
        return "\n".join(chunks).strip()
    return ""


def strip_code_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def safe_error_message(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code} {exc.response.reason_phrase}"
    return redact_sensitive_text(str(exc))


def is_llm_auth_error(exc: Exception) -> bool:
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {401, 403}


def redact_secret_like_text(text: str) -> str:
    redacted = text or ""
    for pattern in SECRET_LIKE_PATTERNS:
        redacted = pattern.sub("[REDACTED_SECRET]", redacted)
    return redacted


def redact_sensitive_text(text: str) -> str:
    return re.sub(r"([?&]key=)[^&\s'\"]+", r"\1[REDACTED]", text or "")


def fallback_summary(feed: FeedConfig, items: list[dict[str, Any]]) -> dict[str, Any]:
    highlight_titles = [item["title"] for item in items[:3] if item.get("title")]
    if not highlight_titles:
        highlight_titles = [f"Tracked items are available in {feed.title}."]
    tldr = (
        f"{feed.title} currently has {len(items)} retained item(s) on the page. "
        f"Key items include {join_titles(highlight_titles)}."
    )
    summary = {"tldr": tldr, "highlights": highlight_titles}
    if is_papers_category(feed.category):
        summary["item_tldrs"] = fallback_item_tldrs(items)
    return summary


def fallback_item_tldrs(items: list[dict[str, Any]]) -> dict[str, str]:
    item_tldrs = {}
    for item in items:
        source_text = normalize_text(str(item.get("text") or item.get("title") or ""))
        sentences = re.split(r"(?<=[.!?])\s+", source_text)
        item_tldr = clip_text(" ".join(sentences[:2]).strip() or source_text, 260)
        if item_tldr:
            item_tldrs[item["id"]] = item_tldr
    return item_tldrs


def join_titles(titles: list[str]) -> str:
    if len(titles) == 1:
        return f'"{titles[0]}"'
    if len(titles) == 2:
        return f'"{titles[0]}" and "{titles[1]}"'
    return ", ".join(f'"{title}"' for title in titles[:-1]) + f', and "{titles[-1]}"'


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": STATE_VERSION, "feeds": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def write_outputs(config: dict[str, Any], report: dict[str, Any]) -> None:
    state = report.pop("state")
    ensure_parent(config["state_path"])
    ensure_parent(config["latest_path"])
    config["site_dir"].mkdir(parents=True, exist_ok=True)

    config["state_path"].write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    config["latest_path"].write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (config["site_dir"] / ".nojekyll").write_text("\n", encoding="utf-8")
    (config["site_dir"] / "index.html").write_text(
        render_site(report),
        encoding="utf-8",
    )


def render_site(report: dict[str, Any]) -> str:
    cards = []
    has_priority_papers = any(
        item.get("topic_matches")
        for feed in report["feeds"]
        if is_papers_category(feed["category"])
        for item in feed["items"]
    )
    if report["feeds"]:
        for category in report["categories"]:
            category_feeds = [feed for feed in report["feeds"] if feed["category"] == category]
            cards.append(
                f"""
                <section class="category">
                  <h2>{escape(category)}</h2>
                  {render_paper_rows(category_feeds) if is_papers_category(category) else render_feed_rows(category_feeds)}
                </section>
                """
            )
    else:
        cards.append(
            """
            <div class="empty">
              <p>No retained items</p>
              <p>The workflow completed, but there are no items currently being kept on the page.</p>
            </div>
            """
        )

    errors_html = ""
    if report["errors"]:
        errors_html = (
            "<section class=\"errors\"><h2>Feed Errors</h2><ul>"
            + "".join(
                f"<li><strong>{escape(error['feed'])}</strong>: {escape(error['message'])}</li>"
                for error in report["errors"]
            )
            + "</ul></section>"
        )

    topic_note = ""
    if has_priority_papers:
        topic_note = (
            '<p class="topic-note">Highlighted papers match tracked topics: AI, deep learning, machine learning, bacteria, microbiology, phages, plasmids, DNA modification or methylation, and computational methods.</p>'
        )

    return textwrap.dedent(
        f"""
        <!DOCTYPE html>
        <html lang="en">
          <head>
            <meta charset="utf-8" />
            <meta name="viewport" content="width=device-width, initial-scale=1" />
            <title>{escape(report['site_title'])}</title>
            <style>
              :root {{
                --bg: #f4f4f5;
                --surface: #ffffff;
                --border: #e4e4e7;
                --text: #18181b;
                --muted: #71717a;
                --accent: #2563eb;
                --accent-bg: #dbeafe;
                --topic-accent: #b45309;
                --topic-bg: #fef3c7;
              }}
              @media (prefers-color-scheme: dark) {{
                :root {{
                  --bg: #09090b;
                  --surface: #18181b;
                  --border: #27272a;
                  --text: #fafafa;
                  --muted: #a1a1aa;
                  --accent: #60a5fa;
                  --accent-bg: #1e3a5f;
                  --topic-accent: #fbbf24;
                  --topic-bg: #3f2d12;
                }}
              }}
              * {{ box-sizing: border-box; margin: 0; padding: 0; }}
              body {{
                font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
                background: var(--bg);
                color: var(--text);
                font-size: 15px;
                line-height: 1.5;
              }}
              a {{ color: var(--accent); text-decoration: none; }}
              a:hover {{ text-decoration: underline; }}
              .shell {{ max-width: 960px; margin: 0 auto; padding: 40px 20px 80px; }}
              .page-hd {{
                padding-bottom: 24px;
                margin-bottom: 28px;
                border-bottom: 1px solid var(--border);
              }}
              .page-hd h1 {{
                font-size: 1.6rem;
                font-weight: 700;
                letter-spacing: -0.02em;
                margin-bottom: 4px;
              }}
              .page-hd p {{ color: var(--muted); font-size: 0.88rem; }}
              .topic-note {{ margin-top: 10px; max-width: 760px; line-height: 1.6; }}
              .stats {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 36px; }}
              .stat {{
                background: var(--surface);
                border: 1px solid var(--border);
                border-radius: 6px;
                padding: 8px 14px;
              }}
              .stat-label {{
                font-size: 0.68rem;
                font-weight: 600;
                text-transform: uppercase;
                letter-spacing: 0.07em;
                color: var(--muted);
                margin-bottom: 2px;
              }}
              .stat-value {{ font-size: 0.9rem; font-weight: 600; }}
              .category {{ margin-bottom: 44px; }}
              .category h2 {{
                font-size: 0.68rem;
                font-weight: 700;
                text-transform: uppercase;
                letter-spacing: 0.1em;
                color: var(--muted);
                padding-bottom: 8px;
                margin-bottom: 14px;
                border-bottom: 1px solid var(--border);
              }}
              .feed-list,
              .paper-list {{ display: grid; gap: 10px; }}
              .feed {{
                background: var(--surface);
                border: 1px solid var(--border);
                border-radius: 8px;
                padding: 14px 16px;
                display: grid;
                grid-template-columns: minmax(0, 220px) minmax(0, 1fr) auto;
                gap: 12px;
                align-items: start;
              }}
              .paper {{
                background: var(--surface);
                border: 1px solid var(--border);
                border-radius: 8px;
                padding: 14px 16px;
                display: grid;
                grid-template-columns: minmax(0, 260px) minmax(0, 1fr) minmax(0, 180px);
                gap: 12px;
                align-items: start;
              }}
              .paper-priority {{ box-shadow: inset 3px 0 0 var(--topic-accent); }}
              .feed-line {{
                display: contents;
              }}
              .feed-title,
              .paper-title {{ font-size: 0.95rem; font-weight: 600; line-height: 1.45; }}
              .feed-title a,
              .paper-title a {{ color: var(--text); }}
              .feed-title a:hover,
              .paper-title a:hover {{ color: var(--accent); text-decoration: none; }}
              .feed-meta {{
                font-size: 0.78rem;
                color: var(--muted);
                display: flex;
                align-items: center;
                gap: 8px;
                white-space: nowrap;
              }}
              .paper-meta {{
                font-size: 0.78rem;
                color: var(--muted);
                line-height: 1.5;
              }}
              .topic-badges {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }}
              .topic-badge {{
                font-size: 0.68rem;
                font-weight: 700;
                letter-spacing: 0.04em;
                padding: 3px 8px;
                border-radius: 999px;
                background: var(--topic-bg);
                color: var(--topic-accent);
              }}
              .rss-badge {{
                font-size: 0.68rem;
                font-weight: 700;
                letter-spacing: 0.04em;
                padding: 3px 7px;
                border-radius: 4px;
                background: var(--accent-bg);
                color: var(--accent);
                white-space: nowrap;
                flex-shrink: 0;
              }}
              .rss-badge:hover {{ text-decoration: none; opacity: 0.75; }}
              .tldr {{
                font-size: 0.85rem;
                color: var(--muted);
                line-height: 1.65;
                margin: 0;
              }}
              .errors {{
                background: var(--surface);
                border: 1px solid var(--border);
                border-radius: 8px;
                padding: 18px;
                margin-top: 28px;
              }}
              .errors h2 {{ font-size: 0.88rem; font-weight: 600; color: #dc2626; margin-bottom: 10px; }}
              .errors ul {{ padding-left: 18px; font-size: 0.84rem; }}
              .errors li {{ margin-bottom: 6px; }}
              .empty {{
                background: var(--surface);
                border: 1px solid var(--border);
                border-radius: 8px;
                padding: 48px 32px;
                text-align: center;
                color: var(--muted);
                font-size: 0.9rem;
              }}
              .empty p + p {{ margin-top: 6px; }}
              @media (max-width: 600px) {{
                .shell {{ padding: 20px 14px 60px; }}
                .page-hd h1 {{ font-size: 1.3rem; }}
                .feed {{
                  grid-template-columns: 1fr;
                  gap: 6px;
                }}
                .paper {{
                  grid-template-columns: 1fr;
                  gap: 6px;
                }}
                .feed-line {{ display: block; }}
                .feed-meta {{ white-space: normal; }}
              }}
            </style>
          </head>
          <body>
            <main class="shell">
              <header class="page-hd">
                <h1>{escape(report['site_title'])}</h1>
                <p>Daily AI TLDRs generated from your NetNewsWire OPML export and published with GitHub Actions.</p>
                {topic_note}
              </header>

              <div class="stats">
                <div class="stat">
                  <div class="stat-label">Updated</div>
                  <div class="stat-value">{escape(report['generated_at_human'])}</div>
                </div>
                <div class="stat">
                  <div class="stat-label">Feeds</div>
                  <div class="stat-value">{report['feeds_with_updates']}</div>
                </div>
                <div class="stat">
                  <div class="stat-label">Items</div>
                  <div class="stat-value">{report['total_items']}</div>
                </div>
                <div class="stat">
                  <div class="stat-label">Model</div>
                  <div class="stat-value">{escape(report['model'])}</div>
                </div>
              </div>

              {''.join(cards)}
              {errors_html}
            </main>
          </body>
        </html>
        """
    ).strip() + "\n"


def render_feed_rows(feeds: list[dict[str, Any]]) -> str:
    rows = []
    for feed in feeds:
        item_count = feed["item_count"]
        rows.append(
            f"""
            <article class="feed">
              <div class="feed-line">
                <div class="feed-title"><a href="{escape(feed['site_url'])}">{escape(feed['title'])}</a></div>
                <p class="tldr">{escape(feed['tldr'])}</p>
              </div>
              <div class="feed-meta">
                <span>{item_count} items</span>
                <a class="rss-badge" href="{escape(feed['feed_url'])}">RSS</a>
              </div>
            </article>
            """
        )
    return f'<div class="feed-list">{"".join(rows)}</div>'


def render_paper_rows(feeds: list[dict[str, Any]]) -> str:
    rows = []
    for feed in feeds:
        for item in feed["items"]:
            link = item.get("link") or feed.get("site_url") or feed.get("feed_url")
            title = escape(item["title"])
            if link:
                title_html = f'<a href="{escape(link)}">{title}</a>'
            else:
                title_html = title

            meta_bits = [feed["title"]]
            if item.get("published"):
                meta_bits.append(item["published"])
            item_tldr = item.get("tldr") or feed["tldr"]
            topic_matches = item.get("topic_matches", [])
            topic_badges = ""
            if topic_matches:
                topic_badges = (
                    '<div class="topic-badges">'
                    + "".join(
                        f'<span class="topic-badge">{escape(topic)}</span>'
                        for topic in topic_matches
                    )
                    + "</div>"
                )
            rows.append(
                f"""
                <article class="paper{' paper-priority' if topic_matches else ''}">
                  <div class="paper-title">{title_html}</div>
                  <div>
                    <p class="tldr">{escape(item_tldr)}</p>
                    {topic_badges}
                  </div>
                  <div class="paper-meta">{" | ".join(escape(bit) for bit in meta_bits)}</div>
                </article>
                """
            )
    return f'<div class="paper-list">{"".join(rows)}</div>'


def ordered_categories(feeds: list[dict[str, Any]]) -> list[str]:
    categories: list[str] = []
    for feed in feeds:
        if feed["category"] not in categories:
            categories.append(feed["category"])
    return categories


def unique_values(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def rendered_timestamp_sort_key(raw_timestamp: str | None) -> float:
    if not raw_timestamp:
        return 0
    try:
        return datetime.strptime(raw_timestamp, "%Y-%m-%d %H:%M UTC").replace(
            tzinfo=timezone.utc
        ).timestamp()
    except ValueError:
        return 0


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def human_timestamp(raw_timestamp: str) -> str:
    return datetime.fromisoformat(raw_timestamp).strftime("%Y-%m-%d %H:%M UTC")


def clip_text(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    normalized = normalize_text(text)
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 1)].rstrip() + "…"


def normalize_text(text: str) -> str:
    no_tags = re.sub(r"<[^>]+>", " ", text or "")
    compact = re.sub(r"\s+", " ", unescape(no_tags))
    return redact_secret_like_text(compact.strip())
