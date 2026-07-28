"""Extract posts and resource references from UniDev's historical engines."""

from __future__ import annotations

import copy
import re
from urllib.parse import parse_qs, urlsplit

from lxml import html
from lxml.html import HtmlElement

from unidev_archive.dates import parse_forum_date
from unidev_archive.encoding import decode_html
from unidev_archive.models import ParsedPage, ParsedPost, ParsedTopicListing
from unidev_archive.urls import era_for_url, resolve_references

_SPACE_RE = re.compile(r"[\t\f\v ]+")
_CSS_URL_RE = re.compile(r"(?:url\(|@import\s+)[\"']?([^\"')\s;]+)", re.I)
_USER_ID_RE = re.compile(r"(?:[?&](?:u|id)=)(\d+)", re.I)
_TOPIC_ID_RE = re.compile(r"(?:[?&](?:t|TOPIC_ID)=)(\d+)", re.I)
_FORUM_ID_RE = re.compile(r"(?:[?&](?:f|FORUM_ID)=)(\d+)", re.I)
_POST_ID_RE = re.compile(r"(?:[?&#](?:p|#p)=?)(\d+)", re.I)
_SNITZ_DATE_RE = re.compile(
    r"Postado\s*-\s*(\d{1,2}/\d{1,2}/\d{2,4}\s*:\s*\d{1,2}:\d{2}:\d{2})", re.I
)
_PHPBB_DATE_RE = re.compile(r"(?:Posted|Enviado)\s*:\s*(.+)$", re.I)


def _class_xpath(name: str) -> str:
    return f'contains(concat(" ", normalize-space(@class), " "), " {name} ")'


def _id_from_url(pattern: re.Pattern[str], url: str) -> int | None:
    match = pattern.search(url.replace("&amp;", "&").replace("\\075", "="))
    return int(match.group(1)) if match else None


def _clean_text(value: str) -> str:
    lines = (_SPACE_RE.sub(" ", line).strip() for line in value.replace("\r", "").split("\n"))
    return "\n".join(line for line in lines if line)


def _element_text(element: HtmlElement) -> str:
    clone = copy.deepcopy(element)
    for br in clone.xpath(".//br"):
        br.tail = "\n" + (br.tail or "")
    for block in clone.xpath(".//p|.//div|.//li|.//pre|.//blockquote"):
        block.tail = "\n" + (block.tail or "")
    return _clean_text("".join(clone.itertext()))


def _sanitize(element: HtmlElement) -> HtmlElement:
    clone = copy.deepcopy(element)
    for unsafe in clone.xpath(".//script|.//iframe|.//object|.//embed|.//form|.//input|.//button"):
        unsafe.drop_tree()
    for node in clone.iter():
        for attribute in tuple(node.attrib):
            if attribute.casefold().startswith("on") or attribute.casefold() in {
                "action",
                "formaction",
            }:
                del node.attrib[attribute]
    return clone


def _inner_html(element: HtmlElement) -> str:
    prefix = element.text or ""
    return prefix + "".join(
        str(html.tostring(child, encoding="unicode", method="html")) for child in element
    )


def _body(element: HtmlElement) -> tuple[str, str]:
    sanitized = _sanitize(element)
    return _inner_html(sanitized).strip(), _element_text(sanitized)


def _references(document: HtmlElement, base_url: str) -> tuple[str, ...]:
    values: list[str] = []
    for attribute in ("src", "href", "background", "data", "poster"):
        values.extend(document.xpath(f"//@{attribute}"))
    for srcset in document.xpath("//@srcset"):
        values.extend(part.strip().split(" ", 1)[0] for part in srcset.split(","))
    css_sources = document.xpath("//@style") + document.xpath("//style/text()")
    for css in css_sources:
        values.extend(_CSS_URL_RE.findall(css))
    return resolve_references(base_url, values)


def _topic_and_forum_ids(url: str) -> tuple[int | None, int | None]:
    return _id_from_url(_TOPIC_ID_RE, url), _id_from_url(_FORUM_ID_RE, url)


def _first_text(nodes: list[str | HtmlElement]) -> str | None:
    for node in nodes:
        value = node if isinstance(node, str) else node.text_content()
        cleaned = _clean_text(str(value))
        if cleaned:
            return cleaned
    return None


