# Feels

Small personal projects and experiments.

## Projects

### RSS AI Summary

`rss-ai-summary/` turns an OPML subscription file into a static RSS digest. It can run with a mock summarizer for local testing, or with an LLM provider for real summaries.

```bash
cd rss-ai-summary
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python src/main.py --mock-summary
```

### KOF Game

`games/kof/` is a small Python/Pygame fighting game demo.

```bash
cd games/kof
pip install -r requirements.txt
python game.py
```
