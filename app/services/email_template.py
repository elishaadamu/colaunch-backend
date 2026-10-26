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
import markdown

from app.config import settings

STUDIO_NAME: str = getattr(settings, "STUDIO_NAME", "CoLaunch")
STUDIO_TAGLINE: str = getattr(settings, "STUDIO_TAGLINE", "Venture Studio & Co-Launch Incubation")


def _get_frontend_base() -> str:
    """Return the canonical frontend base URL (no trailing slash)."""
    return (
        os.getenv("FRONTEND_URL")
        or getattr(settings, "FRONTEND_URL", "https://creator-forge-frontend.vercel.app")
    ).rstrip("/")


def _render_creator_forge_logo_html(size: int = 28) -> str:
    """
    Renders the official Creator Forge 4-node geometric symbol using pure HTML tables.
    100% email client compatible — zero external image dependency, zero SVG blocking.
    """
    cell_size = max(10, size // 2 - 3)
    return (
        f'<table border="0" cellspacing="2" cellpadding="0" style="display:inline-table;'
        f'width:{size}px;height:{size}px;border-collapse:separate;vertical-align:middle;'
        f'background:#ffffff;padding:2px;border:1px solid #e2e8f0;border-radius:6px;">'
        f'<tr>'
        f'<td style="width:{cell_size}px;height:{cell_size}px;background:#1e293b;border-radius:2px;"></td>'
        f'<td style="width:{cell_size}px;height:{cell_size}px;background:#0284c7;border-radius:2px;"></td>'
        f'</tr>'
        f'<tr>'
        f'<td style="width:{cell_size}px;height:{cell_size}px;background:#16a34a;border-radius:2px;"></td>'
        f'<td style="width:{cell_size}px;height:{cell_size}px;background:#0f172a;border-radius:2px;"></td>'
        f'</tr>'
        f'</table>'
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
    logo_html = _render_creator_forge_logo_html(28)
    contact_email = get_contact_reply_email()
    ref_footnote = ""
    if tracking_token:
        ref_footnote = (
            '<div style="margin-top:10px;font-size:11px;color:#94a3b8;'
            'font-family:ui-monospace,SFMono-Regular,Menlo,monospace;">'
            f'Ref: {html.escape(tracking_token)}</div>'
        )
    return (
        '<div style="margin-top:32px;padding-top:18px;border-top:1px solid #e2e8f0;'
        'font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,Helvetica,Arial,sans-serif;">'
        '<table border="0" cellspacing="0" cellpadding="0"><tr>'
        f'<td style="vertical-align:top;padding-right:12px;">{logo_html}</td>'
        '<td style="vertical-align:top;">'
        '<div style="font-size:14px;font-weight:700;color:#0f172a;line-height:1.3;">Alex Rivera</div>'
        '<div style="font-size:13px;color:#64748b;margin-top:2px;">Head of Venture Partnerships, CoLaunch</div>'
        '<div style="font-size:13px;color:#475569;margin-top:6px;line-height:1.6;">'
        f'<a href="mailto:{contact_email}" style="color:#0f172a;text-decoration:underline;">'
        f'{html.escape(contact_email)}</a>'
        '&nbsp;|&nbsp;'
        '<a href="https://colaunch.app" style="color:#0284c7;text-decoration:none;">colaunch.app</a>'
        '</div>'
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
    No dark UI, no newsletter-style cards. Includes a direct landing page link per concept.
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

    contact_email = get_contact_reply_email()
    token_suffix = f" {tracking_token}" if tracking_token else ""
    mailto_subject = urllib.parse.quote(f"Interested in Concept {index + 1}: {app_name}{token_suffix}")
    mailto_body = urllib.parse.quote(
        f"I would be interested in Concept {index + 1} ({app_name}). "
        f"Let's discuss building and launching this together.{token_suffix}"
    )
    mailto_url = f"mailto:{contact_email}?subject={mailto_subject}&body={mailto_body}"

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

    return (
        '<table width="100%" border="0" cellspacing="0" cellpadding="0" style="margin:0 0 24px 0;">'
        '<tr><td style="padding:18px 20px;background:#f8fafc;border:1px solid #e2e8f0;'
        'border-left:3px solid #0f172a;border-radius:6px;">'
        '<div style="font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;'
        f'letter-spacing:0.8px;margin-bottom:4px;">Concept {index + 1} of {total_concepts}</div>'
        f'<div style="font-size:17px;font-weight:700;color:#0f172a;margin-bottom:4px;">'
        f'{html.escape(app_name)}</div>'
        f'{tagline_html}'
        f'{problem_html}'
        f'{features_html}'
        '<table width="100%" border="0" cellspacing="0" cellpadding="0" style="margin-top:10px;">'
        '<tr><td style="font-size:13px;color:#64748b;">'
        f'Pricing: <strong style="color:#0f172a;">{html.escape(pricing)}</strong>'
        '&nbsp;&nbsp;|&nbsp;&nbsp;'
        f'Opportunity score: <strong style="color:#16a34a;">{score}/100</strong>'
        '</td></tr></table>'
        '<div style="margin-top:12px;padding-top:10px;border-top:1px solid #e2e8f0;">'
        '<div style="font-size:13px;color:#475569;margin-bottom:6px;">Landing page preview:</div>'
        f'<a href="{landing_url}" target="_blank" '
        'style="display:inline-block;font-size:13px;color:#0284c7;text-decoration:underline;word-break:break-all;">'
        f'{html.escape(landing_url)}</a>'
        '</div>'
        '<div style="margin-top:12px;">'
        f'<a href="{mailto_url}" '
        'style="display:inline-block;padding:8px 16px;background:#0f172a;color:#ffffff;'
        f'font-size:13px;font-weight:600;text-decoration:none;border-radius:5px;">'
        f'I am interested in Concept {index + 1}</a>'
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
    Includes a landing page link under each concept.
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

    contact_reply_to = get_contact_reply_email()

    cta_html = (
        '<table width="100%" border="0" cellspacing="0" cellpadding="0" style="margin:8px 0 24px 0;">'
        '<tr><td style="padding:16px 20px;background:#f0fdf4;border:1px solid #bbf7d0;border-radius:6px;">'
        '<div style="font-size:14px;font-weight:700;color:#14532d;margin-bottom:6px;">How to move forward</div>'
        '<p style="margin:0 0 10px 0;font-size:14px;color:#334155;line-height:1.6;">'
        'Under our 50/50 partnership, CoLaunch covers 100% of engineering, hosting, payment infrastructure, '
        'and MVP deployment at zero financial cost to you. To move forward, simply reply to this email with '
        'your preferred concept number.</p>'
        '<div style="font-size:13px;color:#475569;padding:10px 14px;background:#ffffff;'
        'border:1px solid #d1fae5;border-radius:4px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;">'
        'Example reply: "I would be interested in Concept 1"</div>'
        '<div style="margin-top:10px;font-size:13px;color:#64748b;">'
        'Or reply directly to this email at: '
        f'<a href="mailto:{contact_reply_to}" style="color:#0284c7;text-decoration:underline;">'
        f'{html.escape(contact_reply_to)}</a></div>'
        '</td></tr></table>'
    )

    return header_html + cards_html + cta_html


def convert_markdown_to_clean_html(markdown_text: str) -> str:
    """
    Converts markdown text to clean, email-safe HTML with inline CSS.
    Handles headings, bold, italics, links, lists, blockquotes.
    No emojis are injected — the input is rendered as-is.
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

    url_match = re.search(r'(https?://[^\s<"\']+)', clean_md)
    cta_btn_html = ""
    if url_match:
        raw_url = url_match.group(1).rstrip(".,)")
        is_portal = any(k in raw_url.lower() for k in ["portal", "launch", "preview", "project", "review"])
        cta_label = "Access Co-Founder Portal" if is_portal else "Review Software Concepts"
        cta_btn_html = (
            '<div style="margin:20px 0 12px 0;">'
            f'<a href="{raw_url}" target="_blank" '
            'style="display:inline-block;padding:10px 20px;background:#0f172a;color:#ffffff;'
            f'font-size:14px;font-weight:600;text-decoration:none;border-radius:6px;">{cta_label}</a>'
            '<div style="margin-top:6px;font-size:12px;color:#64748b;'
            'font-family:ui-monospace,SFMono-Regular,Menlo,monospace;">'
            f'Direct link: <a href="{raw_url}" style="color:#0284c7;text-decoration:underline;">{raw_url}</a>'
            '</div></div>'
        )

    raw_html = markdown.markdown(
        clean_md,
        extensions=["extra", "sane_lists", "nl2br"]
    )

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
    raw_html = re.sub(
        r'<blockquote>\s*<p>(.*?)</p>\s*</blockquote>',
        r'<blockquote style="margin:14px 0;padding:10px 14px;border-left:3px solid #cbd5e1;'
        r'color:#475569;font-size:14px;line-height:1.6;font-style:italic;">\1</blockquote>',
        raw_html, flags=re.DOTALL
    )
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
    raw_html = re.sub(
        r'<code>(.*?)</code>',
        r'<code style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px;'
        r'background:#f1f5f9;color:#0f172a;border:1px solid #e2e8f0;padding:1px 5px;border-radius:3px;">\1</code>',
        raw_html
    )

    if cta_btn_html and "Access Co-Founder Portal" not in raw_html:
        raw_html = f"{raw_html}\n{cta_btn_html}"

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
    - No heavy card UI, no dark backgrounds, no emojis injected by the formatter
    - Concept blocks are clean bordered sections with direct landing page links
    - Responsive for mobile (max-width 620px)
    - High deliverability: all styles inline, no webfonts, no external images
    """
    clean_body, extracted_token = _clean_and_extract_ref_tokens(body_text)
    active_token = tracking_token or extracted_token

    clean_body = re.sub(r'\bundefined\s+content\b', 'content', clean_body, flags=re.IGNORECASE)
    clean_body = re.sub(r'\bundefined\b', 'community', clean_body, flags=re.IGNORECASE)

    formatted_body_html = convert_markdown_to_clean_html(clean_body)

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
    return (
        f'<!DOCTYPE html><html lang="en"><head>'
        f'<meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width, initial-scale=1.0">'
        f'<title>{esc_subject}</title>'
        f'<style>'
        f'body{{margin:0!important;padding:0!important;background-color:#ffffff!important;'
        f'color:#1e293b!important;font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,Helvetica,Arial,sans-serif!important;'
        f'font-size:15px!important;line-height:1.65!important;-webkit-font-smoothing:antialiased;}}'
        f'p{{margin:0 0 16px 0;color:#1e293b;font-size:15px;line-height:1.65;}}'
        f'a{{color:#0284c7;}}'
        f'@media only screen and (max-width:640px){{'
        f'.email-wrapper{{padding:16px 12px!important;}}'
        f'}}'
        f'</style>'
        f'</head>'
        f'<body style="margin:0;padding:0;background-color:#ffffff;color:#1e293b;'
        f'font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,Helvetica,Arial,sans-serif;'
        f'font-size:15px;line-height:1.65;">'
        f'<div class="email-wrapper" style="max-width:620px;margin:0 auto;padding:28px 24px;background-color:#ffffff;">'
        f'<div style="color:#1e293b;font-size:15px;line-height:1.65;">{formatted_body_html}</div>'
        f'{concept_card_html}'
        f'{signature_html}'
        f'</div></body></html>'
    )
