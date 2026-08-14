# Translation Studies RSS

Enhanced RSS feeds for translation and interpreting studies journals. The generator reads official journal RSS feeds, enriches entries with public DOI metadata when needed, and publishes both a combined feed and per-journal feeds.

## Run Locally

```bash
python3 server.py
```

Then subscribe to this URL in NetNewsWire:

```text
http://127.0.0.1:8765/feed.xml
```

Per-journal feeds are also available locally, for example:

```text
http://127.0.0.1:8765/target.xml
http://127.0.0.1:8765/translation-studies.xml
```

Topic feeds are generated from the `tags` field in `journals.json`, for example:

```text
http://127.0.0.1:8765/topic-interpreting.xml
http://127.0.0.1:8765/topic-digital-ai-translation.xml
```

## Notes

- Journals are configured in `journals.json`.
- Metadata is fetched from Semantic Scholar, OpenAlex, and Crossref.
- Metadata is cached in `work/metadata-cache.json` for 30 days.
- Local generated feeds are cached in memory for 30 minutes.
- Chinese translations are added with `deepseek-v4-flash` when `TRANSLATE_TO_ZH=1` and `DEEPSEEK_API_KEY` are set.
- Static publishing validates the generated site before deployment. If any configured journal is missing or any source fails, the build stops instead of publishing a partial feed.

## Generate a Static Feed

```bash
python3 server.py --once public/feed.xml
```

This writes `public/feed.xml`, `public/index.html`, and one XML file per journal.

The static build also writes `public/weekly/latest.md` and a dated weekly Markdown digest under `public/weekly/`.

The static build checks that every journal in `journals.json` produced a feed. A failed source, missing per-journal XML file, missing topic feed, missing weekly digest, or journal-count mismatch will raise an error.

To include Chinese translations locally:

```bash
TRANSLATE_TO_ZH=1 DEEPSEEK_API_KEY=your_api_key python3 server.py --once public/feed.xml
```

When run on this Mac, the dated weekly digest is also copied to the Obsidian publishing folder if it exists:

```text
/Users/lingsongxiong/Nutstore Files/Obsidian/claudesidian-0.13.1/01_Projects/译了么/公众号/RSS
```

Override the target with `RSS_OBSIDIAN_WEEKLY_DIR=/path/to/folder`.

## Host on GitHub Pages

1. Create a GitHub repository and push these files to the `main` branch.
2. In the repository, open `Settings > Pages`.
3. Set `Build and deployment > Source` to `GitHub Actions`.
4. Open the `Actions` tab and run `Publish RSS feed` once, or push to `main`.
5. Subscribe to:

```text
https://YOUR-GITHUB-USERNAME.github.io/YOUR-REPOSITORY/feed.xml
```

The included workflow refreshes the feed every 6 hours and can also be run manually.

For this repository, the combined feed is:

```text
https://xionglingsong.github.io/rss-translation-studies/feed.xml
```

## Included Journals

- Translation Studies
- Perspectives
- The Translator
- The Interpreter and Translator Trainer
- Asia Pacific Translation and Intercultural Studies
- Translation Review
- Target
- Interpreting
- Translation and Interpreting Studies
- Translation Spaces
- Babel
- FORUM
- Translation in Society
- Translation, Cognition & Behavior
- Translation and Translanguaging in Multilingual Contexts
- Digital Translation
- InContext
- Terminology
- Meta
- Journal of Audiovisual Translation
- The Journal of Specialised Translation
- Interpreting and Society

## Topic Feeds

The project also publishes topic-specific RSS feeds. These are generated from journal-level tags, so a journal can appear in more than one topic.

- 综合翻译学: `topic-general-translation-studies.xml`
- 口译研究: `topic-interpreting.xml`
- 译者教育: `topic-translator-education.xml`
- 社会文化: `topic-society-culture.xml`
- 认知过程: `topic-cognition-process.xml`
- 数字与 AI 翻译: `topic-digital-ai-translation.xml`
- 视听翻译: `topic-audiovisual-translation.xml`
- 术语与专门用途翻译: `topic-terminology-specialized-translation.xml`

## Weekly Digest

The site publishes a Markdown digest that can be reused for newsletters, WeChat posts, group updates, or Obsidian drafts.

- Latest digest: `weekly/latest.md`
- Dated digest: `weekly/YYYY-MM-DD.md`

The digest uses items from the most recent 7 days. If no items fall within that window, it falls back to the latest 20 items so the generated article is still useful.

Taylor & Francis, John Benjamins, and JAT entries use Crossref on GitHub Pages because some publisher RSS endpoints may reject GitHub-hosted requests. SAGE and OJS feeds are enriched through DOI or article-page metadata when their RSS entries omit fields such as abstracts, DOIs, or pages. Some reviews or editorial material may not have a public abstract in any metadata source.

To publish Chinese translations on GitHub Pages, add a `DEEPSEEK_API_KEY` repository secret. The workflow already enables `TRANSLATE_TO_ZH`.

## Maintenance Checklist

Use this checklist whenever adding or repairing a journal.

1. Add or update the journal in `journals.json`.
   - Include `slug`, `title`, `publisher`, `homepage`, and either `source_feed` or Crossref fields.
   - Add at least one `tags` value so the journal appears in the relevant topic feeds.
   - Prefer a stable official RSS feed when it works on GitHub Actions.
   - Use `source_type: "crossref"` when publisher RSS blocks GitHub-hosted requests or omits too much metadata.

2. Verify source quality locally.
   - Run `python3 server.py --once public/feed.xml`.
   - Open `public/manifest.json` and confirm `journal_count` equals the number of entries in `journals.json`.
   - Check `errors` is an empty list.
   - Review `weak_abstract_count` and the per-journal `weak_abstracts` values.
   - Review the `topics` section and confirm each expected topic feed has items.
   - Open `public/weekly/latest.md` and confirm the weekly digest has sensible grouping and links.

3. Check the public homepage.
   - Open `public/index.html`.
   - Confirm the journal list is complete.
   - Confirm each row shows source type, topic tags, current build status, item count, Chinese count, and abstract status.
   - Confirm the topic subscription cards show sensible journal and item counts.
   - Test the combined RSS button and at least one per-journal RSS link.

4. Update public documentation.
   - Keep the "Included Journals" list in this README in sync with `journals.json`.
   - Mention special source decisions, such as Crossref fallbacks or journals without official RSS.
   - If the change affects users, add a short update note for WeChat, the website, or the project announcement.

5. Publish safely.
   - Commit the code and config changes together.
   - Push to `main`.
   - Check the `Publish RSS feed` GitHub Actions run.
   - After deployment, open the live `manifest.json` and confirm the expected journal count.
