"""Screen variant extraction, specification parsing, ranking, and interactive disambiguation."""

from dataclasses import dataclass
import re
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass
class ScreenVariant:
    """Represents a product, flight, room, or item variant discovered on screen."""
    title: str
    spec: str
    price: str
    price_val: float
    is_sponsored: bool
    is_accessory: bool
    relevance_score: float
    raw_text: str = ""
    coords: Optional[Tuple[int, int]] = None
    element: Any = None


def extract_variant_spec(text: str) -> str:
    """Extracts specification attributes (storage, sizing, color, class, units) universally."""
    specs: List[str] = []
    low = text.lower()

    # 1. Digital storage / capacity: e.g. 256 GB, 512 GB, 1 TB, 2 TB, 16 GB RAM
    storage_matches = re.findall(r"\b(\d+\s*(?:gb|tb|mb))\b", low)
    if storage_matches:
        # Keep unique, preserve first
        seen_st = set()
        for sm in storage_matches:
            clean_sm = sm.upper().replace(" ", "")
            # Canonicalize representation (e.g. "256GB")
            if clean_sm not in seen_st:
                seen_st.add(clean_sm)
                specs.append(clean_sm)

    # 2. Universal item sizing & dimensions: e.g. Size 9, UK 8, US 10, XL, XXL, M
    num_size_match = re.search(r"\b(?:size|uk|us|eu)\s*[:\-]?\s*([0-9]{1,2}(?:\.[0-9])?)\b", low)
    if num_size_match:
        specs.append(f"Size {num_size_match.group(1)}")
    else:
        alpha_size_match = re.search(r"\b(?:size\s*[:\-]?\s*)?(xxs|xs|s|m|l|xl|xxl|xxxl)\b", low)
        if alpha_size_match and (alpha_size_match.group(1).upper() not in ("M", "S") or "size" in low):
            specs.append(alpha_size_match.group(1).upper())

    # 3. Universal finish & colors
    colors = (
        "natural titanium", "desert titanium", "black titanium", "white titanium",
        "space black", "space grey", "space gray", "midnight", "starlight",
        "titanium", "silver", "gold", "graphite", "black", "white", "blue", "green", "red"
    )
    for c in colors:
        if c in low:
            specs.append(c.title())
            break

    # 4. Travel & Booking flight / seating class
    travel_classes = ("first class", "business class", "premium economy", "economy", "non-stop")
    for tc in travel_classes:
        if tc in low:
            specs.append(tc.title())
            break

    # 5. Pack quantity & physical volume: e.g. Pack of 2, 500g, 1L
    pack_match = re.search(r"\b(pack of \d+|\d+\s*pack|\d+\s*pcs?|\d+\s*kg|\d+\s*g|\d+\s*ml|\d+\s*l)\b", low)
    if pack_match:
        specs.append(pack_match.group(1).title())

    if specs:
        return ", ".join(specs)
    return "Standard Base"


def is_accessory_text(text: str, query: str = "") -> bool:
    """Detects whether an item is an accessory (case, cover, glass, cable) rather than core item."""
    accessory_keywords = {
        "case", "cover", "protector", "guard", "glass", "tempered",
        "charger", "cable", "adapter", "strap", "pouch", "sleeve",
        "skin", "stand", "holder", "bumper", "cleaning kit", "lens guard"
    }
    low_text = text.lower()
    low_query = query.lower()

    # If the user specifically searched for an accessory, do not penalize it
    if any(k in low_query for k in accessory_keywords):
        return False

    # Check if title indicates accessory
    return any(re.search(rf"\b{k}\b", low_text) for k in accessory_keywords)


def is_sponsored_text(text: str) -> bool:
    """Checks whether an item is marked as sponsored, ad, or promoted."""
    low = text.lower()
    return bool(
        re.search(r"\b(?:sponsored|promoted|advertisement)\b", low)
        or "sponsored product" in low
        or "ad\b" in low
    )


def extract_price_info(text: str) -> Tuple[Optional[str], float]:
    """Universal currency and numeric price extractor."""
    m = re.search(r"(?:[₹$€£¥]|Rs\.?)\s*([\d,]+(?:\.\d+)?)", text)
    if not m:
        return None, 0.0
    full_str = m.group(0).strip()
    digits = m.group(1).replace(",", "")
    try:
        val = float(digits)
    except ValueError:
        val = 0.0
    return full_str, val


