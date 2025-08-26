#!/usr/bin/env python3
"""
Translate and update Shopify product metafield custom.farve from English to Danish.

Features
- Dry-run by default; use --apply to write changes
- Batches updates and backs up originals to a timestamped JSON file
- Robust retries with exponential backoff, request timeouts, and rate-limit handling
- Translation handles common colors, shades, and combinations; preserves casing style
- Supports list.single_line_text_field metafields (JSON-encoded array values)

Environment variables
- SHOPIFY_DOMAIN (e.g., multimobler.myshopify.com)
- SHOPIFY_TOKEN  (Admin API token beginning with shpat_)

Usage
  Dry-run (preview only):
    python translate_farve_metafields.py --dry-run
  Apply updates:
    python translate_farve_metafields.py --apply --backup
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import argparse
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
from requests import Response
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from tqdm import tqdm
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box


console = Console()


# ---------------------------- Translation Mapping ----------------------------

def get_english_to_danish_color_map() -> Dict[str, str]:
    """Return a mapping of English color names/phrases to Danish equivalents.

    Keys should be lowercase; replacements are lowercase; casing is applied later.
    Longer phrases should appear explicitly to ensure correct mapping (e.g., "light blue").
    """
    mapping = {
        # Basics
        "black": "sort",
        "white": "hvid",
        "gray": "grå",
        "grey": "grå",
        "silver": "sølv",
        "gold": "guld",
        "bronze": "bronze",
        "copper": "kobber",
        "red": "rød",
        "blue": "blå",
        "green": "grøn",
        "yellow": "gul",
        "orange": "orange",
        "brown": "brun",
        "purple": "lilla",
        "pink": "lyserød",
        "beige": "beige",
        "ivory": "elfenben",
        "cream": "creme",
        "charcoal": "koksgrå",
        "off white": "råhvid",
        "off-white": "råhvid",
        "clear": "gennemsigtig",
        "transparent": "gennemsigtig",
        "neutral": "neutral",
        "multicolor": "flerfarvet",
        "multi color": "flerfarvet",
        "multi-color": "flerfarvet",
        "multi": "flerfarvet",
        # Blues/Greens variants
        "navy": "marineblå",
        "navy blue": "marineblå",
        "sky blue": "himmelblå",
        "royal blue": "kongeblå",
        "light blue": "lyseblå",
        "dark blue": "mørkeblå",
        "teal": "blågrøn",
        "turquoise": "turkis",
        "cyan": "cyan",
        "aqua": "akvamarin",
        "mint": "mintgrøn",
        "mint green": "mintgrøn",
        "olive": "olivengrøn",
        "forest green": "skovgrøn",
        "dark green": "mørkegrøn",
        "light green": "lysegrøn",
        # Reds/Pinks variants
        "burgundy": "bordeaux",
        "wine": "vinrød",
        "maroon": "vinrød",
        "magenta": "magenta",
        "salmon": "laks",
        "coral": "koral",
        "peach": "fersken",
        "rose": "rosenrød",
        # Purples
        "lavender": "lavendel",
        "lilac": "syrénlilla",
        # Yellows/Oranges variants
        "mustard": "sennepsgul",
        # Browns/Neutrals variants
        "tan": "lysebrun",
        "khaki": "kaki",
        "sand": "sand",
        "taupe": "taupe",
        "rust": "rust",
        "terracotta": "terrakotta",
        # Grays variants
        "light gray": "lysegrå",
        "light grey": "lysegrå",
        "dark gray": "mørkegrå",
        "dark grey": "mørkegrå",
        # Whites variants
        "offwhite": "råhvid",
        # Other
        "indigo": "indigo",
        "violet": "violet",
        # Safety / uncommon
        "none": "ingen",
        "n/a": "ingen",
    }
    # Add hyphenated versions for common 2-word keys if not present
    to_add: Dict[str, str] = {}
    for key, val in mapping.items():
        if " " in key:
            hyph = key.replace(" ", "-")
            if hyph not in mapping:
                to_add[hyph] = val
    mapping.update(to_add)
    return mapping


COLOR_MAP = get_english_to_danish_color_map()
SORTED_COLOR_KEYS = sorted(COLOR_MAP.keys(), key=len, reverse=True)

# Canonical palette (Danish, title-cased for display)
CANONICAL_COLORS = [
    "Natur",
    "Sort",
    "Hvid",
    "Grå",
    "Brun",
    "Beige",
    "Sølv",
    "Guld",
    "Kobber",
    "Blå",
    "Grøn",
    "Rød",
    "Gul",
    "Orange",
    "Lilla",
    "Lyserød",
    "Gennemsigtig",
    "Flerfarvet",
]

# Priority for selecting when multiple colors are present
SELECTION_PRIORITY = [
    "Natur",
    "Sort",
    "Hvid",
    "Grå",
    "Brun",
    "Beige",
    "Blå",
    "Grøn",
    "Rød",
    "Gul",
    "Orange",
    "Lilla",
    "Lyserød",
    "Gennemsigtig",
    "Sølv",
    "Guld",
    "Kobber",
    "Flerfarvet",
]
PRIORITY_INDEX = {name: i for i, name in enumerate(SELECTION_PRIORITY)}

# Synonyms mapping (lowercase -> canonical lowercase)
SYNONYM_TO_CANONICAL = {
    # exact canonical
    **{c.lower(): c.lower() for c in CANONICAL_COLORS},
    # grays
    "grå": "grå",
    "lysegrå": "grå",
    "mørkegrå": "grå",
    "koksgrå": "grå",
    "charcoal": "grå",
    # blues
    "blå": "blå",
    "lyseblå": "blå",
    "mørkeblå": "blå",
    "marineblå": "blå",
    "himmelblå": "blå",
    "kongeblå": "blå",
    "cyan": "blå",
    "turkis": "blå",
    "akvamarin": "blå",
    # greens
    "grøn": "grøn",
    "mintgrøn": "grøn",
    "olivengrøn": "grøn",
    "skovgrøn": "grøn",
    "mørkegrøn": "grøn",
    "lysegrøn": "grøn",
    "blågrøn": "grøn",
    # reds / pinks
    "rød": "rød",
    "bordeaux": "rød",
    "vinrød": "rød",
    "maroon": "rød",
    "lyserød": "lyserød",
    "pink": "lyserød",
    "magenta": "lyserød",
    "rosa": "lyserød",
    "støvet rosa": "lyserød",
    "rosenrød": "lyserød",
    # oranges / yellows
    "orange": "orange",
    "koral": "orange",
    "coral": "orange",
    "laks": "orange",
    "salmon": "orange",
    "fersken": "orange",
    "peach": "orange",
    "gul": "gul",
    "sennepsgul": "gul",
    "mustard": "gul",
    # browns / neutrals
    "brun": "brun",
    "rust": "brun",
    "terrakotta": "brun",
    "taupe": "beige",
    "sand": "beige",
    "kaki": "beige",
    "khaki": "beige",
    "tan": "beige",
    "beige": "beige",
    "ivory": "hvid",
    "elfenben": "hvid",
    "creme": "hvid",
    "råhvid": "hvid",
    # metals / transparent
    "sølv": "sølv",
    "guld": "guld",
    "kobber": "kobber",
    "gennemsigtig": "gennemsigtig",
    "transparent": "gennemsigtig",
    "klar": "gennemsigtig",
    # specials
    "natur": "natur",
    "flerfarvet": "flerfarvet",
    "multi": "flerfarvet",
    "multi-color": "flerfarvet",
    "multicolor": "flerfarvet",
    "neutral": "beige",
}

TOKEN_SPLIT_REGEX = re.compile(r"\s*(?:/|,|\+|&|\||\\| og |;|\sand\s|\s-\s)\s*", re.IGNORECASE)
SYNONYM_KEYS_SORTED = sorted(SYNONYM_TO_CANONICAL.keys(), key=len, reverse=True)
TITLE_COLOR_REGEX = re.compile(
    r"(?<![\w\u00C0-\u024F])(" + "|".join(re.escape(k) for k in SYNONYM_KEYS_SORTED) + r")(?<!s)(?![\w\u00C0-\u024F])",
    re.IGNORECASE,
)


def detect_case_style(value: str) -> str:
    """Detect a simple casing style: 'upper', 'title', or 'lower'."""
    letters = [c for c in value if c.isalpha()]
    if letters and all(c.isupper() for c in letters):
        return "upper"
    # naive title check: all words start uppercase (ignoring delimiters)
    words = re.split(r"[\s\-/+,&]+", value.strip())
    if words and all((w[:1].isupper() and w[1:] == w[1:].lower()) or not w for w in words):
        return "title"
    return "lower"


def apply_case_style(style: str, value: str) -> str:
    if style == "upper":
        return value.upper()
    if style == "title":
        # Safely title-case tokens while preserving delimiters (avoid variable-width lookbehinds)
        parts = re.split(r"([\s\-/+,&]+)", value)
        for i in range(0, len(parts)):
            # Even indices are tokens; odd indices are delimiters from split
            if i % 2 == 0 and parts[i]:
                parts[i] = parts[i].capitalize()
        return "".join(parts)
    return value


def translate_color_text(text: str) -> Tuple[str, List[Tuple[str, str]]]:
    """Translate English color words/phrases in a text to Danish based on mapping.

    Returns (translated_text, replacements) where replacements is a list of (from, to).
    Casing style is preserved heuristically.
    """
    if not text:
        return text, []

    original_style = detect_case_style(text)
    lowered = text.lower()

    replacements: List[Tuple[str, str]] = []
    # Replace longest keys first to avoid partial overlaps
    for key in SORTED_COLOR_KEYS:
        if key in lowered:
            # We do a regex replace that's case-insensitive but only when key is a full token or phrase boundary
            # Allow matches across hyphens/spaces exactly as in key (we already include hyphenated variants)
            pattern = re.compile(rf"(?<!\w){re.escape(key)}(?!\w)", flags=re.IGNORECASE)

            def _sub(match: re.Match[str]) -> str:
                src = match.group(0)
                danish = COLOR_MAP[key]
                # We apply lower first; casing added later globally
                replacements.append((src, danish))
                return danish

            lowered = pattern.sub(_sub, lowered)

    translated = lowered
    translated = apply_case_style(original_style, translated)
    return translated, replacements


def canonicalize_token(token: str) -> Optional[str]:
    """Map a token to a canonical color name (Title-case) if recognized."""
    key = token.strip().lower()
    if not key:
        return None
    canonical_l = SYNONYM_TO_CANONICAL.get(key)
    if not canonical_l:
        return None
    return canonical_l.capitalize() if canonical_l != "gennemsigtig" else "Gennemsigtig"


def normalize_color_to_single(value: str) -> Tuple[str, List[str]]:
    """Collapse a color string to a single canonical color.

    - Translate English to Danish first
    - Split by common delimiters (/, +, &, commas, 'og')
    - Map synonyms/shades to canonical palette
    - Resolve multiples using priority order

    Returns (final_color, matched_canonicals)
    """
    translated, _ = translate_color_text(value)
    parts = [p for p in TOKEN_SPLIT_REGEX.split(translated) if p and p.strip()]
    matched: List[str] = []
    for part in parts if parts else [translated]:
        c = canonicalize_token(part)
        if c and c not in matched:
            matched.append(c)
    if not matched:
        return "Flerfarvet", []
    # Select based on priority
    chosen = sorted(matched, key=lambda x: PRIORITY_INDEX.get(x, 9999))[0]
    return chosen, matched


def infer_color_from_title(title: str) -> Optional[str]:
    if not title:
        return None
    found: List[str] = []
    for m in TITLE_COLOR_REGEX.finditer(title):
        token = m.group(1).lower()
        canon_l = SYNONYM_TO_CANONICAL.get(token)
        if not canon_l:
            continue
        candidate = canon_l.capitalize() if canon_l != "gennemsigtig" else "Gennemsigtig"
        if candidate not in found:
            found.append(candidate)
    if not found:
        return None
    return sorted(found, key=lambda x: PRIORITY_INDEX.get(x, 9999))[0]


def infer_color_from_product(p: "ProductMeta", fallback: str) -> Optional[str]:
    # 1) Prefer product title
    title_based = infer_color_from_title(p.title)
    if title_based:
        return title_based
    # 2) Variant selected options (prefer option names that look like color)
    candidates: List[str] = []
    colorish_names = {"farve", "color", "colour", "färg", "couleur"}
    for opts in p.variant_selected_options:
        for name, value in opts:
            if not value:
                continue
            name_l = (name or "").strip().lower()
            value_norm, _ = normalize_color_to_single(value)
            # Prefer if the option name suggests color
            if name_l in colorish_names and value_norm:
                candidates.append(value_norm)
            elif value_norm and value_norm != "Flerfarvet":
                candidates.append(value_norm)
    if candidates:
        # Choose by priority
        uniq = []
        for c in candidates:
            if c not in uniq:
                uniq.append(c)
        chosen = sorted(uniq, key=lambda x: PRIORITY_INDEX.get(x, 9999))[0]
        return chosen
    # 3) Variant titles
    vt_found: List[str] = []
    for vt in p.variant_titles:
        cand = infer_color_from_title(vt)
        if cand and cand not in vt_found:
            vt_found.append(cand)
    if vt_found:
        chosen = sorted(vt_found, key=lambda x: PRIORITY_INDEX.get(x, 9999))[0]
        return chosen
    return fallback or None


def is_json_array_string(s: str) -> bool:
    s = (s or "").strip()
    return s.startswith("[") and s.endswith("]")


def translate_value_by_type(value: str, mtype: str, inferred_color: Optional[str], fallback: str) -> Tuple[str, List[Tuple[str, str]]]:
    """Translate + normalize metafield value considering type.

    - For single_line_text_field: produce a single canonical color
    - For list.single_line_text_field: collapse to a single canonical color inside a 1-item array
    """
    if mtype == "single_line_text_field":
        final, _matches = normalize_color_to_single(value)
        if final == "Flerfarvet":
            final = inferred_color or fallback
        return final, [(value, final)] if value != final else []
    if mtype == "list.single_line_text_field":
        try:
            items = json.loads(value) if is_json_array_string(value) else []
        except json.JSONDecodeError:
            final, _matches = normalize_color_to_single(value)
            if final == "Flerfarvet":
                final = inferred_color or fallback
            return json.dumps([final], ensure_ascii=False), [(value, final)] if value != final else []
        chosen: Optional[str] = None
        for item in items:
            if isinstance(item, str):
                cand, _ = normalize_color_to_single(item)
                if cand:
                    chosen = cand
                    break
        if not chosen:
            chosen = inferred_color or fallback
        new_val = json.dumps([chosen], ensure_ascii=False)
        return new_val, [(value, new_val)] if value != new_val else []
    # Unknown type: keep as single canonical if possible
    final, _matches = normalize_color_to_single(value)
    if final == "Flerfarvet":
        final = inferred_color or fallback
    return final, [(value, final)] if value != final else []


# ---------------------------- Shopify GraphQL API ----------------------------

class ShopifyError(Exception):
    pass


def get_env(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise ShopifyError(f"Missing required environment variable: {name}")
    return v


def build_graphql_session() -> Tuple[str, Dict[str, str]]:
    domain = get_env("SHOPIFY_DOMAIN")
    token = get_env("SHOPIFY_TOKEN")
    url = f"https://{domain}/admin/api/2024-07/graphql.json"
    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    return url, headers


@retry(
    reraise=True,
    stop=stop_after_attempt(6),
    wait=wait_exponential(multiplier=1, min=1, max=20),
    retry=retry_if_exception_type((requests.RequestException, ShopifyError)),
)
def graphql_request(url: str, headers: Dict[str, str], query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
    resp: Response = requests.post(
        url,
        headers=headers,
        data=json.dumps({"query": query, "variables": variables}),
        timeout=30,
    )
    if resp.status_code == 429:
        # Rate-limited; raise to trigger retry with backoff
        raise ShopifyError("Rate limited (429)")
    if not resp.ok:
        raise ShopifyError(f"GraphQL HTTP error {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    if "errors" in data and data["errors"]:
        # Sometimes throttling or other errors appear here
        messages = "; ".join(err.get("message", "") for err in data["errors"])[:500]
        raise ShopifyError(f"GraphQL errors: {messages}")
    return data


PRODUCTS_QUERY = """
query ProductsWithFarve($cursor: String) {
  products(first: 250, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    edges {
      node {
        id
        title
        handle
        metafield(namespace: "custom", key: "farve") {
          id
          namespace
          key
          type
          value
        }
        options { name }
        variants(first: 100) {
          edges {
            node {
              id
              title
              selectedOptions { name value }
            }
          }
        }
      }
    }
  }
}
"""


METAFIELDS_SET_MUTATION = """
mutation SetFarve($metafields: [MetafieldsSetInput!]!) {
  metafieldsSet(metafields: $metafields) {
    metafields { id key namespace type value }
    userErrors { field message code }
  }
}
"""


@dataclass
class ProductMeta:
    id: str
    title: str
    handle: str
    metafield_id: Optional[str]
    metafield_type: Optional[str]
    metafield_value: Optional[str]
    option_names: List[str]
    variant_titles: List[str]
    variant_selected_options: List[List[Tuple[str, str]]]


def fetch_all_products_with_farve(url: str, headers: Dict[str, str]) -> List[ProductMeta]:
    products: List[ProductMeta] = []
    cursor: Optional[str] = None
    while True:
        data = graphql_request(url, headers, PRODUCTS_QUERY, {"cursor": cursor})
        prod = data["data"]["products"]
        for edge in prod["edges"]:
            node = edge["node"]
            mf = node.get("metafield")
            option_names = [o.get("name", "") for o in (node.get("options") or [])]
            variant_titles: List[str] = []
            variant_selected_options: List[List[Tuple[str, str]]] = []
            vconn = node.get("variants") or {}
            for vedge in (vconn.get("edges") or []):
                vnode = vedge.get("node") or {}
                vt = vnode.get("title") or ""
                if vt:
                    variant_titles.append(vt)
                sel = []
                for so in (vnode.get("selectedOptions") or []):
                    n = so.get("name") or ""
                    v = so.get("value") or ""
                    if n or v:
                        sel.append((n, v))
                variant_selected_options.append(sel)
            products.append(
                ProductMeta(
                    id=node["id"],
                    title=node.get("title") or "",
                    handle=node.get("handle") or "",
                    metafield_id=mf.get("id") if mf else None,
                    metafield_type=mf.get("type") if mf else None,
                    metafield_value=mf.get("value") if mf else None,
                    option_names=option_names,
                    variant_titles=variant_titles,
                    variant_selected_options=variant_selected_options,
                )
            )
        if not prod["pageInfo"]["hasNextPage"]:
            break
        cursor = prod["pageInfo"]["endCursor"]
    return products


def chunked(iterable: Iterable[Any], size: int) -> Iterable[List[Any]]:
    chunk: List[Any] = []
    for item in iterable:
        chunk.append(item)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def make_backup_entry(p: ProductMeta, old_val: str, new_val: str) -> Dict[str, Any]:
    return {
        "productId": p.id,
        "title": p.title,
        "handle": p.handle,
        "type": p.metafield_type,
        "oldValue": old_val,
        "newValue": new_val,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Translate product metafield custom.farve to Danish")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Preview changes without writing (default)")
    mode.add_argument("--apply", action="store_true", help="Apply updates to Shopify")
    parser.add_argument("--backup", action="store_true", help="When applying, write backups to backups/farve_*.json")
    parser.add_argument("--batch", type=int, default=20, help="Batch size for updates (default 20)")
    parser.add_argument("--fallback", type=str, default="Natur", help="Fallback color for missing/unrecognized values (default Natur)")
    parser.add_argument("--debug", action="store_true", help="Enable verbose debug output")
    parser.add_argument("--save-report", action="store_true", help="Save dry-run report to logs/dry_run_*.json")
    args = parser.parse_args()

    url, headers = build_graphql_session()

    console.print(Panel.fit("[bold]Scanning products for custom.farve[/bold]", box=box.ROUNDED))
    products = fetch_all_products_with_farve(url, headers)

    to_update: List[Tuple[ProductMeta, str, str, List[Tuple[str, str]]]] = []
    created_missing: int = 0

    for p in tqdm(products, desc="Analyzing", unit="product"):
        inferred = infer_color_from_product(p, args.fallback)
        if not p.metafield_id or p.metafield_value is None or not p.metafield_type:
            # Create new metafield, infer from context first then fallback
            final = inferred or args.fallback
            to_update.append(
                (
                    ProductMeta(
                        id=p.id,
                        title=p.title,
                        handle=p.handle,
                        metafield_id=None,
                        metafield_type="single_line_text_field",
                        metafield_value=None,
                    ),
                    "<missing>",
                    final,
                    [("<missing>", final)],
                )
            )
            created_missing += 1
        else:
            new_val, repls = translate_value_by_type(p.metafield_value, p.metafield_type, inferred, args.fallback)
            if new_val != p.metafield_value:
                to_update.append((p, p.metafield_value, new_val, repls))

    # Summary table
    table = Table(title="custom.farve translation summary", box=box.SIMPLE_HEAVY)
    table.add_column("Metric", style="bold")
    table.add_column("Count", justify="right")
    table.add_row("Products scanned", str(len(products)))
    table.add_row("Metafields to create", str(created_missing))
    table.add_row("Updates needed", str(len(to_update)))
    console.print(table)

    if args.save_report or args.dry_run or not args.apply:
        os.makedirs("logs", exist_ok=True)
        report_path = os.path.join(
            "logs", f"dry_run_farve_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        )
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(
                [
                    {
                        "productId": p.id,
                        "handle": p.handle,
                        "title": p.title,
                        "type": p.metafield_type,
                        "oldValue": old,
                        "newValue": new,
                        "replacements": repls,
                    }
                    for (p, old, new, repls) in to_update
                ],
                f,
                ensure_ascii=False,
                indent=2,
            )
        console.print(f"Saved dry-run report to [bold]{report_path}[/bold]")

    if not args.apply:
        console.print("[green]Dry-run complete. No changes were applied.[/green]")
        return

    if not to_update:
        console.print("[green]Nothing to update. Exiting.[/green]")
        return

    # Prepare backup if requested
    backup_entries: List[Dict[str, Any]] = []
    if args.backup:
        os.makedirs("backups", exist_ok=True)

    console.print(Panel.fit("[bold yellow]Applying updates…[/bold yellow]", box=box.ROUNDED))

    # Batch and send updates
    updated = 0
    for batch in tqdm(list(chunked(to_update, args.batch)), desc="Updating", unit="batch"):
        metafields_payload: List[Dict[str, Any]] = []
        for p, old, new, _repls in batch:
            metafields_payload.append(
                {
                    "ownerId": p.id,
                    "namespace": "custom",
                    "key": "farve",
                    "type": p.metafield_type or "single_line_text_field",
                    "value": new,
                    # If metafield doesn't exist, Shopify will create; we aim to update existing ones
                }
            )
            if args.backup:
                backup_entries.append(make_backup_entry(p, old, new))

        data = graphql_request(url, headers, METAFIELDS_SET_MUTATION, {"metafields": metafields_payload})
        errors = data["data"]["metafieldsSet"]["userErrors"]
        if errors:
            # Log and continue
            msgs = "; ".join(e.get("message", "") for e in errors)
            console.print(f"[red]UserErrors in batch:[/red] {msgs}")
        updated += len(metafields_payload)
        # Gentle sleep to avoid throttling bursts
        time.sleep(0.2)

    if args.backup and backup_entries:
        path = os.path.join("backups", f"farve_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(backup_entries, f, ensure_ascii=False, indent=2)
        console.print(f"Wrote backup to [bold]{path}[/bold]")

    console.print(f"[green]Applied updates to {updated} metafields.[/green]")


if __name__ == "__main__":
    try:
        main()
    except ShopifyError as e:
        console.print(f"[red]Shopify error:[/red] {e}")
        sys.exit(2)
    except requests.RequestException as e:
        console.print(f"[red]Network error:[/red] {e}")
        sys.exit(3)
    except Exception as e:
        console.print(f"[red]Unexpected error:[/red] {e}")
        sys.exit(1)


