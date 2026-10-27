"""
Creator Forge - Dynamic Concept Mockup Card Image Generator
Converts structured concept text (name, app_url, metrics, pricing, target customer)
into a pixel-perfect, high-res PNG image matching the Creator Forge macOS dark-mode
mockup window design.

Zero reliance on slow/hallucinatory external AI image APIs.
Renders deterministic, beautiful UI mockups in <25 milliseconds using Pillow.
"""

import os
import io
import math
from typing import Dict, Any, Optional
from PIL import Image, ImageDraw, ImageFont

# Font resolution helper
def _get_font(size: int, is_bold: bool = False, is_mono: bool = False) -> ImageFont.ImageFont:
    """Attempts to find the cleanest system font on macOS/Linux with fallbacks."""
    font_candidates = []
    if is_mono:
        font_candidates = [
            "/System/Library/Fonts/SFNSMono.ttf",
            "/System/Library/Fonts/Menlo.ttc",
            "/System/Library/Fonts/Monaco.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf" if is_bold else "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/Library/Fonts/Courier New.ttf",
        ]
    elif is_bold:
        font_candidates = [
            "/System/Library/Fonts/SFNS.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "/Library/Fonts/Arial Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ]
    else:
        font_candidates = [
            "/System/Library/Fonts/SFNS.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "/Library/Fonts/Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]

    for path in font_candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue

    return ImageFont.load_default()


