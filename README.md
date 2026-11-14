# VideoGrabber

- `app/videograbber.py` — local app (Tk window + HTTP on 127.0.0.1:47823), launched via /Applications/VideoGrabber.app
- `extension/` — Chrome MV3 extension (load unpacked)
- Config: ~/Library/Application Support/VideoGrabber/config.json (download_dir, site_rules)
- Log: ~/Library/Logs/VideoGrabber.log

site_rules are keyed by the page host (subdomains match):
    "example.com": {"referer": "...", "user_agent": "...", "headers": {"Origin": "..."}}
Restart the app after editing config.

## Install on a new Mac

    brew install yt-dlp ffmpeg python python-tk
    git clone https://github.com/raliev/VideoGrabber.git ~/VideoGrabber
    ~/VideoGrabber/install.sh        # builds /Applications/VideoGrabber.app

Then in Chrome: `chrome://extensions` → Developer mode → Load unpacked → `~/VideoGrabber/extension`.
Use `PYTHON=/path/to/python3 ./install.sh` to pick a specific interpreter (needs tkinter).
