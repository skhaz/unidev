"""Extract posts and resource references from UniDev's historical engines."""

from __future__ import annotations

import copy
import re
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

from lxml import html
from lxml.etree import ParserError
from lxml.html import HtmlElement

from unidev_archive.css import css_reference_values
from unidev_archive.dates import parse_forum_date

# Mapa de tradução para caracteres de controle XML-inválidos
_CONTROL_CHARS = "".join(chr(c) for c in range(32) if c not in (9, 10, 13))
_CONTROL_CHAR_MAP = str.maketrans({c: None for c in _CONTROL_CHARS})
from unidev_archive.encoding import decode_html
from unidev_archive.markup import REMOVED_ELEMENT_NAMES, local_name
from unidev_archive.models import ParsedPage, ParsedPost, ParsedTopicListing
from unidev_archive.srcset import parse_srcset
from unidev_archive.urls import era_for_url, resolve_reference_sets, resolve_references

_SPACE_RE = re.compile(r"[\t\f\v ]+")
_XML_DECL_RE = re.compile(r"^\s*<\?xml[^>]*\?>", re.I)
_USER_ID_RE = re.compile(r"(?:[?&](?:u|id)=)(\d+)", re.I)
_TOPIC_ID_RE = re.compile(r"(?:[?&](?:t|TOPIC_ID)=)(\d+)", re.I)
_FORUM_ID_RE = re.compile(r"(?:[?&](?:f|FORUM_ID)=)(\d+)", re.I)
_POST_ID_RE = re.compile(r"(?:[?&#](?:p|#p)=?)(\d+)", re.I)
_COMMUNITY_THREAD_RE = re.compile(
    r"/forums/(?:thread/(\d+)\.aspx|permalink/(\d+)/\d+/showthread\.aspx|(\d+)/showthread\.aspx)$",
    re.I,
)
_COMMUNITY_FORUM_RE = re.compile(r"/forums/(\d+)/showforum\.aspx", re.I)
_COMMUNITY_USER_ID_RE = re.compile(r"(?:[?&](?:userid|u)=)(\d+)", re.I)
_COMMUNITY_DATE_RE = re.compile(
    r"\b(\d{1,2}-\d{1,2}-\d{4},\s*\d{1,2}:\d{2}(?::\d{2})?\s*[AP]M)\b",
    re.I,
)
_REMOVED_ELEMENTS_XPATH = " | ".join(f"//{tag}" for tag in sorted(REMOVED_ELEMENT_NAMES))
_SAFE_REFERENCE_SCOPE = "//*"
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
        tail = br.tail or ""
        br.tail = "\n" + tail.translate(_CONTROL_CHAR_MAP)
    for block in clone.xpath(".//p|.//div|.//li|.//pre|.//blockquote"):
        tail = block.tail or ""
        block.tail = "\n" + tail.translate(_CONTROL_CHAR_MAP)
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


def _remove_non_published_elements(document: HtmlElement) -> None:
    for element in document.xpath(_REMOVED_ELEMENTS_XPATH):
        parent = element.getparent()
        if parent is not None:
            parent.remove(element)
    for svg in document.xpath("//*[local-name()='svg']"):
        for element in svg.iter():
            if local_name(element.tag) == "style":
                parent = element.getparent()
                if parent is not None:
                    parent.remove(element)
                continue
            for attribute in tuple(element.attrib):
                if local_name(attribute) in {
                    "background",
                    "data",
                    "href",
                    "poster",
                    "src",
                    "srcset",
                    "style",
                }:
                    element.attrib.pop(attribute, None)


