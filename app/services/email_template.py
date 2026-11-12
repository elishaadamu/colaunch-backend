"""
CoLaunch Venture Studio — Retro Email Templates & Markdown Formatter.

Design direction: 70s space-age travel poster.
  * Sunset stripe band (crimson / tangerine / mustard) on every email
  * Midnight header with a round logo badge, so the logo reads on any background
  * Chunky serif headlines, typewriter labels, plain sans body copy
  * "Launch ticket" cards for concepts, boarding-pass block for account details
  * Chunky offset buttons and thick ink borders instead of soft grey SaaS cards

Provides (public API unchanged):
  - format_luxury_html_email(...)        1-on-1 outreach email (Steps 1-6)
  - render_concept_showcase_html(...)    concept tickets for Step 5 & 6
  - convert_markdown_to_clean_html(...)  markdown -> email-safe inline-styled HTML
  - render_otp_verification_email(...)   -> (html, text)
  - render_welcome_signup_email(...)     -> (html, text)

Logo: by default the hosted PNG is used (works in every client with images on).
Set LOGO_USE_CID=1 if you attach the logo inline with Content-ID "colaunch_logo".
Override the hosted file with LOGO_URL.
"""
import os
import re
import html
from typing import Optional, List, Dict, Any

try:
    import markdown
except ImportError:
    markdown = None

from app.config import settings

STUDIO_NAME: str = getattr(settings, "STUDIO_NAME", "CoLaunch")
STUDIO_TAGLINE: str = getattr(settings, "STUDIO_TAGLINE", "Venture Studio & Co-Launch Incubation")

# ──────────────────────────────────────────────────────────────────────────────
# Design tokens
# ──────────────────────────────────────────────────────────────────────────────
INK = "#1C1B3A"        # midnight
CRIMSON = "#C81E3C"    # brand red
ORANGE = "#F26B21"     # tangerine
MUSTARD = "#F4B731"    # mustard
TEAL = "#1F8A82"       # success / score
PAPER = "#FFF8E8"      # card paper
WHITE = "#FFFFFF"
MUTED = "#5B5878"      # secondary text
RULE = "#D9CFB4"       # soft rule on paper
BODY_TXT = "#2B2A48"

F_HEAD = "'Cooper Black','Bookman Old Style',Rockwell,Georgia,'Times New Roman',serif"
F_BODY = "'Trebuchet MS','Gill Sans',Verdana,Helvetica,Arial,sans-serif"
F_TYPE = "'Courier New',Courier,monospace"

DEFAULT_LOGO_URL = "https://app.co-launch.app/images/colaunch_badge.png"


# ──────────────────────────────────────────────────────────────────────────────
# Config helpers
# ──────────────────────────────────────────────────────────────────────────────
def _get_frontend_base() -> str:
    return (
        os.getenv("FRONTEND_URL")
        or getattr(settings, "FRONTEND_URL", "https://app.co-launch.app")
    ).rstrip("/")


def _get_backend_base() -> str:
    return (
        os.getenv("BACKEND_URL")
        or getattr(settings, "BACKEND_URL", "https://creator-forge-backend-jj1y.onrender.com")
    ).rstrip("/")


def _logo_src() -> str:
    if (os.getenv("LOGO_USE_CID") or "").strip().lower() in ("1", "true", "yes"):
        return "cid:colaunch_logo"
    override = (os.getenv("LOGO_URL") or "").strip()
    if override:
        return override
    return f"{_get_frontend_base()}/images/colaunch_badge.png"


def _sanitize_brand_hex(raw: Any, fallback: str = CRIMSON) -> str:
    val = str(raw or "").strip()
    m = re.match(r"^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$", val)
    if not m:
        return fallback
    h = m.group(1)
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return f"#{h.upper()}"


def _contrast_ink(hex_color: str) -> str:
    h = _sanitize_brand_hex(hex_color).lstrip("#")
    r, g, b = (int(h[i:i+2], 16) / 255.0 for i in (0, 2, 4))
    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    lum = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
    return INK if lum > 0.42 else WHITE


def _concept_slug(name: str, index: int = 0) -> str:
    slug = re.sub(r"[^a-z0-9\-]+", "-", (name or f"concept-{index + 1}").lower()).strip("-")
    return slug or f"concept-{index + 1}"


def get_contact_reply_email() -> str:
    """Active contact / reply address from .env / settings."""
    try:
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


# ──────────────────────────────────────────────────────────────────────────────
# Building blocks
# ──────────────────────────────────────────────────────────────────────────────
def _render_colaunch_logo_html(size: int = 36, show_wordmark: bool = True, is_dark: bool = False) -> str:
    """
    Circular CoLaunch rocket badge PNG plus clean serif wordmark.
    Uses a square circle-cropped badge image with zero outer table borders so
    clients that strip border-radius on <td> cannot draw a square box around it.
    """
    text_color = PAPER if is_dark else INK

    wordmark = ""
    if show_wordmark:
        font_sz = max(20, int(size * 0.72))
        wordmark = (
            f'<td style="vertical-align:middle;padding-left:10px;">'
            f'<span style="font-family:{F_HEAD};font-size:{font_sz}px;font-weight:700;'
            f'color:{text_color};letter-spacing:-0.3px;line-height:1;">CoLaunch</span></td>'
        )

    return (
        '<table role="presentation" border="0" cellspacing="0" cellpadding="0" '
        'style="display:inline-table;vertical-align:middle;"><tr>'
        f'<td width="{size}" height="{size}" align="center" valign="middle" '
        f'style="width:{size}px;height:{size}px;padding:0;text-align:center;vertical-align:middle;">'
        f'<img src="{html.escape(_logo_src())}" alt="CoLaunch" width="{size}" height="{size}" '
        f'style="width:{size}px;height:{size}px;display:block;margin:0;border:0;outline:none;border-radius:50%;" />'
        '</td>'
        f'{wordmark}'
        '</tr></table>'
    )


def _render_creator_forge_logo_html(size: int = 28) -> str:
    """Backward-compatible alias."""
    return _render_colaunch_logo_html(size=size, show_wordmark=False)


def _stripes() -> str:
    """Three-band retro sunset stripe (table rows, works in every client)."""
    rows = "".join(
        f'<tr><td height="{h}" bgcolor="{c}" '
        f'style="height:{h}px;line-height:{h}px;font-size:0;background-color:{c};">&nbsp;</td></tr>'
        for c, h in ((CRIMSON, 8), (ORANGE, 8), (MUSTARD, 8))
    )
    return (
        '<table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0">'
        f'{rows}</table>'
    )


