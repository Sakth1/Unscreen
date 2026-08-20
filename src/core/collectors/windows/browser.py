import re
from dataclasses import dataclass

BROWSER_PROCESSES = {
    "chrome.exe": "Chrome",
    "msedge.exe": "Edge",
    "firefox.exe": "Firefox",
    "opera.exe": "Opera",
    "brave.exe": "Brave",
    "vivaldi.exe": "Vivaldi",
    "iexplore.exe": "Internet Explorer",
}

BROWSER_SUFFIXES = {
    "Brave",
    "Chrome",
    "Google Chrome",
    "Microsoft Edge",
    "Edge",
    "Mozilla Firefox",
    "Firefox",
    "Opera",
    "Vivaldi",
    "Internet Explorer",
}

DOMAIN_KEYWORDS = [
    (r"\byoutube\b", "youtube.com"),
    (r"\bgithub\b", "github.com"),
    (r"\breddit\b", "reddit.com"),
    (r"\bstackoverflow\b", "stackoverflow.com"),
    (r"\bstack exchange\b", "stackexchange.com"),
    (r"\btwitter\b", "twitter.com"),
    (r"\bx\.com\b", "x.com"),
    (r"\blinkedin\b", "linkedin.com"),
    (r"\bgoogle\b", "google.com"),
    (r"\bfacebook\b", "facebook.com"),
    (r"\binstagram\b", "instagram.com"),
    (r"\bamazon\b", "amazon.com"),
    (r"\bnetflix\b", "netflix.com"),
    (r"\bspotify\b", "spotify.com"),
    (r"\bdiscord\b", "discord.com"),
    (r"\bwhatsapp\b", "whatsapp.com"),
    (r"\bchatgpt\b", "chatgpt.com"),
    (r"\bnotion\b", "notion.so"),
    (r"\bmedium\b", "medium.com"),
    (r"\bwikipedia\b", "wikipedia.org"),
]

#: Display names for the normalized sites matched by ``DOMAIN_KEYWORDS``;
#: used by the dashboard to give known sites their own top-apps entry
#: instead of fragmenting the browser across every page title (F8).
SITE_NAMES = {
    "youtube.com": "YouTube",
    "github.com": "GitHub",
    "reddit.com": "Reddit",
    "stackoverflow.com": "Stack Overflow",
    "stackexchange.com": "Stack Exchange",
    "twitter.com": "Twitter",
    "x.com": "X",
    "linkedin.com": "LinkedIn",
    "google.com": "Google",
    "facebook.com": "Facebook",
    "instagram.com": "Instagram",
    "amazon.com": "Amazon",
    "netflix.com": "Netflix",
    "spotify.com": "Spotify",
    "discord.com": "Discord",
    "whatsapp.com": "WhatsApp",
    "chatgpt.com": "ChatGPT",
    "notion.so": "Notion",
    "medium.com": "Medium",
    "wikipedia.org": "Wikipedia",
}


@dataclass
class BrowserInfo:
    browser: str
    page_title: str | None
    inferred_domain: str | None


def _is_browser_process(app_name: str) -> str | None:
    return BROWSER_PROCESSES.get(app_name.lower())


def _extract_page_title(window_title: str) -> str | None:
    if not window_title or window_title == "-":
        return None
    for sep in [" — ", " - ", " – ", " | "]:
        if sep in window_title:
            parts = window_title.rsplit(sep, 1)
            suffix = parts[1].strip()
            if suffix in BROWSER_SUFFIXES:
                return parts[0].strip() or None
    if window_title.strip() in BROWSER_SUFFIXES:
        return None
    return window_title.strip()


def _infer_domain(page_title: str | None) -> str | None:
    if not page_title:
        return None
    lower = page_title.lower()
    for pattern, domain in DOMAIN_KEYWORDS:
        if re.search(pattern, lower):
            return domain
    return None


def analyze(app: str, title: str) -> BrowserInfo | None:
    browser = _is_browser_process(app)
    if browser is None:
        return None
    page_title = _extract_page_title(title)
    domain = _infer_domain(page_title)
    return BrowserInfo(
        browser=browser,
        page_title=page_title,
        inferred_domain=domain,
    )
