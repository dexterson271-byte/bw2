# HellCore BedWars Discord Bot

Discord slash-command bot for generating BedWars player stat cards.

## Railway setup

1. Create a new Railway project from this repository.
2. Add these Railway variables:
   - `DISCORD_TOKEN`
   - `API_KEY`
   - `API_BASE` optional, defaults to `http://srv125.godlike.club:26045/api/v1/player/`
3. Railway will install `requirements.txt` and run the worker from `Procfile`.

## Local run

```powershell
$env:DISCORD_TOKEN="your-token"
$env:API_KEY="your-api-key"
python hellcore.py
```
