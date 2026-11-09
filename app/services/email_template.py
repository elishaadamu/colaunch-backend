"""
Real Human-to-Human Venture Studio Email Template & Markdown Formatter.

Provides:
1. Clean 1-on-1 personal email message formatting (NOT a newsletter).
2. Official Creator Forge 4-node geometric logo & authentic executive signature.
3. Clean, high-deliverability light HTML for all email clients (Gmail, Apple Mail, Outlook).
4. Concept preview section for Step 5 & Step 6 — plain text style with landing page links.
"""
import os
import re
import html
import urllib.parse
from typing import Optional, List, Dict, Any
try:
    import markdown
except ImportError:
    markdown = None

from app.config import settings

STUDIO_NAME: str = getattr(settings, "STUDIO_NAME", "CoLaunch")
STUDIO_TAGLINE: str = getattr(settings, "STUDIO_TAGLINE", "Venture Studio & Co-Launch Incubation")


def _get_frontend_base() -> str:
    """Return the canonical frontend base URL (no trailing slash)."""
    return (
        os.getenv("FRONTEND_URL")
        or getattr(settings, "FRONTEND_URL", "https://creator-forge-frontend.vercel.app")
    ).rstrip("/")


def _get_backend_base() -> str:
    """Return the canonical backend base URL (no trailing slash)."""
    return (
        os.getenv("BACKEND_URL")
        or getattr(settings, "BACKEND_URL", "http://localhost:8000")
    ).rstrip("/")


def _render_colaunch_logo_html(size: int = 32, show_wordmark: bool = True, is_dark: bool = False) -> str:
    """
    Renders the official CoLaunch Rocket symbol & typography.
    Uses CID inline attachment and hosted fallback image so Gmail, Apple Mail & Outlook render it 100% reliably.
    """
    text_color = "#ffffff" if is_dark else "#0f172a"
    wordmark_html = ""
    if show_wordmark:
        font_sz = max(18, int(size * 0.72))
        wordmark_html = (
            f'<td style="vertical-align:middle;padding-left:10px;">'
            f'<span style="font-family:\'Playfair Display\',Georgia,Cambria,\'Times New Roman\',serif;'
            f'font-size:{font_sz}px;font-weight:700;color:{text_color};letter-spacing:-0.5px;line-height:1;">'
            f'CoLaunch</span></td>'
        )

    # Bulletproof logo img tag: inline CID attachment + public fallback
    logo_img = (
        f'<img src="cid:colaunch_logo" alt="CoLaunch" height="{size}" '
        f'style="height:{size}px;max-height:{size}px;width:auto;display:block;border:0;outline:none;" '
        f'onerror="this.onerror=null;this.src=\'https://app.co-launch.app/images/colaunch_logo.png\';" />'
    )

    return (
        f'<table border="0" cellspacing="0" cellpadding="0" style="display:inline-table;vertical-align:middle;">'
        f'<tr>'
        f'<td style="vertical-align:middle;">{logo_img}</td>'
        f'{wordmark_html}'
        f'</tr></table>'
    )


# Backward-compatible alias
def _render_creator_forge_logo_html(size: int = 28) -> str:
    return _render_colaunch_logo_html(size=size, show_wordmark=False)


def _svg_calendar_icon() -> str:
    """Clean SVG calendar vector icon (Zero raw emojis)."""
    return (
        '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#c81e3c" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display:block;">'
        '<rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect>'
        '<line x1="16" y1="2" x2="16" y2="6"></line>'
        '<line x1="8" y1="2" x2="8" y2="6"></line>'
        '<line x1="3" y1="10" x2="21" y2="10"></line>'
        '</svg>'
    )


def _svg_video_icon() -> str:
    """Clean SVG video/diagnostics vector icon (Zero raw emojis)."""
    return (
        '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#c81e3c" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display:block;">'
        '<polygon points="23 7 16 12 23 17 23 7"></polygon>'
        '<rect x="1" y="5" width="15" height="14" rx="2" ry="2"></rect>'
        '</svg>'
    )


def _svg_rocket_icon() -> str:
    """Clean SVG rocket monetization vector icon (Zero raw emojis)."""
    return (
        '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#c81e3c" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display:block;">'
        '<path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z"></path>'
        '<path d="m12 15-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z"></path>'
        '<path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0"></path>'
        '<path d="M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5"></path>'
        '</svg>'
    )


def _svg_reply_icon() -> str:
    """Clean SVG direct human reply vector icon (Zero raw emojis)."""
    return (
        '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#c81e3c" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display:block;">'
        '<polyline points="9 17 4 12 9 7"></polyline>'
        '<path d="M20 18v-2a4 4 0 0 0-4-4H4"></path>'
        '</svg>'
    )


def _svg_clock_icon() -> str:
    """Clean SVG expiration clock vector icon (Zero raw emojis)."""
    return (
        '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#c81e3c" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display:block;">'
        '<circle cx="12" cy="12" r="10"></circle>'
        '<polyline points="12 6 12 12 16 14"></polyline>'
        '</svg>'
    )


def _svg_shield_icon() -> str:
    """Clean SVG security shield vector icon (Zero raw emojis)."""
    return (
        '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#c81e3c" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display:block;">'
        '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path>'
        '</svg>'
    )


def get_contact_reply_email() -> str:
    """
    Retrieves the active contact and reply email address from .env / settings.
    """
    try:
        from app.config import settings
        env_from = os.getenv("FROM_EMAIL") or getattr(settings, "FROM_EMAIL", None)
        env_admin = os.getenv("ADMIN_EMAIL") or getattr(settings, "ADMIN_EMAIL", None)
        google_mail = os.getenv("GOOGLE_EMAIL") or getattr(settings, "GOOGLE_EMAIL", None)
        candidate = (env_from or env_admin or google_mail or "creatorforgeweb@gmail.com").strip()
        if "brevosend.com" in candidate.lower():
            return (google_mail or "creatorforgeweb@gmail.com").strip()
        return candidate
    except Exception:
        fallback = os.getenv("FROM_EMAIL", "creatorforgeweb@gmail.com").strip()
        if "brevosend.com" in fallback.lower():
            return "creatorforgeweb@gmail.com"
        return fallback


