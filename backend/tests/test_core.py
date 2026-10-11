"""Unit tests for the pure-logic parts: feed parsing, dedupe, ranking, sudoku, scheduler maths, digest."""
import unittest
from datetime import datetime, timedelta, timezone

import yaml

from app import sudoku
from app.agents import dedupe, ranker
from app.agents.ranker import Matcher
from app.config import ROOT, Settings
from app.demo import demo_edition
from app.digest import build_digest
from app.feeds import FeedError, parse_feed
from app.models import Article
from app.scheduler import next_daily, parse_hhmm
from app.util import canonical_url, clean_text, first_sentences

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
TOPICS = yaml.safe_load(open(ROOT / "backend" / "config" / "topics.yaml", encoding="utf-8"))


def art(title, summary="", hours=2, weight=1.0, source="X", url=None, **kw):
    return Article(title=title, url=url or f"https://e.example/{abs(hash(title))}", source=source, summary=summary,
                   published=NOW - timedelta(hours=hours), weight=weight, **kw)


class FeedTests(unittest.TestCase):
    def test_rss(self):
        xml = b"""<?xml version="1.0"?><rss version="2.0"><channel><item><title>Hello &amp; welcome</title>
        <link>https://a.example/1</link><description>&lt;p&gt;Body &lt;b&gt;text&lt;/b&gt;&lt;/p&gt;</description>
        <pubDate>Mon, 05 Oct 2026 10:00:00 GMT</pubDate></item></channel></rss>"""
        (item,) = parse_feed(xml)
        self.assertEqual(item["title"], "Hello & welcome")
        self.assertEqual(item["summary"], "Body text")
        self.assertEqual(item["published"], datetime(2026, 10, 5, 10, 0, tzinfo=timezone.utc))

    def test_atom_and_content_words(self):
        xml = ("""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>T</title><link rel="alternate" href="https://a.example/x"/>
        <published>2026-10-05T10:00:00Z</published><content type="html">""" + "word " * 500 + "</content></entry></feed>").encode()
        (item,) = parse_feed(xml)
        self.assertEqual(item["link"], "https://a.example/x")
        self.assertEqual(item["content_words"], 500)

    def test_rdf(self):
        xml = b"""<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/"
        xmlns:dc="http://purl.org/dc/elements/1.1/"><item><title>R</title><link>https://a.example/r</link><dc:date>2026-10-05T09:00:00Z</dc:date></item></rdf:RDF>"""
        self.assertEqual(parse_feed(xml)[0]["published"], datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc))

    def test_entity_bomb_and_html_and_garbage_refused(self):
        bomb = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><rss version="2.0"><channel/></rss>'
        for bad in (bomb, b"<!DOCTYPE html><html><body>blocked</body></html>", b"not xml at all", b""):
            with self.assertRaises(FeedError):
                parse_feed(bad)

    def test_html_entities_and_bare_ampersands_repaired(self):
        xml = b'<rss version="2.0"><channel><item><title>AT&T &mdash; deal&nbsp;done</title><link>https://a.example/1</link></item></channel></rss>'
        (item,) = parse_feed(xml)
        self.assertIn("AT&T", item["title"])
        self.assertIn("—", item["title"])

    def test_non_http_links_dropped(self):
        xml = b'<rss version="2.0"><channel><item><title>x</title><link>javascript:alert(1)</link></item></channel></rss>'
        self.assertEqual(parse_feed(xml), [])


class UtilTests(unittest.TestCase):
    def test_clean_text_strips_scripts_and_tags(self):
        self.assertEqual(clean_text("<p>Hi <script>evil()</script><b>there</b> &amp; you</p>"), "Hi there & you")

    def test_truncation_on_word_boundary(self):
        self.assertTrue(clean_text("alpha beta gamma delta", limit=12).endswith("…"))

    def test_first_sentences(self):
        self.assertEqual(first_sentences("One. Two is longer. Three.", 12), "One.")

    def test_canonical_url_drops_tracking(self):
        self.assertEqual(canonical_url("https://A.com/x/?utm_source=a&id=3#frag"), "https://a.com/x?id=3")


