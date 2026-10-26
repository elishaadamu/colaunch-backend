"""
Real Human-to-Human Venture Studio Email Template & Markdown Formatter.

Provides:
1. Clean 1-on-1 personal email message formatting (NOT an automated marketing newsletter).
2. Official Creator Forge 4-node geometric logo & authentic executive signature.
3. Clean, high-deliverability light HTML styling for all email clients (Gmail, Apple Mail, Outlook).
4. Elegant concept preview showcase for Step 5 & Step 6 creator proposals.
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

# Default curated SaaS visual mockups per category (high-performance Unsplash previews)
CATEGORY_MOCKUP_IMAGES = {
    "tech": "https://images.unsplash.com/photo-1551288049-bebda4e38f71?w=1200&auto=format&fit=crop&q=80",
    "productivity": "https://images.unsplash.com/photo-1460925895917-afdab827c52f?w=1200&auto=format&fit=crop&q=80",
    "finance": "https://images.unsplash.com/photo-1642543492481-44e81e3914a7?w=1200&auto=format&fit=crop&q=80",
    "video_editing": "https://images.unsplash.com/photo-1574717024653-61fd2cf4d44d?w=1200&auto=format&fit=crop&q=80",
    "gaming": "https://images.unsplash.com/photo-1542751371-adc38448a05e?w=1200&auto=format&fit=crop&q=80",
    "data_ai": "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=1200&auto=format&fit=crop&q=80",
    "default": "https://images.unsplash.com/photo-1551288049-bebda4e38f71?w=1200&auto=format&fit=crop&q=80",
}


def _render_creator_forge_logo_html(size: int = 28) -> str:
    """
    Renders the official Creator Forge 4-node geometric symbol using pure HTML tables.
    100% email client compatible — zero external image dependency, zero SVG blocking.
    Node 1 (top-left): #1e293b (Slate-800)
    Node 2 (top-right): #0284c7 (Ocean Cyan)
    Node 3 (bottom-left): #16a34a (Studio Emerald)
    Node 4 (bottom-right): #0f172a (Obsidian Slate)
    """
    cell_size = max(10, size // 2 - 3)
    return f'''
    <table border="0" cellspacing="2" cellpadding="0" style="display:inline-table;width:{size}px;height:{size}px;border-collapse:separate;vertical-align:middle;background:#ffffff;padding:2px;border:1px solid #e2e8f0;border-radius:6px;">
      <tr>
        <td style="width:{cell_size}px;height:{cell_size}px;background:#1e293b;border-radius:2px;"></td>
        <td style="width:{cell_size}px;height:{cell_size}px;background:#0284c7;border-radius:2px;"></td>
      </tr>
      <tr>
        <td style="width:{cell_size}px;height:{cell_size}px;background:#16a34a;border-radius:2px;"></td>
        <td style="width:{cell_size}px;height:{cell_size}px;background:#0f172a;border-radius:2px;"></td>
      </tr>
    </table>
    '''


def get_contact_reply_email() -> str:
    """
    Retrieves the active contact and reply email address directly from .env / settings.
    Ensures that any changes to FROM_EMAIL or ADMIN_EMAIL in .env automatically propagate
    to concept cards, 1-click mailto links, and email signatures.
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
    """Renders a real 1-on-1 executive email signature with brand card elements."""
    logo_html = _render_creator_forge_logo_html(32)
    contact_email = get_contact_reply_email()
    ref_footnote = ""
    if tracking_token:
        ref_footnote = f'''
        <div style="margin-top:12px;padding-top:10px;border-top:1px solid #f1f5f9;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px;color:#94a3b8;">
          <span style="color:#64748b;">Studio Reference:</span>
          <span style="background:#f1f5f9;color:#475569;padding:2px 6px;border-radius:4px;font-size:10px;margin-left:4px;">{html.escape(tracking_token)}</span>
        </div>
        '''
    return f'''
    <div style="margin-top:28px;padding-top:20px;border-top:1px solid #e2e8f0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
      <table border="0" cellspacing="0" cellpadding="0">
        <tr>
          <td style="vertical-align:top;padding-right:14px;">
            {logo_html}
          </td>
          <td style="vertical-align:top;">
            <div style="font-size:14px;font-weight:700;color:#0f172a;line-height:1.2;">Alex Rivera</div>
            <div style="font-size:12px;color:#64748b;font-weight:500;margin-top:3px;">Head of Venture Partnerships • CoLaunch</div>
            <div style="font-size:12px;color:#475569;margin-top:8px;line-height:1.5;">
              <a href="mailto:{contact_email}" style="color:#0f172a;text-decoration:underline;font-weight:600;">{html.escape(contact_email)}</a>
              <span style="color:#cbd5e1;margin:0 6px;">•</span>
              <a href="https://colaunch.app" style="color:#0284c7;text-decoration:none;">colaunch.app</a>
              <span style="color:#cbd5e1;margin:0 6px;">•</span>
              <span style="color:#64748b;">San Francisco, CA</span>
            </div>
            {ref_footnote}
          </td>
        </tr>
      </table>
    </div>
    '''


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
    
    # Also strip stray standalone [CF-STAGE:...] tokens if any
    token_match2 = re.search(r'(\[CF-[^\]]+\])', raw_text)
    if token_match2 and not token:
        token = token_match2.group(1).strip()
        raw_text = raw_text[:token_match2.start()] + raw_text[token_match2.end():]

    # Clean trailing dashes or excess whitespace
    cleaned_body = re.sub(r'---\s*$', '', raw_text.strip()).strip()
    return cleaned_body, token


