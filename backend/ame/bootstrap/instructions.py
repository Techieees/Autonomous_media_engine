from __future__ import annotations

from dataclasses import dataclass

OWNER_ACTION_CATEGORIES = frozenset(
    {"bootstrap", "monetization", "checkpoint", "oauth", "verification", "legal", "app_review"}
)
PASSWORD_POLICY = (
    "AME never asks for passwords, cookies, recovery codes, session tokens, or bank details. "
    "Complete every login, consent, KYC, and payout step on the official website in your own browser."
)


@dataclass(frozen=True)
class ChecklistSpec:
    key: str
    title: str
    category: str
    platform: str | None
    blocking: bool = False


CHECKLIST_SPECS: tuple[ChecklistSpec, ...] = (
    ChecklistSpec(
        key="youtube.dedicated_account",
        title="Create dedicated Google/YouTube brand account",
        category="bootstrap",
        platform="youtube",
    ),
    ChecklistSpec(
        key="youtube.oauth",
        title="Complete OAuth",
        category="bootstrap",
        platform="youtube",
    ),
    ChecklistSpec(
        key="instagram.dedicated_account",
        title="Create dedicated Instagram account",
        category="bootstrap",
        platform="instagram",
    ),
    ChecklistSpec(
        key="instagram.professional_conversion",
        title="Convert/configure eligible professional account",
        category="bootstrap",
        platform="instagram",
    ),
    ChecklistSpec(
        key="instagram.oauth",
        title="Complete Meta authorization",
        category="bootstrap",
        platform="instagram",
    ),
    ChecklistSpec(
        key="tiktok.dedicated_account",
        title="Create dedicated account",
        category="bootstrap",
        platform="tiktok",
    ),
    ChecklistSpec(
        key="tiktok.developer_app",
        title="Configure developer application",
        category="bootstrap",
        platform="tiktok",
    ),
    ChecklistSpec(
        key="tiktok.app_review",
        title="Complete platform review/authorization if required",
        category="bootstrap",
        platform="tiktok",
    ),
    ChecklistSpec(
        key="monetization.eligibility",
        title="Not yet eligible",
        category="monetization",
        platform=None,
    ),
)

SPECS_BY_KEY = {spec.key: spec for spec in CHECKLIST_SPECS}
SPECS_BY_TITLE = {spec.title: spec for spec in CHECKLIST_SPECS}


def _block(*paragraphs: str) -> str:
    return "\n\n".join(paragraphs)


def generate_owner_instructions(key: str) -> str:
    generators = {
        "youtube.dedicated_account": _youtube_dedicated_account,
        "youtube.oauth": _youtube_oauth,
        "instagram.dedicated_account": _instagram_dedicated_account,
        "instagram.professional_conversion": _instagram_professional,
        "instagram.oauth": _instagram_oauth,
        "tiktok.dedicated_account": _tiktok_dedicated_account,
        "tiktok.developer_app": _tiktok_developer_app,
        "tiktok.app_review": _tiktok_app_review,
        "monetization.eligibility": _monetization,
    }
    generator = generators.get(key)
    if generator is None:
        raise KeyError(f"unknown checklist key: {key}")
    return generator()


def generate_platform_instructions(platform: str) -> str:
    keys = [spec.key for spec in CHECKLIST_SPECS if spec.platform == platform]
    if not keys:
        raise KeyError(f"unknown platform: {platform}")
    return _block(*(generate_owner_instructions(key) for key in keys))


def generate_first_run_brief() -> str:
    return _block(
        "No production social accounts connected. That is expected on first launch.",
        "Research, scripting, rendering, and dry-run publishing can continue. "
        "Production publishers stay gated until official OAuth completes.",
        PASSWORD_POLICY,
        generate_platform_instructions("youtube"),
        generate_platform_instructions("instagram"),
        generate_platform_instructions("tiktok"),
        generate_owner_instructions("monetization.eligibility"),
    )


def _youtube_dedicated_account() -> str:
    return _block(
        "Create a dedicated Google account and YouTube channel for this brand. "
        "Do not reuse a personal channel that you are not willing to operate through official APIs.",
        "Use https://accounts.google.com and https://www.youtube.com in your own browser. "
        + PASSWORD_POLICY,
        "Recommended: a Brand Account / YouTube channel that you control, with 2-Step Verification "
        "enabled on the Google account. Keep recovery methods only in your password manager.",
        "When the channel exists, mark this item complete. AME does not create Google accounts.",
    )


