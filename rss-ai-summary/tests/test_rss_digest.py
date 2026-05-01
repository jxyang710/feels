from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import feedparser

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import rss_digest


class TestStateMigration(unittest.TestCase):
    def test_migrate_state_from_latest_backfills_pending_items(self) -> None:
        state = {
            "version": 1,
            "feeds": {
                "https://example.com/feed.xml": {
                    "seen_ids": ["legacy-seen-id"],
                }
            },
        }
        latest_report = {
            "feeds": [
                {
                    "title": "Example Feed",
                    "site_url": "https://example.com",
                    "feed_url": "https://example.com/feed.xml",
                    "tldr": "Existing summary",
                    "highlights": ["Hello world"],
                    "items": [
                        {
                            "title": "Hello world",
                            "link": "https://example.com/hello",
                            "published": "2026-04-02 13:00 UTC",
                            "source_kind": "feed",
                        }
                    ],
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            latest_path = Path(tmpdir) / "latest.json"
            latest_path.write_text(
                json.dumps(latest_report, ensure_ascii=False),
                encoding="utf-8",
            )

            rss_digest.migrate_state_from_latest(state, latest_path)

        feed_state = state["feeds"]["https://example.com/feed.xml"]
        self.assertEqual(feed_state["feed_title"], "Example Feed")
        self.assertEqual(feed_state["site_url"], "https://example.com")
        self.assertEqual(feed_state["summary"]["tldr"], "Existing summary")
        self.assertEqual(len(feed_state["pending_items"]), 1)
        self.assertEqual(feed_state["pending_items"][0]["title"], "Hello world")
        self.assertEqual(feed_state["pending_items"][0]["text"], "Hello world")


class TestPersistentBacklog(unittest.TestCase):
    def setUp(self) -> None:
        self.feed = rss_digest.FeedConfig(
            category="Tech",
            title="Example Feed",
            xml_url="https://example.com/feed.xml",
            html_url="https://example.com",
        )
        self.config = {
            "min_feed_content_chars": 50,
            "max_item_chars": 4000,
            "mock_summary": False,
        }

    def test_process_feed_appends_new_items_without_dropping_existing_backlog(self) -> None:
        existing_item = {
            "id": "existing-item-id",
            "signature": rss_digest.item_signature(
                self.feed.xml_url,
                title="Existing item",
                link="https://example.com/existing",
                published="2026-04-01 00:00 UTC",
            ),
            "title": "Existing item",
            "link": "https://example.com/existing",
            "published": "2026-04-01 00:00 UTC",
            "sort_key": rss_digest.rendered_timestamp_sort_key("2026-04-01 00:00 UTC"),
            "source_kind": "feed",
            "text": "Existing item body",
        }
        state = {
            "feeds": {
                self.feed.xml_url: {
                    "pending_items": [existing_item],
                    "summary": {
                        "tldr": "Old summary",
                        "highlights": ["Existing item"],
                    },
                    "seen_ids": [],
                }
            }
        }
        parsed_feed = feedparser.FeedParserDict(
            {
                "feed": {"title": "Example Feed", "link": "https://example.com"},
                "entries": [
                    feedparser.FeedParserDict(
                        {
                            "id": "new-entry-id",
                            "title": "New item",
                            "link": "https://example.com/new",
                            "published_parsed": time.strptime(
                                "2026-04-03 00:00:00",
                                "%Y-%m-%d %H:%M:%S",
                            ),
                            "summary": "New item body",
                        }
                    )
                ],
            }
        )

        with (
            mock.patch.object(rss_digest, "fetch_feed", return_value=parsed_feed),
            mock.patch.object(
                rss_digest,
                "resolve_entry_text",
                return_value=("New item body", "feed"),
            ),
            mock.patch.object(
                rss_digest,
                "summarize_feed",
                return_value={
                    "tldr": "Updated summary",
                    "highlights": ["New item", "Existing item"],
                },
            ),
        ):
            result = rss_digest.process_feed(mock.Mock(), self.config, state, self.feed)

        self.assertIsNone(result["error"])
        self.assertEqual(result["summary"]["item_count"], 2)
        self.assertEqual(
            [item["title"] for item in result["summary"]["items"]],
            ["New item", "Existing item"],
        )
        feed_state = state["feeds"][self.feed.xml_url]
        self.assertEqual(len(feed_state["pending_items"]), 2)
        self.assertEqual(feed_state["summary"]["tldr"], "Updated summary")

    def test_process_feed_keeps_existing_backlog_when_feed_fetch_fails(self) -> None:
        state = {
            "feeds": {
                self.feed.xml_url: {
                    "pending_items": [
                        {
                            "id": "existing-item-id",
                            "signature": rss_digest.item_signature(
                                self.feed.xml_url,
                                title="Existing item",
                                link="https://example.com/existing",
                                published="2026-04-01 00:00 UTC",
                            ),
                            "title": "Existing item",
                            "link": "https://example.com/existing",
                            "published": "2026-04-01 00:00 UTC",
                            "sort_key": rss_digest.rendered_timestamp_sort_key(
                                "2026-04-01 00:00 UTC"
                            ),
                            "source_kind": "feed",
                            "text": "Existing item body",
                        }
                    ],
                    "summary": {
                        "tldr": "Stored summary",
                        "highlights": ["Existing item"],
                    },
                    "feed_title": "Example Feed",
                    "site_url": "https://example.com",
                }
            }
        }

        with mock.patch.object(rss_digest, "fetch_feed", side_effect=ValueError("boom")):
            result = rss_digest.process_feed(mock.Mock(), self.config, state, self.feed)

        self.assertIsNotNone(result["summary"])
        self.assertIn("Feed fetch failed", result["error"])
        self.assertEqual(result["summary"]["item_count"], 1)
        self.assertEqual(result["summary"]["tldr"], "Stored summary")


class TestOpenRouterIntegration(unittest.TestCase):
    def test_load_runtime_config_defaults_to_stepfun_openrouter_model(self) -> None:
        args = argparse.Namespace(
            opml_path="subscriptions.opml",
            state_path="data/state.json",
            latest_path="data/latest.json",
            site_dir="site",
            mock_summary=False,
        )

        with mock.patch.dict(rss_digest.os.environ, {}, clear=True):
            config = rss_digest.load_runtime_config(args)

        self.assertEqual(config["llm_provider"], "openrouter")
        self.assertEqual(config["openrouter_model"], "stepfun/step-3.5-flash:free")

    def test_call_llm_json_uses_openrouter_provider(self) -> None:
        config = {
            "llm_provider": "openrouter",
            "openrouter_api_key": "router-key",
            "openrouter_model": "qwen/qwen3.6-plus:free",
        }

        with mock.patch.object(
            rss_digest,
            "call_openrouter_json",
            return_value={"tldr": "Summary", "highlights": ["One"]},
        ) as call_openrouter:
            result = rss_digest.call_llm_json(config=config, prompt="Summarize this feed")

        self.assertEqual(result, {"tldr": "Summary", "highlights": ["One"]})
        call_openrouter.assert_called_once_with(
            api_key="router-key",
            model="qwen/qwen3.6-plus:free",
            prompt="Summarize this feed",
        )

    def test_call_openrouter_json_requests_json_mode_and_parses_output_text(self) -> None:
        response = mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": [
                            {"type": "reasoning", "text": "hidden reasoning"},
                            {
                                "type": "output_text",
                                "text": '{"tldr": "Summary", "highlights": ["One"]}',
                            },
                        ]
                    }
                }
            ]
        }

        with mock.patch.object(rss_digest.httpx, "post", return_value=response) as post:
            result = rss_digest.call_openrouter_json(
                api_key="test-key",
                model="qwen/qwen3.6-plus:free",
                prompt="Summarize this feed",
            )

        self.assertEqual(result, {"tldr": "Summary", "highlights": ["One"]})
        self.assertEqual(
            post.call_args.kwargs["json"]["response_format"],
            {"type": "json_object"},
        )
        self.assertEqual(
            post.call_args.kwargs["headers"]["X-OpenRouter-Title"],
            "rss-ai-summary",
        )

    def test_process_feed_raises_on_openrouter_auth_error(self) -> None:
        feed = rss_digest.FeedConfig(
            category="Research",
            title="Paper Feed",
            xml_url="https://example.com/feed.xml",
            html_url="https://example.com",
        )
        config = {
            "min_feed_content_chars": 50,
            "max_item_chars": 4000,
            "mock_summary": False,
            "llm_provider": "openrouter",
        }
        state = {"feeds": {feed.xml_url: {"pending_items": [], "seen_ids": []}}}
        parsed_feed = feedparser.FeedParserDict(
            {
                "feed": {"title": "Paper Feed", "link": "https://example.com"},
                "entries": [
                    feedparser.FeedParserDict(
                        {
                            "id": "new-entry-id",
                            "title": "New paper",
                            "link": "https://example.com/paper",
                            "published_parsed": time.strptime(
                                "2026-04-03 00:00:00",
                                "%Y-%m-%d %H:%M:%S",
                            ),
                            "summary": "Paper body",
                        }
                    )
                ],
            }
        )
        response = mock.Mock(status_code=401, reason_phrase="Unauthorized")
        auth_error = rss_digest.httpx.HTTPStatusError(
            "401 Unauthorized",
            request=mock.Mock(),
            response=response,
        )

        with (
            mock.patch.object(rss_digest, "fetch_feed", return_value=parsed_feed),
            mock.patch.object(
                rss_digest,
                "resolve_entry_text",
                return_value=("Paper body", "feed"),
            ),
            mock.patch.object(rss_digest, "summarize_feed", side_effect=auth_error),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "openrouter authentication failed: HTTP 401 Unauthorized",
            ):
                rss_digest.process_feed(mock.Mock(), config, state, feed)

    def test_summarize_feed_returns_item_tldrs_for_papers(self) -> None:
        config = {
            "mock_summary": False,
            "summary_language": "English",
            "max_prompt_chars": 18000,
            "llm_provider": "openrouter",
        }
        feed = rss_digest.FeedConfig(
            category="Papers",
            title="Paper Feed",
            xml_url="https://example.com/feed.xml",
            html_url="https://example.com",
        )
        items = [
            {
                "id": "paper-1",
                "title": "Paper One",
                "link": "https://example.com/1",
                "published": "2026-04-03 00:00 UTC",
                "text": "Paper one abstract.",
            },
            {
                "id": "paper-2",
                "title": "Paper Two",
                "link": "https://example.com/2",
                "published": "2026-04-03 00:00 UTC",
                "text": "Paper two abstract.",
            },
        ]

        with mock.patch.object(
            rss_digest,
            "call_llm_json",
            return_value={
                "tldr": "Feed summary.",
                "highlights": ["Paper One"],
                "item_tldrs": [
                    {"number": 1, "tldr": "Summary one."},
                    {"number": 2, "tldr": "Summary two."},
                ],
            },
        ):
            summary = rss_digest.summarize_feed(config, feed, items)

        self.assertEqual(summary["tldr"], "Feed summary.")
        self.assertEqual(summary["item_tldrs"]["paper-1"], "Summary one.")
        self.assertEqual(summary["item_tldrs"]["paper-2"], "Summary two.")