def _profile_from_cell(cell: HtmlElement) -> tuple[int | None, str | None]:
    links = cell.xpath(
        './/a[contains(@href,"profile.php?mode=viewprofile") or '
        'contains(@href,"memberlist.php?mode=viewprofile")]'
    )
    if not links:
        return None, _first_text(cell.xpath(".//b"))
    return _id_from_url(_USER_ID_RE, links[0].get("href", "")), _first_text([links[0]])


def _parse_phpbb_listings(document: HtmlElement, forum_id: int | None) -> list[ParsedTopicListing]:
    listings: list[ParsedTopicListing] = []
    topic_xpath = (
        f'.//a[{_class_xpath("topiclink")}] | '
        f'.//a[{_class_xpath("topictitle")}]'
    )
    for row in document.xpath(f"//tr[{topic_xpath}]"):
        topic_links = row.xpath(topic_xpath)
        cells = row.xpath("./td")
        if not topic_links or len(cells) < 3:
            continue
        topic_link = topic_links[0]
        topic_id = _id_from_url(_TOPIC_ID_RE, topic_link.get("href", ""))
        if topic_id is None:
            continue
        title_cell = topic_link.xpath("ancestor::td[1]")
        if not title_cell or title_cell[0] not in cells:
            continue
        title_index = cells.index(title_cell[0])
        if title_index + 1 >= len(cells):
            continue
        author_id, author_name = _profile_from_cell(cells[title_index + 1])
        last_cell = cells[-1]
        last_author_id, last_author_name = _profile_from_cell(last_cell)
        last_links = last_cell.xpath('.//a[contains(@href,"viewtopic.php")]/@href')
        last_post_id = _id_from_url(_POST_ID_RE, last_links[-1]) if last_links else None
        created_at = parse_forum_date(topic_link.get("title", ""))
        last_posted_at = parse_forum_date(_clean_text(last_cell.text_content()))
        listings.append(
            ParsedTopicListing(
                topic_id=topic_id,
                forum_id=forum_id,
                title=_clean_text(topic_link.text_content()),
                author_id=author_id,
                author_name=author_name,
                created_at=created_at,
                last_post_id=last_post_id,
                last_author_id=last_author_id,
                last_author_name=last_author_name,
                last_posted_at=last_posted_at,
            )
        )
    return listings


def _snitz_body(content_cell: HtmlElement) -> HtmlElement:
    horizontal_rules = content_cell.xpath("./hr|.//hr")
    if not horizontal_rules:
        return content_cell
    rule = horizontal_rules[0]
    sibling = rule.getnext()
    return sibling if isinstance(sibling, HtmlElement) else content_cell


def _parse_snitz(document: HtmlElement, url: str, source_encoding: str) -> ParsedPage:
    topic_id, forum_id = _topic_and_forum_ids(url)
    forum_name = _first_text(
        document.xpath('//a[contains(translate(@href,"forum","FORUM"),"FORUM.asp?FORUM_ID=")]')
    )
    topic_title = None
    for href in document.xpath('//a[contains(@href,"Topic_Title=")]/@href'):
        values = parse_qs(urlsplit(href.replace("&amp;", "&")).query).get("Topic_Title")
        if values:
            topic_title = values[0]
            break

    posts: list[ParsedPost] = []
    for row in document.xpath("//tr[count(./td) >= 2]"):
        cells = row.xpath("./td")
        row_text = _clean_text(row.text_content())
        date_match = _SNITZ_DATE_RE.search(row_text)
        if not date_match:
            continue
        author_link = cells[0].xpath('.//a[contains(@href,"pop_profile.asp")]')
        author_name = (
            _first_text(author_link) or _first_text(cells[0].xpath(".//b")) or "Desconhecido"
        )
        author_id = (
            _id_from_url(_USER_ID_RE, author_link[0].get("href", "")) if author_link else None
        )
        anchors = cells[1].xpath('.//a[@name and string(number(@name)) != "NaN"]/@name')
        post_id = int(anchors[0]) if anchors else None
        body_html, body_text = _body(_snitz_body(cells[1]))
        raw_date = date_match.group(1).replace(" : ", " ").replace(": ", " ")
        posts.append(
            ParsedPost(
                topic_id=topic_id,
                forum_id=forum_id,
                post_id=post_id,
                author_id=author_id,
                author_name=author_name,
                posted_at=parse_forum_date(raw_date),
                posted_at_raw=raw_date,
                body_html=body_html,
                body_text=body_text,
            )
        )

    return ParsedPage(
        era="snitz",
        topic_id=topic_id,
        forum_id=forum_id,
        topic_title=topic_title,
        forum_name=forum_name,
        source_encoding=source_encoding,
        posts=tuple(posts),
        references=_references(document, url),
    )


