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

The static build also checks that every journal in `journals.json` produced a feed. A failed source, missing per-journal XML file, or journal-count mismatch will raise an error.

To include Chinese translations locally:

```bash
TRANSLATE_TO_ZH=1 DEEPSEEK_API_KEY=your_api_key python3 server.py --once public/feed.xml
```

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

Taylor & Francis, John Benjamins, and JAT entries use Crossref on GitHub Pages because some publisher RSS endpoints may reject GitHub-hosted requests. SAGE and OJS feeds are enriched through DOI or article-page metadata when their RSS entries omit fields such as abstracts, DOIs, or pages. Some reviews or editorial material may not have a public abstract in any metadata source.

To publish Chinese translations on GitHub Pages, add a `DEEPSEEK_API_KEY` repository secret. The workflow already enables `TRANSLATE_TO_ZH`.

## Maintenance Checklist

Use this checklist whenever adding or repairing a journal.

1. Add or update the journal in `journals.json`.
   - Include `slug`, `title`, `publisher`, `homepage`, and either `source_feed` or Crossref fields.
   - Prefer a stable official RSS feed when it works on GitHub Actions.
   - Use `source_type: "crossref"` when publisher RSS blocks GitHub-hosted requests or omits too much metadata.

2. Verify source quality locally.
   - Run `python3 server.py --once public/feed.xml`.
   - Open `public/manifest.json` and confirm `journal_count` equals the number of entries in `journals.json`.
   - Check `errors` is an empty list.
   - Review `weak_abstract_count` and the per-journal `weak_abstracts` values.

3. Check the public homepage.
   - Open `public/index.html`.
   - Confirm the journal list is complete.
   - Confirm each row shows source type, current build status, item count, Chinese count, and abstract status.
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