def generate_concept_card_image(
    concept: Dict[str, Any],
    width: int = 800,
    height: int = 490,
    scale: int = 2
) -> bytes:
    """
    Renders a high-resolution 2x retina PNG of the macOS dark-mode concept card.
    
    Card Structure:
    1. Outer window chrome with dark obsidian background (#080A0C) & subtle studio border.
    2. Window controls: 🔴 🟡 🟢 + https://{app_url} + 🟢 MVP Ready badge.
    3. Center simulated product analytics dashboard (teal/cyan histograms + pink curve trajectory + stats).
    4. 3 Metric boxes: MRR Projected ($XX.XK MRR), Active Users (X,XXX), Retention (XX%).
    5. Footer bar: Target customer (left) + Target pricing (right).
    """
    W = width * scale
    H = height * scale
    
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Resolve concept fields
    name = str(concept.get("name") or "ActionLoop").strip()
    raw_url = str(
        concept.get("appUrl")
        or (concept.get("mockup") or {}).get("appUrl")
        or f"{name.lower().replace(' ', '')}.app"
    ).strip()
    if not raw_url.startswith("http"):
        app_url = f"https://{raw_url}"
    else:
        app_url = raw_url

    # Resolve brand accent color
    raw_color = concept.get("brandColor") or concept.get("brand_color") or concept.get("accentColor")
    accent_rgb = (16, 185, 129)  # Default Electric Emerald
    if raw_color and isinstance(raw_color, str):
        c_clean = raw_color.strip().lstrip("#")
        if len(c_clean) == 3:
            c_clean = "".join(c * 2 for c in c_clean)
        if len(c_clean) == 6:
            try:
                r_c = int(c_clean[0:2], 16)
                g_c = int(c_clean[2:4], 16)
                b_c = int(c_clean[4:6], 16)
                lum = (0.299 * r_c + 0.587 * g_c + 0.114 * b_c) / 255
                if lum < 0.25:
                    accent_rgb = (6, 182, 212)  # Ocean Cyan for dark backgrounds
                else:
                    accent_rgb = (r_c, g_c, b_c)
            except ValueError:
                pass

    mockup_type = str(concept.get("mockupType") or "saas_os").lower()

    # Metrics
    mockup_data = concept.get("mockup") if isinstance(concept.get("mockup"), dict) else {}
    mrr = str(
        concept.get("primaryMetric")
        or mockup_data.get("primaryMetric")
        or "$22.4K MRR"
    ).strip()
    if not mrr.endswith("MRR") and "$" in mrr:
        mrr = f"{mrr} MRR"

    active_users = str(
        concept.get("activeMetric")
        or mockup_data.get("activeMetric")
        or ("1,420 Copilot Runs" if mockup_type == "ai_copilot" else "420 Active" if mockup_type == "knowledge_hub" else "1,200")
    ).strip()

    retention = str(
        concept.get("efficiencyMetric")
        or mockup_data.get("efficiencyMetric")
        or ("98% Precision" if mockup_type == "ai_copilot" else "94% Renewal" if mockup_type == "knowledge_hub" else "91%")
    ).strip()

    customer = str(
        concept.get("customer")
        or concept.get("demographicAlignment")
        or "Overwhelmed high-achiever / creators"
    ).strip()
    if len(customer) > 42:
        customer = customer[:40] + "..."

    pricing = str(concept.get("pricing") or "$49/mo Starter / $99/mo Pro").strip()

    # Fonts
    font_url = _get_font(12 * scale, is_mono=True)
    font_badge = _get_font(10 * scale, is_bold=True)
    font_chart_title = _get_font(11 * scale, is_bold=True)
    font_chart_sub = _get_font(9 * scale, is_mono=True)
    font_metric_label = _get_font(10 * scale, is_bold=True)
    font_metric_val = _get_font(17 * scale, is_bold=True, is_mono=True)
    font_footer_cust = _get_font(11 * scale)
    font_footer_price = _get_font(12 * scale, is_bold=True, is_mono=True)

    # 1. Main outer card container with rounded corners and border
    pad = 4 * scale
    card_rect = [pad, pad, W - pad, H - pad]
    # Obsidian dark background #080A0C
    draw.rounded_rectangle(card_rect, radius=22 * scale, fill=(8, 10, 12, 255), outline=(37, 43, 50, 255), width=2 * scale)

    # 2. Window Chrome Bar (y = 20 * scale to 48 * scale)
    dot_y = 26 * scale
    dot_r = 6 * scale
    # Red, Yellow, Green macOS dots
    draw.ellipse([24 * scale, dot_y - dot_r, 24 * scale + dot_r * 2, dot_y + dot_r], fill=(239, 68, 68))
    draw.ellipse([42 * scale, dot_y - dot_r, 42 * scale + dot_r * 2, dot_y + dot_r], fill=(245, 158, 11))
    # Architecture Spec title in clean monospace
    spec_label = f"{name} • Software Spec"
    draw.text((82 * scale, dot_y - 7 * scale), spec_label, fill=(148, 163, 184, 255), font=font_url)

    # Right Badge: Concept Archetype pill (styled with dynamic brand accent)
    badge_label = "AI Copilot" if mockup_type == "ai_copilot" else "VIP Vault" if mockup_type == "knowledge_hub" else "MVP Ready"
    badge_w = 98 * scale
    badge_h = 22 * scale
    badge_x = W - pad - 20 * scale - badge_w
    badge_y = dot_y - 11 * scale
    draw.rounded_rectangle([badge_x, badge_y, badge_x + badge_w, badge_y + badge_h], radius=6 * scale, fill=(*accent_rgb, 35), outline=(*accent_rgb, 180), width=1 * scale)
    # Dynamic pulse dot inside badge
    draw.ellipse([badge_x + 9 * scale, badge_y + 8 * scale, badge_x + 15 * scale, badge_y + 14 * scale], fill=(*accent_rgb, 255))
    draw.text((badge_x + 20 * scale, badge_y + 4 * scale), badge_label, fill=(*accent_rgb, 255), font=font_badge)

    # Chrome bottom divider
    draw.line([(pad + 12 * scale, 48 * scale), (W - pad - 12 * scale, 48 * scale)], fill=(30, 41, 59, 255), width=1 * scale)

    # 3. Center Simulated Product Interface Canvas
    dash_x1 = 20 * scale
    dash_y1 = 58 * scale
    dash_x2 = W - 20 * scale
    dash_y2 = 338 * scale
    # Dark dashboard background #04060A with border
    draw.rounded_rectangle([dash_x1, dash_y1, dash_x2, dash_y2], radius=14 * scale, fill=(4, 6, 10, 255), outline=(30, 41, 59, 255), width=1 * scale)

    # Mini App Header Bar inside canvas (y = dash_y1 to dash_y1 + 42 * scale)
    hdr_y = dash_y1 + 10 * scale
    # Avatar circle on left with creator signature brand color
    av_r = 13 * scale
    av_x = dash_x1 + 16 * scale
    av_y = hdr_y + 3 * scale
    draw.ellipse([av_x, av_y, av_x + av_r * 2, av_y + av_r * 2], fill=(*accent_rgb, 255))
    initial_char = (name[0] if name else "C").upper()
    font_av = _get_font(12 * scale, is_bold=True)
    draw.text((av_x + 8 * scale, av_y + 5 * scale), initial_char, fill=(255, 255, 255, 255), font=font_av)

    # Concept Name in bold white font
    font_app_name = _get_font(13 * scale, is_bold=True)
    draw.text((av_x + av_r * 2 + 10 * scale, hdr_y + 7 * scale), name, fill=(255, 255, 255, 255), font=font_app_name)

    # Right badge: Live Workspace / Copilot / Vault
    ws_label = "AI Copilot Engine" if mockup_type == "ai_copilot" else "VIP Mastermind Vault" if mockup_type == "knowledge_hub" else "Live Workspace"
    ws_w = 125 * scale
    ws_h = 24 * scale
    ws_x = dash_x2 - 16 * scale - ws_w
    ws_y = hdr_y + 4 * scale
    draw.rounded_rectangle([ws_x, ws_y, ws_x + ws_w, ws_y + ws_h], radius=6 * scale, fill=(15, 23, 42, 255), outline=(*accent_rgb, 120), width=1 * scale)
    font_ws = _get_font(9 * scale, is_mono=True)
    draw.text((ws_x + 10 * scale, ws_y + 6 * scale), ws_label, fill=(*accent_rgb, 255), font=font_ws)

    # Divider under header
    hdr_div_y = dash_y1 + 44 * scale
    draw.line([(dash_x1 + 12 * scale, hdr_div_y), (dash_x2 - 12 * scale, hdr_div_y)], fill=(30, 41, 59, 180), width=1 * scale)

    # Content Area below header
    body_y1 = hdr_div_y + 10 * scale
    body_y2 = dash_y2 - 12 * scale

    # Left Sidebar (Dash / Tasks / Flows tailored by archetype)
    side_w = 125 * scale
    side_x1 = dash_x1 + 12 * scale
    side_x2 = side_x1 + side_w
    draw.rounded_rectangle([side_x1, body_y1, side_x2, body_y2], radius=10 * scale, fill=(15, 23, 42, 230), outline=(30, 41, 59, 255), width=1 * scale)

    if mockup_type == "ai_copilot":
        nav_items = [
            ("Copilot", (*accent_rgb,), True),
            ("Prompts", (251, 191, 36), False),
            ("Outputs", (148, 163, 184), False),
        ]
    elif mockup_type == "knowledge_hub":
        nav_items = [
            ("Vault", (*accent_rgb,), True),
            ("Calls", (251, 191, 36), False),
            ("Presets", (148, 163, 184), False),
        ]
    else:
        nav_items = [
            ("Dash", (*accent_rgb,), True),
            ("Tasks", (251, 191, 36), False),
            ("Flows", (148, 163, 184), False),
        ]

    font_nav = _get_font(11 * scale, is_bold=True)
    nav_h = (body_y2 - body_y1) / 3
    for idx, (label, color, is_active) in enumerate(nav_items):
        ny = body_y1 + idx * nav_h + (nav_h / 2) - 8 * scale
        draw.ellipse([side_x1 + 16 * scale, ny + 4 * scale, side_x1 + 24 * scale, ny + 12 * scale], fill=color)
        draw.text((side_x1 + 32 * scale, ny), label, fill=(241, 245, 249) if is_active else (148, 163, 184), font=font_nav)

    # Right Chart Area (MRR Trajectory + glowing curve styled with dynamic brand accent)
    chart_x1 = side_x2 + 12 * scale
    chart_x2 = dash_x2 - 12 * scale
    draw.rounded_rectangle([chart_x1, body_y1, chart_x2, body_y2], radius=10 * scale, fill=(11, 15, 23, 240), outline=(30, 41, 59, 255), width=1 * scale)

    # Chart Header: MRR Trajectory (left), +38.4% (right)
    chart_hdr_y = body_y1 + 12 * scale
    chart_title = "Copilot Efficiency" if mockup_type == "ai_copilot" else "Mastermind MRR" if mockup_type == "knowledge_hub" else "MRR Trajectory"
    draw.text((chart_x1 + 16 * scale, chart_hdr_y), chart_title, fill=(148, 163, 184, 255), font=_get_font(11 * scale, is_bold=True))
    draw.text((chart_x2 - 76 * scale, chart_hdr_y), "+38.4%", fill=(*accent_rgb, 255), font=_get_font(12 * scale, is_bold=True, is_mono=True))

    # Glowing Area Curve in creator's dynamic brand accent
    chart_base_y = body_y2 - 34 * scale
    chart_top_y = body_y1 + 46 * scale
    chart_span_w = chart_x2 - chart_x1 - 32 * scale

    raw_ctrls = [
        (0.0, 0.88),
        (0.2, 0.84),
        (0.38, 0.80),
        (0.55, 0.65),
        (0.72, 0.38),
        (0.88, 0.22),
        (1.0, 0.12),
    ]
    curve_points = []
    poly_pts = [(chart_x1 + 16 * scale, chart_base_y)]
    for rx, ry in raw_ctrls:
        px = chart_x1 + 16 * scale + rx * chart_span_w
        py = chart_top_y + ry * (chart_base_y - chart_top_y)
        curve_points.append((px, py))
        poly_pts.append((px, py))
    poly_pts.append((chart_x1 + 16 * scale + chart_span_w, chart_base_y))

    # Fill polygon with dynamic brand glow
    draw.polygon(poly_pts, fill=(*accent_rgb, 50))

    sub_poly = [(chart_x1 + 16 * scale, chart_base_y)]
    for px, py in curve_points:
        sub_poly.append((px, py + (chart_base_y - py) * 0.45))
    sub_poly.append((chart_x1 + 16 * scale + chart_span_w, chart_base_y))
    draw.polygon(sub_poly, fill=(*accent_rgb, 28))

    # Draw vibrant accent curve stroke (3px thick)
    for i in range(len(curve_points) - 1):
        draw.line([curve_points[i], curve_points[i + 1]], fill=(*accent_rgb, 255), width=3 * scale)

    # Bottom labels: Launch Day (left) ... Target (right)
    chart_ftr_y = body_y2 - 20 * scale
    draw.text((chart_x1 + 16 * scale, chart_ftr_y), "Launch Day", fill=(100, 116, 139, 255), font=_get_font(9 * scale, is_mono=True))
    target_str = f"Target: {mrr.split()[0]}" if mrr else "Target: $25K"
    target_w = draw.textlength(target_str, font=_get_font(9 * scale, is_mono=True)) if hasattr(draw, "textlength") else 80 * scale
    draw.text((chart_x2 - 16 * scale - target_w, chart_ftr_y), target_str, fill=(100, 116, 139, 255), font=_get_font(9 * scale, is_mono=True))

    # 4. 3 Metric Highlights Cards (y = 350 * scale to 420 * scale)
    cards_y1 = 350 * scale
    cards_y2 = 422 * scale
    total_card_w = W - 40 * scale
    col_w = (total_card_w - 24 * scale) / 3

    # Metric 1: MRR Projected
    c1_x1 = 20 * scale
    c1_x2 = c1_x1 + col_w
    draw.rounded_rectangle([c1_x1, cards_y1, c1_x2, cards_y2], radius=12 * scale, fill=(15, 23, 42, 255), outline=(30, 41, 59, 255), width=1 * scale)
    draw.text((c1_x1 + 18 * scale, cards_y1 + 12 * scale), "MRR PROJECTED", fill=(148, 163, 184, 255), font=font_metric_label)
    draw.text((c1_x1 + 18 * scale, cards_y1 + 34 * scale), mrr, fill=(*accent_rgb, 255), font=font_metric_val)

    # Metric 2: Active Users
    c2_x1 = c1_x2 + 12 * scale
    c2_x2 = c2_x1 + col_w
    draw.rounded_rectangle([c2_x1, cards_y1, c2_x2, cards_y2], radius=12 * scale, fill=(15, 23, 42, 255), outline=(30, 41, 59, 255), width=1 * scale)
    draw.text((c2_x1 + 18 * scale, cards_y1 + 12 * scale), "ACTIVE USERS", fill=(148, 163, 184, 255), font=font_metric_label)
    draw.text((c2_x1 + 18 * scale, cards_y1 + 34 * scale), active_users, fill=(248, 250, 252, 255), font=font_metric_val)

    # Metric 3: Retention
    c3_x1 = c2_x2 + 12 * scale
    c3_x2 = c3_x1 + col_w
    draw.rounded_rectangle([c3_x1, cards_y1, c3_x2, cards_y2], radius=12 * scale, fill=(15, 23, 42, 255), outline=(30, 41, 59, 255), width=1 * scale)
    draw.text((c3_x1 + 18 * scale, cards_y1 + 12 * scale), "RETENTION", fill=(148, 163, 184, 255), font=font_metric_label)
    draw.text((c3_x1 + 18 * scale, cards_y1 + 34 * scale), retention, fill=(56, 189, 248, 255), font=font_metric_val)

    # 5. Footer Bar (Target Customer & Pricing)
    footer_divider_y = 436 * scale
    draw.line([(pad + 16 * scale, footer_divider_y), (W - pad - 16 * scale, footer_divider_y)], fill=(30, 41, 59, 255), width=1 * scale)

    footer_text_y = 452 * scale
    # Target customer on left
    draw.text((24 * scale, footer_text_y), customer, fill=(148, 163, 184, 255), font=font_footer_cust)

    # Pricing on right (styled with dynamic brand accent)
    pricing_w = draw.textlength(pricing, font=font_footer_price) if hasattr(draw, "textlength") else 160 * scale
    draw.text((W - pad - 24 * scale - pricing_w, footer_text_y), pricing, fill=(*accent_rgb, 255), font=font_footer_price)

    # Convert to PNG buffer
    output_buf = io.BytesIO()
    img.save(output_buf, format="PNG", optimize=True)
    return output_buf.getvalue()
