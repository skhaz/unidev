"""Preserve complete historical pages while disabling active and remote behavior."""

from __future__ import annotations

import posixpath
import re
from collections.abc import Mapping
from pathlib import PurePosixPath
from urllib.parse import parse_qsl, urljoin, urlsplit

from lxml import html
from lxml.html import HtmlElement

from unidev_archive.css import (
    CssReference,
    css_syntax_text,
    has_unsupported_network_syntax,
    rewrite_css_references,
)
from unidev_archive.dates import parse_forum_date
from unidev_archive.encoding import decode_html
from unidev_archive.markup import REMOVED_ELEMENT_NAMES, local_name
from unidev_archive.routing import RouteRegistry
from unidev_archive.srcset import SrcsetCandidate, parse_srcset, serialize_srcset
from unidev_archive.urls import canonical_url, era_for_url, unwrap_wayback_url

_CSP = (
    "default-src 'none'; img-src 'self' data:; media-src 'self'; font-src 'self'; "
    "style-src 'self' 'unsafe-inline'; form-action 'self'; base-uri 'none'; "
    "frame-src 'none'; object-src 'none'"
)
_SNITZ_POST_DATE_RE = re.compile(
    r"Postado\s*-\s*(\d{1,2}/\d{1,2}/\d{2,4}\s*:\s*\d{1,2}:\d{2}:\d{2})",
    re.I,
)
_DANGEROUS_CSS_RE = re.compile(
    r"(?:expression\s*\(|behavior\s*:|-moz-binding\s*:|javascript\s*:)", re.I
)
_REMOTE_SCHEMES = {"http", "https"}
_RESOURCE_ATTRIBUTES = {
    "audio": ("src",),
    "img": ("src", "srcset"),
    "input": ("src",),
    "source": ("src", "srcset"),
    "video": ("src", "poster"),
}


def _relative(from_file: PurePosixPath, target: PurePosixPath) -> str:
    return posixpath.relpath(target.as_posix(), start=from_file.parent.as_posix())


def _resource_target(
    url: str,
    resources: Mapping[str, PurePosixPath],
) -> PurePosixPath | None:
    return resources.get(canonical_url(url))


def _remove_dangerous_css_declarations(value: str) -> str:
    syntax = css_syntax_text(value)
    if not _DANGEROUS_CSS_RE.search(syntax):
        return value

    output: list[str] = []
    cursor = 0
    declaration_start = 0
    parentheses = 0
    for index, character in enumerate(syntax):
        if character == "(":
            parentheses += 1
        elif character == ")" and parentheses:
            parentheses -= 1
        elif not parentheses and character == "{":
            declaration_start = index + 1
        elif not parentheses and character in ";}":
            if _DANGEROUS_CSS_RE.search(syntax[declaration_start:index]):
                output.append(value[cursor:declaration_start])
                cursor = index if character == "}" else index + 1
            declaration_start = index + 1
    if _DANGEROUS_CSS_RE.search(syntax[declaration_start:]):
        output.append(value[cursor:declaration_start])
        cursor = len(value)
    output.append(value[cursor:])
    return "".join(output)


def _rewrite_css(
    value: str,
    source_url: str,
    output_file: PurePosixPath,
    resources: Mapping[str, PurePosixPath],
) -> str:
    if has_unsupported_network_syntax(value):
        return ""
    value = _remove_dangerous_css_declarations(value)

    def replacement(reference: CssReference) -> str:
        if not reference.value:
            return "" if reference.is_import else 'url("data:,")'
        if reference.value.startswith("data:"):
            return value[reference.start : reference.end]
        try:
            absolute = unwrap_wayback_url(urljoin(source_url, reference.value))
        except ValueError:
            return "" if reference.is_import else 'url("data:,")'
        target = _resource_target(absolute, resources)
        if target is None:
            return "" if reference.is_import else 'url("data:,")'
        rewritten = f'url("{_relative(output_file, target)}")'
        return "@import " + rewritten if reference.is_import else rewritten

    return rewrite_css_references(value, replacement)


def _mark_missing(element: HtmlElement, attribute: str) -> None:
    element.attrib.pop(attribute, None)
    classes = element.get("class", "").split()
    if "archive-link-missing" not in classes:
        classes.append("archive-link-missing")
    element.set("class", " ".join(classes))
    element.set("title", "Captura local indisponível")
    element.set("aria-disabled", "true")