def extract_screen_variants(
    page: Any,
    query_str: str = "",
    target_variant: Optional[str] = None,
) -> List[ScreenVariant]:
    """
    Extracts all candidate items/variants from current page listings.
    Works dynamically and universally across any e-commerce, travel, or catalog site.
    """
    variants: List[ScreenVariant] = []
    if not page or page.is_closed():
        return variants

    query_tokens = [w for w in re.findall(r"\w+", query_str.lower()) if len(w) > 2 and w not in ("price", "cost", "cheaper")]

    card_selectors = [
        "[data-component-type*='search-result']",
        "article",
        "div[class*='product' i]",
        "div[class*='item' i]",
        "div[data-id]",
        "div[class*='card' i]",
        "div[class*='flight' i]",
        "div[class*='ticket' i]",
        "li:has([class*='price' i])",
    ]

    seen_cards = set()
    for sel in card_selectors:
        try:
            cards = page.locator(sel).all()
            for card in cards[:18]:
                try:
                    txt = card.inner_text().strip()
                    if not txt or len(txt) < 10 or txt in seen_cards:
                        continue
                    seen_cards.add(txt)

                    price_str, price_val = extract_price_info(txt)
                    if not price_str or price_val <= 0:
                        continue

                    # Extract title line
                    first_line = [l.strip() for l in txt.split("\n") if len(l.strip()) > 5]
                    title = first_line[0] if first_line else txt[:60]
                    if len(title) > 90:
                        title = title[:87] + "..."

                    # Check token match against search query using whole word boundaries
                    low_txt = txt.lower()
                    overlap = sum(1 for t in query_tokens if re.search(rf"\b{re.escape(t)}\b", low_txt))
                    if query_tokens and overlap == 0:
                        continue

                    spec = extract_variant_spec(txt)
                    sponsored = is_sponsored_text(txt)
                    accessory = is_accessory_text(txt, query_str)

                    # Compute comprehensive relevance score
                    score = overlap * 25.0
                    if price_val > 0:
                        score += 20.0
                    if not sponsored:
                        score += 50.0  # Prefer organic results
                    else:
                        score -= 20.0

                    if accessory:
                        score -= 120.0  # Heavy penalty for accessories when searching products

                    # Target variant boost
                    if target_variant:
                        clean_target = target_variant.lower().replace(" ", "")
                        clean_spec = spec.lower().replace(" ", "")
                        clean_txt = low_txt.replace(" ", "")
                        if clean_target in clean_spec or clean_target in clean_txt:
                            score += 200.0  # Massive boost for matching target variant
                        else:
                            score -= 40.0

                    # Obtain element bounding box if available
                    box = None
                    try:
                        bb = card.bounding_box()
                        if bb:
                            box = (int(bb["x"] + bb["width"] / 2), int(bb["y"] + bb["height"] / 2))
                    except Exception:
                        pass

                    variants.append(ScreenVariant(
                        title=title,
                        spec=spec,
                        price=price_str,
                        price_val=price_val,
                        is_sponsored=sponsored,
                        is_accessory=accessory,
                        relevance_score=score,
                        raw_text=txt,
                        coords=box,
                        element=card,
                    ))
                except Exception:
                    continue
        except Exception:
            continue

    # Sort descending by relevance score
    variants.sort(key=lambda v: v.relevance_score, reverse=True)
    return variants


