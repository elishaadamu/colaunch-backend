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

# Color contrast & opaque surface blending helpers (avoid RGBA alpha bleed onto light email canvases)
def _relative_luminance(rgb) -> float:
    def _lin(c: int) -> float:
        v = max(0.0, min(1.0, c / 255.0))
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * _lin(rgb[0]) + 0.7152 * _lin(rgb[1]) + 0.0722 * _lin(rgb[2])


def _contrast_ratio(rgb_a, rgb_b) -> float:
    la = _relative_luminance(rgb_a)
    lb = _relative_luminance(rgb_b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _mix_rgb(fg, bg, weight_fg: float):
    w = max(0.0, min(1.0, weight_fg))
    return (
        int(fg[0] * w + bg[0] * (1.0 - w)),
        int(fg[1] * w + bg[1] * (1.0 - w)),
        int(fg[2] * w + bg[2] * (1.0 - w)),
    )


def _safe_text_on_dark(accent_rgb, bg_rgb, min_ratio: float = 5.0):
    """Brightens accent_rgb toward white until it hits min_ratio contrast on bg_rgb."""
    for step in range(21):
        cand = _mix_rgb((255, 255, 255), accent_rgb, step / 20.0)
        if _contrast_ratio(cand, bg_rgb) >= min_ratio:
            return (*cand, 255)
    return (248, 250, 252, 255)


def _on_accent_ink(accent_rgb):
    w_cr = _contrast_ratio((255, 255, 255), accent_rgb)
    b_cr = _contrast_ratio((11, 12, 16), accent_rgb)
    return (255, 255, 255, 255) if w_cr >= b_cr else (11, 12, 16, 255)


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
                font = ImageFont.truetype(path, size)
            except Exception:
                continue
            # Variable fonts (e.g. macOS SFNS) default to Regular; select the right weight.
            try:
                names = [n.decode() if isinstance(n, bytes) else n for n in font.get_variation_names()]
                want = "Bold" if is_bold else "Regular"
                if want in names:
                    font.set_variation_by_name(want)
                elif is_bold and "Semibold" in names:
                    font.set_variation_by_name("Semibold")
            except Exception:
                pass
            return font

    return ImageFont.load_default()


def _legacy_spec_card_image(
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
    accent_rgb = (200, 30, 60)  # Default Crimson Red (#c81e3c)
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
                    accent_rgb = (200, 30, 60)  # Crimson Red for dark backgrounds
                else:
                    accent_rgb = (r_c, g_c, b_c)
            except ValueError:
                pass

    mockup_type = str(concept.get("mockupType") or "saas_os").lower()
    raw_variant = concept.get("variant")
    if raw_variant is not None and str(raw_variant).strip() != "":
        try:
            variant_idx = int(raw_variant) % 3
        except (ValueError, TypeError):
            variant_idx = 0
    elif mockup_type == "ai_copilot":
        variant_idx = 1
    elif mockup_type == "knowledge_hub":
        variant_idx = 2
    else:
        variant_idx = 0

    # Real features + tagline when supplied (avoid invented stats)
    raw_features = concept.get("features") or concept.get("keyFeatures") or []
    features_list = [str(f).strip() for f in raw_features if str(f).strip()][:4] if isinstance(raw_features, list) else []
    tagline = str(concept.get("tagline") or concept.get("summary") or "").strip()

    # Metrics (only when real feature mode isn't overriding)
    mockup_data = concept.get("mockup") if isinstance(concept.get("mockup"), dict) else {}
    mrr = str(
        concept.get("primaryMetric")
        or mockup_data.get("primaryMetric")
        or "50/50 Split"
    ).strip()

    active_users = str(
        concept.get("activeMetric")
        or mockup_data.get("activeMetric")
        or ("Included" if features_list else "Sample preview")
    ).strip()

    retention = str(
        concept.get("efficiencyMetric")
        or mockup_data.get("efficiencyMetric")
        or "Zero Upfront Cost"
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

    # Opaque surface palette derived from dark surfaces (never alpha-bleed onto light email cards)
    card_bg_rgb = (8, 10, 12)
    dash_bg_rgb = (4, 6, 10)
    side_bg_rgb = (15, 23, 42)
    chart_bg_rgb = (11, 15, 23)

    badge_fill_rgb = _mix_rgb(accent_rgb, card_bg_rgb, 0.22)
    badge_text_rgba = _safe_text_on_dark(accent_rgb, badge_fill_rgb, 5.0)
    ws_fill_rgb = _mix_rgb(accent_rgb, dash_bg_rgb, 0.18)
    ws_text_rgba = _safe_text_on_dark(accent_rgb, ws_fill_rgb, 5.0)
    active_pill_rgb = _mix_rgb(accent_rgb, side_bg_rgb, 0.28)
    bar_soft_rgb = _mix_rgb(accent_rgb, chart_bg_rgb, 0.42)
    accent_text_card = _safe_text_on_dark(accent_rgb, card_bg_rgb, 5.0)
    accent_text_panel = _safe_text_on_dark(accent_rgb, side_bg_rgb, 5.0)
    accent_text_chart = _safe_text_on_dark(accent_rgb, chart_bg_rgb, 5.0)
    on_accent_initial = _on_accent_ink(accent_rgb)

    # Right Badge: Concept Archetype pill (styled with dynamic brand accent on opaque dark tint)
    badge_label = "AI Copilot" if variant_idx == 1 else "VIP Vault" if variant_idx == 2 else "Workflow OS"
    badge_w = 98 * scale
    badge_h = 22 * scale
    badge_x = W - pad - 20 * scale - badge_w
    badge_y = dot_y - 11 * scale
    draw.rounded_rectangle([badge_x, badge_y, badge_x + badge_w, badge_y + badge_h], radius=6 * scale, fill=(*badge_fill_rgb, 255), outline=(*accent_rgb, 255), width=1 * scale)
    # Dynamic pulse dot inside badge
    draw.ellipse([badge_x + 9 * scale, badge_y + 8 * scale, badge_x + 15 * scale, badge_y + 14 * scale], fill=(*accent_rgb, 255))
    draw.text((badge_x + 20 * scale, badge_y + 4 * scale), badge_label, fill=badge_text_rgba, font=font_badge)

    # Chrome bottom divider
    draw.line([(pad + 12 * scale, 48 * scale), (W - pad - 12 * scale, 48 * scale)], fill=(30, 41, 59, 255), width=1 * scale)

    # 3. Center Simulated Product Interface Canvas
    dash_x1 = 20 * scale
    dash_y1 = 58 * scale
    dash_x2 = W - 20 * scale
    dash_y2 = 338 * scale
    # Dark dashboard background #04060A with border
    draw.rounded_rectangle([dash_x1, dash_y1, dash_x2, dash_y2], radius=14 * scale, fill=(*dash_bg_rgb, 255), outline=(30, 41, 59, 255), width=1 * scale)

    # Mini App Header Bar inside canvas (y = dash_y1 to dash_y1 + 42 * scale)
    hdr_y = dash_y1 + 10 * scale
    # Avatar circle on left with creator signature brand color
    av_r = 13 * scale
    av_x = dash_x1 + 16 * scale
    av_y = hdr_y + 3 * scale
    draw.ellipse([av_x, av_y, av_x + av_r * 2, av_y + av_r * 2], fill=(*accent_rgb, 255))
    initial_char = (name[0] if name else "C").upper()
    font_av = _get_font(12 * scale, is_bold=True)
    draw.text((av_x + 8 * scale, av_y + 5 * scale), initial_char, fill=on_accent_initial, font=font_av)

    # Concept Name in bold white font
    font_app_name = _get_font(13 * scale, is_bold=True)
    draw.text((av_x + av_r * 2 + 10 * scale, hdr_y + 7 * scale), name, fill=(255, 255, 255, 255), font=font_app_name)

    # Right badge: Live Workspace / Copilot Studio / Member Hub
    ws_label = "Copilot Studio" if variant_idx == 1 else "Member Hub" if variant_idx == 2 else "Live Workspace"
    ws_w = 118 * scale
    ws_h = 24 * scale
    ws_x = dash_x2 - 16 * scale - ws_w
    ws_y = hdr_y + 4 * scale
    draw.rounded_rectangle([ws_x, ws_y, ws_x + ws_w, ws_y + ws_h], radius=6 * scale, fill=(*ws_fill_rgb, 255), outline=(*accent_rgb, 255), width=1 * scale)
    font_ws = _get_font(9 * scale, is_mono=True)
    draw.text((ws_x + 12 * scale, ws_y + 6 * scale), ws_label, fill=ws_text_rgba, font=font_ws)

    # Divider under header
    hdr_div_y = dash_y1 + 44 * scale
    draw.line([(dash_x1 + 12 * scale, hdr_div_y), (dash_x2 - 12 * scale, hdr_div_y)], fill=(30, 41, 59, 255), width=1 * scale)

    # Content Area below header
    body_y1 = hdr_div_y + 10 * scale
    body_y2 = dash_y2 - 12 * scale
    body_x1 = dash_x1 + 12 * scale
    body_x2 = dash_x2 - 12 * scale

    def _clip_label(txt: str, limit: int = 24) -> str:
        t = str(txt or "").strip()
        return t if len(t) <= limit else t[: limit - 1].rstrip(" &-,/") + "…"

    card_surface_rgb = (20, 28, 46)
    font_nav = _get_font(10 * scale, is_bold=True)
    font_mono_sm = _get_font(9 * scale, is_mono=True)
    font_sub_sm = _get_font(9 * scale)

    if variant_idx == 1:
        # VARIANT 1: AI Copilot Prompt Bar + Context Panel + Trajectory Output Studio
        cmd_h = 30 * scale
        cmd_y2 = body_y1 + cmd_h
        draw.rounded_rectangle([body_x1, body_y1, body_x2, cmd_y2], radius=8 * scale, fill=(*chart_bg_rgb, 255), outline=(*accent_rgb, 255), width=1 * scale)
        prompt_text = _clip_label(tagline or f"Generate {name} launch deliverable", 52)
        draw.text((body_x1 + 12 * scale, body_y1 + 8 * scale), ">", fill=accent_text_chart, font=font_mono_sm)
        draw.text((body_x1 + 26 * scale, body_y1 + 8 * scale), prompt_text, fill=(241, 245, 249, 255), font=font_nav)
        run_w = 56 * scale
        draw.rounded_rectangle([body_x2 - run_w - 6 * scale, body_y1 + 5 * scale, body_x2 - 6 * scale, cmd_y2 - 5 * scale], radius=5 * scale, fill=(*accent_rgb, 255))
        draw.text((body_x2 - run_w + 8 * scale, body_y1 + 8 * scale), "Run", fill=on_accent_initial, font=font_mono_sm)

        studio_y1 = cmd_y2 + 10 * scale
        ctx_w = 216 * scale
        ctx_x2 = body_x1 + ctx_w
        draw.rounded_rectangle([body_x1, studio_y1, ctx_x2, body_y2], radius=10 * scale, fill=(*side_bg_rgb, 255), outline=(30, 41, 59, 255), width=1 * scale)
        draw.text((body_x1 + 12 * scale, studio_y1 + 10 * scale), "CONTEXT INPUTS", fill=accent_text_panel, font=font_mono_sm)

        ctx_items = features_list[:3] if features_list else ["Audience Prompts", "Voice & Tone Spec", "Asset Templates"]
        row_h = (body_y2 - (studio_y1 + 30 * scale) - 8 * scale) / max(len(ctx_items), 1)
        for idx, feat in enumerate(ctx_items):
            ry1 = studio_y1 + 30 * scale + idx * row_h + 2 * scale
            ry2 = studio_y1 + 30 * scale + (idx + 1) * row_h - 3 * scale
            is_act = idx == 0
            draw.rounded_rectangle(
                [body_x1 + 8 * scale, ry1, ctx_x2 - 8 * scale, ry2],
                radius=6 * scale,
                fill=(*active_pill_rgb, 255) if is_act else (*card_surface_rgb, 255),
                outline=(*accent_rgb, 255) if is_act else (38, 49, 68, 255),
                width=1 * scale,
            )
            mid_y = (ry1 + ry2) / 2
            draw.text((body_x1 + 16 * scale, mid_y - 6 * scale), _clip_label(feat, 22), fill=(255, 255, 255, 255) if is_act else (203, 213, 225, 255), font=font_nav)

        out_x1 = ctx_x2 + 12 * scale
        draw.rounded_rectangle([out_x1, studio_y1, body_x2, body_y2], radius=10 * scale, fill=(*chart_bg_rgb, 255), outline=(30, 41, 59, 255), width=1 * scale)
        out_title = _clip_label(features_list[3] if len(features_list) > 3 else f"{name} Output Stream", 28)
        draw.text((out_x1 + 14 * scale, studio_y1 + 10 * scale), out_title, fill=(241, 245, 249, 255), font=_get_font(11 * scale, is_bold=True))
        draw.text((body_x2 - 96 * scale, studio_y1 + 11 * scale), "Sample Preview", fill=accent_text_chart, font=font_mono_sm)

        # Output summary skeleton blocks + smooth trajectory polyline (no histogram bars)
        draw.rounded_rectangle([out_x1 + 14 * scale, studio_y1 + 34 * scale, body_x2 - 14 * scale, studio_y1 + 58 * scale], radius=6 * scale, fill=(*card_surface_rgb, 255), outline=(38, 49, 68, 255), width=1 * scale)
        draw.rounded_rectangle([out_x1 + 22 * scale, studio_y1 + 42 * scale, out_x1 + 148 * scale, studio_y1 + 50 * scale], radius=3 * scale, fill=(*bar_soft_rgb, 255))
        draw.rounded_rectangle([out_x1 + 158 * scale, studio_y1 + 42 * scale, body_x2 - 24 * scale, studio_y1 + 50 * scale], radius=3 * scale, fill=(51, 65, 85, 255))

        pts = []
        curve_x1 = out_x1 + 18 * scale
        curve_x2 = body_x2 - 18 * scale
        curve_y_bot = body_y2 - 28 * scale
        curve_y_top = studio_y1 + 74 * scale
        ratios = [0.18, 0.34, 0.29, 0.56, 0.64, 0.88]
        for i, rv in enumerate(ratios):
            px = curve_x1 + (curve_x2 - curve_x1) * (i / (len(ratios) - 1))
            py = curve_y_bot - rv * (curve_y_bot - curve_y_top)
            pts.append((px, py))
        draw.line([(curve_x1, curve_y_bot), (curve_x2, curve_y_bot)], fill=(38, 49, 68, 255), width=1 * scale)
        draw.line(pts, fill=(*accent_rgb, 255), width=3 * scale)
        for i, (px, py) in enumerate(pts):
            r = 4 * scale if i == len(pts) - 1 else 3 * scale
            dot_fill = (255, 255, 255, 255) if i == len(pts) - 1 else (*accent_rgb, 255)
            draw.ellipse([px - r, py - r, px + r, py + r], fill=dot_fill, outline=(*accent_rgb, 255), width=1 * scale)
        draw.text((out_x1 + 14 * scale, body_y2 - 18 * scale), "Sample data, for illustration", fill=(148, 163, 184, 255), font=font_mono_sm)

    elif variant_idx == 2:
        # VARIANT 2: Member Vault & Cohort Hub (Top Filter Tabs + 2x2 Vault Grid Tiles)
        tabs_y2 = body_y1 + 26 * scale
        tab_labels = ["All Modules", "Playbooks", "Live Calls", "Peers"]
        tx = body_x1
        for idx, tlabel in enumerate(tab_labels):
            tw = (len(tlabel) * 6.4 + 22) * scale
            is_act = idx == 0
            draw.rounded_rectangle(
                [tx, body_y1, tx + tw, tabs_y2],
                radius=6 * scale,
                fill=(*active_pill_rgb, 255) if is_act else (*side_bg_rgb, 255),
                outline=(*accent_rgb, 255) if is_act else (30, 41, 59, 255),
                width=1 * scale,
            )
            draw.text((tx + 11 * scale, body_y1 + 6 * scale), tlabel, fill=(255, 255, 255, 255) if is_act else (148, 163, 184, 255), font=font_mono_sm)
            tx += tw + 8 * scale

        grid_y1 = tabs_y2 + 10 * scale
        grid_y2 = body_y2 - 22 * scale
        col_gap = 10 * scale
        row_gap = 10 * scale
        tile_w = (body_x2 - body_x1 - col_gap) / 2
        tile_h = (grid_y2 - grid_y1 - row_gap) / 2
        vault_items = features_list[:4] if features_list else [
            "Core Resource Vault",
            "Weekly Live Coaching",
            "Private Peer Network",
            "Verified Playbook Pack",
        ]
        while len(vault_items) < 4:
            vault_items.append(f"{name} Module {len(vault_items) + 1}")

        for idx, feat in enumerate(vault_items[:4]):
            c = idx % 2
            r = idx // 2
            vx1 = body_x1 + c * (tile_w + col_gap)
            vy1 = grid_y1 + r * (tile_h + row_gap)
            vx2 = vx1 + tile_w
            vy2 = vy1 + tile_h
            is_act = idx == 0
            draw.rounded_rectangle(
                [vx1, vy1, vx2, vy2],
                radius=9 * scale,
                fill=(*active_pill_rgb, 255) if is_act else (*side_bg_rgb, 255),
                outline=(*accent_rgb, 255) if is_act else (38, 49, 68, 255),
                width=1 * scale,
            )
            # Top accent bar inside tile
            draw.rounded_rectangle([vx1 + 12 * scale, vy1 + 10 * scale, vx1 + 42 * scale, vy1 + 14 * scale], radius=2 * scale, fill=(*accent_rgb, 255) if is_act else (*bar_soft_rgb, 255))
            tag = f"0{idx + 1} • INCLUDED"
            draw.text((vx2 - 86 * scale, vy1 + 8 * scale), tag, fill=accent_text_panel if not is_act else (255, 255, 255, 255), font=font_mono_sm)
            draw.text((vx1 + 12 * scale, vy1 + 26 * scale), _clip_label(feat, 25), fill=(255, 255, 255, 255) if is_act else (226, 232, 240, 255), font=font_nav)
            sub_line = _clip_label(tagline or "Member-only access & templates", 32)
            draw.text((vx1 + 12 * scale, vy1 + 44 * scale), sub_line, fill=(203, 213, 225, 255) if is_act else (148, 163, 184, 255), font=font_sub_sm)

        draw.text((body_x1, body_y2 - 14 * scale), "Sample data, for illustration", fill=(148, 163, 184, 255), font=font_mono_sm)
        draw.text((body_x2 - 110 * scale, body_y2 - 14 * scale), "Cohort Vault Spec", fill=accent_text_chart, font=font_mono_sm)

    else:
        # VARIANT 0: 3-Column Workflow & Sprint Pipeline Board
        col_gap = 10 * scale
        col_w = (body_x2 - body_x1 - 2 * col_gap) / 3
        board_y2 = body_y2 - 26 * scale
        cols = [
            ("01 QUEUE", features_list[0] if len(features_list) > 0 else "Core Workflow Setup", "Intake Trigger", True),
            ("02 AUTOMATED", features_list[1] if len(features_list) > 1 else "Asset Pipeline", features_list[2] if len(features_list) > 2 else "Auto-Routing", False),
            ("03 DELIVERED", features_list[3] if len(features_list) > 3 else "Client Handoff", "One-Click Publish", False),
        ]
        for idx, (col_hdr, card_a, card_b, is_focus) in enumerate(cols):
            cx1 = body_x1 + idx * (col_w + col_gap)
            cx2 = cx1 + col_w
            draw.rounded_rectangle([cx1, body_y1, cx2, board_y2], radius=9 * scale, fill=(*side_bg_rgb, 255), outline=(30, 41, 59, 255), width=1 * scale)
            draw.text((cx1 + 10 * scale, body_y1 + 9 * scale), col_hdr, fill=accent_text_panel if is_focus else (148, 163, 184, 255), font=font_mono_sm)

            # Primary card in column
            c1_y1 = body_y1 + 28 * scale
            c1_y2 = c1_y1 + 58 * scale
            draw.rounded_rectangle(
                [cx1 + 8 * scale, c1_y1, cx2 - 8 * scale, c1_y2],
                radius=7 * scale,
                fill=(*active_pill_rgb, 255) if is_focus else (*card_surface_rgb, 255),
                outline=(*accent_rgb, 255) if is_focus else (38, 49, 68, 255),
                width=1 * scale,
            )
            draw.ellipse([cx1 + 16 * scale, c1_y1 + 13 * scale, cx1 + 22 * scale, c1_y1 + 19 * scale], fill=(*accent_rgb, 255) if is_focus else (148, 163, 184, 255))
            draw.text((cx1 + 28 * scale, c1_y1 + 10 * scale), _clip_label(card_a, 17), fill=(255, 255, 255, 255), font=font_nav)
            draw.rounded_rectangle([cx1 + 16 * scale, c1_y1 + 36 * scale, cx2 - 36 * scale, c1_y1 + 43 * scale], radius=3 * scale, fill=(*bar_soft_rgb, 255) if is_focus else (51, 65, 85, 255))

            # Secondary card in column
            c2_y1 = c1_y2 + 8 * scale
            c2_y2 = board_y2 - 10 * scale
            draw.rounded_rectangle([cx1 + 8 * scale, c2_y1, cx2 - 8 * scale, c2_y2], radius=7 * scale, fill=(*chart_bg_rgb, 255), outline=(30, 41, 59, 255), width=1 * scale)
            draw.text((cx1 + 16 * scale, c2_y1 + 12 * scale), _clip_label(card_b, 19), fill=(203, 213, 225, 255), font=font_sub_sm)
            draw.rounded_rectangle([cx1 + 16 * scale, c2_y1 + 30 * scale, cx2 - 48 * scale, c2_y1 + 36 * scale], radius=3 * scale, fill=(51, 65, 85, 255))

        # Bottom horizontal milestone bar
        bar_y1 = board_y2 + 8 * scale
        bar_y2 = body_y2 - 2 * scale
        draw.rounded_rectangle([body_x1, bar_y1, body_x2, bar_y2], radius=6 * scale, fill=(*chart_bg_rgb, 255), outline=(30, 41, 59, 255), width=1 * scale)
        prog_w = int((body_x2 - body_x1 - 150 * scale) * 0.62)
        draw.rounded_rectangle([body_x1 + 8 * scale, bar_y1 + 5 * scale, body_x1 + 8 * scale + prog_w, bar_y2 - 5 * scale], radius=4 * scale, fill=(*accent_rgb, 255))
        draw.text((body_x2 - 134 * scale, bar_y1 + 3 * scale), "Sample Preview", fill=(148, 163, 184, 255), font=font_mono_sm)

    # 4. 3 Detail Highlights Cards (no invented numbers)
    cards_y1 = 350 * scale
    cards_y2 = 422 * scale
    total_card_w = W - 40 * scale
    col_w = (total_card_w - 24 * scale) / 3

    c1_x1 = 20 * scale
    c1_x2 = c1_x1 + col_w
    draw.rounded_rectangle([c1_x1, cards_y1, c1_x2, cards_y2], radius=12 * scale, fill=(*side_bg_rgb, 255), outline=(30, 41, 59, 255), width=1 * scale)
    draw.text((c1_x1 + 16 * scale, cards_y1 + 12 * scale), "PARTNERSHIP", fill=(148, 163, 184, 255), font=font_metric_label)
    draw.text((c1_x1 + 16 * scale, cards_y1 + 34 * scale), mrr if len(mrr) <= 14 else "50/50 Split", fill=accent_text_panel, font=font_metric_val)

    c2_x1 = c1_x2 + 12 * scale
    c2_x2 = c2_x1 + col_w
    draw.rounded_rectangle([c2_x1, cards_y1, c2_x2, cards_y2], radius=12 * scale, fill=(*side_bg_rgb, 255), outline=(30, 41, 59, 255), width=1 * scale)
    draw.text((c2_x1 + 16 * scale, cards_y1 + 12 * scale), "KEY MODULES", fill=(148, 163, 184, 255), font=font_metric_label)
    mod_txt = f"{len(features_list)} Included" if features_list else active_users
    draw.text((c2_x1 + 16 * scale, cards_y1 + 34 * scale), mod_txt[:15], fill=(248, 250, 252, 255), font=font_metric_val)

    c3_x1 = c2_x2 + 12 * scale
    c3_x2 = c3_x1 + col_w
    draw.rounded_rectangle([c3_x1, cards_y1, c3_x2, cards_y2], radius=12 * scale, fill=(*side_bg_rgb, 255), outline=(30, 41, 59, 255), width=1 * scale)
    draw.text((c3_x1 + 16 * scale, cards_y1 + 12 * scale), "CREATOR COST", fill=(148, 163, 184, 255), font=font_metric_label)
    draw.text((c3_x1 + 16 * scale, cards_y1 + 34 * scale), "$0 Upfront" if features_list else retention[:15], fill=(111, 207, 155, 255), font=font_metric_val)

    # 5. Footer Bar (Target Customer & Pricing)
    footer_divider_y = 436 * scale
    draw.line([(pad + 16 * scale, footer_divider_y), (W - pad - 16 * scale, footer_divider_y)], fill=(30, 41, 59, 255), width=1 * scale)

    footer_text_y = 452 * scale
    # Target customer on left
    draw.text((24 * scale, footer_text_y), customer, fill=(148, 163, 184, 255), font=font_footer_cust)

    # Pricing on right (styled with high-contrast accent on dark card)
    pricing_w = draw.textlength(pricing, font=font_footer_price) if hasattr(draw, "textlength") else 160 * scale
    draw.text((W - pad - 24 * scale - pricing_w, footer_text_y), pricing, fill=accent_text_card, font=font_footer_price)

    # Convert to PNG buffer
    output_buf = io.BytesIO()
    img.save(output_buf, format="PNG", optimize=True)
    return output_buf.getvalue()


# ──────────────────────────────────────────────────────────────────────────────
# Landing-page hero renderer (what the email shows now)
#
# Mirrors the hero of <ConceptLandingPage> on the website so the image in the
# outreach email is the same design the creator sees when they click through:
# nav bar → archetype pill → headline (tagline) → problem → CTA + pricing →
# "Made with {creator}" on the left, and the archetype product mockup on the right.
# Palette follows deriveBrandPalette() in ConceptLandingPage.jsx.
# ──────────────────────────────────────────────────────────────────────────────

_ARCHETYPE_LABELS = [
    "Workflow Operating System",
    "AI Intelligence Copilot",
    "Member Vault & Private Cohort",
]


def _parse_accent(raw_color) -> tuple:
    accent = (200, 30, 60)
    if raw_color and isinstance(raw_color, str):
        c = raw_color.strip().lstrip("#")
        if len(c) == 3:
            c = "".join(ch * 2 for ch in c)
        if len(c) == 6:
            try:
                rgb = (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))
                # Near-black brand colours would vanish on the dark page; fall back to crimson.
                if _relative_luminance(rgb) > 0.02:
                    accent = rgb
            except ValueError:
                pass
    return accent


def _landing_palette(accent):
    page = (8, 9, 12)
    surface = (16, 19, 26)
    raised = (23, 27, 36)
    muted_bg = _mix_rgb(accent, page, 0.07)
    soft_bg = _mix_rgb(accent, surface, 0.13)
    tint = accent
    for i in range(21):
        cand = _mix_rgb((255, 255, 255), accent, i / 20.0)
        if (_contrast_ratio(cand, soft_bg) >= 5.5 and _contrast_ratio(cand, raised) >= 5.5
                and _contrast_ratio(cand, muted_bg) >= 5.5):
            tint = cand
            break
    return {
        "solid": accent,
        "on_solid": _on_accent_ink(accent),
        "tint": tint,
        "border": _mix_rgb(accent, surface, 0.34),
        "page": page,
        "surface": surface,
        "raised": raised,
        "muted_bg": muted_bg,
        "soft_bg": soft_bg,
        "line": (38, 41, 50),
        "text": (243, 244, 246),
        "body": (211, 215, 224),
        "muted": (174, 181, 194),
    }


def _wrap(draw, text: str, font, max_w: float, max_lines: int):
    words = (text or "").split()
    lines, cur = [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
            continue
        if cur:
            lines.append(cur)
        cur = w
        if len(lines) == max_lines:
            break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if len(lines) == max_lines and len(" ".join(lines)) < len(" ".join(words)):
        last = lines[-1]
        while last and draw.textlength(last + "…", font=font) > max_w:
            last = last[:-1]
        lines[-1] = last.rstrip(" ,.;:") + "…"
    return lines


def _fit(draw, text: str, font, max_w: float) -> str:
    text = (text or "").strip()
    if draw.textlength(text, font=font) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text.rstrip() + "…"


def _feature_titles(raw) -> list:
    out = []
    for f in raw or []:
        t = f if isinstance(f, str) else (f.get("title") or f.get("name") or "") if isinstance(f, dict) else str(f)
        t = str(t).split(":")[0].strip()
        if t:
            out.append(t)
    return out[:4]


def generate_concept_card_image(
    concept: Dict[str, Any],
    width: int = 800,
    height: int = 396,
    scale: int = 2,
) -> bytes:
    s = scale
    W, H = width * s, height * s
    accent = _parse_accent(concept.get("brandColor") or concept.get("brand_color") or concept.get("accentColor"))
    P = _landing_palette(accent)

    # Layout variant: same rule as the website (concept index 0/1/2, else mockup type)
    raw_variant = concept.get("variant")
    variant = 0
    if raw_variant is not None and str(raw_variant).strip() != "":
        try:
            variant = int(raw_variant) % 3
        except (TypeError, ValueError):
            variant = 0
    else:
        mt = str(concept.get("mockupType") or "").lower()
        variant = 1 if mt == "ai_copilot" else 2 if mt == "knowledge_hub" else 0

    name = str(concept.get("name") or "New product").strip()
    tagline = str(concept.get("tagline") or "").strip() or name
    problem = str(concept.get("problem") or concept.get("description") or "").strip()
    pricing = str(concept.get("pricing") or "").replace("•", "·").strip()
    creator = str(concept.get("creatorName") or concept.get("creator") or "").strip()
    features = _feature_titles(concept.get("features") or concept.get("keyFeatures"))
    while len(features) < 4:
        features.append(["Daily setup", "Focus sessions", "Progress tracking", "Weekly review"][len(features)])

    img = Image.new("RGB", (W, H), P["page"])
    d = ImageDraw.Draw(img)

    f_logo = _get_font(13 * s, is_bold=True)
    f_nav = _get_font(13 * s, is_bold=True)
    f_btn_sm = _get_font(11 * s, is_bold=True)
    f_pill = _get_font(10 * s, is_bold=True)
    f_h1 = _get_font(29 * s, is_bold=True)
    f_body = _get_font(13 * s)
    f_btn = _get_font(12 * s, is_bold=True)
    f_price = _get_font(12 * s)
    f_small = _get_font(11 * s)
    f_card_title = _get_font(11 * s, is_bold=True)
    f_card = _get_font(9 * s, is_bold=True)
    f_caption = _get_font(9 * s)

    # Hero band: soft crimson-tinted gradient (top → page)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=_mix_rgb(P["muted_bg"], P["page"], 1 - t * 0.55))

    # ── Nav ───────────────────────────────────────────────────────────────────
    nav_h = 52 * s
    pad_x = 36 * s
    d.line([(0, nav_h), (W, nav_h)], fill=P["line"], width=s)
    logo = 26 * s
    ly = (nav_h - logo) // 2
    d.rounded_rectangle([pad_x, ly, pad_x + logo, ly + logo], radius=6 * s, fill=P["solid"])
    initial = (name[:1] or "C").upper()
    iw = d.textlength(initial, font=f_logo)
    d.text((pad_x + (logo - iw) / 2, ly + 5 * s), initial, fill=P["on_solid"], font=f_logo)
    d.text((pad_x + logo + 10 * s, ly + 5 * s), _fit(d, name, f_nav, 300 * s), fill=P["text"], font=f_nav)

    cta_sm = "Get early access"
    cta_w = d.textlength(cta_sm, font=f_btn_sm) + 24 * s
    bx2 = W - pad_x
    bx1 = bx2 - cta_w
    d.rectangle([bx1, ly, bx2, ly + logo], fill=P["solid"])
    d.text((bx1 + 12 * s, ly + 7 * s), cta_sm, fill=P["on_solid"], font=f_btn_sm)
    li_w = d.textlength("Log in", font=f_btn_sm) + 22 * s
    d.rectangle([bx1 - 8 * s - li_w, ly, bx1 - 8 * s, ly + logo], outline=P["line"], width=s, fill=P["page"])
    d.text((bx1 - 8 * s - li_w + 11 * s, ly + 7 * s), "Log in", fill=P["body"], font=f_btn_sm)

    # ── Left column ───────────────────────────────────────────────────────────
    left_w = 380 * s
    y = nav_h + 34 * s
    pill_label = _ARCHETYPE_LABELS[variant]
    pw = d.textlength(pill_label, font=f_pill) + 34 * s
    d.rounded_rectangle([pad_x, y, pad_x + pw, y + 22 * s], radius=11 * s, fill=P["soft_bg"], outline=P["border"], width=s)
    d.ellipse([pad_x + 10 * s, y + 8 * s, pad_x + 16 * s, y + 14 * s], fill=P["solid"])
    d.text((pad_x + 22 * s, y + 5 * s), pill_label, fill=P["tint"], font=f_pill)
    y += 36 * s

    for line in _wrap(d, tagline, f_h1, left_w, 3):
        d.text((pad_x, y), line, fill=P["text"], font=f_h1)
        y += 36 * s
    y += 6 * s
    if problem:
        for line in _wrap(d, problem, f_body, left_w - 10 * s, 3):
            d.text((pad_x, y), line, fill=P["body"], font=f_body)
            y += 20 * s
    y += 16 * s

    cta = "Get early access"
    cw = d.textlength(cta, font=f_btn) + 36 * s
    d.rectangle([pad_x, y, pad_x + cw, y + 38 * s], fill=P["solid"])
    d.text((pad_x + 18 * s, y + 12 * s), cta, fill=P["on_solid"], font=f_btn)
    if pricing:
        d.text((pad_x + cw + 14 * s, y + 12 * s), _fit(d, pricing, f_price, left_w - cw - 14 * s), fill=P["body"], font=f_price)
    y += 38 * s + 22 * s
    if creator and y + 24 * s < H - 16 * s:
        r = 11 * s
        d.ellipse([pad_x, y, pad_x + 2 * r, y + 2 * r], fill=P["raised"], outline=P["border"], width=s)
        ci = (creator[:1] or "C").upper()
        d.text((pad_x + r - d.textlength(ci, font=f_card) / 2, y + 6 * s), ci, fill=P["tint"], font=f_card)
        d.text((pad_x + 2 * r + 9 * s, y + 4 * s), _fit(d, f"Made with {creator}", f_small, left_w - 40 * s), fill=P["muted"], font=f_small)

    # ── Right column: product mockup (same archetype as the website) ────────
    mx1, my1 = 446 * s, nav_h + 30 * s
    mx2, my2 = W - pad_x, my1 + 270 * s
    d.rounded_rectangle([mx1, my1, mx2, my2], radius=12 * s, fill=P["surface"], outline=P["border"], width=s)
    ix1, iy1, ix2, iy2 = mx1 + 10 * s, my1 + 10 * s, mx2 - 10 * s, my2 - 10 * s
    d.rounded_rectangle([ix1, iy1, ix2, iy2], radius=8 * s, fill=(12, 14, 19), outline=P["line"], width=s)
    d.ellipse([ix1 + 12 * s, iy1 + 13 * s, ix1 + 20 * s, iy1 + 21 * s], fill=P["solid"])
    d.text((ix1 + 28 * s, iy1 + 10 * s), _fit(d, name, f_card_title, 170 * s), fill=P["text"], font=f_card_title)
    d.rounded_rectangle([ix2 - 76 * s, iy1 + 9 * s, ix2 - 12 * s, iy1 + 25 * s], radius=8 * s, outline=P["border"], width=s, fill=P["soft_bg"])
    d.line([(ix2 - 62 * s, iy1 + 17 * s), (ix2 - 22 * s, iy1 + 17 * s)], fill=P["solid"], width=2 * s)
    body_top = iy1 + 36 * s
    inner_w = ix2 - ix1 - 24 * s
    bar = (44, 48, 60)

    if variant == 0:
        # Workflow board: 5 columns, middle one highlighted, connector dots
        cols = 5
        gap = 7 * s
        col_w = (inner_w - gap * (cols - 1)) / cols
        col_h = 124 * s
        for i in range(cols):
            cx1 = ix1 + 12 * s + i * (col_w + gap)
            hot = i == 2
            d.rounded_rectangle([cx1, body_top, cx1 + col_w, body_top + col_h], radius=6 * s,
                                fill=P["soft_bg"] if hot else (20, 23, 31), outline=P["solid"] if hot else P["line"], width=s)
            d.line([(cx1 + 8 * s, body_top + 10 * s), (cx1 + col_w * 0.55, body_top + 10 * s)], fill=P["solid"] if hot else bar, width=3 * s)
            for k in range(3):
                ky = body_top + 22 * s + k * 30 * s
                if ky + 22 * s > body_top + col_h - 4 * s:
                    break
                card_fill = P["solid"] if (hot and k == 0) else _mix_rgb(P["solid"], (20, 23, 31), 0.35) if k == 0 else (32, 36, 46)
                d.rounded_rectangle([cx1 + 6 * s, ky, cx1 + col_w - 6 * s, ky + 22 * s], radius=4 * s, fill=card_fill)
            if i < cols - 1:
                dy = body_top + 33 * s
                d.ellipse([cx1 + col_w - 3 * s, dy - 3 * s, cx1 + col_w + 3 * s, dy + 3 * s], fill=P["solid"])
        chips_y = body_top + col_h + 12 * s
        chip_w = (inner_w - 10 * s) / 2
        for i, ft in enumerate(features[1:3]):
            x = ix1 + 12 * s + i * (chip_w + 10 * s)
            d.rounded_rectangle([x, chips_y, x + chip_w, chips_y + 40 * s], radius=6 * s,
                                fill=(20, 23, 31), outline=P["border"] if i == 0 else P["line"], width=s)
            d.rounded_rectangle([x + 10 * s, chips_y + 13 * s, x + 24 * s, chips_y + 27 * s], radius=3 * s, fill=P["soft_bg"])
            d.ellipse([x + 14 * s, chips_y + 17 * s, x + 20 * s, chips_y + 23 * s], fill=P["solid"])
            d.text((x + 32 * s, chips_y + 14 * s), _fit(d, ft, f_card, chip_w - 40 * s), fill=P["body"], font=f_card)
    elif variant == 1:
        # Copilot: command bar with the tagline + Generate, then generated output rows
        d.rounded_rectangle([ix1 + 12 * s, body_top, ix2 - 12 * s, body_top + 34 * s], radius=6 * s, fill=(20, 23, 31), outline=P["border"], width=s)
        d.text((ix1 + 22 * s, body_top + 11 * s), _fit(d, tagline, f_card, inner_w - 90 * s), fill=P["body"], font=f_card)
        gw = 64 * s
        d.rounded_rectangle([ix2 - 18 * s - gw, body_top + 6 * s, ix2 - 18 * s, body_top + 28 * s], radius=4 * s, fill=P["solid"])
        d.text((ix2 - 18 * s - gw + 10 * s, body_top + 11 * s), "Generate", fill=P["on_solid"], font=f_card)
        ry = body_top + 46 * s
        for i, ft in enumerate(features[:3]):
            d.rounded_rectangle([ix1 + 12 * s, ry, ix2 - 12 * s, ry + 38 * s], radius=6 * s,
                                fill=P["soft_bg"] if i == 0 else (20, 23, 31), outline=P["border"] if i == 0 else P["line"], width=s)
            d.text((ix1 + 22 * s, ry + 7 * s), _fit(d, ft, f_card, inner_w - 30 * s), fill=P["text"] if i == 0 else P["body"], font=f_card)
            d.line([(ix1 + 22 * s, ry + 26 * s), (ix1 + 22 * s + inner_w * (0.7 - i * 0.12), ry + 26 * s)], fill=bar, width=3 * s)
            ry += 46 * s
            if ry + 38 * s > iy2 - 8 * s:
                break
    else:
        # Member vault: 2×2 module tiles with lock badges
        gap = 8 * s
        tile_w = (inner_w - gap) / 2
        tile_h = ((iy2 - 12 * s) - body_top - gap) / 2  # fills the fixed-height frame
        for i, ft in enumerate(features[:4]):
            tx = ix1 + 12 * s + (i % 2) * (tile_w + gap)
            ty = body_top + (i // 2) * (tile_h + gap)
            hot = i == 0
            d.rounded_rectangle([tx, ty, tx + tile_w, ty + tile_h], radius=6 * s,
                                fill=P["soft_bg"] if hot else (20, 23, 31), outline=P["border"] if hot else P["line"], width=s)
            d.rounded_rectangle([tx + 10 * s, ty + 10 * s, tx + 28 * s, ty + 28 * s], radius=4 * s, fill=P["solid"] if hot else (32, 36, 46))
            d.text((tx + 10 * s, ty + 36 * s), _fit(d, ft, f_card, tile_w - 20 * s), fill=P["text"] if hot else P["body"], font=f_card)
            d.line([(tx + 10 * s, ty + 54 * s), (tx + tile_w * 0.6, ty + 54 * s)], fill=bar, width=3 * s)

    cap = "Sample data, for illustration"
    d.text(((mx1 + mx2) / 2 - d.textlength(cap, font=f_caption) / 2, my2 + 10 * s), cap, fill=P["muted"], font=f_caption)

    # Subtle bottom hairline in brand colour
    d.line([(0, H - s), (W, H - s)], fill=P["border"], width=s)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