def _button(href: str, label: str, bg: str = CRIMSON, color: str = WHITE) -> str:
    """Chunky retro button: ink outline + heavy bottom edge reads as a raised key."""
    safe_href = html.escape(href, quote=True)
    return (
        '<table role="presentation" border="0" cellspacing="0" cellpadding="0"><tr>'
        f'<td bgcolor="{bg}" style="background-color:{bg};border:2px solid {INK};'
        f'border-bottom:5px solid {INK};border-radius:10px;">'
        f'<a href="{safe_href}" target="_blank" '
        f'style="display:inline-block;padding:12px 24px;font-family:{F_BODY};font-size:15px;'
        f'font-weight:700;color:{color} !important;text-decoration:none;letter-spacing:0.2px;">'
        f'{label}</a></td></tr></table>'
    )


def _tag(text: str, bg: str = MUSTARD, color: str = INK, border: str = INK) -> str:
    """Typewriter-style stamp label."""
    return (
        f'<span style="display:inline-block;font-family:{F_TYPE};font-size:11px;font-weight:700;'
        f'letter-spacing:0.5px;color:{color};background-color:{bg};border:1.5px solid {border};'
        f'padding:3px 9px;border-radius:4px;">{text}</span>'
    )


def _dash_rule(color: str = INK, margin: str = "18px 0") -> str:
    return f'<div style="margin:{margin};border-top:2px dashed {color};font-size:0;line-height:0;height:0;">&nbsp;</div>'


def _round_mark(text: str, bg: str, color: str = INK, size: int = 44, font_size: int = 15) -> str:
    """Round chunky marker used instead of inline SVG (Gmail strips <svg>)."""
    return (
        f'<table role="presentation" border="0" cellspacing="0" cellpadding="0"><tr>'
        f'<td width="{size}" height="{size}" align="center" valign="middle" bgcolor="{bg}" '
        f'style="width:{size}px;height:{size}px;background-color:{bg};border:2px solid {INK};'
        f'border-radius:50%;text-align:center;vertical-align:middle;font-family:{F_HEAD};'
        f'font-size:{font_size}px;font-weight:700;color:{color};line-height:{size}px;">{text}</td>'
        f'</tr></table>'
    )


# Legacy SVG helpers kept so existing imports don't break (Gmail strips inline SVG,
# so the new templates no longer use them).
def _svg_calendar_icon() -> str:
    return _round_mark("A", MUSTARD)


def _svg_video_icon() -> str:
    return _round_mark("B", ORANGE)


def _svg_rocket_icon() -> str:
    return _round_mark("C", CRIMSON, WHITE)


def _svg_reply_icon() -> str:
    return _round_mark("Re", MUSTARD, size=40, font_size=13)


def _svg_clock_icon() -> str:
    return _round_mark("15", MUSTARD, size=34, font_size=12)


def _svg_shield_icon() -> str:
    return _round_mark("!", CRIMSON, WHITE, size=34, font_size=14)


# ──────────────────────────────────────────────────────────────────────────────
# Shared email shell
# ──────────────────────────────────────────────────────────────────────────────
_SHELL_CSS = """
    body { margin:0 !important; padding:0 !important; width:100% !important; -webkit-text-size-adjust:100%; -ms-text-size-adjust:100%; -webkit-font-smoothing:antialiased; }
    table { border-collapse:collapse; mso-table-lspace:0pt; mso-table-rspace:0pt; }
    td { padding:0; }
    img { border:0; line-height:100%; outline:none; text-decoration:none; -ms-interpolation-mode:bicubic; }
    a { color:#C81E3C; }
    @media only screen and (max-width:620px) {
      .canvas { padding:18px 10px 24px 10px !important; }
      .px { padding-left:20px !important; padding-right:20px !important; }
      .hd { padding:16px 20px !important; }
      .h1 { font-size:28px !important; line-height:1.15 !important; }
      .code { font-size:32px !important; letter-spacing:6px !important; }
      .stack { display:block !important; width:100% !important; padding-right:0 !important; text-align:left !important; }
      .stack-gap { padding-top:8px !important; }
      .tag-cell { display:block !important; width:100% !important; text-align:left !important; padding-top:10px !important; }
      .btn-full a { display:block !important; text-align:center !important; }
      .btn-full table { width:100% !important; }
    }
"""


def _email_shell(
    title: str,
    preheader: str,
    tag_label: str,
    inner_html: str,
    footer_html: str,
    max_width: int = 600,
) -> str:
    esc_title = html.escape(title)
    esc_pre = html.escape(preheader)
    # Header: ink band with logo left, stamp right. Wraps on mobile.
    header = (
        f'<tr><td class="hd" bgcolor="{INK}" style="background-color:{INK};padding:18px 28px;'
        f'border-bottom:3px solid {MUSTARD};">'
        '<table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0"><tr>'
        f'<td class="stack" align="left" valign="middle">{_render_colaunch_logo_html(38, True, True)}</td>'
        f'<td class="tag-cell" align="right" valign="middle">{_tag(html.escape(tag_label))}</td>'
        '</tr></table></td></tr>'
    )
    return f"""<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="X-UA-Compatible" content="IE=edge">
  <meta name="color-scheme" content="light only">
  <meta name="supported-color-schemes" content="light only">
  <title>{esc_title}</title>
  <style>{_SHELL_CSS}</style>
</head>
<body style="margin:0;padding:0;background-color:{INK};font-family:{F_BODY};color:{BODY_TXT};">
  <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;mso-hide:all;">{esc_pre}&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;</div>
  <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" bgcolor="{INK}" style="background-color:{INK};">
    <tr><td align="center" bgcolor="{INK}" class="canvas" style="background-color:{INK};padding:36px 14px 40px 14px;">
      <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="max-width:{max_width}px;background-color:{PAPER};border:3px solid {PAPER};border-radius:14px;overflow:hidden;">
        <tr><td>{_stripes()}</td></tr>
        {header}
        <tr><td class="px" bgcolor="{PAPER}" style="background-color:{PAPER};padding:34px 34px 26px 34px;">
          {inner_html}
        </td></tr>
        <tr><td>{_stripes()}</td></tr>
      </table>
      <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="max-width:{max_width}px;">
        <tr><td align="center" style="padding:18px 12px 4px 12px;font-family:{F_TYPE};font-size:11px;line-height:1.7;color:#B9B6D6;">
          {footer_html}
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _standard_footer(extra_line: str = "") -> str:
    extra = f"<br>{extra_line}" if extra_line else ""
    return (
        f'CoLaunch Venture Studio &bull; 50/50 Co-Founder Partnerships &bull; '
        f'<a href="https://colaunch.app" style="color:{MUSTARD};text-decoration:underline;">colaunch.app</a>'
        f'{extra}<br>&copy; 2026 CoLaunch Technologies Inc.'
    )


# ──────────────────────────────────────────────────────────────────────────────
# Signature
# ──────────────────────────────────────────────────────────────────────────────
def _render_executive_signature_html(creator_name: str = "", tracking_token: str = "") -> str:
    """Plain 1-on-1 signature on a dashed rule. Hidden Ref token kept for IMAP reply attribution."""
    logo_html = _render_colaunch_logo_html(size=40, show_wordmark=False)
    contact_email = get_contact_reply_email()
    ref_footnote = ""
    if tracking_token:
        ref_footnote = (
            f'<!-- Ref: {html.escape(tracking_token)} -->'
            '<div style="display:none!important;visibility:hidden;mso-hide:all;font-size:0px;color:transparent;'
            'line-height:0px;max-height:0px;max-width:0px;opacity:0;overflow:hidden;">'
            f'Ref: {html.escape(tracking_token)}</div>'
        )
    return (
        f'<div style="margin-top:32px;padding-top:20px;border-top:2px dashed {INK};font-family:{F_BODY};">'
        '<table role="presentation" border="0" cellspacing="0" cellpadding="0"><tr>'
        f'<td valign="top" style="vertical-align:top;padding-right:14px;">{logo_html}</td>'
        '<td valign="top" style="vertical-align:top;">'
        f'<div style="font-family:{F_HEAD};font-size:17px;font-weight:700;color:{INK};line-height:1.25;">CoLaunch Venture Partnerships</div>'
        f'<div style="font-family:{F_TYPE};font-size:12px;color:{MUTED};margin-top:3px;">Venture Partnerships &bull; CoLaunch Studio</div>'
        f'<div style="font-size:13px;color:{BODY_TXT};margin-top:8px;line-height:1.6;">'
        f'<a href="mailto:{html.escape(contact_email)}" style="color:{CRIMSON};text-decoration:none;font-weight:700;">{html.escape(contact_email)}</a>'
        f'&nbsp;&nbsp;|&nbsp;&nbsp;<a href="https://colaunch.app" style="color:{INK};text-decoration:underline;">colaunch.app</a>'
        '</div>'
        f'<div style="font-size:12px;color:{MUTED};margin-top:4px;">Reply directly to this email to reach our studio partners.</div>'
        f'{ref_footnote}'
        '</td></tr></table></div>'
    )


def _clean_and_extract_ref_tokens(raw_text: str) -> tuple:
    """
    Extract internal tracking tokens like 'Ref: [CF-STAGE:... | CF-CID:...]'
    so they don't clutter the body or break markdown parsing.
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