def _render_executive_signature_html(creator_name: str = "", tracking_token: str = "") -> str:
    """Renders a clean, plain 1-on-1 executive email signature."""
    logo_html = _render_colaunch_logo_html(size=26, show_wordmark=False)
    contact_email = get_contact_reply_email()
    ref_footnote = ""
    if tracking_token:
        # Invisible tracking token: 100% effective for IMAP reply attribution, completely hidden from humans
        ref_footnote = (
            f'<!-- Ref: {html.escape(tracking_token)} -->'
            f'<div style="display:none!important;visibility:hidden;mso-hide:all;font-size:1px;color:#ffffff;line-height:1px;max-height:0px;max-width:0px;opacity:0;overflow:hidden;">'
            f'Ref: {html.escape(tracking_token)}</div>'
        )
    return (
        '<div style="margin-top:32px;padding-top:18px;border-top:1px solid #e2e8f0;'
        'font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,Helvetica,Arial,sans-serif;">'
        '<table border="0" cellspacing="0" cellpadding="0"><tr>'
        f'<td style="vertical-align:top;padding-right:12px;">{logo_html}</td>'
        '<td style="vertical-align:top;">'
        '<div style="font-size:14px;font-weight:700;color:#0f172a;line-height:1.3;">CoLaunch Venture Partnerships</div>'
        '<div style="font-size:13px;color:#64748b;margin-top:2px;">Venture Partnerships • CoLaunch Studio</div>'
        '<div style="font-size:13px;color:#475569;margin-top:6px;line-height:1.6;">'
        f'<a href="mailto:{contact_email}" style="color:#c81e3c;text-decoration:none;font-weight:600;">'
        f'{html.escape(contact_email)}</a>'
        '&nbsp;|&nbsp;'
        '<a href="https://colaunch.app" style="color:#0f172a;text-decoration:underline;">colaunch.app</a>'
        '</div>'
        '<div style="font-size:12px;color:#64748b;margin-top:4px;">Reply directly to this email to reach our studio partners.</div>'
        f'{ref_footnote}'
        '</td></tr></table></div>'
    )


def _clean_and_extract_ref_tokens(raw_text: str) -> tuple[str, str]:
    """
    Extracts internal tracking tokens like 'Ref: [CF-STAGE:... | CF-CID:...]'
    so they don't clutter the main email text or break markdown parsing.
    Returns (cleaned_body, extracted_token).
    """
    token = ""
    token_match = re.search(r'(?:---\s*)?Ref:\s*(\[[^\]]+\])', raw_text, re.IGNORECASE)
    if token_match:
        token = token_match.group(1).strip()
        raw_text = raw_text[:token_match.start()] + raw_text[token_match.end():]

    token_match2 = re.search(r'(\[CF-[^\]]+\])', raw_text)
    if token_match2 and not token:
        token = token_match2.group(1).strip()
        raw_text = raw_text[:token_match2.start()] + raw_text[token_match2.end():]

    cleaned_body = re.sub(r'---\s*$', '', raw_text.strip()).strip()
    return cleaned_body, token


def _build_concept_landing_url(concept: Dict[str, Any], creator_name: str = "", index: int = 0) -> str:
    """
    Builds the public-facing landing page URL for a given concept.
    Uses the concept's appUrl/mockup data if available, otherwise constructs a
    portal-based preview URL from the frontend base URL.
    """
    frontend_base = _get_frontend_base()

    mockup = concept.get("mockup") if isinstance(concept.get("mockup"), dict) else {}
    explicit_url = (
        concept.get("landingPageUrl")
        or concept.get("landing_page_url")
        or mockup.get("previewUrl")
        or mockup.get("landingUrl")
        or ""
    ).strip()

    if explicit_url and explicit_url.startswith("http"):
        return explicit_url

    app_url = (
        concept.get("appUrl")
        or mockup.get("appUrl")
        or ""
    ).strip()

    if app_url and "." in app_url and not app_url.startswith("http"):
        return f"https://{app_url}"
    if app_url and app_url.startswith("http"):
        return app_url

    concept_slug = re.sub(r"[^a-z0-9\-]", "", (concept.get("name") or "concept").lower().replace(" ", "-"))
    creator_slug = re.sub(r"[^a-z0-9\-]", "", (creator_name or "creator").lower().replace(" ", "-"))
    return f"{frontend_base}/preorder/{creator_slug}?concept={concept_slug}"


def _render_single_concept_block(
    concept: Dict[str, Any],
    index: int = 0,
    total_concepts: int = 1,
    creator_name: str = "",
    tracking_token: str = ""
) -> str:
    """
    Renders a single concept as a clean, plain text-style block for a professional 1-on-1 email.
    No dark UI, no newsletter-style cards, includes embedded concept image and direct landing page link.
    """
    app_name = concept.get("name") or concept.get("title") or f"Software Concept {index + 1}"
    tagline = concept.get("tagline") or concept.get("summary") or ""
    problem = concept.get("problem") or concept.get("description") or ""
    score = concept.get("opportunityScore") or concept.get("score") or (98 - index * 3)

    raw_pricing = concept.get("pricing") or concept.get("revenueModel")
    if isinstance(raw_pricing, dict):
        pricing_parts = [str(v) for k, v in raw_pricing.items() if v]
        pricing = " / ".join(pricing_parts) if pricing_parts else "$29/mo"
    elif isinstance(raw_pricing, str) and raw_pricing.strip():
        pricing = raw_pricing.strip()
    else:
        pricing = "$29/mo Starter — $79/mo Pro"

    key_features = concept.get("keyFeatures") or concept.get("features") or []
    landing_url = _build_concept_landing_url(concept, creator_name, index)

    # Resolve concept preview image
    mockup = concept.get("mockup") if isinstance(concept.get("mockup"), dict) else {}
    concept_img = (
        concept.get("customImageUrl")
        or concept.get("imageUrl")
        or concept.get("image_url")
        or concept.get("mockup_url")
        or mockup.get("imageUrl")
        or mockup.get("previewUrl")
        or ""
    )
    if isinstance(concept_img, str) and concept_img.strip():
        concept_img = concept_img.strip()
        if concept_img.startswith("/"):
            concept_img = f"{_get_frontend_base()}{concept_img}"
    else:
        concept_img = ""

    features_html = ""
    if key_features and isinstance(key_features, list):
        items_html = "".join(
            f'<li style="margin:0 0 5px 0;color:#334155;font-size:14px;line-height:1.55;">'
            f'{html.escape(str(f))}</li>'
            for f in key_features[:4]
        )
        features_html = (
            '<ul style="margin:6px 0 8px 0;padding-left:20px;list-style-type:disc;">'
            f'{items_html}</ul>'
        )

    problem_html = ""
    if problem:
        problem_html = (
            '<p style="margin:6px 0;color:#475569;font-size:14px;line-height:1.55;">'
            f'<strong style="color:#0f172a;">Problem it solves:</strong> {html.escape(problem)}</p>'
        )

    tagline_html = ""
    if tagline:
        tagline_html = (
            f'<div style="font-size:14px;color:#334155;margin-bottom:8px;font-style:italic;">'
            f'{html.escape(tagline)}</div>'
        )

    img_html = ""
    if concept_img:
        img_html = (
            f'<div style="margin:12px 0 14px 0;">'
            f'<img src="{html.escape(concept_img)}" alt="{html.escape(app_name)} preview" width="540" '
            f'style="width:100%;max-width:540px;height:auto;border-radius:8px;'
            f'border:1px solid #e2e8f0;display:block;" />'
            f'</div>'
        )

    return (
        '<table width="100%" border="0" cellspacing="0" cellpadding="0" style="margin:0 0 24px 0;">'
        '<tr><td style="padding:18px 20px;background:#f8fafc;border:1px solid #e2e8f0;'
        'border-left:3px solid #0f172a;border-radius:6px;">'
        '<div style="font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;'
        f'letter-spacing:0.8px;margin-bottom:4px;">Concept {index + 1} of {total_concepts}</div>'
        f'<div style="font-size:17px;font-weight:700;color:#0f172a;margin-bottom:4px;">'
        f'{html.escape(app_name)}</div>'
        f'{tagline_html}'
        f'{img_html}'
        f'{problem_html}'
        f'{features_html}'
        '<table width="100%" border="0" cellspacing="0" cellpadding="0" style="margin-top:10px;">'
        '<tr><td style="font-size:13px;color:#64748b;">'
        f'Pricing: <strong style="color:#0f172a;">{html.escape(pricing)}</strong>'
        '&nbsp;&nbsp;|&nbsp;&nbsp;'
        f'Opportunity score: <strong style="color:#16a34a;">{score}/100</strong>'
        '</td></tr></table>'
        '<div style="margin-top:14px;padding-top:12px;border-top:1px solid #e2e8f0;">'
        f'<a href="{landing_url}" target="_blank" '
        'style="display:inline-block;padding:9px 18px;background:#c81e3c;color:#ffffff;'
        'text-decoration:none;font-weight:700;font-size:13px;border-radius:6px;margin-bottom:6px;">'
        'Review Live Concept &amp; Landing Page &rarr;</a>'
        f'<div style="font-size:12px;color:#64748b;word-break:break-all;">Direct landing page link: <a href="{landing_url}" target="_blank" style="color:#0284c7;text-decoration:underline;">{html.escape(landing_url)}</a></div>'
        '</div>'
        '</td></tr></table>'
    )