def _youtube_oauth() -> str:
    return _block(
        "Create a Google Cloud project, enable YouTube Data API v3, and create a Web OAuth client.",
        "Put YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET in the AME server environment only. "
        "Never paste secrets into chat, tickets, or Git. Set YOUTUBE_REDIRECT_URI to the AME callback "
        "(default http://localhost:8000/api/v1/oauth/youtube/callback) and add that exact URI "
        "to the Google Cloud authorized redirect list.",
        "Add the dedicated channel owner as a test user while the OAuth consent screen is in testing.",
        "Start OAuth from the AME dashboard Connect action. Google will ask you to sign in and grant "
        "upload, readonly, and analytics scopes. AME stores only encrypted tokens and never asks "
        "for the Google password.",
        "If Google shows an extra confirmation or unverified-app warning, accept it yourself. "
        "AME cannot click through consent screens.",
    )


def _instagram_dedicated_account() -> str:
    return _block(
        "Create a dedicated Instagram account for this brand at https://www.instagram.com. "
        "Do not share the Instagram password with AME or anyone in chat.",
        PASSWORD_POLICY,
        "Use an email and phone number you control. Enable the official app's security options. "
        "AME does not create Instagram accounts and will not operate a personal login form.",
    )


def _instagram_professional() -> str:
    return _block(
        "In the Instagram app, convert the dedicated account to a Professional account "
        "(Business or Creator). This is a Meta requirement for the official content publishing APIs.",
        "Follow Instagram Settings → Account type and tools → Switch to professional account. "
        "Complete any in-app professional conversion screens yourself.",
        "If Meta asks you to connect a Facebook Page, do that in the official Meta/Instagram UI. "
        "AME cannot perform professional conversion, Page linking, or identity checks.",
        PASSWORD_POLICY,
        "Leave this item open until the Instagram account is professional and eligible for "
        "instagram_business_content_publish. Production publish stays gated until then.",
    )


def _instagram_oauth() -> str:
    return _block(
        "In Meta for Developers, create (or reuse) an app, add the Instagram product, and configure "
        "Instagram API with Instagram Login. Use a dedicated app for AME.",
        "Put META_APP_ID and META_APP_SECRET in the AME server environment only. "
        "Set META_REDIRECT_URI to the AME Instagram callback and add that exact URI in the Meta app.",
        "Start authorization from the AME dashboard. Sign in as the dedicated professional Instagram "
        "account in your browser and grant business basic, content publish, and insights permissions.",
        "AME never asks for the Instagram or Facebook password. If Meta shows App Review, CAPTCHA, "
        "or business verification, complete those on Meta's site and keep this checklist updated.",
    )


def _tiktok_dedicated_account() -> str:
    return _block(
        "Create a dedicated TikTok account for this brand at https://www.tiktok.com. "
        "Do not reuse an account you are unwilling to authorize through TikTok's official Login Kit.",
        PASSWORD_POLICY,
        "Enable the official app's security options. AME does not create TikTok accounts.",
    )


def _tiktok_developer_app() -> str:
    return _block(
        "In TikTok for Developers, create an app for AME and configure Login Kit plus the "
        "Content Posting API. Use the dedicated TikTok account as the app owner or admin.",
        "Put TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET in the AME server environment only. "
        "Set TIKTOK_REDIRECT_URI to the AME TikTok callback and register that exact URI on the app.",
        "Start OAuth from the AME dashboard. TikTok will ask the owner to authorize "
        "user.info.basic, video.upload, and video.publish. AME stores encrypted tokens only.",
        PASSWORD_POLICY,
    )


def _tiktok_app_review() -> str:
    return _block(
        "TikTok requires app review before unaudited apps can publish to production accounts. "
        "Submit the official Content Posting API / Login Kit review from TikTok for Developers.",
        "While review is pending, AME records the connection as needs_platform_review and continues "
        "research and dry-run work. Do not ask AME to bypass review, CAPTCHA, or anti-bot checks.",
        "After TikTok approves the app and production publish is permitted, mark this item complete. "
        "If TikTok still requires a human confirmation on each post, leave a human action open; "
        "AME will persist awaiting_platform_required_approval instead of inventing a publish.",
        PASSWORD_POLICY,
    )


def _monetization() -> str:
    return _block(
        "Monetization is not yet eligible. YouTube Partner Program, Instagram/Meta bonuses, and "
        "TikTok Creativity Program each have their own eligibility, identity, and payout requirements.",
        "Apply only on the official platform sites when the channel meets their public thresholds. "
        "AME must never collect KYC documents, tax IDs, bank details, or payout account changes.",
        "AME records only actual platform-reported revenue (kind=actual) or explicitly labeled "
        "forecasts (kind=forecast). It will not fabricate earnings.",
        "Leave this item open until a platform confirms eligibility in its official UI. "
        + PASSWORD_POLICY,
    )
