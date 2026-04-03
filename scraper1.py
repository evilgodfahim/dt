#!/usr/bin/env python3
import json, os, sys, requests, xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from datetime import datetime, timezone
import re

FLARESOLVERR_URL = os.environ.get("FLARESOLVERR_URL", "http://localhost:8191")
MAX_ITEMS = 500
IMAGE_CDN = "https://ecdn.dhakatribune.net/contents/uploads/"
MEDIA_NS  = "http://search.yahoo.com/mrss/"

FEEDS = [
    {
        "url":    "https://bangla.dhakatribune.com/",
        "output": "bangla.xml",
        "title":  "Dhaka Tribune - Bangla",
        "desc":   "Latest news from Bangla Dhaka Tribune",
    },
]

# Bengali script range. If a title does not contain this, it is skipped.
_BENGALI_RE = re.compile(r"[\u0980-\u09FF]")


def is_bangla_text(text):
    text = (text or "").strip()
    if not text:
        return False
    return bool(_BENGALI_RE.search(text))


def flaresolverr_get(url):
    r = requests.post(
        f"{FLARESOLVERR_URL}/v1",
        json={"cmd": "request.get", "url": url, "maxTimeout": 90000},
        timeout=100,
    )
    r.raise_for_status()
    d = r.json()
    if d.get("status") != "ok":
        raise RuntimeError(f"FlareSolverr: {d.get('message')}")
    return d["solution"]["response"]


def thumbnail_from_ari(raw):
    try:
        path = json.loads(raw).get("path", "").split("?")[0]
        if path:
            return IMAGE_CDN + path
    except Exception:
        pass
    return ""


def extract_articles(html):
    soup = BeautifulSoup(html, "lxml")
    articles, seen = [], set()
    for each in soup.select(".each"):
        a = each.select_one("a.link_overlay")
        if not a:
            continue
        href = a.get("href", "")
        if href.startswith("//"):
            href = "https:" + href
        if not href.startswith("http") or href in seen:
            continue
        seen.add(href)

        title = (a.get("title") or a.get_text(strip=True)).strip()
        if not title:
            continue

        # Keep only Bangla articles.
        # This is intentionally lightweight: titles without Bengali script are skipped.
        if not is_bangla_text(title):
            continue

        thumb = ""
        span = each.select_one("span[data-ari]")
        if span:
            thumb = thumbnail_from_ari(span.get("data-ari", ""))
        summary = ""
        sd = each.select_one(".summery")
        if sd:
            summary = sd.get_text(strip=True)
        published = ""
        ts = each.select_one("span.time.aitm")
        if ts:
            published = ts.get("data-published", "")
        articles.append({
            "title":     title,
            "link":      href,
            "thumb":     thumb,
            "summary":   summary,
            "published": published,
        })
    return articles


def load_existing(path):
    if not os.path.exists(path):
        return []
    ET.register_namespace("media", MEDIA_NS)
    try:
        ch = ET.parse(path).getroot().find("channel")
        if ch is None:
            return []
        out = []
        for el in ch.findall("item"):
            th = el.find(f"{{{MEDIA_NS}}}thumbnail")
            out.append({
                "title":     (el.findtext("title")       or "").strip(),
                "link":      (el.findtext("link")        or "").strip(),
                "summary":   (el.findtext("description") or "").strip(),
                "published": (el.findtext("pubDate")     or "").strip(),
                "thumb":     th.get("url", "") if th is not None else "",
            })
        return out
    except Exception as e:
        print(f"[WARN] {path}: {e}", file=sys.stderr)
        return []


def merge(new_items, existing):
    seen, out = set(), []
    for item in new_items + existing:
        k = item.get("link", "")
        if k and k not in seen:
            seen.add(k)
            out.append(item)
    return out[:MAX_ITEMS]


def save(path, items, title, link, desc):
    ET.register_namespace("media", MEDIA_NS)
    root = ET.Element("rss", version="2.0")
    root.set("xmlns:media", MEDIA_NS)
    ch = ET.SubElement(root, "channel")
    ET.SubElement(ch, "title").text        = title
    ET.SubElement(ch, "link").text         = link
    ET.SubElement(ch, "description").text  = desc
    ET.SubElement(ch, "lastBuildDate").text = datetime.now(timezone.utc).strftime(
        "%a, %d %b %Y %H:%M:%S +0000")
    for d in items:
        it = ET.SubElement(ch, "item")
        ET.SubElement(it, "title").text        = d.get("title", "")
        ET.SubElement(it, "link").text         = d.get("link",  "")
        ET.SubElement(it, "description").text  = d.get("summary", "")
        g = ET.SubElement(it, "guid")
        g.set("isPermaLink", "true")
        g.text = d.get("link", "")
        if d.get("published"):
            ET.SubElement(it, "pubDate").text = d["published"]
        if d.get("thumb"):
            th = ET.SubElement(it, f"{{{MEDIA_NS}}}thumbnail")
            th.set("url", d["thumb"])
            mc = ET.SubElement(it, f"{{{MEDIA_NS}}}content")
            mc.set("url",    d["thumb"])
            mc.set("medium", "image")
    ET.indent(root, space="  ")
    with open(path, "wb") as f:
        ET.ElementTree(root).write(f, encoding="utf-8", xml_declaration=True)
    print(f"[OK] {path} -> {len(items)} items")


def main():
    for cfg in FEEDS:
        print(f"\n[INFO] Fetching {cfg['url']}")
        try:
            html = flaresolverr_get(cfg["url"])
        except Exception as e:
            print(f"[SKIP] {e}", file=sys.stderr)
            continue
        new  = extract_articles(html)
        old  = load_existing(cfg["output"])
        all_ = merge(new, old)
        save(cfg["output"], all_, cfg["title"], cfg["url"], cfg["desc"])


if __name__ == "__main__":
    main()