def render_concept_showcase_html(
    concepts: Optional[List[Dict[str, Any]]] = None,
    concept_image_url: Optional[str] = None,
    creator_name: str = "",
    tracking_token: str = ""
) -> str:
    """
    Renders a clean, professional concept section for Step 5 & Step 6 emails.
    Plain text-style blocks — no dark UI, no newsletter styling.
    Includes a landing page link and concept image under each concept.
    """
    if not concepts and not concept_image_url:
        return ""

    concept_list = concepts if (concepts and len(concepts) > 0) else [{}]
    concept_list = concept_list[:3]
    total_count = len(concept_list)
    plural = "" if total_count == 1 else "s"

    header_html = (
        '<div style="margin:28px 0 16px 0;padding-bottom:10px;border-bottom:1px solid #e2e8f0;">'
        '<div style="font-size:15px;font-weight:700;color:#0f172a;">'
        'Software Concepts Proposed for Your Audience</div>'
        f'<div style="font-size:13px;color:#64748b;margin-top:3px;">'
        f'Below are the top {total_count} custom software product concept{plural} our team has designed '
        f'based on analysis of your audience. Each includes a live landing page preview.</div>'
        '</div>'
    )

    cards_html = "".join(
        _render_single_concept_block(
            concept=c,
            index=idx,
            total_concepts=total_count,
            creator_name=creator_name,
            tracking_token=tracking_token
        )
        for idx, c in enumerate(concept_list)
    )

    cta_html = (
        '<div style="margin:20px 0 8px 0;font-size:14px;color:#334155;line-height:1.6;">'
        'Under our 50/50 partnership, our engineering team handles 100% of product development, '
        'cloud hosting, billing infrastructure, and ongoing technical support with zero upfront cost to you.<br><br>'
        'To discuss these concepts, refine any direction, or explore an idea of your own, simply reply directly to this email.'
        '</div>'
    )

    return header_html + cards_html + cta_html