def _parse_phpbb2(document: HtmlElement, url: str, source_encoding: str) -> ParsedPage:
    topic_id, forum_id = _topic_and_forum_ids(url)
    topic_title = _first_text(document.xpath(f"//td[{_class_xpath('toprow')}]//b"))
    if not topic_title:
        title = _first_text(document.xpath("//title"))
        topic_title = re.split(r"(?:Exibir t[oó]pico|View topic)\s*-\s*", title or "", maxsplit=1)[
            -1
        ]
    forum_name = _first_text(document.xpath('//link[@rel="up"]/@title')) or _first_text(
        document.xpath('//a[contains(@href,"viewforum.php?f=")]')
    )
    if urlsplit(url).path.casefold().endswith("viewforum.php"):
        forum_name, topic_title = topic_title, None

    posts: list[ParsedPost] = []
    for anchor in document.xpath('//a[@name and string(number(@name)) != "NaN"]'):
        table = anchor.xpath("ancestor::table[1]")
        row = anchor.xpath("ancestor::tr[1]")
        if not table or not row:
            continue
        cells = row[0].xpath("./td")
        if len(cells) < 2:
            continue
        author_name = _first_text(cells[0].xpath(".//b")) or "Desconhecido"
        profile_links = table[0].xpath('.//a[contains(@href,"profile.php?mode=viewprofile")]/@href')
        author_id = _id_from_url(_USER_ID_RE, profile_links[0]) if profile_links else None
        body_nodes = cells[1].xpath(f".//span[{_class_xpath('largetext')}]")
        if not body_nodes:
            continue
        table_text = _clean_text(table[0].text_content())
        date_match = re.search(
            r"(?:Seg|Ter|Qua|Qui|Sex|Sáb|Sab|Dom|Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+"
            r"[A-Za-zÀ-ÿ]+\s+\d{1,2},\s+\d{4}\s+\d{1,2}:\d{2}\s+(?:am|pm)",
            table_text,
            re.I,
        )
        raw_date = date_match.group(0) if date_match else None
        body_html, body_text = _body(body_nodes[0])
        posts.append(
            ParsedPost(
                topic_id=topic_id,
                forum_id=forum_id,
                post_id=int(anchor.get("name")),
                author_id=author_id,
                author_name=author_name,
                posted_at=parse_forum_date(raw_date) if raw_date else None,
                posted_at_raw=raw_date,
                body_html=body_html,
                body_text=body_text,
            )
        )

    return ParsedPage(
        era="phpbb2",
        topic_id=topic_id,
        forum_id=forum_id,
        topic_title=topic_title,
        forum_name=forum_name,
        source_encoding=source_encoding,
        posts=tuple(posts),
        references=_references(document, url),
        listings=tuple(_parse_phpbb_listings(document, forum_id)),
    )


def _parse_phpbb3_print(document: HtmlElement, url: str, source_encoding: str) -> ParsedPage:
    topic_id, forum_id = _topic_and_forum_ids(url)
    topic_title = _first_text(document.xpath('//div[@id="page-header"]//h2'))
    posts: list[ParsedPost] = []
    for post in document.xpath(f"//div[{_class_xpath('post')}]"):
        author_name = (
            _first_text(post.xpath(f".//div[{_class_xpath('author')}]//strong")) or "Desconhecido"
        )
        raw_date = _first_text(post.xpath(f".//div[{_class_xpath('date')}]//strong"))
        content = post.xpath(f".//div[{_class_xpath('content')}]")
        if not content:
            continue
        body_html, body_text = _body(content[0])
        posts.append(
            ParsedPost(
                topic_id=topic_id,
                forum_id=forum_id,
                post_id=None,
                author_id=None,
                author_name=author_name,
                posted_at=parse_forum_date(raw_date) if raw_date else None,
                posted_at_raw=raw_date,
                body_html=body_html,
                body_text=body_text,
            )
        )
    return ParsedPage(
        era="phpbb3",
        topic_id=topic_id,
        forum_id=forum_id,
        topic_title=topic_title,
        forum_name=None,
        source_encoding=source_encoding,
        posts=tuple(posts),
        references=_references(document, url),
    )


