# Taylor & Francis RSS Abstract Proxy

This proxy turns the Taylor & Francis table-of-contents feed for `ritt20` into an RSS 2.0 feed with abstracts when public metadata is available.

## Run Locally

```bash
python3 server.py
```

Then subscribe to this URL in NetNewsWire:

```text
http://127.0.0.1:8765/feed.xml
```

To force a refresh in the browser:

```text
http://127.0.0.1:8765/feed.xml?refresh=1
```

## Notes

- Source feed: `https://www.tandfonline.com/feed/rss/ritt20`
- Abstracts are fetched from Semantic Scholar first, then OpenAlex.
- Abstracts are cached in `work/abstract-cache.json` for 30 days.
- The generated feed is cached in memory for 30 minutes.

## Generate a Static Feed

```bash
python3 server.py --once public/feed.xml
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