# ──────────────────────────────────────────────────────────────────────────────
# Concepts (Step 5 & 6)
# ──────────────────────────────────────────────────────────────────────────────
def _build_concept_landing_url(concept: Dict[str, Any], creator_name: str = "", index: int = 0) -> str:
    """Public landing-page URL for a concept (explicit URL > appUrl > portal preview)."""
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
    if explicit_url and explicit_url.startswith("/"):
        return f"{frontend_base}{explicit_url}"

    app_url = (concept.get("appUrl") or mockup.get("appUrl") or "").strip()

    if app_url and "." in app_url and not app_url.startswith("http"):
        return f"https://{app_url}"
    if app_url and app_url.startswith("http"):
        return app_url

    c_slug = _concept_slug(str(concept.get("name") or concept.get("title") or ""), index)
    creator_slug = re.sub(r"[^a-z0-9\-]", "", (creator_name or "creator").lower().replace(" ", "-")) or "creator"
    return f"{frontend_base}/preorder/{creator_slug}?concept={c_slug}"


def _build_concept_card_image_url(
    concept: Dict[str, Any],
    accent_hex: str,
    features: List[str],
) -> str:
    """Build deterministic backend concept preview PNG URL with real features & branding."""
    from urllib.parse import urlencode

    mockup = concept.get("mockup") if isinstance(concept.get("mockup"), dict) else {}
    raw_img = (
        concept.get("customImageUrl")
        or concept.get("imageUrl")
        or concept.get("image_url")
        or concept.get("mockup_url")
        or mockup.get("imageUrl")
        or mockup.get("previewUrl")
        or ""
    )
    backend_base = _get_backend_base()
    if "localhost" in backend_base or "127.0.0.1" in backend_base:
        public_backend = os.getenv("PUBLIC_BACKEND_URL") or "https://creator-forge-backend-jj1y.onrender.com"
    else:
        public_backend = backend_base

    if isinstance(raw_img, str) and raw_img.strip():
        clean = raw_img.strip()
        if clean.startswith("/api/"):
            return f"{public_backend}{clean}"
        if clean.startswith("/"):
            return f"{_get_frontend_base()}{clean}"
        if clean.startswith("http"):
            # If frontend accidentally prefixed /api/outreach/concept-card-image with Vercel origin, rewrite to backend
            if "/api/outreach/concept-card-image" in clean:
                path_part = clean[clean.index("/api/outreach/concept-card-image"):]
                return f"{public_backend}{path_part}"
            return clean

    app_name = str(concept.get("name") or concept.get("title") or "Software Concept").strip()
    app_url = str(concept.get("appUrl") or mockup.get("appUrl") or f"{_concept_slug(app_name)}.app").strip()
    params = {
        "name": app_name,
        "app_url": app_url,
        "tagline": str(concept.get("tagline") or concept.get("summary") or "")[:90],
        "features": "|".join(str(f).strip() for f in features[:4] if str(f).strip()),
        "customer": str(concept.get("customer") or concept.get("demographicAlignment") or "Creator Community")[:60],
        "pricing": str(concept.get("pricing") or "$29/mo Starter • $79/mo Pro")[:50],
        "brand_color": accent_hex,
        "mockup_type": str(concept.get("mockupType") or "saas_os"),
    }
    return f"{public_backend}/api/outreach/concept-card-image?{urlencode(params)}"