def _parse_phpbb3(document: HtmlElement, url: str, source_encoding: str) -> ParsedPage:
    if document.xpath(f"//div[{_class_xpath('post')}]//div[{_class_xpath('content')}]"):
        return _parse_phpbb3_print(document, url, source_encoding)

    topic_id, forum_id = _topic_and_forum_ids(url)
    topic_title = _first_text(document.xpath('//div[@id="pageheader"]//h2')) or _first_text(
        document.xpath('//h2/a[contains(@href,"viewtopic.php")]')
    )
    forum_name = _first_text(document.xpath('//link[@rel="up"]/@title')) or _first_text(
        document.xpath('//a[contains(@href,"viewforum.php?f=")]')
    )
    if urlsplit(url).path.casefold().endswith("viewforum.php"):
        forum_name, topic_title = topic_title, None
    posts: list[ParsedPost] = []
    for anchor in document.xpath(
        '//a[starts-with(@name,"p") and string(number(substring(@name,2))) != "NaN"]'
    ):
        table = anchor.xpath("ancestor::table[1]")
        row = anchor.xpath("ancestor::tr[1]")
        if not table or not row:
            continue
        author_name = (
            _first_text(row[0].xpath(f".//*[{_class_xpath('postauthor')}]")) or "Desconhecido"
        )
        profile_links = table[0].xpath(
            './/a[contains(@href,"memberlist.php?mode=viewprofile")]/@href'
        )
        author_id = _id_from_url(_USER_ID_RE, profile_links[0]) if profile_links else None
        body_nodes = table[0].xpath(f".//div[{_class_xpath('postbody')}]")
        if not body_nodes:
            continue
        header_text = _clean_text(row[0].text_content())
        date_match = _PHPBB_DATE_RE.search(header_text)
        raw_date = date_match.group(1).strip() if date_match else None
        body_html, body_text = _body(body_nodes[0])
        posts.append(
            ParsedPost(
                topic_id=topic_id,
                forum_id=forum_id,
                post_id=int(anchor.get("name", "")[1:]),
                author_id=author_id,
                author_name=author_name,
                posted_at=parse_forum_date(raw_date) if raw_date else None,
                posted_at_raw=raw_date,
                body_html=body_html,
                body_text=body_text,
            )
        )

    return ParsedPage(
        era="phpbb3",
        topic_id=topic_id,
        forum_id=forum_id,
        topic_title=topic_title,
        forum_name=forum_name,
        source_encoding=source_encoding,
        posts=tuple(posts),
        references=_references(document, url),
        listings=tuple(_parse_phpbb_listings(document, forum_id)),
    )


def parse_page(raw: bytes, original_url: str, era: str | None = None) -> ParsedPage:
    """Decode and parse one captured forum page."""

    decoded = decode_html(raw)
    document = html.fromstring(decoded.text, base_url=original_url)
    selected_era = era or era_for_url(original_url)
    if selected_era == "snitz":
        return _parse_snitz(document, original_url, decoded.source_encoding)
    if selected_era == "phpbb2":
        return _parse_phpbb2(document, original_url, decoded.source_encoding)
    if selected_era == "phpbb3":
        return _parse_phpbb3(document, original_url, decoded.source_encoding)
    raise ValueError(f"motor de fórum não suportado para {original_url!r}")


def parse_css_references(raw: bytes, original_url: str) -> tuple[str, ...]:
    decoded = decode_html(raw)
    return resolve_references(original_url, _CSS_URL_RE.findall(decoded.text))