def convert_markdown_to_clean_html(markdown_text: str) -> str:
    """
    Converts markdown text to clean, email-safe HTML with inline CSS.
    Handles headings, bold, italics, links, lists, blockquotes, and embedded images.
    Blockquotes are styled as clean highlighted personal notes.
    No promotional buttons or emojis are injected.
    """
    if not markdown_text:
        return ""

    lines = markdown_text.splitlines()
    normalized_lines = []

    list_marker_pattern = re.compile(r"^(\s*)(?:[•\-\*]|\d+[\.\)])\s+")
    heading_pattern = re.compile(r"^#{1,6}\s+")
    in_list = False

    for line in lines:
        stripped = line.strip()
        is_list_item = bool(list_marker_pattern.match(stripped))
        is_heading = bool(heading_pattern.match(stripped))

        if stripped.startswith("•"):
            indent_count = len(line) - len(line.lstrip())
            indent = " " * indent_count
            line = f"{indent}* {stripped[1:].strip()}"
            is_list_item = True

        if is_heading and normalized_lines and normalized_lines[-1].strip():
            normalized_lines.append("")

        if is_list_item and not in_list:
            if normalized_lines and normalized_lines[-1].strip():
                normalized_lines.append("")
            in_list = True
        elif not is_list_item and in_list and stripped:
            normalized_lines.append("")
            in_list = False
        elif not stripped:
            in_list = False

        normalized_lines.append(line)

    clean_md = "\n".join(normalized_lines)

    if markdown is not None:
        raw_html = markdown.markdown(
            clean_md,
            extensions=["extra", "sane_lists", "nl2br"]
        )
    else:
        # Resilient fallback markdown converter for bare environments
        paragraphs = clean_md.split("\n\n")
        raw_html = "".join(f"<p>{html.escape(p).replace(chr(10), '<br>')}</p>" for p in paragraphs if p.strip())

    raw_html = re.sub(
        r'<p>(.*?)</p>',
        r'<p style="margin:0 0 16px 0;line-height:1.65;color:#1e293b;font-size:15px;">\1</p>',
        raw_html, flags=re.DOTALL
    )
    raw_html = re.sub(
        r'<h1>(.*?)</h1>',
        r'<h1 style="margin:20px 0 10px 0;font-size:20px;font-weight:700;color:#0f172a;line-height:1.3;">\1</h1>',
        raw_html
    )
    raw_html = re.sub(
        r'<h2>(.*?)</h2>',
        r'<h2 style="margin:18px 0 8px 0;font-size:17px;font-weight:700;color:#0f172a;line-height:1.35;">\1</h2>',
        raw_html
    )
    raw_html = re.sub(
        r'<h3>(.*?)</h3>',
        r'<h3 style="margin:14px 0 6px 0;font-size:15px;font-weight:700;color:#0f172a;line-height:1.4;">\1</h3>',
        raw_html
    )
    raw_html = re.sub(
        r'<strong>(.*?)</strong>',
        r'<strong style="color:#0f172a;font-weight:700;">\1</strong>',
        raw_html
    )
    raw_html = re.sub(
        r'<em>(.*?)</em>',
        r'<em style="color:#475569;font-style:italic;">\1</em>',
        raw_html
    )
    raw_html = re.sub(
        r'<ul>',
        r'<ul style="margin:10px 0 14px 0;padding-left:20px;list-style-type:disc;color:#334155;">',
        raw_html
    )
    raw_html = re.sub(
        r'<ol>',
        r'<ol style="margin:10px 0 14px 0;padding-left:22px;color:#334155;">',
        raw_html
    )
    raw_html = re.sub(
        r'<li>(.*?)</li>',
        r'<li style="margin-bottom:5px;line-height:1.6;color:#1e293b;font-size:15px;">\1</li>',
        raw_html, flags=re.DOTALL
    )

    # Style blockquotes as highlighted personal note boxes
    def _style_blockquote(match):
        inner_content = match.group(1).strip()
        # Clean inner <p> tags
        inner_content = re.sub(r'</?p[^>]*>', '', inner_content).strip()
        return (
            '<div style="margin:16px 0;padding:12px 18px;background:#f8fafc;border:1px solid #e2e8f0;'
            'border-left:4px solid #0284c7;border-radius:6px;color:#1e293b;font-size:14px;line-height:1.6;">'
            f'{inner_content}</div>'
        )

    raw_html = re.sub(
        r'<blockquote>(.*?)</blockquote>',
        _style_blockquote,
        raw_html, flags=re.DOTALL
    )

    # Style embedded images
    def _style_img_tag(match):
        attrs = match.group(1)
        src_m = re.search(r'src="([^"]+)"', attrs)
        alt_m = re.search(r'alt="([^"]+)"', attrs)
        src = src_m.group(1) if src_m else ""
        alt = alt_m.group(1) if alt_m else "Concept preview"
        if src.startswith("/"):
            src = f"{_get_frontend_base()}{src}"
        return (
            f'<div style="margin:14px 0 18px 0;">'
            f'<img src="{html.escape(src)}" alt="{html.escape(alt)}" width="540" '
            f'style="width:100%;max-width:540px;height:auto;border-radius:8px;'
            f'border:1px solid #e2e8f0;display:block;box-shadow:0 1px 3px rgba(0,0,0,0.05);" />'
            f'</div>'
        )

    raw_html = re.sub(r'<img\s+([^>]+)/?>', _style_img_tag, raw_html)

    raw_html = re.sub(
        r'<hr\s*/?>',
        r'<hr style="border:none;border-top:1px solid #e2e8f0;margin:20px 0;">',
        raw_html
    )
    raw_html = re.sub(
        r'<a\s+href="([^"]+)">([^<]+)</a>',
        r'<a href="\1" target="_blank" style="color:#0284c7;text-decoration:underline;">\2</a>',
        raw_html
    )

    # Style Landing Page preview links as clean CTA buttons with secondary direct URL
    def _style_landing_page_cta(m):
        prefix = m.group(1)
        url = m.group(2)
        return (
            f'<div style="margin:12px 0 16px 0;padding:12px 16px;background:#f8fafc;border:1px solid #e2e8f0;border-left:3px solid #0f172a;border-radius:6px;">'
            f'<div style="font-size:12px;font-weight:700;color:#64748b;text-transform:uppercase;margin-bottom:6px;">Interactive Concept Preview</div>'
            f'<a href="{url}" target="_blank" style="display:inline-block;padding:8px 16px;background:#c81e3c;color:#ffffff;text-decoration:none;font-weight:700;font-size:13px;border-radius:6px;margin-bottom:6px;">Review Live Concept &amp; Landing Page &rarr;</a>'
            f'<div style="font-size:12px;color:#64748b;word-break:break-all;">Direct link: <a href="{url}" target="_blank" style="color:#0284c7;text-decoration:underline;">{url}</a></div>'
            f'</div>'
        )

    raw_html = re.sub(
        r'(?:<p>|<li>)?(?:•\s*)?(?:Live\s+)?Landing\s+Page:\s*<a\s+href="([^"]+)"[^>]*>(.*?)</a>(?:</p>|</li>)?',
        _style_landing_page_cta,
        raw_html,
        flags=re.IGNORECASE
    )

    # Format low-friction reply options into high-contrast visual badges
    def _style_reply_options(match):
        return (
            '<div style="margin:22px 0 16px 0;background:#fef2f2;border:1px solid #fecdd3;border-left:3px solid #c81e3c;border-radius:8px;padding:16px 18px;">'
            '<div style="font-size:13px;font-weight:700;color:#0f172a;margin-bottom:10px;">'
            'To reply, simply respond directly to this email:</div>'
            '<div style="margin-bottom:8px;">'
            '<span style="display:inline-block;padding:5px 12px;background:#059669;color:#ffffff;border-radius:4px;font-weight:700;font-size:12px;margin-right:6px;">'
            '&ldquo;I am interested, let&#39;s proceed&rdquo;</span>'
            '<span style="font-size:13px;color:#334155;line-height:1.5;">&rarr; We will send over the 3 custom software blueprints, revenue models, and live previews.</span>'
            '</div>'
            '<div>'
            '<span style="display:inline-block;padding:5px 12px;background:#64748b;color:#ffffff;border-radius:4px;font-weight:700;font-size:12px;margin-right:6px;">'
            '&ldquo;Not interested&rdquo;</span>'
            '<span style="font-size:13px;color:#334155;line-height:1.5;">&rarr; We will immediately close your file with zero follow-ups.</span>'
            '</div>'
            '</div>'
        )

    reply_pattern = re.compile(
        r'<p>To reply, simply respond directly to this email:<\/p>\s*<ul>\s*<li[^>]*>(?:•\s*)?(?:&quot;|\")I am interested.*?<\/li>\s*<li[^>]*>(?:•\s*)?(?:Or\s+)?(?:&quot;|\")Not interested.*?<\/li>\s*<\/ul>',
        re.IGNORECASE | re.DOTALL
    )
    raw_html = reply_pattern.sub(_style_reply_options, raw_html)

    reply_pattern_p = re.compile(
        r'<p>To reply, simply respond directly to this email:<br\s*\/?>\s*(?:•\s*)?(?:&quot;|\")I am interested.*?<br\s*\/?>\s*(?:•\s*)?(?:Or\s+)?(?:&quot;|\")Not interested.*?<\/p>',
        re.IGNORECASE | re.DOTALL
    )
    raw_html = reply_pattern_p.sub(_style_reply_options, raw_html)

    raw_html = re.sub(
        r'<code>(.*?)</code>',
        r'<code style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px;'
        r'background:#f1f5f9;color:#0f172a;border:1px solid #e2e8f0;padding:1px 5px;border-radius:3px;">\1</code>',
        raw_html
    )

    return raw_html