def _render_single_concept_block(
    concept: Dict[str, Any],
    index: int = 0,
    total_concepts: int = 1,
    creator_name: str = "",
    tracking_token: str = ""
) -> str:
    """Rich concept card styled around the concept's brand color and full product spec."""
    from urllib.parse import quote

    default_cycle = (CRIMSON, "#10B981", "#06B6D4")
    accent_hex = _sanitize_brand_hex(
        concept.get("brandColor") or concept.get("brand_color") or concept.get("accentColor"),
        fallback=default_cycle[index % len(default_cycle)],
    )
    accent_ink = _contrast_ink(accent_hex)

    app_name = str(concept.get("name") or concept.get("title") or f"Software Concept {index + 1}").strip()
    tagline = str(concept.get("tagline") or concept.get("summary") or "").strip()
    problem = str(concept.get("problem") or concept.get("description") or "").strip()
    score = concept.get("opportunityScore") or concept.get("score") or (98 - index * 3)
    target_audience = str(concept.get("customer") or concept.get("demographicAlignment") or "").strip()
    audience_evidence = str(concept.get("audienceEvidence") or "").strip()
    mvp_difficulty = str(concept.get("mvpDifficulty") or "2 weeks to MVP").strip()
    revenue_model = str(concept.get("revenueModel") or "50/50 Co-Founder Revenue Share").strip()

    raw_pricing = concept.get("pricing") or concept.get("revenueModel")
    if isinstance(raw_pricing, dict):
        pricing_parts = [str(v) for v in raw_pricing.values() if v]
        pricing = " / ".join(pricing_parts) if pricing_parts else "$29/mo"
    elif isinstance(raw_pricing, str) and raw_pricing.strip():
        pricing = raw_pricing.strip()
    else:
        pricing = "$29/mo Starter • $79/mo Pro"

    raw_features = concept.get("keyFeatures") or concept.get("features") or concept.get("coreFeatures") or []
    key_features = [str(f).strip() for f in raw_features if str(f).strip()][:4] if isinstance(raw_features, list) else []

    landing_url = _build_concept_landing_url(concept, creator_name, index)
    concept_img = _build_concept_card_image_url(concept, accent_hex, key_features)

    contact_email = get_contact_reply_email()
    mailto_subj = quote(f"Interested in Concept {index + 1}: {app_name}")
    mailto_body = quote(f"Hi CoLaunch team,\n\nI'd like to explore Concept {index + 1} ({app_name}) and review next steps.")
    mailto_href = f"mailto:{contact_email}?subject={mailto_subj}&body={mailto_body}"

    tagline_html = (
        f'<div style="font-family:{F_BODY};font-size:15px;color:#CBD5E1;margin-top:6px;line-height:1.45;">'
        f'{html.escape(tagline)}</div>'
        if tagline else ""
    )

    img_html = ""
    if concept_img:
        img_html = (
            '<div style="margin:0 0 18px 0;">'
            f'<a href="{html.escape(landing_url, quote=True)}" target="_blank" style="text-decoration:none;display:block;">'
            f'<img src="{html.escape(concept_img)}" alt="{html.escape(app_name)} preview" width="520" '
            f'style="width:100%;max-width:520px;height:auto;border-radius:10px;border:2px solid {INK};display:block;" />'
            '</a></div>'
        )

    problem_html = ""
    if problem:
        problem_html = (
            f'<div style="margin:0 0 14px 0;padding:12px 14px;background-color:{PAPER};border-left:4px solid {accent_hex};border-radius:6px;">'
            f'<div style="font-family:{F_TYPE};font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.6px;color:{MUTED};margin-bottom:3px;">Problem it solves</div>'
            f'<div style="color:{BODY_TXT};font-size:14px;line-height:1.55;">{html.escape(problem)}</div>'
            '</div>'
        )

    features_html = ""
    if key_features:
        items = "".join(
            '<tr>'
            f'<td width="16" valign="top" style="width:16px;padding:3px 8px 6px 0;color:{accent_hex};font-weight:700;font-size:14px;line-height:1.4;">&#9679;</td>'
            f'<td valign="top" style="padding:0 0 6px 0;color:{BODY_TXT};font-size:14px;line-height:1.5;">{html.escape(f)}</td>'
            '</tr>'
            for f in key_features
        )
        features_html = (
            '<div style="margin:0 0 14px 0;">'
            f'<div style="font-family:{F_TYPE};font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.6px;color:{MUTED};margin-bottom:6px;">What you get inside</div>'
            f'<table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0">{items}</table>'
            '</div>'
        )

    audience_html = ""
    if target_audience or audience_evidence:
        aud_rows = []
        if target_audience:
            aud_rows.append(
                f'<div style="font-size:13px;color:{BODY_TXT};line-height:1.5;margin-bottom:4px;">'
                f'<strong style="color:{INK};">Built for:</strong> {html.escape(target_audience)}</div>'
            )
        if audience_evidence:
            aud_rows.append(
                f'<div style="font-size:13px;color:{MUTED};line-height:1.5;">'
                f'<strong style="color:{INK};">Audience signal:</strong> {html.escape(audience_evidence)}</div>'
            )
        audience_html = (
            f'<div style="margin:0 0 14px 0;padding:10px 12px;background-color:#F8FAFC;border:1px solid {RULE};border-radius:8px;">'
            + "".join(aud_rows)
            + '</div>'
        )

    stats = (
        f'<table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" '
        f'style="margin:6px 0 0 0;background-color:{PAPER};border:1.5px solid {RULE};border-radius:8px;"><tr>'
        f'<td class="stack" valign="top" style="padding:10px 12px;">'
        f'<div style="font-family:{F_TYPE};font-size:10px;text-transform:uppercase;color:{MUTED};">Pricing</div>'
        f'<div style="font-size:13px;font-weight:700;color:{INK};margin-top:3px;">{html.escape(pricing)}</div></td>'
        f'<td class="stack stack-gap" valign="top" style="padding:10px 12px;">'
        f'<div style="font-family:{F_TYPE};font-size:10px;text-transform:uppercase;color:{MUTED};">Timeline &amp; split</div>'
        f'<div style="font-size:13px;font-weight:700;color:{INK};margin-top:3px;">{html.escape(mvp_difficulty)} &bull; 50/50 split</div></td>'
        f'<td class="stack stack-gap" valign="top" align="right" style="padding:10px 12px;text-align:right;">'
        f'<div style="font-family:{F_TYPE};font-size:10px;text-transform:uppercase;color:{MUTED};">Audience fit</div>'
        f'<div style="font-family:{F_HEAD};font-size:19px;font-weight:700;color:{TEAL};margin-top:2px;">{score}/100</div></td>'
        '</tr></table>'
    )

    return (
        f'<table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" '
        f'style="margin:0 0 26px 0;border:3px solid {INK};border-radius:12px;overflow:hidden;background-color:{WHITE};">'
        # top color strip
        f'<tr><td height="6" bgcolor="{accent_hex}" style="height:6px;line-height:6px;font-size:0;background-color:{accent_hex};">&nbsp;</td></tr>'
        # dark header matching ConceptLandingPage aesthetic
        f'<tr><td bgcolor="#10131A" style="background-color:#10131A;padding:18px 20px;border-bottom:3px dashed {accent_hex};">'
        '<table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0"><tr>'
        f'<td align="left" valign="middle">{_tag(f"Concept {index + 1} of {total_concepts}", bg=accent_hex, color=accent_ink, border=accent_hex)}</td>'
        f'<td align="right" valign="middle" style="font-family:{F_TYPE};font-size:11px;font-weight:700;color:#6FCF9B;">Fit {score}/100</td>'
        '</tr></table>'
        f'<div style="font-family:{F_HEAD};font-size:24px;font-weight:700;color:{WHITE};margin-top:10px;line-height:1.2;">'
        f'{html.escape(app_name)}</div>'
        f'{tagline_html}'
        '</td></tr>'
        # body
        f'<tr><td style="padding:18px 20px 20px 20px;background-color:{WHITE};">'
        f'{img_html}{problem_html}{features_html}{audience_html}{stats}'
        f'{_dash_rule(RULE, "16px 0 16px 0")}'
        '<table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0"><tr>'
        f'<td class="stack btn-full" valign="middle">{_button(landing_url, "View live landing page &rarr;", bg=accent_hex, color=accent_ink)}</td>'
        f'<td class="stack stack-gap" align="right" valign="middle" style="text-align:right;">'
        f'<a href="{html.escape(mailto_href, quote=True)}" style="font-family:{F_BODY};font-size:13px;font-weight:700;color:{INK};text-decoration:underline;">'
        f'Select Concept {index + 1} &rarr;</a></td>'
        '</tr></table>'
        f'<div style="font-size:12px;color:{MUTED};margin-top:12px;word-break:break-all;line-height:1.5;">'
        f'Landing page: <a href="{html.escape(landing_url, quote=True)}" target="_blank" '
        f'style="color:{CRIMSON};text-decoration:underline;">{html.escape(landing_url)}</a></div>'
        '</td></tr></table>'
    )


