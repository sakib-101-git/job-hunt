"""Job descriptions as plain text: HTML structure turned into line breaks and
bullets, entities decoded, Unicode NFKC, whitespace collapsed."""

import html
import re
import unicodedata
from html.parser import HTMLParser

BULLET = "• "

_BLOCK = {
    "p",
    "div",
    "br",
    "tr",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "ul",
    "ol",
    "table",
    "section",
}
_SKIP = {"script", "style", "head", "title"}
_SPACES = re.compile(r"[ \t\f\v\u00a0]+")  # \u00a0: no-break space
_BLANK_LINES = re.compile(r"\n{3,}")


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP:
            self._skip += 1
        elif tag == "li":
            self.parts.append("\n" + BULLET)
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK and tag != "br":  # <br> has no closing break
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def clean_text(text: str) -> str:
    """NFKC, collapsed spaces, at most one blank line between paragraphs."""
    text = unicodedata.normalize("NFKC", text).replace("\r\n", "\n").replace("\r", "\n")
    lines = [_SPACES.sub(" ", line).strip() for line in text.split("\n")]
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def html_to_text(markup: str) -> str:
    # Some sources double-escape their HTML ("&lt;p&gt;").
    if "<" not in markup and "&lt;" in markup:
        markup = html.unescape(markup)
    parser = _TextParser()
    parser.feed(markup)
    parser.close()
    return clean_text("".join(parser.parts))


def truncate(text: str, limit: int) -> str:
    """Cut at a line or word boundary at or before `limit` characters."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    boundary = max(cut.rfind("\n"), cut.rfind(" "))
    return (cut[:boundary] if boundary > limit // 2 else cut).rstrip() + " …"
