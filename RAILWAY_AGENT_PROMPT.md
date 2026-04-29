# Railway Agent Prompt

Deploy this Python Discord bot as a Railway worker service.

Use `python hellcore.py` as the start command if Railway does not detect the `Procfile`.

Install dependencies from `requirements.txt`.

Required environment variables:

- `DISCORD_TOKEN`: Discord bot token.
- `API_KEY`: BedWars API key.

Optional environment variable:

- `API_BASE`: Player API base URL. Default is `http://srv125.godlike.club:26045/api/v1/player/`.

Do not expose secrets in logs or source files. Keep the service running as a background worker, not a web server.