def _reference_sets(
    document: HtmlElement,
    base_url: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    values: list[str] = []
    asset_values: list[str] = []
    for attribute in ("src", "background", "data", "poster"):
        references = document.xpath(f"{_SAFE_REFERENCE_SCOPE}/@{attribute}")
        values.extend(references)
        asset_values.extend(references)
    values.extend(document.xpath(f"{_SAFE_REFERENCE_SCOPE}[local-name()!='link']/@href"))
    loading_links = document.xpath(
        f"{_SAFE_REFERENCE_SCOPE}[local-name()='link' and ("
        "contains(translate(@rel, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', "
        "'abcdefghijklmnopqrstuvwxyz'), 'stylesheet') or "
        "contains(translate(@rel, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', "
        "'abcdefghijklmnopqrstuvwxyz'), 'icon') or "
        "contains(translate(@rel, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', "
        "'abcdefghijklmnopqrstuvwxyz'), 'preload'))]/@href"
    )
    values.extend(loading_links)
    asset_values.extend(loading_links)
    for srcset in document.xpath(f"{_SAFE_REFERENCE_SCOPE}/@srcset"):
        references = [candidate.url for candidate in parse_srcset(str(srcset))]
        values.extend(references)
        asset_values.extend(references)
    css_values = document.xpath(
        f"{_SAFE_REFERENCE_SCOPE}/@style | {_SAFE_REFERENCE_SCOPE}[local-name()='style']/text()"
    )
    for css in css_values:
        references = css_reference_values(str(css))
        values.extend(references)
        asset_values.extend(references)
    return resolve_reference_sets(base_url, values, asset_values)


def _topic_and_forum_ids(url: str) -> tuple[int | None, int | None]:
    return _id_from_url(_TOPIC_ID_RE, url), _id_from_url(_FORUM_ID_RE, url)


def _uses_indirect_topic_url(url: str, topic_id: int | None) -> bool:
    if topic_id is None:
        return True
    view = parse_qs(urlsplit(url).query).get("view", ())
    return any(value.casefold() in {"next", "previous"} for value in view)


def _dominant_link_id(
    document: HtmlElement,
    endpoint: str,
    pattern: re.Pattern[str],
    fallback: int | None,
) -> int | None:
    counts: dict[int, int] = {}
    for link in document.xpath(f'//*[@href and contains(@href,"{endpoint}")]/@href'):
        identifier = _id_from_url(pattern, str(link))
        if identifier is not None:
            counts[identifier] = counts.get(identifier, 0) + 1
    if not counts:
        return fallback
    return max(counts, key=lambda identifier: (counts[identifier], identifier == fallback))


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
    topic_xpath = f".//a[{_class_xpath('topiclink')}] | .//a[{_class_xpath('topictitle')}]"
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
        references=(),
    )


def _parse_phpbb2(document: HtmlElement, url: str, source_encoding: str) -> ParsedPage:
    topic_id, forum_id = _topic_and_forum_ids(url)
    page_path = urlsplit(url).path.casefold()
    if page_path.endswith("viewtopic.php") and _uses_indirect_topic_url(url, topic_id):
        topic_id = _dominant_link_id(document, "viewtopic.php", _TOPIC_ID_RE, topic_id)
    up_links = document.xpath('//link[@rel="up"]')
    if forum_id is None and up_links:
        forum_id = _id_from_url(_FORUM_ID_RE, up_links[0].get("href", ""))
    topic_title = _first_text(document.xpath(f"//td[{_class_xpath('toprow')}]//b"))
    if not topic_title:
        title = _first_text(document.xpath("//title"))
        topic_title = re.split(r"(?:Exibir t[oó]pico|View topic)\s*-\s*", title or "", maxsplit=1)[
            -1
        ]
    forum_name = (_clean_text(up_links[0].get("title", "")) if up_links else None) or _first_text(
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
        references=(),
        listings=tuple(_parse_phpbb_listings(document, forum_id)),
    )


def _parse_phpbb3_print(document: HtmlElement, url: str, source_encoding: str) -> ParsedPage:
    topic_id, forum_id = _topic_and_forum_ids(url)
    if _uses_indirect_topic_url(url, topic_id):
        topic_id = _dominant_link_id(document, "viewtopic.php", _TOPIC_ID_RE, topic_id)
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
        references=(),
    )


