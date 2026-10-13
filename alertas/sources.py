"""Lectores de fuentes públicas: RSS/Atom."""
import hashlib
import html
import re
import xml.etree.ElementTree as ET

from .httpc import get_bytes


def _local(tag):
    return tag.split("}")[-1]


def _text(el):
    return (el.text or "").strip() if el is not None else ""


def _clean(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def parse_feed(data: bytes):
    root = ET.fromstring(data)
    items = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        d = {}
        for c in el:
            d.setdefault(_local(c.tag), c)
        title = _clean(_text(d.get("title")))
        body = _clean(_text(d.get("description")) or _text(d.get("summary")) or _text(d.get("content")))
        link_el = d.get("link")
        link = ""
        if link_el is not None:
            link = link_el.get("href") or _text(link_el)
        author = ""
        a = d.get("author") or d.get("creator")
        if a is not None:
            name = next((x for x in a if _local(x.tag) == "name"), None)
            author = _text(name) if name is not None else _text(a)
        uid = _text(d.get("guid")) or _text(d.get("id")) or link
        text = (title + ". " + body).strip(". ").strip() if body and body != title else title
        if not uid:
            uid = hashlib.sha1(text.encode("utf-8")).hexdigest()
        items.append({"id": uid, "text": text, "url": link if link.startswith("http") else "", "author": author})
    return items


def fetch_feed(url):
    return parse_feed(get_bytes(url))