class TestRenderSite(unittest.TestCase):
    def test_render_site_shows_linked_title_followed_by_tldr(self) -> None:
        report = {
            "site_title": "Daily Feed TLDR",
            "generated_at_human": "2026-04-03 12:00 UTC",
            "model": "qwen/qwen3.6-plus:free",
            "feeds_with_updates": 1,
            "total_items": 2,
            "categories": ["Research"],
            "feeds": [
                {
                    "category": "Research",
                    "title": "Paper Feed",
                    "site_url": "https://example.com/papers",
                    "feed_url": "https://example.com/feed.xml",
                    "item_count": 2,
                    "tldr": "A short summary of the latest papers.",
                    "highlights": ["Paper A", "Paper B"],
                    "items": [
                        {"title": "Paper A", "link": "https://example.com/a"},
                        {"title": "Paper B", "link": "https://example.com/b"},
                    ],
                }
            ],
            "errors": [],
        }

        html = rss_digest.render_site(report)

        self.assertIn(
            '<div class="feed-title"><a href="https://example.com/papers">Paper Feed</a></div>',
            html,
        )
        self.assertIn('<p class="tldr">A short summary of the latest papers.</p>', html)
        self.assertIn('<div class="feed-list">', html)
        self.assertNotIn("<details>", html)

    def test_render_site_lists_each_paper_with_its_tldr(self) -> None:
        report = {
            "site_title": "Daily Feed TLDR",
            "generated_at_human": "2026-04-03 12:00 UTC",
            "model": "qwen/qwen3.6-plus:free",
            "feeds_with_updates": 1,
            "total_items": 2,
            "categories": ["Papers"],
            "feeds": [
                {
                    "category": "Papers",
                    "title": "Nature Communications",
                    "site_url": "https://www.nature.com/ncomms",
                    "feed_url": "https://www.nature.com/ncomms.rss",
                    "item_count": 2,
                    "tldr": "Feed summary.",
                    "highlights": ["Paper A", "Paper B"],
                    "items": [
                        {
                            "title": "Paper A",
                            "link": "https://example.com/a",
                            "published": "2026-04-03 00:00 UTC",
                            "tldr": "Paper A TLDR.",
                        },
                        {
                            "title": "Paper B",
                            "link": "https://example.com/b",
                            "published": "2026-04-02 00:00 UTC",
                            "tldr": "Paper B TLDR.",
                        },
                    ],
                }
            ],
            "errors": [],
        }

        html = rss_digest.render_site(report)

        self.assertIn('<div class="paper-list">', html)
        self.assertIn(
            '<div class="paper-title"><a href="https://example.com/a">Paper A</a></div>',
            html,
        )
        self.assertIn('<p class="tldr">Paper A TLDR.</p>', html)
        self.assertIn('Nature Communications | 2026-04-03 00:00 UTC', html)


