from copy import deepcopy
import re
from urllib.parse import urlsplit

from browsergym.utils.obs import flatten_axtree_to_str, flatten_dom_to_str, prune_html


_SOURCE_MARKER = re.compile(r"^\s*\[SOURCE:[a-z_]+\]")
_INHERITED_SOURCE_TAGS = {
    "third_party_content",
    "user_generated_content",
    "embedded_or_non_visible_content",
}
_NAVIGATION_TAGS = {"nav", "navigation", "header", "footer", "sidebar", "breadcrumb", "login"}
_MAIN_TAGS = {"main", "article", "content", "post", "product", "description", "title", "details"}
_FORM_TAGS = {"form", "input", "button", "submit", "select", "textarea", "email", "password", "checkout"}
_AD_MARKERS = {"ad", "ads", "advert", "advertisement", "advertising", "sponsored", "promo", "promotion"}
_USER_CONTENT_MARKERS = {"comment", "comments", "review", "reviews", "ugc", "usergenerated", "testimonial"}
_NAVIGATION_MARKERS = {"nav", "navigation", "menu", "header", "footer", "sidebar", "breadcrumb", "login", "search"}
_MAIN_MARKERS = {"main", "article", "content", "post", "product", "description", "title", "details"}


def _string_at(strings: list[str], index: int) -> str | None:
    if not isinstance(index, int) or index < 0 or index >= len(strings):
        return None
    return strings[index]


def _node_attributes(nodes: dict, node_index: int, strings: list[str]) -> dict[str, str | None]:
    attribute_indexes = nodes.get("attributes", [])[node_index]
    attributes = {}
    for index in range(0, len(attribute_indexes) - 1, 2):
        name = _string_at(strings, attribute_indexes[index])
        if name:
            attributes[name.lower()] = _string_at(strings, attribute_indexes[index + 1])
    return attributes


def _attribute_words(attributes: dict[str, str | None]) -> set[str]:
    values = [attributes.get(name) or "" for name in ("id", "class", "role", "aria-label", "data-testid")]
    return {word for value in values for word in re.findall(r"[a-z0-9]+", value.lower())}


def _is_external_url(url: str | None, page_url: str | None) -> bool:
    if not url:
        return False
    embedded_host = urlsplit(url).hostname
    if not embedded_host:
        return False
    page_host = urlsplit(page_url or "").hostname
    return page_host is None or embedded_host.lower() != page_host.lower()


def _element_source_tag(
    tag_name: str,
    attributes: dict[str, str | None],
    inherited_tag: str,
    page_url: str | None,
) -> str:
    lowered_attributes = {name: (value or "").lower() for name, value in attributes.items()}
    words = _attribute_words(attributes)

    if (
        "hidden" in attributes
        or lowered_attributes.get("aria-hidden") == "true"
        or re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", lowered_attributes.get("style", ""))
    ):
        return "embedded_or_non_visible_content"
    if inherited_tag in _INHERITED_SOURCE_TAGS:
        return inherited_tag
    if words & _AD_MARKERS:
        return "third_party_content"
    if words & _USER_CONTENT_MARKERS:
        return "user_generated_content"
    if tag_name in {"script", "style", "noscript"}:
        return "embedded_or_non_visible_content"
    if tag_name == "iframe":
        if _is_external_url(attributes.get("src"), page_url):
            return "third_party_content"
        return "embedded_or_non_visible_content"
    if tag_name in {"form", "input", "button", "select", "textarea", "option", "label"} or words & _FORM_TAGS:
        return "site_form_content"
    if tag_name in {"nav", "header", "footer"} or words & _NAVIGATION_MARKERS:
        return "site_navigation"
    if tag_name in {"main", "article"} or words & _MAIN_MARKERS:
        return "site_main_content"
    if inherited_tag != "site_content":
        return inherited_tag
    return "site_content"