def render_concept_showcase_html(
    concepts: Optional[List[Dict[str, Any]]] = None,
    concept_image_url: Optional[str] = None,
    creator_name: str = "",
    tracking_token: str = "",
    include_closing_cta: bool = True,
) -> str:
    """Concept section for Step 5 & 6: header, up to 3 concept tickets, optional closing note."""
    if not concepts and not concept_image_url:
        return ""

    concept_list = concepts if (concepts and len(concepts) > 0) else [{}]
    concept_list = concept_list[:3]
    total_count = len(concept_list)
    plural = "" if total_count == 1 else "s"

    header_html = (
        f'<div style="margin:26px 0 18px 0;padding:14px 18px;background-color:{MUSTARD};'
        f'border:3px solid {INK};border-radius:10px;">'
        f'<div style="font-family:{F_HEAD};font-size:20px;font-weight:700;color:{INK};line-height:1.25;">'
        'Software Concepts Proposed for Your Audience</div>'
        f'<div style="font-size:13px;color:{INK};margin-top:5px;line-height:1.5;">'
        f'Below are the top {total_count} custom software concept{plural} designed for your community '
        '— each includes full details and a live branded landing page to review.</div>'
        '</div>'
    )

    cards_html = "".join(
        _render_single_concept_block(c, idx, total_count, creator_name, tracking_token)
        for idx, c in enumerate(concept_list)
    )

    cta_html = ""
    if include_closing_cta:
        cta_html = (
            f'<div style="margin:20px 0 8px 0;font-size:14px;color:{BODY_TXT};line-height:1.65;">'
            'Under our 50/50 partnership, our engineering team handles 100% of product development, '
            'cloud hosting, billing infrastructure, and ongoing technical support with zero upfront cost to you.<br><br>'
            'To discuss these concepts, refine any direction, or explore an idea of your own, simply reply directly to this email.'
            '</div>'
        )

    return header_html + cards_html + cta_html