def _rewrite_anchor(
    element: HtmlElement,
    source_url: str,
    capture_timestamp: str | None,
    output_file: PurePosixPath,
    registry: RouteRegistry,
    resources: Mapping[str, PurePosixPath],
) -> None:
    reference = element.get("href")
    if not reference or reference.startswith("#"):
        return
    if reference.casefold().startswith(("mailto:", "tel:")):
        _mark_missing(element, "href")
        return
    if reference.casefold().startswith(("javascript:", "vbscript:", "data:")):
        _mark_missing(element, "href")
        return
    try:
        absolute = unwrap_wayback_url(urljoin(source_url, reference))
        parsed = urlsplit(absolute)
        _ = parsed.port
    except ValueError:
        _mark_missing(element, "href")
        return
    if (
        parsed.scheme not in _REMOTE_SCHEMES
        or not parsed.hostname
        or parsed.hostname.casefold() == "web.archive.org"
    ):
        _mark_missing(element, "href")
        return
    resolution = registry.resolve(absolute, capture_timestamp)
    if resolution is not None:
        target = _relative(output_file, resolution.path)
        element.set(
            "href",
            target + (f"#{resolution.fragment}" if resolution.fragment else ""),
        )
        return
    resource = _resource_target(absolute, resources)
    if resource is not None:
        element.set("href", _relative(output_file, resource))
        return
    _mark_missing(element, "href")


def _rewrite_resource(
    element: HtmlElement,
    attribute: str,
    source_url: str,
    output_file: PurePosixPath,
    resources: Mapping[str, PurePosixPath],
) -> None:
    reference = element.get(attribute)
    if not reference:
        return
    if attribute == "srcset":
        rewritten: list[SrcsetCandidate] = []
        for candidate in parse_srcset(reference):
            if candidate.url.startswith("data:"):
                rewritten.append(candidate)
                continue
            try:
                absolute = unwrap_wayback_url(urljoin(source_url, candidate.url))
            except ValueError:
                continue
            target = _resource_target(absolute, resources)
            if target is not None:
                rewritten.append(
                    SrcsetCandidate(
                        url=_relative(output_file, target),
                        descriptor=candidate.descriptor,
                    )
                )
        if rewritten:
            element.set(attribute, serialize_srcset(tuple(rewritten)))
        else:
            _mark_missing(element, attribute)
        return
    if reference.startswith("data:"):
        return
    try:
        absolute = unwrap_wayback_url(urljoin(source_url, reference))
    except ValueError:
        _mark_missing(element, attribute)
        return
    target = _resource_target(absolute, resources)
    if target is None:
        _mark_missing(element, attribute)
    else:
        element.set(attribute, _relative(output_file, target))


def _serialize(element: HtmlElement, method: str) -> str:
    value = html.tostring(element, encoding="unicode", method=method)
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _element_source_text(value: object) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)


def preserve_svg(raw: bytes) -> str:
    """Remove executable and network-capable SVG features before same-origin publication."""

    document = html.fromstring(raw)
    blocked = {
        "script",
        "foreignobject",
        "iframe",
        "object",
        "embed",
        "link",
        "audio",
        "video",
        "animate",
        "animatemotion",
        "animatetransform",
        "set",
        "discard",
        "style",
    }
    for element in list(document.iter()):
        tag = local_name(element.tag)
        if tag in blocked:
            parent = element.getparent()
            if parent is not None:
                parent.remove(element)
            continue
        for attribute in tuple(element.attrib):
            lowered = local_name(attribute)
            value = element.get(attribute, "").strip()
            unsafe_href = lowered == "href" and value and not value.startswith("#")
            unsafe_value = "javascript:" in value.casefold() or "url(" in value.casefold()
            if lowered.startswith("on") or unsafe_href or lowered == "style" or unsafe_value:
                element.attrib.pop(attribute, None)
    return _serialize(document, "xml")


def preserve_stylesheet(
    raw: bytes,
    source_url: str,
    output_file: PurePosixPath,
    resources: Mapping[str, PurePosixPath],
) -> str:
    """Decode and rewrite every CSS subresource to a verified local target."""

    return _rewrite_css(
        decode_html(raw).text,
        source_url,
        output_file,
        resources,
    )


def _class_xpath(name: str) -> str:
    return f'contains(concat(" ", normalize-space(@class), " "), " {name} ")'