class DedupeTests(unittest.TestCase):
    def test_rewordings_merge_and_credit_outlets(self):
        a = art("OpenAI launches GPT-6 with stronger reasoning", source="TechCrunch")
        b = art("OpenAI releases GPT-6, a stronger reasoning model", source="The Verge")
        c = art("SpaceX Starship completes test flight", source="SpaceNews")
        reps = dedupe.cluster([a, b, c])
        self.assertEqual(len(reps), 2)
        gpt = next(r for r in reps if "GPT-6" in r.title)
        self.assertEqual(gpt.cluster_size, 2)
        self.assertEqual(len(gpt.also), 1)

    def test_different_version_numbers_stay_separate(self):
        a = art("OpenAI launches GPT-5 for everyone")
        b = art("OpenAI launches GPT-6 for everyone")
        self.assertEqual(len(dedupe.cluster([a, b])), 2)

    def test_same_url_with_tracking_merges(self):
        a = art("Same story", url="https://x.example/a?utm_source=1")
        b = art("Totally different words here", url="https://x.example/a")
        self.assertEqual(len(dedupe.cluster([a, b])), 1)

    def test_drop_seen(self):
        fresh = art("Anthropic raises funding at record valuation", url="https://x.example/new")
        kept = dedupe.drop_seen([fresh], set(), ["Anthropic raises funding at a record valuation"])
        self.assertEqual(kept, [])
        kept = dedupe.drop_seen([fresh], {canonical_url("https://x.example/new")}, [])
        self.assertEqual(kept, [])
        self.assertEqual(len(dedupe.drop_seen([fresh], set(), ["Unrelated headline about bakeries"])), 1)


class RankerTests(unittest.TestCase):
    def setUp(self):
        self.m = Matcher(TOPICS)

    def rank(self, *articles):
        return ranker.rank(list(articles), self.m, NOW)

    def test_relevant_beats_irrelevant_and_noise_dropped(self):
        good = art("OpenAI launches GPT-6", "ChatGPT users get the model today.")
        dull = art("Local bakery opens new store", "Bread.")
        deal = art("Best 10 deals on headphones this Black Friday", "Anthropic Claude OpenAI")
        out = self.rank(good, dull, deal)
        self.assertEqual([a.title for a in out], [good.title])
        self.assertEqual(good.entities, ["OpenAI"])

    def test_ai_does_not_match_inside_words(self):
        a = self.m.analyze("He said the plan failed", "It was said that Spain would wait")
        self.assertEqual(a.core, 0)

    def test_bare_meta_needs_context(self):
        self.assertEqual(self.m.analyze("The new meta in chess openings", "").entities, [])
        self.assertEqual(self.m.analyze("Meta unveils new AI model", "").entities, ["Meta"])

    def test_bare_claude_needs_context(self):
        self.assertEqual(self.m.analyze("Claude Monet exhibit opens", "paintings").entities, [])
        self.assertEqual(self.m.analyze("Claude now writes code", "the AI model from Anthropic").entities, ["Anthropic"])

    def test_overlapping_keywords_not_double_counted(self):
        both = self.m.analyze("Google DeepMind ships update", "")
        only_long = sum(both.beat_scores.values())
        # "google deepmind" (9) + "google" (bigtech 5) would double count "deepmind" (8) without overlap suppression
        self.assertLessEqual(only_long, 2 * (9 + 5))
        self.assertNotIn("deepmind", both.hits)

    def test_stale_news_dropped(self):
        self.assertEqual(self.rank(art("OpenAI launches GPT-6", hours=80)), [])

    def test_coverage_bonus_cannot_rescue_irrelevant(self):
        weak = art("Quarterly bakery results", "bread sales", cluster_size=5)
        self.assertEqual(self.rank(weak), [])

    def test_impact_assignment_in_range(self):
        stories = [art(f"OpenAI story {i}") for i in range(10)]
        for i, s in enumerate(stories):
            s.score = 100 - i
        ranker.assign_impact(stories)
        self.assertTrue(all(1 <= s.impact <= 5 for s in stories))
        self.assertEqual(stories[0].impact, 5)


class SudokuTests(unittest.TestCase):
    def test_puzzles_are_unique_valid_symmetric_deterministic(self):
        for diff in ("easy", "medium", "hard"):
            p = sudoku.generate(diff, 7)
            grid, sol = [int(c) for c in p["puzzle"]], [int(c) for c in p["solution"]]
            self.assertEqual(sudoku.count_solutions(grid, 2), 1, diff)
            self.assertEqual(sudoku.solve(grid), sol)
            self.assertTrue(all((grid[i] == 0) == (grid[80 - i] == 0) for i in range(81)))
            self.assertTrue(all(g in (0, s) for g, s in zip(grid, sol)))
            self.assertEqual(p, sudoku.generate(diff, 7))
        self.assertLess(sudoku.generate("easy", 1)["clues"], 45)
        self.assertGreater(sudoku.generate("easy", 1)["clues"], sudoku.generate("hard", 1)["clues"])

    def test_solution_is_a_valid_grid(self):
        s = [int(c) for c in sudoku.generate("medium", 3)["solution"]]
        groups = [s[r * 9:(r + 1) * 9] for r in range(9)] + [s[c::9] for c in range(9)]
        groups += [[s[(br * 3 + r) * 9 + bc * 3 + c] for r in range(3) for c in range(3)] for br in range(3) for bc in range(3)]
        self.assertTrue(all(sorted(g) == list(range(1, 10)) for g in groups))

    def test_invalid_and_unsolvable_grids(self):
        bad = [5, 5] + [0] * 79  # two 5s in the first row
        self.assertEqual(sudoku.count_solutions(bad), 0)
        self.assertIsNone(sudoku.solve(bad))
        with self.assertRaises(ValueError):
            sudoku.generate("impossible", 1)

    def test_daily_is_stable_per_date_and_differs_across_dates(self):
        self.assertEqual(sudoku.daily("2026-10-05", "medium"), sudoku.daily("2026-10-05", "medium"))
        self.assertNotEqual(sudoku.daily("2026-10-05", "medium")["puzzle"], sudoku.daily("2026-10-06", "medium")["puzzle"])
        self.assertNotEqual(sudoku.random_puzzle("easy")["puzzle"], sudoku.random_puzzle("easy")["puzzle"])


class SchedulerTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_hhmm("07:30").hour, 7)
        for bad in ("7", "25:00x", "", "aa:bb"):
            with self.assertRaises(ValueError):
                parse_hhmm(bad)

    def test_next_daily(self):
        at = parse_hhmm("07:30")
        before = datetime(2026, 10, 5, 6, 0, tzinfo=timezone.utc)
        after = datetime(2026, 10, 5, 8, 0, tzinfo=timezone.utc)
        self.assertEqual(next_daily(before, at), datetime(2026, 10, 5, 7, 30, tzinfo=timezone.utc))
        self.assertEqual(next_daily(after, at), datetime(2026, 10, 6, 7, 30, tzinfo=timezone.utc))
        self.assertEqual(next_daily(datetime(2026, 10, 5, 7, 30, tzinfo=timezone.utc), at).day, 6)


class DigestTests(unittest.TestCase):
    def test_demo_digest_builds_and_measures(self):
        s = Settings(base_url="http://x.local")
        d = build_digest(demo_edition(s), s, "http://x.local/api/unsubscribe?token=t")
        self.assertGreaterEqual(d.minutes, 1)
        self.assertIn("SAMPLE", d.html)
        self.assertIn("token=t", d.html)
        self.assertIn("Unsubscribe", d.text)

    def test_hostile_headlines_escaped(self):
        s = Settings()
        ed = demo_edition(s)
        ed["lead"]["headline"] = "<script>alert(1)</script> & co"
        ed["reads"][0]["title"] = '"><img src=x onerror=alert(1)>'
        html = build_digest(ed, s).html
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertNotIn("<img src=x", html)

    def test_empty_edition_refused(self):
        with self.assertRaises(ValueError):
            build_digest({"lead": None, "sections": []}, Settings())

    def test_budget_limits_length(self):
        s = Settings(digest_word_budget=250)
        small = build_digest(demo_edition(s), s).words
        big = build_digest(demo_edition(Settings(digest_word_budget=2000)), Settings(digest_word_budget=2000)).words
        self.assertLessEqual(small, big)


if __name__ == "__main__":
    unittest.main()


class GoogleNewsTests(unittest.TestCase):
    """Google News RSS wraps titles as 'Headline - Outlet', puts the outlet in <source>, and fills the
    description with an HTML link containing &nbsp; entities, which strict XML parsers choke on."""

    FEED = b"""<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>q</title>
    <item><title>Meta superintelligence lab hires top researchers - Reuters</title>
    <link>https://news.google.com/rss/articles/CBMi?oc=5</link><pubDate>Mon, 05 Oct 2026 08:00:00 GMT</pubDate>
    <description>&lt;a href="https://x"&gt;Meta superintelligence lab hires top researchers&lt;/a&gt;&amp;nbsp;&amp;nbsp;&lt;font color="#6f6f6f"&gt;Reuters&lt;/font&gt;</description>
    <source url="https://www.reuters.com">Reuters</source></item>
    <item><title>Nvidia &amp; partners expand AI data center plans &mdash; Bloomberg</title>
    <link>https://news.google.com/rss/articles/CBMj?oc=5</link><pubDate>Mon, 05 Oct 2026 07:00:00 GMT</pubDate>
    <description>raw &nbsp; entity</description><source url="https://bloomberg.com">Bloomberg</source></item></channel></rss>"""

    def test_titles_sources_and_entities(self):
        from app.agents.scout import ScoutAgent, SourceSpec
        agent = ScoutAgent(fetcher=lambda url: self.FEED)
        result = agent.scout([SourceSpec("Google News · test", "https://news.google.com/rss/search?q=x")])
        self.assertEqual(len(result.articles), 2, result.report)
        first, second = result.articles
        self.assertEqual(first.title, "Meta superintelligence lab hires top researchers")
        self.assertEqual(first.source, "Reuters")
        self.assertEqual(first.summary, "", "Google's description just repeats the headline")
        self.assertIn("Nvidia & partners", second.title)