def resolve_screen_variant(
    variants: List[ScreenVariant],
    query_str: str = "",
    target_variant: Optional[str] = None,
    disambiguation_callback: Optional[Callable[[str, List[Dict[str, Any]]], Optional[str]]] = None,
) -> Tuple[Optional[ScreenVariant], Optional[str]]:
    """
    Resolves the best variant candidate from screen items.
    If target_variant is provided, prioritizes matching it for cross-site consistency.
    If multiple distinct variants exist and a disambiguation_callback is provided, prompts the user.
    Otherwise anchors to the highest-scoring organic base variant.
    """
    if not variants:
        return None, None

    core_variants = [v for v in variants if not v.is_accessory]
    if not core_variants:
        # If user searched for a core product, but only accessories were returned, prompt or return None
        if not is_accessory_text(query_str):
            if disambiguation_callback and callable(disambiguation_callback) and variants:
                options_payload = [
                    {
                        "index": i + 1,
                        "title": opt.title,
                        "spec": opt.spec,
                        "price": opt.price,
                        "price_val": opt.price_val,
                    }
                    for i, opt in enumerate(variants[:5])
                ]
                prompt_msg = f"No core product listings found for '{query_str}' (only accessories/related items found):"
                try:
                    user_choice = disambiguation_callback(prompt_msg, options_payload)
                    if user_choice:
                        user_choice_str = str(user_choice).strip()
                        if user_choice_str.isdigit():
                            chosen_idx = int(user_choice_str) - 1
                            if 0 <= chosen_idx < len(variants):
                                return variants[chosen_idx], variants[chosen_idx].spec
                        for opt in variants:
                            if user_choice_str.lower() in opt.spec.lower() or user_choice_str.lower() in opt.title.lower():
                                return opt, opt.spec
                except Exception:
                    pass
            return None, None

    active_variants = sorted(core_variants, key=lambda v: v.relevance_score, reverse=True)

    # 1. Target variant already anchored from an earlier step or initial goal
    if target_variant:
        clean_target = target_variant.lower().replace(" ", "")
        # A. Exact full spec match (e.g. "256gb,deserttitanium")
        for v in active_variants:
            if clean_target in v.spec.lower().replace(" ", "") or clean_target in v.title.lower().replace(" ", ""):
                return v, v.spec

        # B. Primary capacity / size / class attribute match (e.g. "256gb" or "size 9")
        target_tokens = [tok.strip().lower() for tok in re.split(r"[,;]+", target_variant) if tok.strip()]
        for tok in target_tokens:
            clean_tok = tok.replace(" ", "")
            # Storage / capacity match e.g. "256gb", "1tb"
            if re.search(r"\d+(?:gb|tb|mb)", clean_tok):
                for v in active_variants:
                    if clean_tok in v.spec.lower().replace(" ", "") or clean_tok in v.title.lower().replace(" ", ""):
                        return v, v.spec
            # Sizing / apparel match e.g. "size9", "xl"
            if re.search(r"(?:size\d+|xxs|xs|s|m|l|xl|xxl|xxxl)", clean_tok):
                for v in active_variants:
                    if clean_tok in v.spec.lower().replace(" ", "") or clean_tok in v.title.lower().replace(" ", ""):
                        return v, v.spec

    # 2. Extract variant from query_str if user explicitly stated it (e.g. "iphone 16 pro max 256gb")
    query_spec = extract_variant_spec(query_str)
    if query_spec != "Standard Base":
        clean_qs = query_spec.lower().replace(" ", "")
        for v in active_variants:
            if clean_qs in v.spec.lower().replace(" ", "") or clean_qs in v.title.lower().replace(" ", ""):
                return v, v.spec

    # 3. Deduplicate variants by spec to offer clean distinct options
    distinct_options: List[ScreenVariant] = []
    seen_specs = set()
    for v in active_variants:
        spec_key = (v.spec, v.price_val)
        if spec_key not in seen_specs:
            seen_specs.add(spec_key)
            distinct_options.append(v)

    # 4. Interactive disambiguation if multiple distinct variants exist and callback is registered
    if len(distinct_options) > 1 and disambiguation_callback and callable(disambiguation_callback):
        options_payload = [
            {
                "index": i + 1,
                "title": opt.title,
                "spec": opt.spec,
                "price": opt.price,
                "price_val": opt.price_val,
            }
            for i, opt in enumerate(distinct_options[:5])
        ]
        prompt_msg = f"Multiple variants found for '{query_str or 'product'}':"
        try:
            user_choice = disambiguation_callback(prompt_msg, options_payload)
            if user_choice:
                user_choice_str = str(user_choice).strip()
                # Check numeric index
                if user_choice_str.isdigit():
                    chosen_idx = int(user_choice_str) - 1
                    if 0 <= chosen_idx < len(distinct_options):
                        chosen = distinct_options[chosen_idx]
                        return chosen, chosen.spec
                # Check text match
                for opt in distinct_options:
                    if user_choice_str.lower() in opt.spec.lower() or user_choice_str.lower() in opt.title.lower():
                        return opt, opt.spec
        except Exception:
            pass

    # 5. Non-interactive default: pick top-scoring organic base variant
    best = active_variants[0]
    return best, best.spec


def find_page_variant_buttons(page: Any) -> List[Dict[str, Any]]:
    """Discovers interactive size/variant/swatch buttons on a product or booking page."""
    buttons: List[Dict[str, Any]] = []
    if not page or page.is_closed():
        return buttons

    selectors = [
        "button[class*='size' i]",
        "button[class*='swatch' i]",
        "div[class*='size' i] button",
        "div[class*='swatch' i] button",
        "div[class*='variant' i] button",
        "div[class*='capacity' i] button",
        "li[class*='swatch' i]",
        "div[data-testid*='size' i]",
        "div[role='radiogroup'] button",
    ]
    seen = set()
    for sel in selectors:
        try:
            locs = page.locator(sel).all()
            for loc in locs[:12]:
                try:
                    txt = loc.inner_text().strip()
                    if not txt or len(txt) > 25 or txt in seen:
                        continue
                    seen.add(txt)
                    buttons.append({
                        "text": txt,
                        "element": loc,
                    })
                except Exception:
                    continue
        except Exception:
            continue
    return buttons