def _parse_phpbb3_portal_posts(document: HtmlElement) -> tuple[ParsedPost, ...]:
    posts: list[ParsedPost] = []
    seen_post_ids: set[int] = set()
    for table in document.xpath(f"//table[{_class_xpath('tablebg')}]"):
        title_links = table.xpath('.//h4/a[contains(@href,"viewtopic.php") and .//strong][1]')
        body_nodes = table.xpath(
            f'.//td[{_class_xpath("genmed")}]/div[contains(@style,"margin")][1]'
        )
        if not title_links or not body_nodes:
            continue
        post_id = _id_from_url(_POST_ID_RE, title_links[0].get("href", ""))
        if post_id is not None and post_id in seen_post_ids:
            continue
        if post_id is not None:
            seen_post_ids.add(post_id)
        reply_links = table.xpath('.//a[contains(@href,"posting.php")]/@href')
        topic_id = _id_from_url(_TOPIC_ID_RE, reply_links[0]) if reply_links else None
        forum_links = table.xpath('.//a[contains(@href,"viewforum.php?f=")]')
        forum_id = (
            _id_from_url(_FORUM_ID_RE, forum_links[0].get("href", "")) if forum_links else None
        )
        profile_links = table.xpath('.//a[contains(@href,"memberlist.php?mode=viewprofile")][1]')
        author_id = (
            _id_from_url(_USER_ID_RE, profile_links[0].get("href", "")) if profile_links else None
        )
        author_name = _element_text(profile_links[0]) if profile_links else "Desconhecido"
        table_text = _clean_text(table.text_content())
        posted_at = parse_forum_date(table_text)
        body_html, body_text = _body(body_nodes[0])
        posts.append(
            ParsedPost(
                topic_id=topic_id,
                forum_id=forum_id,
                post_id=post_id,
                author_id=author_id,
                author_name=author_name,
                posted_at=posted_at,
                posted_at_raw=posted_at,
                body_html=body_html,
                body_text=body_text,
                topic_title=_element_text(title_links[0]),
                forum_name=_element_text(forum_links[0]) if forum_links else None,
            )
        )
    return tuple(posts)


def _parse_community_server(
    document: HtmlElement,
    url: str,
    source_encoding: str,
) -> ParsedPage:
    topic_match = _COMMUNITY_THREAD_RE.search(urlsplit(url).path)
    topic_value = (
        topic_match.group(1) or topic_match.group(2) or topic_match.group(3)
        if topic_match
        else None
    )
    topic_id = int(topic_value) if topic_value is not None else None
    forum_links = document.xpath(
        '//a[contains(translate(@href, "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "showforum.aspx")]'
    )
    forum_id = None
    forum_name = None
    for link in forum_links:
        match = _COMMUNITY_FORUM_RE.search(link.get("href", ""))
        if match:
            forum_id = int(match.group(1))
            forum_name = _clean_text(link.get("title", "")) or _clean_text(link.text_content())
            break
    title = _first_text(document.xpath("//title"))
    topic_title = title.removeprefix("Forum Unidev - ").strip() if title else None
    posts: list[ParsedPost] = []
    for container in document.xpath(f"//div[{_class_xpath('ForumPostArea')}]"):
        anchors = container.xpath(".//a[@name]")
        anchor = next((item for item in anchors if item.get("name", "").isdigit()), None)
        body_nodes = container.xpath(f".//div[{_class_xpath('ForumPostContentText')}]")
        if anchor is None or not body_nodes:
            continue
        author_links = container.xpath(f".//li[{_class_xpath('ForumPostUserName')}]/a")
        author_name = _first_text(author_links) or "Desconhecido"
        identity_links = container.xpath(
            './/img[contains(translate(@src, "ABCDEFGHIJKLMNOPQRSTUVWXYZ", '
            '"abcdefghijklmnopqrstuvwxyz"), "avatar.aspx?userid=")]/@src | '
            './/a[contains(translate(@href, "ABCDEFGHIJKLMNOPQRSTUVWXYZ", '
            '"abcdefghijklmnopqrstuvwxyz"), "searchresults.aspx?u=")]/@href'
        )
        author_id = (
            _id_from_url(_COMMUNITY_USER_ID_RE, str(identity_links[0])) if identity_links else None
        )
        date_cells = anchor.xpath("ancestor::td[1]")
        headers = container.xpath(f".//h4[{_class_xpath('ForumPostHeader')}]")
        header_text = (
            _clean_text(date_cells[0].text_content())
            if date_cells
            else _clean_text(headers[0].text_content())
            if headers
            else ""
        )
        date_match = _COMMUNITY_DATE_RE.search(header_text)
        raw_date = date_match.group(1) if date_match else None
        body_html, body_text = _body(body_nodes[0])
        posts.append(
            ParsedPost(
                topic_id=topic_id,
                forum_id=forum_id,
                post_id=int(anchor.get("name", "")),
                author_id=author_id,
                author_name=author_name,
                posted_at=parse_forum_date(raw_date) if raw_date else None,
                posted_at_raw=raw_date,
                body_html=body_html,
                body_text=body_text,
                topic_title=topic_title,
                forum_name=forum_name,
            )
        )
    return ParsedPage(
        era="community-server",
        topic_id=topic_id,
        forum_id=forum_id,
        topic_title=topic_title,
        forum_name=forum_name,
        source_encoding=source_encoding,
        posts=tuple(posts),
        references=(),
    )


