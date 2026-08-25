"""
============================================================
 ShelfSenseAI — 4-Language Translation System
============================================================

This module provides a lightweight translation system for the
4 official languages of Malaysia:

  1. English (en)     — Default
  2. Bahasa Melayu (ms)
  3. Chinese / Mandarin (zh)
  4. Tamil (ta)

ARCHITECTURE
------------
- Translations are stored as a nested dictionary: translations[key][lang].
- The `_t(key, lang)` helper function looks up a key in the current
  language and falls back to English if the key is missing.
- A Flask context processor injects `_t` and `current_language` into
  all Jinja2 templates, enabling dynamic translation in HTML.

DESIGN DECISIONS
----------------
- No external library (Flask-Babel, gettext) to keep dependencies minimal
  for an FYP project.
- Dictionary-based approach is fast, maintainable, and easy to extend
  with new languages or UI strings.
- All keys use UPPERCASE_SNAKE_CASE for consistency and readability.
- Fallback to English ensures the UI never shows raw keys even if a
  translation is incomplete.
============================================================
"""

# -------------------------------------------------
# Translation Dictionary
# Keys use UPPERCASE_SNAKE_CASE convention.
# Each key maps to a dict of {lang_code: translated_string}.
# -------------------------------------------------
TRANSLATIONS = {
    # --- Navigation ---
    "NAV_DASHBOARD": {
        "en": "Dashboard",
        "ms": "Papan Pemuka",
        "zh": "仪表盘",
        "ta": "டாஷ்போர்ட்",
    },
    "NAV_PRODUCTS": {
        "en": "Products",
        "ms": "Produk",
        "zh": "产品",
        "ta": "தயாரிப்புகள்",
    },
    "NAV_SALES": {
        "en": "Sales",
        "ms": "Jualan",
        "zh": "销售",
        "ta": "விற்பனை",
    },
    "NAV_INVENTORY": {
        "en": "Inventory",
        "ms": "Inventori",
        "zh": "库存",
        "ta": "சரக்கு",
    },
    "NAV_EMPLOYEES": {
        "en": "Employees",
        "ms": "Pekerja",
        "zh": "员工",
        "ta": "ஊழியர்கள்",
    },
    "NAV_NOTIFICATIONS": {
        "en": "Notifications",
        "ms": "Pemberitahuan",
        "zh": "通知",
        "ta": "அறிவிப்புகள்",
    },
    "NAV_LOGOUT": {
        "en": "Logout",
        "ms": "Log Keluar",
        "zh": "退出",
        "ta": "வெளியேறு",
    },
    "NAV_PROFILE": {
        "en": "Profile",
        "ms": "Profil",
        "zh": "个人资料",
        "ta": "சுயவிவரம்",
    },
    "NAV_SETTINGS": {
        "en": "Settings",
        "ms": "Tetapan",
        "zh": "设置",
        "ta": "அமைப்புகள்",
    },

    # --- Dashboard ---
    "DASHBOARD_TITLE": {
        "en": "Dashboard",
        "ms": "Papan Pemuka",
        "zh": "仪表盘",
        "ta": "டாஷ்போர்ட்",
    },
    "TOTAL_PRODUCTS": {
        "en": "Total Products",
        "ms": "Jumlah Produk",
        "zh": "总产品数",
        "ta": "மொத்த தயாரிப்புகள்",
    },
    "LOW_STOCK_ALERTS": {
        "en": "Low Stock Alerts",
        "ms": "Amaran Stok Rendah",
        "zh": "低库存警报",
        "ta": "குறைந்த சரக்கு எச்சரிக்கைகள்",
    },
    "INVENTORY_VALUE": {
        "en": "Inventory Value",
        "ms": "Nilai Inventori",
        "zh": "库存价值",
        "ta": "சரக்கு மதிப்பு",
    },
    "ACTION_REQUIRED": {
        "en": "Action Required",
        "ms": "Tindakan Diperlukan",
        "zh": "需要操作",
        "ta": "நடவடிக்கை தேவை",
    },

    # --- Products ---
    "ADD_PRODUCT": {
        "en": "Add Product",
        "ms": "Tambah Produk",
        "zh": "添加产品",
        "ta": "தயாரிப்பு சேர்",
    },
    "EDIT_PRODUCT": {
        "en": "Edit Product",
        "ms": "Sunting Produk",
        "zh": "编辑产品",
        "ta": "தயாரிப்பைத் திருத்து",
    },
    "PRODUCT_NAME": {
        "en": "Product Name",
        "ms": "Nama Produk",
        "zh": "产品名称",
        "ta": "தயாரிப்பு பெயர்",
    },
    "COST_PRICE": {
        "en": "Cost Price (RM)",
        "ms": "Harga Kos (RM)",
        "zh": "成本价 (RM)",
        "ta": "விலை (RM)",
    },
    "SELLING_PRICE": {
        "en": "Selling Price (RM)",
        "ms": "Harga Jualan (RM)",
        "zh": "售价 (RM)",
        "ta": "விற்பனை விலை (RM)",
    },
    "TARGET_MARGIN": {
        "en": "Target Margin %",
        "ms": "Sasaran Margin %",
        "zh": "目标利润率 %",
        "ta": "இலக்கு விளிம்பு %",
    },
    "CURRENT_STOCK": {
        "en": "Current Stock",
        "ms": "Stok Semasa",
        "zh": "当前库存",
        "ta": "தற்போதைய சரக்கு",
    },

    # --- Market Intelligence ---
    "MARKET_INTELLIGENCE": {
        "en": "Market Intelligence",
        "ms": "Perisikan Pasaran",
        "zh": "市场情报",
        "ta": "சந்தை நுண்ணறிவு",
    },
    "MARKET_SUMMARY": {
        "en": "Market Summary",
        "ms": "Ringkasan Pasaran",
        "zh": "市场摘要",
        "ta": "சந்தை சுருக்கம்",
    },
    "VERIFY": {
        "en": "Verify",
        "ms": "Sahkan",
        "zh": "验证",
        "ta": "சரிபார்",
    },
    "REJECT": {
        "en": "Reject",
        "ms": "Tolak",
        "zh": "拒绝",
        "ta": "நிராகரி",
    },

    # --- Pricing ---
    "PRICING_RECOMMENDATION": {
        "en": "Pricing Recommendation",
        "ms": "Cadangan Harga",
        "zh": "价格建议",
        "ta": "விலை பரிந்துரை",
    },
    "APPLY_RECOMMENDATION": {
        "en": "Apply Recommendation",
        "ms": "Guna Cadangan",
        "zh": "应用建议",
        "ta": "பரிந்துரையைப் பயன்படுத்து",
    },
    "AI_INSIGHT": {
        "en": "AI Assistant Insight",
        "ms": "Pandangan Pembantu AI",
        "zh": "AI助手洞察",
        "ta": "AI உதவியாளர் நுண்ணறிவு",
    },

    # --- Sales ---
    "RECORD_SALE": {
        "en": "Record Sale",
        "ms": "Rekod Jualan",
        "zh": "记录销售",
        "ta": "விற்பனையைப் பதிவு செய்",
    },
    "QUANTITY_SOLD": {
        "en": "Quantity Sold",
        "ms": "Kuantiti Dijual",
        "zh": "销售数量",
        "ta": "விற்கப்பட்ட அளவு",
    },

    # --- Inventory ---
    "RECEIVE_STOCK": {
        "en": "Receive Stock",
        "ms": "Terima Stok",
        "zh": "接收库存",
        "ta": "சரக்கைப் பெறு",
    },
    "ADJUST_STOCK": {
        "en": "Adjust Stock",
        "ms": "Sesuaikan Stok",
        "zh": "调整库存",
        "ta": "சரக்கை சரிசெய்",
    },

    # --- Profile & Settings ---
    "PROFILE": {
        "en": "Profile",
        "ms": "Profil",
        "zh": "个人资料",
        "ta": "சுயவிவரம்",
    },
    "ACCOUNT_DETAILS": {
        "en": "Account Details",
        "ms": "Butiran Akaun",
        "zh": "账户详情",
        "ta": "கணக்கு விவரங்கள்",
    },
    "SHOP_SETTINGS": {
        "en": "Shop Settings",
        "ms": "Tetapan Kedai",
        "zh": "店铺设置",
        "ta": "கடை அமைப்புகள்",
    },
    "LANGUAGE_PREFERENCES": {
        "en": "Language Preferences",
        "ms": "Pilihan Bahasa",
        "zh": "语言偏好",
        "ta": "மொழி விருப்பத்தேர்வுகள்",
    },
    "SAVE_CHANGES": {
        "en": "Save Changes",
        "ms": "Simpan Perubahan",
        "zh": "保存更改",
        "ta": "மாற்றங்களைச் சேமி",
    },
    "UPDATE_PASSWORD": {
        "en": "Update Password",
        "ms": "Kemas Kini Kata Laluan",
        "zh": "更新密码",
        "ta": "கடவுச்சொல்லைப் புதுப்பி",
    },
    "SHOP_NAME": {
        "en": "Shop Name",
        "ms": "Nama Kedai",
        "zh": "店铺名称",
        "ta": "கடையின் பெயர்",
    },
    "STATE": {
        "en": "State",
        "ms": "Negeri",
        "zh": "州",
        "ta": "மாநிலம்",
    },
    "DISTRICT": {
        "en": "District",
        "ms": "Daerah",
        "zh": "地区",
        "ta": "மாவட்டம்",
    },
    "ROLE": {
        "en": "Role",
        "ms": "Peranan",
        "zh": "角色",
        "ta": "பங்கு",
    },
    "OWNER": {
        "en": "Owner",
        "ms": "Pemilik",
        "zh": "所有者",
        "ta": "உரிமையாளர்",
    },
    "MANAGER": {
        "en": "Manager",
        "ms": "Pengurus",
        "zh": "经理",
        "ta": "மேலாளர்",
    },
    "STAFF": {
        "en": "Staff",
        "ms": "Kakitangan",
        "zh": "员工",
        "ta": "ஊழியர்",
    },
    "UNASSIGNED": {
        "en": "Unassigned",
        "ms": "Belum Ditugaskan",
        "zh": "未分配",
        "ta": "நியமிக்கப்படாத",
    },
    "EMAIL": {
        "en": "Email",
        "ms": "E-mel",
        "zh": "电子邮件",
        "ta": "மின்னஞ்சல்",
    },
    "LANGUAGE": {
        "en": "Language",
        "ms": "Bahasa",
        "zh": "语言",
        "ta": "மொழி",
    },
    "BACK": {
        "en": "Back",
        "ms": "Kembali",
        "zh": "返回",
        "ta": "பின் செல்",
    },
    "SUBMIT": {
        "en": "Submit",
        "ms": "Hantar",
        "zh": "提交",
        "ta": "சமர்ப்",
    },
    "CONFIRM": {
        "en": "Confirm",
        "ms": "Sahkan",
        "zh": "确认",
        "ta": "உறுதிப்படுத்து",
    },
    "CANCEL": {
        "en": "Cancel",
        "ms": "Batal",
        "zh": "取消",
        "ta": "ரத்து செய்",
    },

    # --- Footer ---
    "FOOTER_TEXT": {
        "en": "Smart Inventory & Pricing",
        "ms": "Inventori & Harga Pintar",
        "zh": "智能库存与定价",
        "ta": "ஸ்மார்ட் இன்வென்டரி மற்றும் விலைநிர்ணயம்",
    },
}

# -------------------------------------------------
# Language display names
# -------------------------------------------------
LANGUAGE_NAMES = {
    "en": "English",
    "ms": "Bahasa Melayu",
    "zh": "中文 (Mandarin)",
    "ta": "தமிழ் (Tamil)",
}

LANGUAGE_OPTIONS = [(k, v) for k, v in LANGUAGE_NAMES.items()]


def _t(key, lang='en'):
    """Translate a UI string key to the specified language.

    Looks up the key in the TRANSLATIONS dictionary and returns the
    translated string for the given language code. Falls back to English
    if the key is missing or the language is not available.

    Args:
        key: The translation key (UPPERCASE_SNAKE_CASE string).
        lang: The language code ('en', 'ms', 'zh', 'ta'). Defaults to 'en'.

    Returns:
        The translated string, or the English translation as fallback.
    """
    entry = TRANSLATIONS.get(key)
    if entry is None:
        return key  # Unknown key — return the key itself as fallback
    return entry.get(lang, entry.get('en', key))