def _remove_out_of_period_posts(
    document: HtmlElement,
    source_url: str,
    period_start: str,
    period_end: str,
) -> None:
    era = era_for_url(source_url)
    candidates: list[tuple[HtmlElement, str | None]] = []
    if era == "snitz":
        for row in document.xpath("//tr[count(./td) >= 2]"):
            match = _SNITZ_POST_DATE_RE.search(row.text_content())
            if match and row.xpath('./td[2]//a[@name and string(number(@name)) != "NaN"]'):
                candidates.append((row, match.group(1).replace(" : ", " ").replace(": ", " ")))
    elif era == "phpbb2":
        seen: set[HtmlElement] = set()
        for anchor in document.xpath('//a[@name and string(number(@name)) != "NaN"]'):
            tables = anchor.xpath("ancestor::table[1]")
            if not tables or tables[0] in seen:
                continue
            table = tables[0]
            if not table.xpath(f".//span[{_class_xpath('largetext')}]"):
                continue
            seen.add(table)
            candidates.append((table, table.text_content()))
    elif era == "phpbb3":
        print_posts = document.xpath(
            f"//div[{_class_xpath('post')} and .//div[{_class_xpath('content')}]]"
        )
        if print_posts:
            for post in print_posts:
                dates = post.xpath(f".//div[{_class_xpath('date')}]")
                candidates.append((post, dates[0].text_content() if dates else None))
        else:
            seen_tables: set[HtmlElement] = set()
            for anchor in document.xpath(
                '//a[starts-with(@name,"p") and string(number(substring(@name,2))) != "NaN"]'
            ):
                tables = anchor.xpath("ancestor::table[1]")
                if not tables or tables[0] in seen_tables:
                    continue
                table = tables[0]
                if not table.xpath(f".//div[{_class_xpath('postbody')}]"):
                    continue
                seen_tables.add(table)
                rows = anchor.xpath("ancestor::tr[1]")
                candidates.append((table, rows[0].text_content() if rows else None))
            for table in document.xpath(f"//table[{_class_xpath('tablebg')}]"):
                if table in seen_tables:
                    continue
                if table.xpath(
                    './/h4/a[contains(@href,"viewtopic.php") and .//strong]'
                ) and table.xpath(
                    f'.//td[{_class_xpath("genmed")}]/div[contains(@style,"margin")]'
                ):
                    candidates.append((table, table.text_content()))
    elif era == "community-server":
        for post in document.xpath(f"//div[{_class_xpath('ForumPostArea')}]"):
            anchors = post.xpath('.//a[@name and string(number(@name)) != "NaN"]')
            if not anchors:
                continue
            cells = anchors[0].xpath("ancestor::td[1]")
            headers = post.xpath(f".//h4[{_class_xpath('ForumPostHeader')}]")
            candidates.append(
                (
                    post,
                    cells[0].text_content()
                    if cells
                    else headers[0].text_content()
                    if headers
                    else None,
                )
            )
    for element, raw_date in candidates:
        posted_at = parse_forum_date(raw_date or "")
        if posted_at is None or not period_start <= posted_at <= period_end:
            parent = element.getparent()
            if parent is not None:
                parent.remove(element)


def _add_general_search(
    head: HtmlElement, body: HtmlElement | None, output_file: PurePosixPath
) -> None:
    stylesheet = head.makeelement("link")
    stylesheet.set("rel", "stylesheet")
    stylesheet.set("href", _relative(output_file, PurePosixPath("assets/archive-search.css")))
    head.append(stylesheet)
    if body is None or output_file == PurePosixPath("busca", "index.html"):
        return

    toolbar = body.makeelement("aside")
    toolbar.set("id", "unidev-archive-search")
    toolbar.set("aria-label", "Busca geral")
    toolbar.set("data-pagefind-ignore", "all")

    home = toolbar.makeelement("a")
    home.set("class", "unidev-archive-home")
    home.set("href", _relative(output_file, PurePosixPath("index.html")))
    home.text = "Arquivo UniDev"
    toolbar.append(home)

    form = toolbar.makeelement("form", method="get", role="search")
    form.set("action", _relative(output_file, PurePosixPath("busca/index.html")))
    label = form.makeelement("label", **{"for": "unidev-general-search-input"})
    label.text = "Busca geral"
    form.append(label)
    search_input = form.makeelement(
        "input",
        id="unidev-general-search-input",
        name="q",
        type="search",
        autocomplete="off",
        required="required",
        placeholder="Tópicos, mensagens, código ou usuários",
    )
    form.append(search_input)
    button = form.makeelement("button", type="submit")
    button.text = "Buscar"
    form.append(button)
    toolbar.append(form)
    body.insert(0, toolbar)