class TestPaperTopicHighlighting(unittest.TestCase):
    def test_matches_requested_priority_topics(self) -> None:
        item = {
            "title": "AI pipeline for plasmid phage detection in bacterial communities",
            "tldr": "A computational method uses deep learning to analyze DNA methylation.",
            "text": "",
        }

        self.assertEqual(
            rss_digest.paper_topic_matches(item),
            [
                "AI",
                "Deep Learning",
                "Bacteria",
                "Phages",
                "Plasmids",
                "DNA Modification/Methylation",
                "Computational Methods",
            ],
        )

    def test_build_feed_digest_adds_topic_matches_for_papers(self) -> None:
        feed = rss_digest.FeedConfig(
            category="Papers",
            title="Papers",
            xml_url="https://example.com/rss",
        )
        items = [
            {
                "id": "1",
                "title": "Machine learning for microbiology",
                "link": "https://example.com/paper",
                "published": "2026-04-04 00:00 UTC",
                "sort_key": 1,
                "source_kind": "feed",
                "text": "This bioinformatics pipeline analyzes microbial DNA methylation.",
                "tldr": "A computational framework for microbiology.",
            }
        ]

        digest = rss_digest.build_feed_digest(
            feed=feed,
            site_url="https://example.com",
            items=items,
            summary={"tldr": "summary", "highlights": ["highlight"]},
        )

        self.assertEqual(
            digest["items"][0]["topic_matches"],
            [
                "Machine Learning",
                "Microbiology",
                "DNA Modification/Methylation",
                "Computational Methods",
            ],
        )

    def test_render_site_marks_priority_papers_and_topic_note(self) -> None:
        report = {
            "site_title": "Daily Feed TLDR",
            "generated_at_human": "2026-04-03 12:00 UTC",
            "model": "qwen/qwen3.6-plus:free",
            "feeds_with_updates": 1,
            "total_items": 1,
            "categories": ["Papers"],
            "feeds": [
                {
                    "category": "Papers",
                    "title": "Papers",
                    "site_url": "https://example.com",
                    "feed_url": "https://example.com/rss",
                    "item_count": 1,
                    "tldr": "Feed summary.",
                    "highlights": ["Paper A"],
                    "items": [
                        {
                            "title": "AI for phage biology",
                            "link": "https://example.com/paper",
                            "published": "2026-04-04 00:00 UTC",
                            "tldr": "paper tldr",
                            "topic_matches": ["AI", "Phages"],
                        }
                    ],
                }
            ],
            "errors": [],
        }

        html = rss_digest.render_site(report)

        self.assertIn('class="paper paper-priority"', html)
        self.assertIn("topic-badge", html)
        self.assertIn("Highlighted papers match tracked topics", html)


class TestTextRedaction(unittest.TestCase):
    def test_normalize_text_redacts_secret_like_tokens(self) -> None:
        openai_like_token = "sk-" + "a" * 24
        xai_like_token = "xai-" + "b" * 24
        text = (
            f"Example key {openai_like_token} "
            f"and {xai_like_token} should not persist."
        )

        normalized = rss_digest.normalize_text(text)

        self.assertIn("[REDACTED_SECRET]", normalized)
        self.assertNotIn(openai_like_token, normalized)
        self.assertNotIn(xai_like_token, normalized)


if __name__ == "__main__":
    unittest.main()