# ──────────────────────────────────────────────────────────────────────────────
# Markdown -> email-safe HTML
# ──────────────────────────────────────────────────────────────────────────────
def convert_markdown_to_clean_html(markdown_text: str) -> str:
    """
    Markdown -> inline-styled, email-safe HTML in the retro palette.
    Handles headings, bold, italics, links, lists, blockquotes, images, landing-page
    CTAs, the 3-point venture pillars and the reply-option stamps.
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
        raw_html = markdown.markdown(clean_md, extensions=["extra", "sane_lists", "nl2br"])
    else:
        paragraphs = clean_md.split("\n\n")
        raw_html = "".join(f"<p>{html.escape(p).replace(chr(10), '<br>')}</p>" for p in paragraphs if p.strip())

    raw_html = re.sub(
        r'<p>(.*?)</p>',
        rf'<p style="margin:0 0 16px 0;line-height:1.7;color:{BODY_TXT};font-size:15px;">\1</p>',
        raw_html, flags=re.DOTALL
    )
    for tag, size, mt in (("h1", 26, 24), ("h2", 21, 22), ("h3", 17, 18)):
        raw_html = re.sub(
            rf'<{tag}>(.*?)</{tag}>',
            rf'<{tag} style="margin:{mt}px 0 10px 0;font-family:{F_HEAD};font-size:{size}px;font-weight:700;'
            rf'color:{INK};line-height:1.2;">\1</{tag}>',
            raw_html
        )
    raw_html = re.sub(
        r'<strong>(.*?)</strong>',
        rf'<strong style="color:{INK};font-weight:700;">\1</strong>',
        raw_html
    )
    raw_html = re.sub(
        r'<em>(.*?)</em>',
        rf'<em style="color:{MUTED};font-style:italic;">\1</em>',
        raw_html
    )
    raw_html = re.sub(
        r'<ul>',
        rf'<ul style="margin:10px 0 16px 0;padding-left:22px;list-style-type:square;color:{BODY_TXT};">',
        raw_html
    )
    raw_html = re.sub(
        r'<ol>',
        rf'<ol style="margin:10px 0 16px 0;padding-left:24px;color:{BODY_TXT};">',
        raw_html
    )
    raw_html = re.sub(
        r'<li>(.*?)</li>',
        rf'<li style="margin-bottom:6px;line-height:1.65;color:{BODY_TXT};font-size:15px;">\1</li>',
        raw_html, flags=re.DOTALL
    )

    # Blockquotes become mustard "studio notes"
    def _style_blockquote(match):
        inner = re.sub(r'</?p[^>]*>', '', match.group(1).strip()).strip()
        return (
            f'<div style="margin:18px 0;padding:14px 18px;background-color:{WHITE};border:2px solid {INK};'
            f'border-left:8px solid {MUSTARD};border-radius:8px;color:{BODY_TXT};font-size:14px;line-height:1.65;">'
            f'{inner}</div>'
        )

    raw_html = re.sub(r'<blockquote>(.*?)</blockquote>', _style_blockquote, raw_html, flags=re.DOTALL)

    def _style_img_tag(match):
        attrs = match.group(1)
        src_m = re.search(r'src="([^"]+)"', attrs)
        alt_m = re.search(r'alt="([^"]+)"', attrs)
        src = src_m.group(1) if src_m else ""
        alt = alt_m.group(1) if alt_m else "Concept preview"
        if src.startswith("/"):
            src = f"{_get_frontend_base()}{src}"
        return (
            '<div style="margin:16px 0 20px 0;">'
            f'<img src="{html.escape(src)}" alt="{html.escape(alt)}" width="520" '
            f'style="width:100%;max-width:520px;height:auto;border-radius:8px;border:2px solid {INK};display:block;" />'
            '</div>'
        )

    raw_html = re.sub(r'<img\s+([^>]+)/?>', _style_img_tag, raw_html)

    raw_html = re.sub(
        r'<hr\s*/?>',
        f'<div style="margin:22px 0;border-top:2px dashed {INK};font-size:0;line-height:0;height:0;">&nbsp;</div>',
        raw_html
    )
    raw_html = re.sub(
        r'<a\s+href="([^"]+)">([^<]+)</a>',
        rf'<a href="\1" target="_blank" style="color:{CRIMSON};text-decoration:underline;font-weight:700;">\2</a>',
        raw_html
    )

    # "Landing Page: <link>" lines become a ticket-style CTA (wrapper tag regex now tolerates inline styles)
    def _style_landing_page_cta(m):
        url = m.group(1)
        return (
            f'<div style="margin:14px 0 18px 0;padding:14px 16px;background-color:{WHITE};border:2px solid {INK};border-radius:10px;">'
            f'<div style="font-family:{F_TYPE};font-size:11px;font-weight:700;color:{MUTED};margin-bottom:8px;">Interactive concept preview</div>'
            f'{_button(url, "Review live concept &amp; landing page")}'
            f'<div style="font-size:12px;color:{MUTED};word-break:break-all;margin-top:10px;">Direct link: '
            f'<a href="{url}" target="_blank" style="color:{CRIMSON};text-decoration:underline;">{url}</a></div>'
            '</div>'
        )

    raw_html = re.sub(
        r'(?:<p[^>]*>|<li[^>]*>)?(?:•\s*)?(?:Live\s+)?Landing\s+Page:\s*<a\s+href="([^"]+)"[^>]*>(.*?)</a>(?:</p>|</li>)?',
        _style_landing_page_cta,
        raw_html,
        flags=re.IGNORECASE
    )

    # 3-point venture pillars
    def _pillar(mark: str, mark_bg: str, mark_color: str, title: str, desc: str, last: bool = False) -> str:
        divider = "" if last else f'<tr><td colspan="2" style="border-top:2px dashed {RULE};font-size:0;line-height:0;height:12px;">&nbsp;</td></tr>'
        return (
            '<tr>'
            f'<td width="64" valign="top" style="width:64px;padding:0 12px 0 0;">{_round_mark(mark, mark_bg, mark_color, 52, 15)}</td>'
            '<td valign="top">'
            f'<div style="font-family:{F_HEAD};font-size:16px;font-weight:700;color:{INK};line-height:1.25;">{title}</div>'
            f'<div style="font-size:13px;color:{BODY_TXT};line-height:1.55;margin-top:3px;">{desc}</div>'
            '</td></tr>'
            f'{divider}'
        )

    def _style_venture_pillars(_match):
        return (
            f'<div style="margin:22px 0 26px 0;background-color:{WHITE};border:3px solid {INK};border-radius:12px;padding:18px 18px 6px 18px;">'
            '<table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0">'
            + _pillar("$0", MUSTARD, INK, "Zero Financial or Engineering Cost",
                      "Our studio covers 100% of software engineering, UI/UX design, cloud hosting, and continuous technical support.")
            + _pillar("50/50", CRIMSON, WHITE, "50/50 Co-Founder Partnership",
                      "We build real, recurring monthly software apps ($9–$29/mo) and split all net platform profits 50/50.")
            + _pillar("3x", TEAL, WHITE, "3 Custom Software Prototypes Ready",
                      "Our team has architected 3 custom software product concepts designed specifically for your community.",
                      last=True)
            + '<tr><td colspan="2" style="height:12px;font-size:0;line-height:0;">&nbsp;</td></tr>'
            '</table></div>'
        )

    pillars_pattern = re.compile(
        r'<p[^>]*>I head venture partnerships at CoLaunch\..*?recurring software revenue stream\.</p>',
        re.IGNORECASE | re.DOTALL
    )
    raw_html = pillars_pattern.sub(_style_venture_pillars, raw_html)

    # Reply options as two stamps
    def _style_reply_options(_match):
        return (
            f'<div style="margin:26px 0 22px 0;background-color:{MUSTARD};border:3px solid {INK};border-radius:12px;padding:18px 20px;">'
            f'<div style="font-family:{F_HEAD};font-size:17px;font-weight:700;color:{INK};margin-bottom:14px;line-height:1.25;">'
            'To reply, simply respond directly to this email:</div>'
            '<table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="margin-bottom:12px;"><tr>'
            f'<td class="stack" valign="middle" style="padding-right:12px;">'
            f'<span style="display:inline-block;padding:7px 14px;background-color:{TEAL};color:{WHITE};border:2px solid {INK};'
            f'border-radius:8px;font-weight:700;font-size:13px;">&ldquo;I am interested, let&#39;s proceed&rdquo;</span></td>'
            f'<td class="stack stack-gap" valign="middle" style="font-size:13px;color:{INK};line-height:1.5;">'
            'We will send over the concepts, revenue roadmap, and live previews.</td>'
            '</tr></table>'
            '<table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0"><tr>'
            f'<td class="stack" valign="middle" style="padding-right:12px;">'
            f'<span style="display:inline-block;padding:7px 14px;background-color:{INK};color:{WHITE};border:2px solid {INK};'
            f'border-radius:8px;font-weight:700;font-size:13px;">&ldquo;Not interested&rdquo;</span></td>'
            f'<td class="stack stack-gap" valign="middle" style="font-size:13px;color:{INK};line-height:1.5;">'
            'We will immediately close your file with zero follow-ups.</td>'
            '</tr></table></div>'
        )

    reply_pattern = re.compile(
        r'<p[^>]*>To reply, simply respond directly to this email:<\/p>\s*<ul[^>]*>\s*<li[^>]*>(?:•|\*|\-)?\s*(?:&quot;|\"|&ldquo;)?I am interested.*?<\/li>\s*<li[^>]*>(?:•|\*|\-)?\s*(?:Or\s+)?(?:&quot;|\"|&ldquo;)?Not interested.*?<\/li>\s*<\/ul>',
        re.IGNORECASE | re.DOTALL
    )
    raw_html = reply_pattern.sub(_style_reply_options, raw_html)

    reply_pattern_p = re.compile(
        r'<p[^>]*>To reply, simply respond directly to this email:<br\s*\/?>\s*(?:•|\*|\-)?\s*(?:&quot;|\"|&ldquo;)?I am interested.*?<br\s*\/?>\s*(?:•|\*|\-)?\s*(?:Or\s+)?(?:&quot;|\"|&ldquo;)?Not interested.*?<\/p>',
        re.IGNORECASE | re.DOTALL
    )
    raw_html = reply_pattern_p.sub(_style_reply_options, raw_html)

    reply_pattern_two_p = re.compile(
        r'<p[^>]*>To reply, simply respond directly to this email:<\/p>\s*<p[^>]*>(?:•|\*|\-)?\s*(?:&quot;|\"|&ldquo;)?I am interested.*?<br\s*\/?>\s*(?:•|\*|\-)?\s*(?:Or\s+)?(?:&quot;|\"|&ldquo;)?Not interested.*?<\/p>',
        re.IGNORECASE | re.DOTALL
    )
    raw_html = reply_pattern_two_p.sub(_style_reply_options, raw_html)

    raw_html = re.sub(
        r'<code>(.*?)</code>',
        rf'<code style="font-family:{F_TYPE};font-size:13px;background-color:{WHITE};color:{INK};'
        rf'border:1px solid {RULE};padding:1px 5px;border-radius:3px;">\1</code>',
        raw_html
    )

    return raw_html


# ──────────────────────────────────────────────────────────────────────────────
# 1) Outreach email
# ──────────────────────────────────────────────────────────────────────────────
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
    1-on-1 outreach email in the retro studio look.
    Personal copy sits on warm paper under a sunset-stripe header; concepts render as tickets.
    """
    clean_body, extracted_token = _clean_and_extract_ref_tokens(body_text)
    active_token = tracking_token or extracted_token

    clean_body = re.sub(r'\bundefined\s+content\b', 'content', clean_body, flags=re.IGNORECASE)
    clean_body = re.sub(r'\bundefined\b', 'community', clean_body, flags=re.IGNORECASE)

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

    has_explicit_concepts = bool(active_concepts and len(active_concepts) > 0)
    inline_concept_pattern = re.compile(
        r'(?:^|\n)(?:1\.\s+(?:\*\*|[A-Z])|#{1,3}\s*Concept\s*1\b)[\s\S]*?(?=\n(?:Under our 50/50|>\s*\*\*100%\s*Flexible|Let me know|Please review|Best regards)|\Z)',
        re.IGNORECASE,
    )
    has_closing_copy = bool(
        re.search(r'(?:Under our 50/50|100% Flexible|Let me know|Best regards)', clean_body, re.IGNORECASE)
    )

    slot_token = "[[COLAUNCH_CONCEPT_CARDS_SLOT]]"
    slotted_in_body = False
    body_for_html = clean_body
    if has_explicit_concepts and inline_concept_pattern.search(clean_body):
        body_for_html = inline_concept_pattern.sub(f"\n\n{slot_token}\n\n", clean_body, count=1)
        slotted_in_body = slot_token in body_for_html

    formatted_body_html = convert_markdown_to_clean_html(body_for_html)

    has_concepts_in_body = bool(
        re.search(
            r'(?:1\.\s+\*\*|1\.\s+[A-Z]|landing page:|concept 1|what it is:|opportunity score)',
            clean_body,
            re.IGNORECASE
        )
    )

    concept_card_html = ""
    if has_explicit_concepts or not has_concepts_in_body:
        concept_card_html = render_concept_showcase_html(
            concepts=active_concepts,
            concept_image_url=concept_image_url,
            creator_name=creator_name,
            tracking_token=active_token,
            include_closing_cta=not has_closing_copy,
        )

    if slotted_in_body and concept_card_html:
        formatted_body_html = re.sub(
            rf'(?:<p[^>]*>\s*)?{re.escape(slot_token)}(?:\s*</p>)?',
            lambda _m: concept_card_html,
            formatted_body_html,
            count=1,
        )
        concept_card_html = ""

    signature_html = _render_executive_signature_html(creator_name, active_token)

    inner = (
        f'<div style="color:{BODY_TXT};font-size:15px;line-height:1.7;">{formatted_body_html}</div>'
        f'{concept_card_html}{signature_html}'
    )

    return _email_shell(
        title=subject,
        preheader=subject,
        tag_label="Venture partnership",
        inner_html=inner,
        footer_html=_standard_footer(),
        max_width=620,
    )


