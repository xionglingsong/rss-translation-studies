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

## Generate a Static Feed

```bash
python3 server.py --once public/feed.xml
```

This writes `public/feed.xml`, `public/index.html`, and one XML file per journal.

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
- Meta
- Journal of Audiovisual Translation
- The Journal of Specialised Translation
- Interpreting and Society

Taylor & Francis and SAGE feeds are enriched through DOI metadata because their RSS descriptions often contain only issue information or shortened abstracts. John Benjamins feeds use Crossref on GitHub Actions because the publisher RSS endpoint may reject GitHub-hosted requests. OJS feeds can be enriched from article page metadata when their RSS entries omit fields such as abstracts, DOIs, or pages. Some reviews or editorial material may not have a public abstract in any metadata source.

To publish Chinese translations on GitHub Pages, add a `DEEPSEEK_API_KEY` repository secret. The workflow already enables `TRANSLATE_TO_ZH`.