def _render_single_concept_card(
    concept: Dict[str, Any],
    index: int = 0,
    total_concepts: int = 1,
    concept_image_url: Optional[str] = None,
    tracking_token: str = ""
) -> str:
    """Renders an individual concept showcase card with clean, modern light styling and Creator Forge palette."""
    app_name = concept.get("name") or concept.get("title") or f"Software Concept #{index + 1}"
    tagline = concept.get("tagline") or concept.get("summary") or concept.get("description") or "Tailored software suite engineered for your community"
    raw_pricing = concept.get("pricing") or concept.get("revenueModel")
    if isinstance(raw_pricing, dict):
        pricing_parts = [f"{str(v)}" for k, v in raw_pricing.items() if v]
        pricing = " • ".join(pricing_parts) if pricing_parts else "$29/mo Starter • $79/mo Pro"
    elif isinstance(raw_pricing, str) and raw_pricing.strip():
        pricing = raw_pricing.strip()
    else:
        pricing = "$29/mo Starter • $79/mo Pro"

    problem = concept.get("problem") or concept.get("description") or ""
    score = concept.get("opportunityScore") or concept.get("score") or (98 - index * 3)
    mockup_data = concept.get("mockup") if isinstance(concept.get("mockup"), dict) else {}

    app_url = mockup_data.get("appUrl") or f"{str(app_name).lower().replace(' ', '')}.app"
    primary_metric = mockup_data.get("primaryMetric") or "$18.4K Projected MRR"
    active_metric = mockup_data.get("activeMetric") or "1,240 Target Users"
    efficiency_metric = mockup_data.get("efficiencyMetric") or "91%"
    customer_label = concept.get("customer") or concept.get("demographicAlignment") or ""

    # Determine software archetype for concrete specification
    raw_mockup_type = str(concept.get("mockupType") or "").lower()
    if not raw_mockup_type:
        raw_mockup_type = "saas_os" if index == 0 else ("ai_copilot" if index == 1 else "knowledge_hub")

    if "copilot" in raw_mockup_type or "ai" in raw_mockup_type:
        archetype_badge_text = "AI Copilot Engine"
        mod_1_label = "[01] CONTEXT & INTELLIGENCE"
        mod_2_label = "[02] NEURAL PIPELINE"
    elif "knowledge" in raw_mockup_type or "hub" in raw_mockup_type or "vault" in raw_mockup_type:
        archetype_badge_text = "Knowledge Hub & Vault"
        mod_1_label = "[01] CURATED VAULT"
        mod_2_label = "[02] COMMUNITY WORKSPACE"
    else:
        archetype_badge_text = "SaaS Operating System"
        mod_1_label = "[01] CORE ENGINE"
        mod_2_label = "[02] CLIENT WORKFLOW"

    # Harmonious Creator Forge palette (No generic purple)
    raw_brand = concept.get("brandColor") or ""
    purple_shades = ["#8b5cf6", "#6366f1", "#7c3aed", "#a855f7", "#9333ea", "#4f46e5", "#c084fc", "#e879f9", "#d946ef", "#805ad5"]
    if not raw_brand or any(p in raw_brand.lower() for p in purple_shades):
        brand_color = "#16A34A" if index == 0 else ("#0F172A" if index == 1 else "#0284C7")
    else:
        brand_color = raw_brand

    is_modified = bool(concept.get("isModifiedByAdmin") or concept.get("customImageUrl"))
    modified_badge = ""
    if is_modified:
        modified_badge = '<span style="background:#ecfdf5;border:1px solid #a7f3d0;color:#065f46;padding:2px 7px;border-radius:6px;font-size:9px;font-weight:700;letter-spacing:0.5px;text-transform:uppercase;margin-right:6px;">✓ Customized</span>'

    features_html = ""
    key_features = concept.get("keyFeatures") or concept.get("features") or []
    if key_features and isinstance(key_features, list):
        f_items = "".join(
            f'<li style="margin-bottom:6px;line-height:1.5;color:#334155;font-size:13px;">'
            f'<span style="color:#16a34a;font-weight:bold;margin-right:6px;">✓</span>{html.escape(str(f))}</li>'
            for f in key_features[:4]
        )
        features_html = f'''
        <div style="margin-top:14px;padding-top:12px;border-top:1px solid #f1f5f9;">
          <div style="font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:8px;">
            Key Built-In Features:
          </div>
          <ul style="margin:0;padding:0;list-style:none;">
            {f_items}
          </ul>
        </div>
        '''

    problem_html = ""
    if problem:
        problem_html = f'''
        <div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:10px;padding:10px 14px;margin-top:12px;font-size:12px;color:#475569;line-height:1.5;">
          <strong style="color:#0f172a;">Solves:</strong> {html.escape(problem)}
        </div>
        '''

    demographic = concept.get("demographicAlignment") or concept.get("customer") or ""
    demographic_html = ""
    if demographic:
        demographic_html = f'''
        <div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:10px;padding:10px 14px;margin-top:8px;font-size:12px;color:#475569;line-height:1.5;">
          <strong style="color:#0f172a;">Target Audience:</strong> {html.escape(demographic)}
        </div>
        '''

    feat_1_title = html.escape(str(key_features[0])) if (key_features and len(key_features) > 0) else f"{html.escape(app_name)} Engine"
    feat_1_desc = "Unified workflow automation engineered for high-retention execution."
    feat_2_title = html.escape(str(key_features[1])) if (key_features and len(key_features) > 1) else "Interactive Interface"
    feat_2_desc = f"Bespoke software interface tailored directly for {html.escape(customer_label or 'your community')}."

    # Native Software Architecture Blueprint Specification (Pure Email-Safe HTML/CSS, Zero External Images)
    architecture_canvas_html = f'''
    <table width="100%" border="0" cellspacing="0" cellpadding="0" style="margin:16px 0 14px 0;background:#0f172a;border-radius:12px;border:1px solid #1e293b;overflow:hidden;box-shadow:0 3px 10px rgba(15,23,42,0.08);">
      <tr>
        <td style="padding:9px 14px;background:#1e293b;border-bottom:1px solid #334155;">
          <table width="100%" border="0" cellspacing="0" cellpadding="0">
            <tr>
              <td align="left">
                <span style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:10px;font-weight:800;color:#38bdf8;letter-spacing:0.8px;text-transform:uppercase;">
                  ⚡ ARCHITECTURE SPECIFICATION &bull; {archetype_badge_text.upper()}
                </span>
              </td>
              <td align="right">
                <span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:#10b981;margin-right:4px;"></span>
                <span style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:10px;font-weight:700;color:#34d399;text-transform:uppercase;">
                  MVP READY
                </span>
              </td>
            </tr>
          </table>
        </td>
      </tr>
      <tr>
        <td style="padding:12px 14px;">
          <table width="100%" border="0" cellspacing="0" cellpadding="0">
            <tr>
              <td width="31%" style="vertical-align:top;background:#131d31;border:1px solid #27354f;border-radius:8px;padding:9px 10px;">
                <div style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:9px;font-weight:800;color:#38bdf8;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:3px;">
                  {mod_1_label}
                </div>
                <div style="font-size:11px;font-weight:700;color:#f8fafc;line-height:1.3;margin-bottom:4px;">
                  {feat_1_title}
                </div>
                <div style="font-size:10px;color:#94a3b8;line-height:1.35;">
                  {feat_1_desc}
                </div>
              </td>
              <td width="3%">&nbsp;</td>
              <td width="31%" style="vertical-align:top;background:#131d31;border:1px solid #27354f;border-radius:8px;padding:9px 10px;">
                <div style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:9px;font-weight:800;color:#34d399;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:3px;">
                  {mod_2_label}
                </div>
                <div style="font-size:11px;font-weight:700;color:#f8fafc;line-height:1.3;margin-bottom:4px;">
                  {feat_2_title}
                </div>
                <div style="font-size:10px;color:#94a3b8;line-height:1.35;">
                  {feat_2_desc}
                </div>
              </td>
              <td width="3%">&nbsp;</td>
              <td width="31%" style="vertical-align:top;background:#131d31;border:1px solid #27354f;border-radius:8px;padding:9px 10px;">
                <div style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:9px;font-weight:800;color:#fbbf24;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:3px;">
                  [03] REVENUE ENGINE
                </div>
                <div style="font-size:11px;font-weight:700;color:#f8fafc;line-height:1.3;margin-bottom:4px;">
                  {html.escape(pricing)}
                </div>
                <div style="font-size:10px;color:#94a3b8;line-height:1.35;">
                  50/50 Creator Split &bull; Automated Stripe Billing
                </div>
              </td>
            </tr>
          </table>
        </td>
      </tr>
      <tr>
        <td style="padding:6px 14px;background:#090d16;border-top:1px solid #1e293b;text-align:center;">
          <span style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:9px;color:#64748b;letter-spacing:0.5px;text-transform:uppercase;">
            100% ENGINEERED &amp; HOSTED BY COLAUNCH &bull; ZERO UPFRONT COST &bull; 50/50 CO-LAUNCH
          </span>
        </td>
      </tr>
    </table>
    '''

    concept_badge = f"CONCEPT #{index + 1}" if total_concepts > 1 else "PROPOSED SOFTWARE PRODUCT"
    token_suffix = f" {tracking_token}" if tracking_token else ""
    select_action_subject = urllib.parse.quote(f"Interested in Concept {index + 1}: {app_name}{token_suffix}")
    select_action_body = urllib.parse.quote(f"I will be interested in Concept {index + 1} ({app_name}). Let's build and launch this together!{token_suffix}")

    return f'''
    <!-- CONCEPT SHOWCASE CARD #{index + 1} (Light Clean Theme) -->
    <table width="100%" border="0" cellspacing="0" cellpadding="0" style="margin:18px 0 24px 0;background:#f8fafc;border-radius:16px;border:1px solid #e2e8f0;border-top:3px solid {brand_color};overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.04);">
      <!-- Window Chrome Header (No URL, Pure Software Spec Archetype) -->
      <tr>
        <td style="padding:12px 18px;background:#ffffff;border-bottom:1px solid #e2e8f0;">
          <table width="100%" border="0" cellspacing="0" cellpadding="0">
            <tr>
              <td align="left" style="vertical-align:middle;">
                <span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:#ef4444;margin-right:5px;"></span>
                <span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:#f59e0b;margin-right:5px;"></span>
                <span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:#16a34a;margin-right:10px;"></span>
                <span style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:10px;font-weight:700;color:#334155;background:#f1f5f9;padding:3px 9px;border-radius:6px;border:1px solid #cbd5e1;letter-spacing:0.5px;text-transform:uppercase;">
                  {archetype_badge_text.upper()}
                </span>
              </td>
              <td align="right" style="vertical-align:middle;">
                {modified_badge}
                <span style="background:#f1f5f9;border:1px solid #cbd5e1;color:#334155;padding:3px 8px;border-radius:6px;font-size:10px;font-weight:700;letter-spacing:0.5px;text-transform:uppercase;margin-right:6px;">
                  {concept_badge}
                </span>
                <span style="background:#ecfdf5;border:1px solid #a7f3d0;color:#065f46;padding:3px 8px;border-radius:6px;font-size:10px;font-weight:700;letter-spacing:0.5px;text-transform:uppercase;">
                  Score: {score}/100
                </span>
              </td>
            </tr>
          </table>
        </td>
      </tr>
      <!-- Card Main Body -->
      <tr>
        <td style="padding:20px 22px;">
          <!-- Concept Title & Badge -->
          <table width="100%" border="0" cellspacing="0" cellpadding="0">
            <tr>
              <td>
                <div style="font-size:17px;font-weight:800;color:#0f172a;letter-spacing:-0.3px;">
                  {html.escape(app_name)}
                </div>
                <div style="font-size:13px;color:{brand_color};font-weight:600;margin-top:2px;">
                  {html.escape(tagline)}
                </div>
              </td>
              <td align="right" style="vertical-align:top;">
                <div style="font-size:10px;color:#64748b;text-transform:uppercase;font-weight:700;margin-bottom:2px;text-align:right;">Target Pricing</div>
                <span style="display:inline-block;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px;font-weight:700;color:#0369a1;background:#f0f9ff;padding:4px 10px;border-radius:6px;border:1px solid #bae6fd;white-space:nowrap;">
                  {html.escape(pricing)}
                </span>
              </td>
            </tr>
          </table>

          {architecture_canvas_html}

          <!-- Metric Highlights Grid -->
          <table width="100%" border="0" cellspacing="0" cellpadding="0" style="margin-top:14px;">
            <tr>
              <td width="32%" style="background:#ffffff;border:1px solid #e2e8f0;border-radius:8px;padding:9px;text-align:center;">
                <div style="font-size:10px;color:#64748b;text-transform:uppercase;font-weight:700;letter-spacing:0.5px;">Est. Revenue</div>
                <div style="font-size:13px;font-weight:800;color:#16a34a;margin-top:2px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;">{html.escape(primary_metric)}</div>
              </td>
              <td width="2%">&nbsp;</td>
              <td width="32%" style="background:#ffffff;border:1px solid #e2e8f0;border-radius:8px;padding:9px;text-align:center;">
                <div style="font-size:10px;color:#64748b;text-transform:uppercase;font-weight:700;letter-spacing:0.5px;">Target Users</div>
                <div style="font-size:13px;font-weight:800;color:#0284c7;margin-top:2px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;">{html.escape(active_metric)}</div>
              </td>
              <td width="2%">&nbsp;</td>
              <td width="32%" style="background:#ffffff;border:1px solid #e2e8f0;border-radius:8px;padding:9px;text-align:center;">
                <div style="font-size:10px;color:#64748b;text-transform:uppercase;font-weight:700;letter-spacing:0.5px;">Build Speed</div>
                <div style="font-size:13px;font-weight:800;color:#0f172a;margin-top:2px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;">{html.escape(efficiency_metric)}</div>
              </td>
            </tr>
          </table>

          {problem_html}
          {demographic_html}
          {features_html}

          <!-- 1-Click Select Action for Concept -->
          <table width="100%" border="0" cellspacing="0" cellpadding="0" style="margin-top:16px;padding-top:12px;border-top:1px solid #f1f5f9;">
            <tr>
              <td align="left" style="vertical-align:middle;">
                <span style="font-size:11px;color:#64748b;font-weight:500;">
                  Prefer this direction?
                </span>
              </td>
              <td align="right" style="vertical-align:middle;">
                <a href="mailto:{get_contact_reply_email()}?subject={select_action_subject}&body={select_action_body}" style="display:inline-block;padding:7px 15px;background:{brand_color};color:#ffffff;font-size:11px;font-weight:700;text-decoration:none;border-radius:7px;letter-spacing:0.3px;">
                  Select Concept #{index + 1} &rarr;
                </a>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
    '''