def _parse_phpbb3(document: HtmlElement, url: str, source_encoding: str) -> ParsedPage:
    if document.xpath(f"//div[{_class_xpath('post')}]//div[{_class_xpath('content')}]"):
        return _parse_phpbb3_print(document, url, source_encoding)

    page_path = urlsplit(url).path.casefold()
    topic_id, forum_id = _topic_and_forum_ids(url)
    if page_path.endswith("viewtopic.php") and _uses_indirect_topic_url(url, topic_id):
        topic_id = _dominant_link_id(document, "viewtopic.php", _TOPIC_ID_RE, topic_id)
    up_links = document.xpath('//link[@rel="up"]')
    if forum_id is None and up_links:
        forum_id = _id_from_url(_FORUM_ID_RE, up_links[0].get("href", ""))
    topic_title = _first_text(document.xpath('//div[@id="pageheader"]//h2')) or _first_text(
        document.xpath('//h2/a[contains(@href,"viewtopic.php")]')
    )
    forum_name = (_clean_text(up_links[0].get("title", "")) if up_links else None) or _first_text(
        document.xpath('//a[contains(@href,"viewforum.php?f=")]')
    )
    if page_path.endswith("viewforum.php"):
        forum_name, topic_title = topic_title, None
    posts: list[ParsedPost] = (
        list(_parse_phpbb3_portal_posts(document)) if page_path.endswith("/portal.php") else []
    )
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
        references=(),
        listings=tuple(_parse_phpbb_listings(document, forum_id)),
    )


def parse_html_reference_sets(
    raw: bytes,
    original_url: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Extract all and subresource references without requiring an era parser."""

    decoded = decode_html(raw)
    text = _XML_DECL_RE.sub("", decoded.text, count=1)
    if not text.strip():
        return (), ()
    try:
        document = html.document_fromstring(text, base_url=original_url)
    except ParserError:
        return (), ()
    _remove_non_published_elements(document)
    return _reference_sets(document, original_url)


def parse_page(raw: bytes, original_url: str, era: str | None = None) -> ParsedPage:
    """Decode and parse one captured forum page."""

    decoded = decode_html(raw)
    selected_era = era or era_for_url(original_url)
    text = _XML_DECL_RE.sub("", decoded.text, count=1)
    if not text.strip():
        if selected_era is None:
            raise ValueError(f"motor de fórum não suportado para {original_url!r}")
        topic_id, forum_id = _topic_and_forum_ids(original_url)
        return ParsedPage(
            era=selected_era,
            topic_id=topic_id,
            forum_id=forum_id,
            topic_title=None,
            forum_name=None,
            source_encoding=decoded.source_encoding,
            posts=(),
            references=(),
        )
    document = html.fromstring(text, base_url=original_url)
    _remove_non_published_elements(document)
    references, asset_references = _reference_sets(document, original_url)
    if selected_era == "community-server":
        page = _parse_community_server(document, original_url, decoded.source_encoding)
    elif selected_era == "snitz":
        page = _parse_snitz(document, original_url, decoded.source_encoding)
    elif selected_era == "phpbb2":
        page = _parse_phpbb2(document, original_url, decoded.source_encoding)
    elif selected_era == "phpbb3":
        page = _parse_phpbb3(document, original_url, decoded.source_encoding)
    else:
        raise ValueError(f"motor de fórum não suportado para {original_url!r}")
    return replace(
        page,
        references=references,
        asset_references=asset_references,
    )


def parse_css_references(raw: bytes, original_url: str) -> tuple[str, ...]:
    decoded = decode_html(raw)
    return resolve_references(original_url, css_reference_values(decoded.text))