def preserve_document(
    raw: bytes,
    source_url: str,
    output_file: PurePosixPath,
    registry: RouteRegistry,
    resources: Mapping[str, PurePosixPath],
    capture_timestamp: str | None = None,
    period_start: str | None = None,
    period_end: str | None = None,
) -> str:
    """Return a UTF-8 historical document with only local, inert subresources."""

    decoded = decode_html(raw)
    source = decoded.text if decoded.text.strip() else "<html><head></head><body></body></html>"
    document = html.document_fromstring(source, base_url=source_url)
    if period_start is not None and period_end is not None:
        _remove_out_of_period_posts(document, source_url, period_start, period_end)
    head_nodes = document.xpath("//head")
    body_nodes = document.xpath("//body")
    is_print_view = any(
        key.casefold() == "view" and value.casefold() == "print"
        for key, value in parse_qsl(urlsplit(source_url).query, keep_blank_values=True)
    )
    if body_nodes and not is_print_view:
        body_nodes[0].set("data-pagefind-body", "")
    if head_nodes:
        head = head_nodes[0]
    else:
        head = document.makeelement("head")
        document.insert(0, head)

    for element in list(document.iter()):
        raw_tag = str(element.tag) if isinstance(element.tag, str) else ""
        tag = local_name(raw_tag)
        parent = element
        inside_svg = tag == "svg"
        while not inside_svg and (parent := parent.getparent()) is not None:
            parent_tag = local_name(parent.tag)
            inside_svg = parent_tag == "svg"
        if tag in REMOVED_ELEMENT_NAMES or (inside_svg and tag == "style"):
            parent = element.getparent()
            if parent is not None:
                parent.remove(element)
            continue
        if tag == "meta":
            http_equiv = element.get("http-equiv", "").casefold()
            if element.get("charset") is not None or http_equiv in {
                "content-type",
                "content-security-policy",
                "refresh",
            }:
                parent = element.getparent()
                if parent is not None:
                    parent.remove(element)
                continue
        if tag == "link" and not any(
            token in element.get("rel", "").casefold()
            for token in ("stylesheet", "icon", "preload")
        ):
            parent = element.getparent()
            if parent is not None:
                parent.remove(element)
            continue
        for attribute in tuple(element.attrib):
            lowered = local_name(attribute)
            value = element.attrib[attribute]
            if (
                lowered.startswith("on")
                or lowered == "srcdoc"
                or (inside_svg and lowered == "style")
                or (
                    inside_svg and ("url(" in value.casefold() or "javascript:" in value.casefold())
                )
                or (inside_svg and lowered == "href" and not value.startswith("#"))
            ):
                element.attrib.pop(attribute, None)
        if tag == "form":
            for attribute in ("action", "method", "target"):
                element.attrib.pop(attribute, None)
            element.set("aria-disabled", "true")
            for control in element.xpath(".//input|.//button|.//select|.//textarea"):
                control.set("disabled", "disabled")
        if tag in {"a", "area"}:
            _rewrite_anchor(
                element,
                source_url,
                capture_timestamp,
                output_file,
                registry,
                resources,
            )
        elif tag == "link":
            _rewrite_resource(element, "href", source_url, output_file, resources)
        for attribute in _RESOURCE_ATTRIBUTES.get(tag, ()):
            _rewrite_resource(element, attribute, source_url, output_file, resources)
        if element.get("background") is not None:
            _rewrite_resource(element, "background", source_url, output_file, resources)
        if (style := element.get("style")) is not None:
            rewritten = _rewrite_css(style, source_url, output_file, resources)
            if rewritten:
                element.set("style", rewritten)
            else:
                element.attrib.pop("style", None)
        if tag == "style" and element.text:
            element.text = _rewrite_css(
                _element_source_text(element.text),
                source_url,
                output_file,
                resources,
            )

    if not head.xpath(
        ".//link[contains(concat(' ', normalize-space(translate(@rel, 'ICON', 'icon')), ' '), ' icon ')][@href]"
    ):
        icon = head.makeelement("link", rel="icon", href="data:,")
        head.append(icon)

    charset = head.makeelement("meta", charset="utf-8")
    csp = head.makeelement("meta")
    csp.set("http-equiv", "Content-Security-Policy")
    csp.set("content", _CSP)
    head.insert(0, csp)
    head.insert(0, charset)
    _add_general_search(head, body_nodes[0] if body_nodes else None, output_file)
    return "<!doctype html>\n" + _serialize(document, "html")
