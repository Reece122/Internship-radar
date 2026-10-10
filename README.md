# Internship Radar

Polls company job boards (Greenhouse, Ashby, Lever, Workday) every 10 minutes for Summer 2027
engineering internships and writes the open ones to `data/feed.json`. New postings open a GitHub issue,
which emails the repo owner.

- `config.json`: which boards to read, title filters, and companies to leave out of alerts.
- `watcher.py`: the poller. Standard library only.
- `data/status.json`: boards that could not be read on the last run.