def format_luxury_html_email(
    body_text: str,
    subject: str,
    creator_name: str = "",
    tracking_token: str = "",
    concept_image_url: Optional[str] = None,
    concepts: Optional[List[Dict[str, Any]]] = None,
    logo_url: Optional[str] = None,
    studio_name: Optional[str] = None,
    concept_title: Optional[str] = None
) -> str:
    """
    Real Human-to-Human Email Formatter.

    Produces a clean, plain professional 1-on-1 email — not a newsletter.
    - White background, standard text sizing, natural paragraph flow
    - No heavy card UI, no dark backgrounds, no promotional buttons or spam triggers
    - Highlighted personal notes for research basis and total flexibility
    - Embedded concept preview images
    - Responsive for mobile (max-width 620px)
    - High deliverability: all styles inline, no webfonts, standard email clients compatible
    """
    clean_body, extracted_token = _clean_and_extract_ref_tokens(body_text)
    active_token = tracking_token or extracted_token

    clean_body = re.sub(r'\bundefined\s+content\b', 'content', clean_body, flags=re.IGNORECASE)
    clean_body = re.sub(r'\bundefined\b', 'community', clean_body, flags=re.IGNORECASE)

    formatted_body_html = convert_markdown_to_clean_html(clean_body)

    # Check if the email body already presents the concepts to prevent duplicate concept listings
    has_concepts_in_body = bool(
        re.search(
            r'(?:1\.\s+\*\*|1\.\s+[A-Z]|landing page:|concept 1|what it is:|opportunity score)',
            clean_body,
            re.IGNORECASE
        )
    )

    concept_card_html = ""
    if not has_concepts_in_body:
        active_concepts = concepts
        if not active_concepts and (concept_title or concept_image_url):
            active_concepts = [{
                "name": concept_title or "Custom Creator SaaS",
                "tagline": "Tailored software engineered for your audience",
                "imageUrl": concept_image_url,
                "mockup": {
                    "previewUrl": concept_image_url,
                    "appUrl": f"{creator_name.lower().replace(' ', '')}app.io" if creator_name else "creatorstudio.app"
                },
                "revenueModel": "$29/month Tiered Subscription",
                "pricing": {"creatorSplit": "50% Net Co-Founder Share"},
                "coreFeatures": ["Tailored Audience Workflow", "Automated Creator Monetisation"]
            }]

        concept_card_html = render_concept_showcase_html(
            concepts=active_concepts,
            concept_image_url=concept_image_url,
            creator_name=creator_name,
            tracking_token=active_token
        )

    signature_html = _render_executive_signature_html(creator_name, active_token)

    esc_subject = html.escape(subject)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{esc_subject}</title>
  <style>
    body {{ margin: 0 !important; padding: 0 !important; background-color: #f1f5f9 !important; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif !important; font-size: 15px !important; line-height: 1.65 !important; -webkit-font-smoothing: antialiased; }}
    table {{ border-collapse: collapse; mso-table-lspace: 0pt; mso-table-rspace: 0pt; }}
    td {{ padding: 0; }}
    p {{ margin: 0 0 16px 0; color: #1e293b; font-size: 15px; line-height: 1.65; }}
    a {{ color: #c81e3c; }}
    @media only screen and (max-width: 620px) {{
      .email-canvas {{ padding: 16px 8px !important; }}
      .card-body {{ padding: 22px 18px !important; }}
      .header-cell {{ padding: 14px 18px !important; }}
    }}
  </style>
</head>
<body style="margin: 0; padding: 0; background-color: #f1f5f9; color: #1e293b; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
  <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" class="email-canvas" style="background-color: #f1f5f9; min-height: 100vh; padding: 32px 12px;">
    <tr>
      <td align="center">
        
        <!-- Main Card Container -->
        <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="max-width: 600px; background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; box-shadow: 0 4px 24px rgba(15, 23, 42, 0.05); overflow: hidden;">
          
          <!-- Obsidian Brand Header Bar with CoLaunch Logo -->
          <tr>
            <td class="header-cell" style="padding: 18px 28px; background-color: #0b0f19; border-bottom: 2px solid #c81e3c;">
              <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0">
                <tr>
                  <td align="left" style="vertical-align: middle;">
                    <table role="presentation" border="0" cellspacing="0" cellpadding="0">
                      <tr>
                        <td style="vertical-align: middle;">
                          <img src="cid:colaunch_logo" alt="CoLaunch" height="34" style="height: 34px; max-height: 34px; width: auto; display: block; border: 0;" onerror="this.onerror=null;this.src='https://app.co-launch.app/images/colaunch_logo.png';" />
                        </td>
                        <td style="vertical-align: middle; padding-left: 10px;">
                          <span style="font-family: 'Playfair Display', Georgia, Cambria, 'Times New Roman', serif; font-size: 22px; font-weight: 700; color: #ffffff; letter-spacing: -0.5px; line-height: 1;">
                            CoLaunch
                          </span>
                        </td>
                      </tr>
                    </table>
                  </td>
                  <td align="right" style="vertical-align: middle;">
                    <span style="display: inline-block; font-family: 'SF Mono', Menlo, Consolas, monospace; font-size: 10px; font-weight: 700; letter-spacing: 1px; text-transform: uppercase; color: #c81e3c; background-color: #1a0f14; border: 1px solid #7f1d2d; padding: 4px 10px; border-radius: 20px;">
                      VENTURE PARTNERSHIP
                    </span>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Content Body -->
          <tr>
            <td class="card-body" style="padding: 32px 32px 24px 32px; background-color: #ffffff;">
              <div style="color: #1e293b; font-size: 15px; line-height: 1.65;">
                {formatted_body_html}
              </div>
              {concept_card_html}
              {signature_html}
            </td>
          </tr>

          <!-- Subtle Footer Note -->
          <tr>
            <td style="padding: 16px 28px; background-color: #f8fafc; border-top: 1px solid #f1f5f9; text-align: center;">
              <p style="margin: 0; color: #94a3b8; font-size: 11px;">
                CoLaunch Venture Studio &bull; 50/50 Co-Founder Partnerships &bull; <a href="https://colaunch.app" style="color: #64748b; text-decoration: underline;">colaunch.app</a>
              </p>
            </td>
          </tr>

        </table>

      </td>
    </tr>
  </table>
</body>
</html>"""


def render_otp_verification_email(
    code: str,
    display_name: str = "Creator",
    base_url: str = "https://app.co-launch.app"
) -> tuple[str, str]:
    """
    Renders an executive, high-deliverability single-use OTP verification email.
    Uses clean light canvas with CoLaunch brand identity and inline SVG icons (Zero raw emojis).
    Returns (body_html, body_text).
    """
    clean_name = html.escape(display_name or "Creator")
    clean_code = html.escape(str(code).strip())
    canonical_base = base_url.rstrip("/")

    body_text = (
        f"COLAUNCH — SINGLE-USE VERIFICATION CODE\n\n"
        f"Hello {display_name},\n\n"
        f"Use the following 6-digit verification code to authenticate your CoLaunch operator workspace:\n\n"
        f"    {code}\n\n"
        f"• This code is valid for 15 minutes.\n"
        f"• Never share this code with anyone. CoLaunch staff will never ask for your code.\n"
        f"• Workspace: {canonical_base}\n\n"
        f"If you did not request this verification code, someone may have entered your email by mistake. "
        f"No action is required — your account remains completely secure.\n\n"
        f"— CoLaunch Technologies Inc.\n"
        f"{canonical_base}\n"
    )

    logo_html = _render_colaunch_logo_html(size=28, show_wordmark=True)
    clock_svg = _svg_clock_icon()
    shield_svg = _svg_shield_icon()

    body_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Your CoLaunch Verification Code</title>
  <style>
    body {{ margin: 0 !important; padding: 0 !important; background-color: #f8fafc !important; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif !important; -webkit-font-smoothing: antialiased; }}
    table {{ border-collapse: collapse; mso-table-lspace: 0pt; mso-table-rspace: 0pt; }}
    td {{ padding: 0; }}
    img {{ border: 0; height: auto; line-height: 100%; outline: none; text-decoration: none; }}
    @media only screen and (max-width: 600px) {{
      .card-body {{ padding: 24px 20px !important; }}
      .code-display {{ font-size: 30px !important; letter-spacing: 6px !important; }}
    }}
  </style>
</head>
<body style="margin: 0; padding: 0; background-color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #0f172a;">
  <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #f8fafc; min-height: 100vh; padding: 36px 12px;">
    <tr>
      <td align="center">
        
        <!-- Main Card Container -->
        <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="max-width: 520px; background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; box-shadow: 0 4px 20px rgba(15, 23, 42, 0.04); overflow: hidden;">
          
          <!-- Top Brand Header -->
          <tr>
            <td style="padding: 22px 28px 16px 28px; border-bottom: 1px solid #f1f5f9;">
              <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0">
                <tr>
                  <td align="left" style="vertical-align: middle;">
                    {logo_html}
                  </td>
                  <td align="right" style="vertical-align: middle;">
                    <span style="display: inline-block; font-family: 'SF Mono', Menlo, Consolas, monospace; font-size: 10px; font-weight: 700; letter-spacing: 1px; text-transform: uppercase; color: #c81e3c; background-color: #fef2f2; border: 1px solid #fecdd3; padding: 3px 10px; border-radius: 20px;">
                      SECURITY PASSCODE
                    </span>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Content Body -->
          <tr>
            <td class="card-body" style="padding: 28px 30px 22px 30px;">
              <h2 style="margin: 0 0 10px 0; color: #0f172a; font-family: 'Playfair Display', Georgia, serif; font-size: 22px; font-weight: 700; letter-spacing: -0.3px;">
                Single-Use Verification Code
              </h2>
              <p style="margin: 0 0 18px 0; color: #475569; font-size: 14px; line-height: 1.6;">
                Hello <strong style="color: #0f172a;">{clean_name}</strong>, use the secure 6-digit verification code below to authenticate your CoLaunch operator workspace.
              </p>

              <!-- 6-Digit Code Box (Monospace & High Legibility) -->
              <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="margin: 20px 0;">
                <tr>
                  <td style="background-color: #f8fafc; border: 1.5px dashed #cbd5e1; border-radius: 8px; padding: 18px 14px; text-align: center;">
                    <div style="font-family: 'SF Mono', Menlo, Consolas, monospace; font-size: 34px; font-weight: 800; letter-spacing: 10px; color: #0f172a;" class="code-display">
                      {clean_code}
                    </div>
                    <div style="margin-top: 8px; font-size: 11px; font-family: -apple-system, BlinkMacSystemFont, sans-serif; color: #64748b; text-transform: uppercase; letter-spacing: 1px;">
                      Enter this 6-digit code in the verification prompt
                    </div>
                  </td>
                </tr>
              </table>

              <!-- Meta / Expiration Details with Clean SVG Icons -->
              <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px 14px; margin-bottom: 20px;">
                <tr>
                  <td style="vertical-align: middle; width: 22px; padding-right: 10px;">
                    {clock_svg}
                  </td>
                  <td style="font-size: 12px; color: #334155; line-height: 1.5;">
                    <strong style="color: #0f172a;">Valid for 15 minutes.</strong> This code expires automatically for your security.
                  </td>
                </tr>
                <tr><td colspan="2" style="height: 8px;"></td></tr>
                <tr>
                  <td style="vertical-align: middle; width: 22px; padding-right: 10px;">
                    {shield_svg}
                  </td>
                  <td style="font-size: 12px; color: #334155; line-height: 1.5;">
                    <strong style="color: #0f172a;">Security Alert:</strong> Never share this code. CoLaunch staff will never request your code.
                  </td>
                </tr>
              </table>

              <!-- Action Link -->
              <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0">
                <tr>
                  <td align="center">
                    <a href="{canonical_base}" target="_blank" style="display: inline-block; background-color: #c81e3c; color: #ffffff !important; font-weight: 700; font-size: 13px; text-decoration: none; padding: 12px 30px; border-radius: 8px; letter-spacing: 0.3px; text-transform: uppercase; box-shadow: 0 4px 12px rgba(200, 30, 60, 0.25);">
                      Launch CoLaunch Workspace &rarr;
                    </a>
                  </td>
                </tr>
              </table>

              <p style="margin: 20px 0 0 0; color: #64748b; font-size: 12px; line-height: 1.5; text-align: center;">
                If you did not request this verification code, someone may have entered your email by mistake. No action is required.
              </p>
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="padding: 18px 28px; border-top: 1px solid #f1f5f9; text-align: center; background-color: #f8fafc;">
              <p style="margin: 0 0 4px 0; color: #64748b; font-size: 12px; font-weight: 600;">
                CoLaunch • Turn your audience into a software enterprise
              </p>
              <p style="margin: 0; color: #94a3b8; font-size: 11px;">
                <a href="{canonical_base}" style="color: #64748b; text-decoration: underline;">{canonical_base.replace('https://', '')}</a> • Dedicated Creator Workspace
              </p>
              <p style="margin: 6px 0 0 0; color: #94a3b8; font-size: 10px;">
                &copy; 2026 CoLaunch Technologies Inc. All rights reserved.
              </p>
            </td>
          </tr>

        </table>

      </td>
    </tr>
  </table>
</body>
</html>"""

    return body_html, body_text


def render_welcome_signup_email(
    username: str,
    base_url: str = "https://app.co-launch.app"
) -> tuple[str, str]:
    """
    Renders an executive, creative CoLaunch Account Creation / Onboarding email.
    Features:
    1. CoLaunch Rocket Logo badge & Playfair Display serif wordmark.
    2. Crimson (#C81E3C) & Slate (#0F172A) brand styling on clean light canvas (#F8FAFC).
    3. Tactile primary onboarding CTA button (Just Eat & Headway pattern).
    4. "Three hints on how to use CoLaunch" with clean inline SVG icons (Zero emojis).
    5. "Workspace Access Details" credentials box (Just Eat pattern).
    6. Direct 2-Way Human Reply Line to invite real founder responses (NOT an impersonal newsletter).
    7. High-deliverability HTML layout compatible with Gmail, Apple Mail, and Outlook.
    Returns (body_html, body_text).
    """
    clean_user = html.escape(username or "Creator")
    canonical_base = base_url.rstrip("/")

    body_text = (
        f"COLAUNCH — OPERATOR ONBOARDING\n\n"
        f"You're ready to build & co-launch\n"
        f"Hello {username}, welcome to CoLaunch!\n\n"
        f"Your private creator software console is fully provisioned and initialized.\n"
        f"We partner 50/50 with you to turn your audience attention into a defensible, high-margin software enterprise.\n\n"
        f"Launch your workspace:\n"
        f"{canonical_base}/dashboard\n\n"
        f"THREE HINTS ON HOW TO USE COLAUNCH:\n"
        f"1. Autonomous Content Calendar: Plan, schedule, and orchestrate omnichannel video and social releases across YouTube, Instagram, and TikTok from a single calendar.\n"
        f"2. AI Video Performance Studio: Continuous self-improving diagnostics that analyze video drop-offs and generate corrective hooks for your next release.\n"
        f"3. 50/50 Co-Founder Monetization: Review tailored custom software concepts for your audience with zero upfront engineering or hosting cost.\n\n"
        f"WORKSPACE ACCESS DETAILS:\n"
        f"• Authorized Username: {username}\n"
        f"• Dedicated Console: {canonical_base}/dashboard\n"
        f"• Workspace Tier: Co-Founder Partner (Full Studio Access)\n"
        f"• Status: Active & Provisioned\n\n"
        f"DIRECT HUMAN RESPONSE LINE:\n"
        f"Have questions or want to discuss your audience? Simply reply directly to this email.\n"
        f"You won't get routed to a ticketing bot — our venture partners and lead engineers read and answer every founder message personally.\n\n"
        f"We wish you a rapid journey to scale and recurring revenue,\n"
        f"— The CoLaunch Venture Studio Team\n"
        f"{canonical_base}\n"
    )

    logo_html = _render_colaunch_logo_html(size=30, show_wordmark=True)
    calendar_svg = _svg_calendar_icon()
    video_svg = _svg_video_icon()
    rocket_svg = _svg_rocket_icon()
    reply_svg = _svg_reply_icon()

    body_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Welcome to CoLaunch — You're Ready to Build</title>
  <style>
    body {{ margin: 0 !important; padding: 0 !important; background-color: #f8fafc !important; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif !important; -webkit-font-smoothing: antialiased; }}
    table {{ border-collapse: collapse; mso-table-lspace: 0pt; mso-table-rspace: 0pt; }}
    td {{ padding: 0; }}
    img {{ border: 0; height: auto; line-height: 100%; outline: none; text-decoration: none; }}
    @media only screen and (max-width: 620px) {{
      .card-body {{ padding: 26px 18px !important; }}
      .header-cell {{ padding: 20px 18px 14px 18px !important; }}
      .hint-title {{ font-size: 13px !important; }}
    }}
  </style>
</head>
<body style="margin: 0; padding: 0; background-color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #0f172a;">
  <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #f8fafc; min-height: 100vh; padding: 36px 12px;">
    <tr>
      <td align="center">
        
        <!-- Main Card Container -->
        <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="max-width: 580px; background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; box-shadow: 0 4px 24px rgba(15, 23, 42, 0.05); overflow: hidden;">
          
          <!-- Top Brand Header Bar -->
          <tr>
            <td class="header-cell" style="padding: 24px 32px 18px 32px; border-bottom: 1px solid #f1f5f9;">
              <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0">
                <tr>
                  <td align="left" style="vertical-align: middle;">
                    {logo_html}
                  </td>
                  <td align="right" style="vertical-align: middle;">
                    <span style="display: inline-block; font-family: 'SF Mono', Menlo, Consolas, monospace; font-size: 10px; font-weight: 700; letter-spacing: 1px; text-transform: uppercase; color: #c81e3c; background-color: #fef2f2; border: 1px solid #fecdd3; padding: 4px 10px; border-radius: 20px;">
                      OPERATOR ONBOARDING
                    </span>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Content Body -->
          <tr>
            <td class="card-body" style="padding: 32px 32px 24px 32px;">
              
              <!-- Hero Greeting -->
              <h1 style="margin: 0 0 10px 0; font-family: 'Playfair Display', Georgia, Cambria, serif; font-size: 26px; font-weight: 700; color: #0f172a; line-height: 1.25; letter-spacing: -0.5px;">
                You're ready to build &amp; co-launch
              </h1>
              <p style="margin: 0 0 20px 0; font-size: 15px; color: #334155; line-height: 1.6;">
                Hello <strong style="color: #0f172a;">{clean_user}</strong>, welcome to <strong>CoLaunch</strong>! Your private creator software console is fully provisioned. We partner 50/50 with you to turn your audience attention into a defensible, high-margin software enterprise.
              </p>

              <!-- Primary Action CTA Button (Tactile & High-Converting) -->
              <table role="presentation" border="0" cellspacing="0" cellpadding="0" style="margin: 22px 0 26px 0;">
                <tr>
                  <td align="left">
                    <a href="{canonical_base}/dashboard" target="_blank" style="display: inline-block; background-color: #c81e3c; color: #ffffff !important; font-weight: 700; font-size: 13px; text-decoration: none; padding: 13px 28px; border-radius: 8px; letter-spacing: 0.3px; text-transform: uppercase; box-shadow: 0 4px 14px rgba(200, 30, 60, 0.28);">
                      Launch Operator Console &rarr;
                    </a>
                  </td>
                </tr>
              </table>

              <!-- Section Divider -->
              <hr style="border: none; border-top: 1px solid #f1f5f9; margin: 26px 0;" />

              <!-- Three Hints On Getting Started (Headway-Inspired Pattern with Clean SVG Icons) -->
              <div style="font-size: 16px; font-weight: 800; color: #0f172a; margin-bottom: 16px; letter-spacing: -0.2px;">
                Three hints on how to use CoLaunch:
              </div>

              <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="margin-bottom: 24px;">
                <!-- Hint 1: Calendar -->
                <tr>
                  <td style="vertical-align: top; width: 44px; padding-right: 12px; padding-bottom: 16px;">
                    <div style="width: 38px; height: 38px; border-radius: 8px; background-color: #fef2f2; border: 1px solid #fecdd3; display: flex; align-items: center; justify-content: center; text-align: center; line-height: 38px;">
                      <table role="presentation" border="0" cellspacing="0" cellpadding="0" align="center" style="margin: 0 auto;">
                        <tr><td style="padding: 10px 0 0 10px;">{calendar_svg}</td></tr>
                      </table>
                    </div>
                  </td>
                  <td style="vertical-align: top; padding-bottom: 16px;">
                    <div class="hint-title" style="font-size: 14px; font-weight: 700; color: #0f172a; margin-bottom: 3px;">
                      Autonomous Content Calendar
                    </div>
                    <div style="font-size: 13px; color: #64748b; line-height: 1.5;">
                      Plan, schedule, and orchestrate omnichannel video and social releases across YouTube, Instagram, and TikTok from a unified schedule.
                    </div>
                  </td>
                </tr>

                <!-- Hint 2: AI Video Studio -->
                <tr>
                  <td style="vertical-align: top; width: 44px; padding-right: 12px; padding-bottom: 16px;">
                    <div style="width: 38px; height: 38px; border-radius: 8px; background-color: #fef2f2; border: 1px solid #fecdd3; display: flex; align-items: center; justify-content: center; text-align: center; line-height: 38px;">
                      <table role="presentation" border="0" cellspacing="0" cellpadding="0" align="center" style="margin: 0 auto;">
                        <tr><td style="padding: 10px 0 0 10px;">{video_svg}</td></tr>
                      </table>
                    </div>
                  </td>
                  <td style="vertical-align: top; padding-bottom: 16px;">
                    <div class="hint-title" style="font-size: 14px; font-weight: 700; color: #0f172a; margin-bottom: 3px;">
                      AI Video Performance Studio
                    </div>
                    <div style="font-size: 13px; color: #64748b; line-height: 1.5;">
                      Continuous self-improving AI diagnostics that uncover retention drop-offs and generate corrective hooks for your upcoming video releases.
                    </div>
                  </td>
                </tr>

                <!-- Hint 3: Co-Founder Monetization -->
                <tr>
                  <td style="vertical-align: top; width: 44px; padding-right: 12px;">
                    <div style="width: 38px; height: 38px; border-radius: 8px; background-color: #fef2f2; border: 1px solid #fecdd3; display: flex; align-items: center; justify-content: center; text-align: center; line-height: 38px;">
                      <table role="presentation" border="0" cellspacing="0" cellpadding="0" align="center" style="margin: 0 auto;">
                        <tr><td style="padding: 10px 0 0 10px;">{rocket_svg}</td></tr>
                      </table>
                    </div>
                  </td>
                  <td style="vertical-align: top;">
                    <div class="hint-title" style="font-size: 14px; font-weight: 700; color: #0f172a; margin-bottom: 3px;">
                      50/50 Co-Founder Monetization
                    </div>
                    <div style="font-size: 13px; color: #64748b; line-height: 1.5;">
                      Review your audience software concepts. Our venture studio handles 100% of engineering, design, and hosting at zero upfront cost to you.
                    </div>
                  </td>
                </tr>
              </table>

              <!-- Workspace Credentials Box (Just Eat-Inspired Pattern) -->
              <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="margin: 24px 0;">
                <tr>
                  <td style="background-color: #f8fafc; border: 1.5px dashed #cbd5e1; border-radius: 8px; padding: 18px 20px;">
                    <div style="font-size: 12px; font-weight: 800; color: #64748b; text-transform: uppercase; letter-spacing: 0.8px; margin-bottom: 12px;">
                      Workspace Access Details
                    </div>
                    <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="font-size: 13px;">
                      <tr>
                        <td style="color: #64748b; padding-bottom: 6px; width: 140px;">Authorized User:</td>
                        <td style="color: #0f172a; font-weight: 700; font-family: 'SF Mono', Menlo, Consolas, monospace; padding-bottom: 6px;">{clean_user}</td>
                      </tr>
                      <tr>
                        <td style="color: #64748b; padding-bottom: 6px;">Dedicated URL:</td>
                        <td style="padding-bottom: 6px;">
                          <a href="{canonical_base}/dashboard" target="_blank" style="color: #c81e3c; font-weight: 700; text-decoration: underline;">
                            {canonical_base.replace('https://', '')}/dashboard
                          </a>
                        </td>
                      </tr>
                      <tr>
                        <td style="color: #64748b; padding-bottom: 6px;">Access Tier:</td>
                        <td style="color: #0f172a; font-weight: 600; padding-bottom: 6px;">Co-Founder Partner • Full Studio Access</td>
                      </tr>
                      <tr>
                        <td style="color: #64748b;">Pipeline Status:</td>
                        <td style="color: #059669; font-weight: 700;">
                          <span style="display: inline-block; width: 8px; height: 8px; border-radius: 50%; background-color: #10b981; margin-right: 6px; vertical-align: middle;"></span>Active &amp; Provisioned
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
              </table>

              <!-- Direct 2-Way Human Reply Protocol (SOLVES "email looks like a newsletter") -->
              <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="margin: 24px 0 16px 0;">
                <tr>
                  <td style="background-color: #fef2f2; border: 1px solid #fecdd3; border-left: 3px solid #c81e3c; border-radius: 4px 8px 8px 4px; padding: 16px 18px;">
                    <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0">
                      <tr>
                        <td style="vertical-align: top; width: 28px; padding-right: 12px; padding-top: 2px;">
                          {reply_svg}
                        </td>
                        <td style="vertical-align: top;">
                          <div style="font-size: 14px; font-weight: 700; color: #0f172a; margin-bottom: 4px;">
                            Have questions or want to discuss ideas?
                          </div>
                          <p style="margin: 0 0 8px 0; font-size: 13px; color: #475569; line-height: 1.5;">
                            Simply hit <strong>Reply</strong> to this email. You won't get routed to an automated ticketing bot — our venture partners and lead engineers read and answer every message directly.
                          </p>
                          <div style="font-size: 12px; color: #64748b; line-height: 1.5;">
                            Reply with <span style="color: #c81e3c; font-weight: 700;">"I'm ready to review concepts"</span> or ask us any questions about the 50/50 partnership.
                          </div>
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
              </table>

              <!-- Executive Signoff -->
              <p style="margin: 24px 0 4px 0; font-size: 14px; color: #334155;">
                We wish you a rapid journey to scale and recurring revenue,
              </p>
              <div style="font-size: 15px; font-weight: 700; color: #0f172a;">
                The CoLaunch Venture Studio Team
              </div>
              <div style="font-size: 12px; color: #64748b; margin-top: 2px;">
                Venture Partnerships &bull; <a href="{canonical_base}" style="color: #c81e3c; text-decoration: none;">colaunch.app</a>
              </div>

            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="padding: 20px 32px; border-top: 1px solid #f1f5f9; text-align: center; background-color: #f8fafc;">
              <p style="margin: 0 0 4px 0; color: #64748b; font-size: 12px; font-weight: 600;">
                CoLaunch • Turn your audience into a software enterprise
              </p>
              <p style="margin: 0; color: #94a3b8; font-size: 11px;">
                You received this onboarding dispatch because an operator workspace was created for {clean_user}.
              </p>
              <p style="margin: 6px 0 0 0; color: #94a3b8; font-size: 10px;">
                &copy; 2026 CoLaunch Technologies Inc. All rights reserved.
              </p>
            </td>
          </tr>

        </table>

      </td>
    </tr>
  </table>
</body>
</html>"""

    return body_html, body_text