def tag_dom_snapshot(dom_snapshot: dict, page_url: str | None = None) -> tuple[dict, dict[str, str]]:
    """Copy a BrowserGym DOM snapshot and label its text nodes before flattening."""
    tagged_snapshot = deepcopy(dom_snapshot)
    strings = tagged_snapshot["strings"]
    documents = tagged_snapshot["documents"]
    document_tags = {0: "site_content"}
    bid_source_tags = {}

    for document_index, document in enumerate(documents):
        nodes = document["nodes"]
        frame_indexes = nodes.get("contentDocumentIndex", {}).get("index", [])
        child_document_indexes = nodes.get("contentDocumentIndex", {}).get("value", [])
        for node_index, child_document_index in zip(frame_indexes, child_document_indexes):
            attributes = _node_attributes(nodes, node_index, strings)
            if _is_external_url(attributes.get("src"), page_url):
                document_tags[child_document_index] = "third_party_content"
            else:
                document_tags[child_document_index] = "embedded_or_non_visible_content"

    processed_documents = set()

    def process_document(document_index: int, inherited_tag: str) -> None:
        if document_index in processed_documents or document_index >= len(documents):
            return
        processed_documents.add(document_index)
        nodes = documents[document_index]["nodes"]
        node_names = nodes["nodeName"]
        children = [[] for _ in node_names]
        for node_index, parent_index in enumerate(nodes["parentIndex"]):
            if 0 <= parent_index < len(children):
                children[parent_index].append(node_index)

        def visit(node_index: int, parent_tag: str) -> None:
            node_type = nodes["nodeType"][node_index]
            if node_type == 3 or node_type == 4:
                value_index = nodes["nodeValue"][node_index]
                value = _string_at(strings, value_index)
                if value and value.strip() and not _SOURCE_MARKER.match(value):
                    strings.append(f"[SOURCE:{parent_tag}] {value}")
                    nodes["nodeValue"][node_index] = len(strings) - 1
                return

            tag_name = (_string_at(strings, node_names[node_index]) or "").lower()
            attributes = _node_attributes(nodes, node_index, strings)
            source_tag = _element_source_tag(tag_name, attributes, parent_tag, page_url)
            bid = attributes.get("bid")
            if bid:
                bid_source_tags[bid] = source_tag

            for child_index in children[node_index]:
                visit(child_index, source_tag)

            content_documents = nodes.get("contentDocumentIndex", {})
            if node_index in content_documents.get("index", []):
                position = content_documents["index"].index(node_index)
                child_document_index = content_documents["value"][position]
                process_document(child_document_index, document_tags.get(child_document_index, source_tag))

        for node_index, parent_index in enumerate(nodes["parentIndex"]):
            if parent_index == -1:
                visit(node_index, inherited_tag)

    process_document(0, document_tags[0])
    for document_index, source_tag in document_tags.items():
        process_document(document_index, source_tag)
    return tagged_snapshot, bid_source_tags


def tag_axtree_snapshot(axtree_object: dict, bid_source_tags: dict[str, str]) -> dict:
    """Copy an accessibility tree and attach DOM-derived tags to matching nodes."""
    tagged_axtree = deepcopy(axtree_object)
    for node in tagged_axtree["nodes"]:
        source_tag = bid_source_tags.get(str(node.get("browsergym_id", "")))
        if source_tag is None:
            continue
        properties = node.setdefault("properties", [])
        properties[:] = [prop for prop in properties if prop.get("name") != "browsergym_source"]
        properties.append(
            {"name": "browsergym_source", "value": {"value": f"[SOURCE:{source_tag}]"}}
        )
    return tagged_axtree


def _text_source_tag(line: str, default_tag: str) -> str:
    words = set(re.findall(r"[a-z0-9]+", line.lower()))
    if words & (_AD_MARKERS | {"cookie", "banner", "iframe", "widget"}):
        return "third_party_content"
    if words & _NAVIGATION_TAGS:
        return "site_navigation"
    if words & _MAIN_TAGS:
        return "site_main_content"
    if words & _FORM_TAGS:
        return "site_form_content"
    if words & {"script", "style", "hidden", "noscript"}:
        return "embedded_or_non_visible_content"
    return default_tag


def annotate_provenance_text(text: str | None, default_tag: str = "site_content") -> str:
    """Add best-effort source labels to flattened accessibility-tree text."""
    if not text:
        return ""
    tagged_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            tagged_lines.append("")
        elif _SOURCE_MARKER.search(stripped):
            tagged_lines.append(stripped)
        else:
            tagged_lines.append(f"[SOURCE:{_text_source_tag(stripped, default_tag)}] {stripped}")
    return "\n".join(tagged_lines)


def preprocess_page_content(
    axtree_object: dict,
    dom_object: dict,
    page_url: str | None = None,
    tag_sources: bool = False,
) -> tuple[str, str]:
    """Prepare accessibility and DOM text, optionally tagging DOM nodes before flattening."""
    if tag_sources:
        dom_object, bid_source_tags = tag_dom_snapshot(dom_object, page_url)
        axtree_object = tag_axtree_snapshot(axtree_object, bid_source_tags)
    axtree_text = annotate_provenance_text(flatten_axtree_to_str(axtree_object)) if tag_sources else flatten_axtree_to_str(axtree_object)
    dom_text = prune_html(flatten_dom_to_str(dom_object))
    return axtree_text, dom_text