# ──────────────────────────────────────────────────────────────────────────────
# 2) OTP verification email
# ──────────────────────────────────────────────────────────────────────────────
def render_otp_verification_email(
    code: str,
    display_name: str = "Creator",
    base_url: str = "https://app.co-launch.app"
) -> tuple:
    """Single-use verification code as a boarding-pass stub. Returns (body_html, body_text)."""
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

    inner = f"""
          <h1 class="h1" style="margin:0 0 12px 0;font-family:{F_HEAD};font-size:30px;font-weight:700;color:{INK};line-height:1.15;letter-spacing:-0.3px;">
            Single-Use Verification Code
          </h1>
          <p style="margin:0 0 22px 0;color:{BODY_TXT};font-size:15px;line-height:1.65;">
            Hello <strong style="color:{INK};">{clean_name}</strong>, use the secure 6-digit verification code below to authenticate your CoLaunch operator workspace.
          </p>

          <!-- Code ticket -->
          <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="margin:0 0 22px 0;border:3px solid {INK};border-radius:12px;overflow:hidden;background-color:{WHITE};">
            <tr><td align="center" style="padding:24px 14px 18px 14px;">
              <div class="code" style="font-family:{F_TYPE};font-size:42px;font-weight:700;letter-spacing:10px;color:{INK};line-height:1.1;">{clean_code}</div>
            </td></tr>
            <tr><td align="center" bgcolor="{INK}" style="background-color:{INK};border-top:3px dashed {MUSTARD};padding:10px 14px;font-family:{F_TYPE};font-size:12px;color:{PAPER};">
              Enter this 6-digit code in the verification prompt
            </td></tr>
          </table>

          <!-- Rules -->
          <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="margin:0 0 24px 0;">
            <tr>
              <td width="52" valign="top" style="width:52px;padding:0 12px 14px 0;">{_round_mark("15", MUSTARD, INK, 40, 13)}</td>
              <td valign="middle" style="padding-bottom:14px;font-size:13px;color:{BODY_TXT};line-height:1.55;">
                <strong style="color:{INK};">Valid for 15 minutes.</strong> This code expires automatically for your security.
              </td>
            </tr>
            <tr>
              <td width="52" valign="top" style="width:52px;padding:0 12px 0 0;">{_round_mark("!", CRIMSON, WHITE, 40, 16)}</td>
              <td valign="middle" style="font-size:13px;color:{BODY_TXT};line-height:1.55;">
                <strong style="color:{INK};">Security alert:</strong> Never share this code. CoLaunch staff will never request your code.
              </td>
            </tr>
          </table>

          <div class="btn-full">{_button(canonical_base, "Open CoLaunch workspace")}</div>

          <p style="margin:24px 0 0 0;color:{MUTED};font-size:12px;line-height:1.6;">
            If you did not request this verification code, someone may have entered your email by mistake. No action is required.
          </p>
    """

    body_html = _email_shell(
        title="Your CoLaunch Verification Code",
        preheader=f"Your CoLaunch code is {code}. It expires in 15 minutes.",
        tag_label="Security passcode",
        inner_html=inner,
        footer_html=_standard_footer(
            f'<a href="{canonical_base}" style="color:{MUSTARD};text-decoration:underline;">'
            f'{canonical_base.replace("https://", "")}</a> &bull; Dedicated Creator Workspace'
        ),
        max_width=540,
    )
    return body_html, body_text