def render_concept_showcase_html(
    concepts: Optional[List[Dict[str, Any]]] = None,
    concept_image_url: Optional[str] = None,
    creator_name: str = "",
    tracking_token: str = ""
) -> str:
    """
    Renders an eye-catching, responsive concept showcase for Step 5 & Step 6 emails.
    Shows the full breakdown of all engineered concepts with visual mockups, prominent prices, and features.
    Guarantees Concept 1, Concept 2, and Concept 3 are all positioned above the "How to Move Forward" callout.
    """
    if not concepts and not concept_image_url:
        return ""

    concept_list = concepts if (concepts and len(concepts) > 0) else [{}]
    # Limit to top 3 concepts
    concept_list = concept_list[:3]
    total_count = len(concept_list)

    header_bar = ""
    if total_count > 1:
        header_bar = f'''
        <div style="margin:24px 0 10px 0;padding-bottom:8px;border-bottom:1px solid #e2e8f0;display:flex;align-items:center;justify-content:space-between;">
          <span style="font-size:12px;font-weight:700;color:#0f172a;text-transform:uppercase;letter-spacing:0.8px;">
            💡 Top {total_count} Software Opportunities &amp; Pricing Breakdown
          </span>
        </div>
        '''

    cards = []
    for idx, c in enumerate(concept_list):
        cards.append(_render_single_concept_card(
            concept=c,
            index=idx,
            total_concepts=total_count,
            concept_image_url=concept_image_url,
            tracking_token=tracking_token
        ))

    # Dynamic 1-click response buttons for each concept
    contact_reply_to = get_contact_reply_email()
    concept_buttons = []
    token_suffix = f" {tracking_token}" if tracking_token else ""
    for idx, c in enumerate(concept_list):
        c_name = c.get("name") or f"Concept {idx + 1}"
        c_num = idx + 1
        subj_encoded = urllib.parse.quote(f"Interested in Concept {c_num}: {c_name}{token_suffix}")
        body_encoded = urllib.parse.quote(f"I will be interested in Concept {c_num} ({c_name}). Let's build and launch this together!{token_suffix}")
        mailto_url = f"mailto:{contact_reply_to}?subject={subj_encoded}&body={body_encoded}"
        concept_buttons.append(
            f'<a href="{mailto_url}" style="display:inline-block;background:#0f172a;color:#ffffff;padding:9px 15px;border-radius:8px;font-size:12px;font-weight:700;text-decoration:none;margin:4px 6px 4px 0;border:1px solid #1e293b;">'
            f'👉 &ldquo;I will be interested in Concept {c_num}&rdquo;'
            f'</a>'
        )
    buttons_html = "".join(concept_buttons)

    # 🚀 HOW TO MOVE FORWARD CALLOUT - Positioned cleanly below ALL concept cards
    cta_and_replies = f'''
    <!-- 🚀 HOW TO MOVE FORWARD CALLOUT (Placed strictly below all 3 concepts) -->
    <table width="100%" border="0" cellspacing="0" cellpadding="0" style="margin:24px 0 20px 0;">
      <tr>
        <td style="padding:18px 20px;background:#f8fafc;border:1px solid #cbd5e1;border-left:4px solid #16a34a;border-radius:12px;">
          <div style="font-size:13px;font-weight:800;color:#0f172a;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px;">
            🚀 How to Move Forward (Select Your Preferred Concept):
          </div>
          <p style="margin:0 0 10px 0;font-size:13px;color:#334155;line-height:1.55;">
            Under our 50/50 partnership, CoLaunch covers <strong>100% of engineering, hosting, payment infrastructure, and MVP deployment at zero financial cost to you</strong>.
            To move forward, <strong>simply reply to this email</strong> with your preferred concept:
          </p>
          <div style="margin:8px 0 12px 0;padding:10px 14px;background:#ffffff;border:1px dashed #94a3b8;border-radius:8px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;color:#0f172a;">
            💬 <strong>&ldquo;I will be interested in Concept 1&rdquo;</strong> (or Concept 2, Concept 3)
          </div>
          <div style="font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px;">
            Or click below to reply in 1 click:
          </div>
          <div style="margin-top:6px;">
            {buttons_html}
          </div>
        </td>
      </tr>
    </table>
    '''

    all_cards_html = "".join(cards)
    return header_bar + all_cards_html + cta_and_replies