# ──────────────────────────────────────────────────────────────────────────────
# 3) Welcome / onboarding email
# ──────────────────────────────────────────────────────────────────────────────
def render_welcome_signup_email(
    username: str,
    base_url: str = "https://app.co-launch.app"
) -> tuple:
    """Account-created onboarding email. Returns (body_html, body_text)."""
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

    def hint(mark, bg, color, title, desc, last=False):
        pad = "0" if last else "0 0 18px 0"
        return (
            '<tr>'
            f'<td width="58" valign="top" style="width:58px;padding:0 14px {"0" if last else "18px"} 0;">{_round_mark(mark, bg, color, 46, 17)}</td>'
            f'<td valign="top" style="padding:{pad};">'
            f'<div style="font-family:{F_HEAD};font-size:17px;font-weight:700;color:{INK};line-height:1.25;">{title}</div>'
            f'<div style="font-size:13px;color:{BODY_TXT};line-height:1.6;margin-top:3px;">{desc}</div>'
            '</td></tr>'
        )

    def pass_row(label, value_html, last=False):
        border = "" if last else f"border-bottom:1px dashed {RULE};"
        return (
            '<tr>'
            f'<td class="stack" valign="top" style="width:140px;padding:9px 8px 9px 0;{border}font-family:{F_TYPE};font-size:12px;color:{MUTED};">{label}</td>'
            f'<td class="stack" valign="top" style="padding:9px 0;{border}font-size:13px;color:{INK};font-weight:700;">{value_html}</td>'
            '</tr>'
        )

    inner = f"""
          <h1 class="h1" style="margin:0 0 14px 0;font-family:{F_HEAD};font-size:34px;font-weight:700;color:{INK};line-height:1.12;letter-spacing:-0.5px;">
            You're ready to build &amp; co-launch
          </h1>
          <p style="margin:0 0 24px 0;font-size:15px;color:{BODY_TXT};line-height:1.7;">
            Hello <strong style="color:{INK};">{clean_user}</strong>, welcome to <strong style="color:{INK};">CoLaunch</strong>! Your private creator software console is fully provisioned. We partner 50/50 with you to turn your audience attention into a defensible, high-margin software enterprise.
          </p>

          <div class="btn-full" style="margin:0 0 30px 0;">{_button(canonical_base + "/dashboard", "Launch operator console")}</div>

          {_dash_rule(INK, "0 0 26px 0")}

          <div style="font-family:{F_HEAD};font-size:22px;font-weight:700;color:{INK};margin:0 0 18px 0;line-height:1.2;">
            Three hints on how to use CoLaunch
          </div>

          <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="margin:0 0 30px 0;">
            {hint("1", MUSTARD, INK, "Autonomous Content Calendar",
                  "Plan, schedule, and orchestrate omnichannel video and social releases across YouTube, Instagram, and TikTok from a unified schedule.")}
            {hint("2", ORANGE, INK, "AI Video Performance Studio",
                  "Continuous self-improving AI diagnostics that uncover retention drop-offs and generate corrective hooks for your upcoming video releases.")}
            {hint("3", CRIMSON, WHITE, "50/50 Co-Founder Monetization",
                  "Review your audience software concepts. Our venture studio handles 100% of engineering, design, and hosting at zero upfront cost to you.", last=True)}
          </table>

          <!-- Boarding pass: workspace access details -->
          <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="margin:0 0 28px 0;border:3px solid {INK};border-radius:12px;overflow:hidden;background-color:{WHITE};">
            <tr><td bgcolor="{INK}" style="background-color:{INK};padding:12px 20px;border-bottom:3px dashed {MUSTARD};font-family:{F_HEAD};font-size:17px;font-weight:700;color:{WHITE};">
              Workspace access details
            </td></tr>
            <tr><td style="padding:8px 20px 10px 20px;">
              <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0">
                {pass_row("Authorized user", f'<span style="font-family:{F_TYPE};">{clean_user}</span>')}
                {pass_row("Dedicated URL", f'<a href="{canonical_base}/dashboard" target="_blank" style="color:{CRIMSON};text-decoration:underline;">{canonical_base.replace("https://", "")}/dashboard</a>')}
                {pass_row("Access tier", "Co-Founder Partner &bull; Full Studio Access")}
                {pass_row("Pipeline status", f'<span style="color:{TEAL};">Active &amp; Provisioned</span>', last=True)}
              </table>
            </td></tr>
          </table>

          <!-- Human reply line -->
          <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="margin:0 0 26px 0;">
            <tr><td bgcolor="{MUSTARD}" style="background-color:{MUSTARD};border:3px solid {INK};border-radius:12px;padding:18px 20px;">
              <div style="font-family:{F_HEAD};font-size:19px;font-weight:700;color:{INK};line-height:1.25;margin-bottom:6px;">
                Have questions or want to discuss ideas?
              </div>
              <p style="margin:0 0 8px 0;font-size:14px;color:{INK};line-height:1.6;">
                Simply hit <strong>Reply</strong> to this email. You won't get routed to an automated ticketing bot — our venture partners and lead engineers read and answer every message directly.
              </p>
              <div style="font-size:13px;color:{INK};line-height:1.55;">
                Reply with <strong style="background-color:{WHITE};border:1.5px solid {INK};border-radius:4px;padding:1px 6px;">"I'm ready to review concepts"</strong> or ask us any questions about the 50/50 partnership.
              </div>
            </td></tr>
          </table>

          <p style="margin:0 0 4px 0;font-size:14px;color:{BODY_TXT};">We wish you a rapid journey to scale and recurring revenue,</p>
          <div style="font-family:{F_HEAD};font-size:18px;font-weight:700;color:{INK};">The CoLaunch Venture Studio Team</div>
          <div style="font-family:{F_TYPE};font-size:12px;color:{MUTED};margin-top:3px;">
            Venture Partnerships &bull; <a href="{canonical_base}" style="color:{CRIMSON};text-decoration:none;">colaunch.app</a>
          </div>
    """

    body_html = _email_shell(
        title="Welcome to CoLaunch — You're Ready to Build",
        preheader="Your creator console is live. Here are three ways to get started.",
        tag_label="Operator onboarding",
        inner_html=inner,
        footer_html=_standard_footer(
            f"You received this onboarding email because an operator workspace was created for {clean_user}."
        ),
        max_width=600,
    )
    return body_html, body_text