def convert_markdown_to_clean_html(markdown_text: str) -> str:
    """
    Converts markdown text to clean, email-safe HTML with inline CSS.
    Handles headings, bold, italics, links, lists, blockquotes, and CTA portal buttons.
    """
    if not markdown_text:
        return ""

    # 1. Normalize lines, headings, and bullet characters (•, -, *) to standard markdown lists
    lines = markdown_text.splitlines()
    normalized_lines = []
    
    list_marker_pattern = re.compile(r"^(\s*)(?:[•\-\*]|\d+[\.\)])\s+")
    heading_pattern = re.compile(r"^#{1,6}\s+")
    in_list = False

    for line in lines:
        stripped = line.strip()
        is_list_item = bool(list_marker_pattern.match(stripped))
        is_heading = bool(heading_pattern.match(stripped))

        # If line starts with unicode bullet •, convert to markdown *
        if stripped.startswith("•"):
            indent_count = len(line) - len(line.lstrip())
            indent = " " * indent_count
            line = f"{indent}* {stripped[1:].strip()}"
            is_list_item = True

        # Ensure blank line before heading if previous line was not blank
        if is_heading and normalized_lines and normalized_lines[-1].strip():
            normalized_lines.append("")

        # Ensure blank line before list starts if previous line was not blank
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

    # 2. Check for portal/preview URL to render an authentic CTA button
    url_match = re.search(r'(https?://[^\s<"\']+)', clean_md)
    cta_btn_html = ""
    if url_match:
        raw_url = url_match.group(1).rstrip(".,)")
        is_portal = any(k in raw_url.lower() for k in ["portal", "launch", "preview", "project", "review"])
        cta_label = "Access Co-Founder Portal →" if is_portal else "Review Software Concepts →"
        
        cta_btn_html = f'''
        <div style="margin:22px 0 14px 0;">
          <a href="{raw_url}" target="_blank" style="display:inline-block;padding:12px 24px;background:#0f172a;color:#ffffff;font-size:14px;font-weight:700;text-decoration:none;border-radius:8px;letter-spacing:0.2px;">
            {cta_label}
          </a>
          <div style="margin-top:6px;font-size:11px;color:#64748b;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;">
            Direct link: <a href="{raw_url}" style="color:#0284c7;text-decoration:underline;">{raw_url}</a>
          </div>
        </div>
        '''

    # 3. Parse with Python markdown parser
    raw_html = markdown.markdown(
        clean_md,
        extensions=["extra", "sane_lists", "nl2br"]
    )

    # 4. Post-process and inject clean inline CSS for high-deliverability email client rendering
    # Paragraphs
    raw_html = re.sub(
        r'<p>(.*?)</p>',
        r'<p style="margin:0 0 16px 0;line-height:1.65;color:#1e293b;font-size:15px;">\1</p>',
        raw_html,
        flags=re.DOTALL
    )

    # Headings
    raw_html = re.sub(
        r'<h1>(.*?)</h1>',
        r'<h1 style="margin:20px 0 12px 0;font-size:20px;font-weight:800;color:#0f172a;letter-spacing:-0.4px;line-height:1.3;">\1</h1>',
        raw_html
    )
    raw_html = re.sub(
        r'<h2>(.*?)</h2>',
        r'<h2 style="margin:18px 0 10px 0;font-size:17px;font-weight:700;color:#0f172a;letter-spacing:-0.3px;line-height:1.35;">\1</h2>',
        raw_html
    )
    raw_html = re.sub(
        r'<h3>(.*?)</h3>',
        r'<h3 style="margin:16px 0 8px 0;font-size:15px;font-weight:700;color:#0f172a;letter-spacing:-0.2px;line-height:1.4;">\1</h3>',
        raw_html
    )

    # Bold & Strong
    raw_html = re.sub(
        r'<strong>(.*?)</strong>',
        r'<strong style="color:#0f172a;font-weight:700;">\1</strong>',
        raw_html
    )

    # Emphasis & Italics
    raw_html = re.sub(
        r'<em>(.*?)</em>',
        r'<em style="color:#475569;font-style:italic;">\1</em>',
        raw_html
    )

    # Unordered Lists
    raw_html = re.sub(
        r'<ul>',
        r'<ul style="margin:12px 0 16px 0;padding-left:20px;color:#16a34a;line-height:1.6;">',
        raw_html
    )

    # Ordered Lists
    raw_html = re.sub(
        r'<ol>',
        r'<ol style="margin:12px 0 16px 0;padding-left:22px;color:#0284c7;line-height:1.6;">',
        raw_html
    )

    # List Items
    raw_html = re.sub(
        r'<li>(.*?)</li>',
        r'<li style="margin-bottom:6px;line-height:1.6;color:#1e293b;font-size:15px;"><span style="color:#1e293b;">\1</span></li>',
        raw_html,
        flags=re.DOTALL
    )

    # Blockquotes
    raw_html = re.sub(
        r'<blockquote>\s*<p>(.*?)</p>\s*</blockquote>',
        r'<blockquote style="margin:16px 0;padding:12px 16px;border-left:3px solid #16a34a;background:#f8fafc;border-radius:0 8px 8px 0;color:#334155;font-size:14px;line-height:1.6;font-style:italic;">\1</blockquote>',
        raw_html,
        flags=re.DOTALL
    )

    # Horizontal Rules
    raw_html = re.sub(
        r'<hr\s*/?>',
        r'<hr style="border:none;border-top:1px solid #e2e8f0;margin:20px 0;">',
        raw_html
    )

    # Anchor links
    raw_html = re.sub(
        r'<a\s+href="([^"]+)">([^<]+)</a>',
        r'<a href="\1" target="_blank" style="color:#0284c7;font-weight:600;text-decoration:underline;">\2</a>',
        raw_html
    )

    # Code tags
    raw_html = re.sub(
        r'<code>(.*?)</code>',
        r'<code style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px;background:#f1f5f9;color:#0f172a;border:1px solid #e2e8f0;padding:2px 6px;border-radius:4px;">\1</code>',
        raw_html
    )

    # Append CTA button if present and not already embedded
    if cta_btn_html and "Access Co-Founder Portal" not in raw_html:
        raw_html = f"{raw_html}\n{cta_btn_html}"

    # Format Creator Forge 50/50 Venture Model block if present into responsive hero card
    if "50/50" in raw_html and ("venture model" in raw_html.lower() or "partner" in raw_html.lower()):
        hero_card_html = '''
        <div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;padding:20px;margin:20px 0;box-shadow:0 1px 3px rgba(0,0,0,0.05);">
          <div style="font-size:15px;font-weight:700;color:#0f172a;margin-bottom:8px;">
            CoLaunch <span style="color:#f59e0b;">⚡</span> <span style="color:#94a3b8;font-weight:normal;">|</span> <span style="color:#334155;font-size:14px;font-weight:600;">50/50 Venture Model</span>
          </div>
          <p style="margin:0 0 16px 0;color:#334155;font-size:14px;line-height:1.6;">
            We partner <strong>50/50 with creators</strong> to build custom software tools and monetization apps for their audience. Our team handles 100% of the engineering, hosting, payment setup, and customer support with zero upfront cost to you.
          </p>
          <table width="100%" border="0" cellspacing="0" cellpadding="0" style="border-top:1px solid #f1f5f9;padding-top:12px;">
            <tr>
              <td width="32%" style="background:#f8fafc;border-radius:8px;padding:12px;border:1px solid #f1f5f9;vertical-align:top;">
                <div style="font-size:11px;font-weight:700;color:#0284c7;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">Full Delivery</div>
                <div style="font-size:12px;color:#1e293b;font-weight:500;line-height:1.4;">100% Engineering, UI/UX &amp; QA</div>
              </td>
              <td width="2%">&nbsp;</td>
              <td width="32%" style="background:#f8fafc;border-radius:8px;padding:12px;border:1px solid #f1f5f9;vertical-align:top;">
                <div style="font-size:11px;font-weight:700;color:#16a34a;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">Zero Risk</div>
                <div style="font-size:12px;color:#1e293b;font-weight:500;line-height:1.4;">$0 Upfront Cost &amp; Co-ownership</div>
              </td>
              <td width="2%">&nbsp;</td>
              <td width="32%" style="background:#f8fafc;border-radius:8px;padding:12px;border:1px solid #f1f5f9;vertical-align:top;">
                <div style="font-size:11px;font-weight:700;color:#0f172a;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">Hands-Off Ops</div>
                <div style="font-size:12px;color:#1e293b;font-weight:500;line-height:1.4;">Global Hosting, Billing &amp; 24/7 Support</div>
              </td>
            </tr>
          </table>
        </div>
        '''
        raw_html = re.sub(
            r'<p[^>]*>.*?(?:Creator Forge.*?50/50 Venture Model|We partner.*?50/50 with creators).*?</p>(?:\s*<ul[^>]*>.*?</ul>)?',
            hero_card_html,
            raw_html,
            count=1,
            flags=re.DOTALL | re.IGNORECASE
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
    Formats outreach and replies as a real 1-on-1 personal email message:
    - Clean white/native email canvas (no dark-mode newsletter marketing card)
    - High-deliverability typography (#1e293b on #ffffff)
    - Official Creator Forge geometric logo & authentic executive signature
    - Discrete tracking token
    - Clean concept preview cards for Step 5 / Step 6 proposals
    """
    clean_body, extracted_token = _clean_and_extract_ref_tokens(body_text)
    active_token = tracking_token or extracted_token

    # Guard against accidental "undefined content" or "undefined"
    clean_body = re.sub(r'\bundefined\s+content\b', 'content', clean_body, flags=re.IGNORECASE)
    clean_body = re.sub(r'\bundefined\b', 'community', clean_body, flags=re.IGNORECASE)

    # Convert markdown body into clean styled HTML
    formatted_body_html = convert_markdown_to_clean_html(clean_body)

    # Concept cards if present
    active_concepts = concepts
    if not active_concepts and (concept_title or concept_image_url):
        active_concepts = [{
            "name": concept_title or "Custom Creator SaaS",
            "tagline": "Tailored software engineered for your audience",
            "imageUrl": concept_image_url,
            "mockup": {"previewUrl": concept_image_url, "appUrl": f"{creator_name.lower().replace(' ', '')}app.io" if creator_name else "creatorstudio.app"},
            "revenueModel": "$29/month Tiered Subscription",
            "pricing": {"creatorSplit": "50% Net Co-Founder Share"},
            "coreFeatures": ["Tailored Audience Workflow", "Automated Creator Monetization"]
        }]

    concept_card_html = render_concept_showcase_html(
        concepts=active_concepts,
        concept_image_url=concept_image_url,
        creator_name=creator_name,
        tracking_token=active_token
    )

    signature_html = _render_executive_signature_html(creator_name, active_token)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{html.escape(subject)}</title>
  <style>
    body {{
      margin: 0 !important;
      padding: 0 !important;
      background-color: #ffffff !important;
      color: #1e293b !important;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif !important;
      font-size: 15px !important;
      line-height: 1.65 !important;
      -webkit-font-smoothing: antialiased;
    }}
    p {{
      margin: 0 0 16px 0;
      color: #1e293b;
      font-size: 15px;
      line-height: 1.65;
    }}
    a {{
      color: #0284c7;
      text-decoration: underline;
    }}
    @media only screen and (max-width: 600px) {{
      .email-wrapper {{
        padding: 16px 12px !important;
      }}
    }}
  </style>
</head>
<body style="margin:0;padding:0;background-color:#ffffff;color:#1e293b;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;font-size:15px;line-height:1.65;">
  <div class="email-wrapper" style="max-width:600px;margin:0 auto;padding:24px 20px;background-color:#ffffff;color:#1e293b;">
    <!-- Real Email Message Body -->
    <div style="color:#1e293b;font-size:15px;line-height:1.65;">
      {formatted_body_html}
    </div>

    <!-- Proposal Concepts Breakdown if attached -->
    {concept_card_html}

    <!-- Real Executive Email Signature -->
    {signature_html}
  </div>
</body>
</html>"""
