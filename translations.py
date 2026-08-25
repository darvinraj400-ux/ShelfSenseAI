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
    # === Navigation ===
    "NAV_DASHBOARD": {
        "en": "Dashboard", "ms": "Papan Pemuka", "zh": "仪表盘", "ta": "டாஷ்போர்ட்",
    },
    "NAV_PRODUCTS": {
        "en": "Products", "ms": "Produk", "zh": "产品", "ta": "தயாரிப்புகள்",
    },
    "NAV_SALES": {
        "en": "Sales", "ms": "Jualan", "zh": "销售", "ta": "விற்பனை",
    },
    "NAV_INVENTORY": {
        "en": "Inventory", "ms": "Inventori", "zh": "库存", "ta": "சரக்கு",
    },
    "NAV_EMPLOYEES": {
        "en": "Employees", "ms": "Pekerja", "zh": "员工", "ta": "ஊழியர்கள்",
    },
    "NAV_NOTIFICATIONS": {
        "en": "Notifications", "ms": "Pemberitahuan", "zh": "通知", "ta": "அறிவிப்புகள்",
    },
    "NAV_LOGOUT": {
        "en": "Logout", "ms": "Log Keluar", "zh": "退出", "ta": "வெளியேறு",
    },
    "NAV_PROFILE": {
        "en": "Profile", "ms": "Profil", "zh": "个人资料", "ta": "சுயவிவரம்",
    },
    "NAV_SETTINGS": {
        "en": "Settings", "ms": "Tetapan", "zh": "设置", "ta": "அமைப்புகள்",
    },

    # === Common ===
    "BACK": {
        "en": "Back", "ms": "Kembali", "zh": "返回", "ta": "பின் செல்",
    },
    "SUBMIT": {
        "en": "Submit", "ms": "Hantar", "zh": "提交", "ta": "சமர்ப்",
    },
    "CONFIRM": {
        "en": "Confirm", "ms": "Sahkan", "zh": "确认", "ta": "உறுதிப்படுத்து",
    },
    "CANCEL": {
        "en": "Cancel", "ms": "Batal", "zh": "取消", "ta": "ரத்து செய்",
    },
    "EDIT": {
        "en": "Edit", "ms": "Sunting", "zh": "编辑", "ta": "திருத்து",
    },
    "DELETE": {
        "en": "Delete", "ms": "Padam", "zh": "删除", "ta": "நீக்கு",
    },
    "ACTIVE": {
        "en": "Active", "ms": "Aktif", "zh": "活跃", "ta": "செயலில்",
    },
    "PENDING": {
        "en": "Pending", "ms": "Menunggu", "zh": "待处理", "ta": "நிலுவையில்",
    },
    "EXPIRED": {
        "en": "Expired", "ms": "Tamat tempoh", "zh": "已过期", "ta": "காலாவதியானது",
    },
    "ACCEPT": {
        "en": "Accept", "ms": "Terima", "zh": "接受", "ta": "ஏற்கவும்",
    },
    "REJECT": {
        "en": "Reject", "ms": "Tolak", "zh": "拒绝", "ta": "நிராகரி",
    },
    "REVOKE": {
        "en": "Revoke", "ms": "Batal", "zh": "撤销", "ta": "நீக்கு",
    },
    "REMOVE": {
        "en": "Remove", "ms": "Buang", "zh": "移除", "ta": "நீக்கு",
    },
    "VIEW_ONLY": {
        "en": "view only", "ms": "lihat sahaja", "zh": "仅查看", "ta": "பார்வை மட்டும்",
    },

    # === Dashboard ===
    "DASHBOARD_TITLE": {
        "en": "Dashboard", "ms": "Papan Pemuka", "zh": "仪表盘", "ta": "டாஷ்போர்ட்",
    },
    "TOTAL_PRODUCTS": {
        "en": "Total Products", "ms": "Jumlah Produk", "zh": "总产品数", "ta": "மொத்த தயாரிப்புகள்",
    },
    "LOW_STOCK_ALERTS": {
        "en": "Low Stock Alerts", "ms": "Amaran Stok Rendah", "zh": "低库存警报", "ta": "குறைந்த சரக்கு எச்சரிக்கைகள்",
    },
    "INVENTORY_VALUE": {
        "en": "Inventory Value", "ms": "Nilai Inventori", "zh": "库存价值", "ta": "சரக்கு மதிப்பு",
    },
    "ACTION_REQUIRED": {
        "en": "Action Required", "ms": "Tindakan Diperlukan", "zh": "需要操作", "ta": "நடவடிக்கை தேவை",
    },
    "BRAND": {
        "en": "Brand", "ms": "Jenama", "zh": "品牌", "ta": "பிராண்ட்",
    },
    "CATEGORY": {
        "en": "Category", "ms": "Kategori", "zh": "类别", "ta": "வகை",
    },
    "SIZE": {
        "en": "Size", "ms": "Saiz", "zh": "尺寸", "ta": "அளவு",
    },
    "ACTIONS": {
        "en": "Actions", "ms": "Tindakan", "zh": "操作", "ta": "செயல்கள்",
    },
    "SUGGESTED_PRICE": {
        "en": "Suggested Price", "ms": "Harga Cadangan", "zh": "建议价格", "ta": "பரிந்துரைக்கப்பட்ட விலை",
    },
    "STOCK_LEVELS_HEALTHY": {
        "en": "All stock levels healthy", "ms": "Semua paras stok sihat", "zh": "所有库存水平正常", "ta": "அனைத்து சரக்கு நிலைகளும் ஆரோக்கியமானவை",
    },
    "TOTAL_COST_STOCK": {
        "en": "Total cost x stock", "ms": "Jumlah kos x stok", "zh": "总成本 x 库存", "ta": "மொத்த செலவு x சரக்கு",
    },
    "NO_NOTIFICATIONS": {
        "en": "No notifications yet.", "ms": "Tiada pemberitahuan lagi.", "zh": "暂无通知。", "ta": "இன்னும் அறிவிப்புகள் இல்லை.",
    },
    "INVITE_UNASSIGNED_HINT": {
        "en": "Your account is not linked to a shop yet. If you received an invitation, accept it from",
        "ms": "Akaun anda belum dipautkan ke kedai. Jika anda menerima jemputan, terimanya dari",
        "zh": "您的账户尚未关联店铺。如果您收到了邀请，请从以下链接接受",
        "ta": "உங்கள் கணக்கு இன்னும் கடையுடன் இணைக்கப்படவில்லை. நீங்கள் ஒரு அழைப்பைப் பெற்றிருந்தால், அதை இங்கிருந்து ஏற்றுக்கொள்ளுங்கள்",
    },

    # === Products ===
    "ADD_PRODUCT": {
        "en": "Add Product", "ms": "Tambah Produk", "zh": "添加产品", "ta": "தயாரிப்பு சேர்",
    },
    "EDIT_PRODUCT": {
        "en": "Edit Product", "ms": "Sunting Produk", "zh": "编辑产品", "ta": "தயாரிப்பைத் திருத்து",
    },
    "PRODUCT_NAME": {
        "en": "Product Name", "ms": "Nama Produk", "zh": "产品名称", "ta": "தயாரிப்பு பெயர்",
    },
    "COST_PRICE": {
        "en": "Cost Price (RM)", "ms": "Harga Kos (RM)", "zh": "成本价 (RM)", "ta": "விலை (RM)",
    },
    "SELLING_PRICE": {
        "en": "Selling Price (RM)", "ms": "Harga Jualan (RM)", "zh": "售价 (RM)", "ta": "விற்பனை விலை (RM)",
    },
    "TARGET_MARGIN": {
        "en": "Target Margin %", "ms": "Sasaran Margin %", "zh": "目标利润率 %", "ta": "இலக்கு விளிம்பு %",
    },
    "CURRENT_STOCK": {
        "en": "Current Stock", "ms": "Stok Semasa", "zh": "当前库存", "ta": "தற்போதைய சரக்கு",
    },
    "NO_PRODUCTS": {
        "en": "No products yet.", "ms": "Tiada produk lagi.", "zh": "暂无产品。", "ta": "இன்னும் தயாரிப்புகள் இல்லை.",
    },
    "ADD_FIRST_PRODUCT": {
        "en": "Add your first product", "ms": "Tambah produk pertama anda", "zh": "添加您的第一个产品", "ta": "உங்கள் முதல் தயாரிப்பைச் சேர்க்கவும்",
    },
    "ADD_PRODUCT_FIRST": {
        "en": "Add a product first", "ms": "Tambah produk terlebih dahulu", "zh": "请先添加产品", "ta": "முதலில் ஒரு தயாரிப்பைச் சேர்க்கவும்",
    },
    "PRODUCT_INFO": {
        "en": "Product Information", "ms": "Maklumat Produk", "zh": "产品信息", "ta": "தயாரிப்பு தகவல்",
    },
    "QUANTITY_PER_PRODUCT": {
        "en": "Quantity per Product", "ms": "Kuantiti Setiap Produk", "zh": "每产品数量", "ta": "ஒரு தயாரிப்புக்கான அளவு",
    },
    "QUANTITY_HINT": {
        "en": "Quantity and unit describe the size of one product/package, not your current stock.",
        "ms": "Kuantiti dan unit menggambarkan saiz satu produk/bungkusan, bukan stok semasa anda.",
        "zh": "数量和单位描述的是单个产品/包装的大小，而非当前库存。",
        "ta": "அளவு மற்றும் அலகு ஒரு தயாரிப்பு/பேக்கேஜின் அளவை விவரிக்கிறது, உங்கள் தற்போதைய சரக்கு அல்ல.",
    },
    "AI_ASSISTED_PRICING": {
        "en": "AI Assisted Pricing", "ms": "Harga Berbantuan AI", "zh": "AI辅助定价", "ta": "AI உதவி விலைநிர்ணயம்",
    },
    "MANAGE_PRODUCT_INFO": {
        "en": "Manage your product information and let AI calculate the best selling price.",
        "ms": "Urus maklumat produk anda dan biarkan AI mengira harga jualan terbaik.",
        "zh": "管理您的产品信息，让AI计算最佳销售价格。",
        "ta": "உங்கள் தயாரிப்பு தகவலை நிர்வகிக்கவும், AI சிறந்த விற்பனை விலையை கணக்கிட அனுமதிக்கவும்.",
    },
    "ENTER_PRODUCT_DETAILS": {
        "en": "Enter your product details below. ShelfSense AI will use these values to generate smart pricing recommendations.",
        "ms": "Masukkan butiran produk anda di bawah. ShelfSense AI akan menggunakan nilai ini untuk menjana cadangan harga pintar.",
        "zh": "在下方输入您的产品详情。ShelfSense AI 将使用这些值生成智能定价建议。",
        "ta": "உங்கள் தயாரிப்பு விவரங்களை கீழே உள்ளிடவும். ShelfSense AI இந்த மதிப்புகளைப் பயன்படுத்தி புத்திசாலி விலைநிர்ணய பரிந்துரைகளை உருவாக்கும்.",
    },
    "AI_PRICING_TIP": {
        "en": "AI Pricing Tip",
        "ms": "Tip Harga AI",
        "zh": "AI定价提示",
        "ta": "AI விலைநிர்ணய குறிப்பு",
    },
    "AI_PRICING_TIP_TEXT": {
        "en": "Providing an accurate cost price and realistic target margin helps ShelfSense AI generate better pricing recommendations and improve profitability.",
        "ms": "Memberikan harga kos yang tepat dan margin sasaran yang realistik membantu ShelfSense AI menjana cadangan harga yang lebih baik dan meningkatkan keuntungan.",
        "zh": "提供准确的成本价和实际的目标利润率有助于 ShelfSense AI 生成更好的定价建议并提高盈利能力。",
        "ta": "துல்லியமான செலவு விலை மற்றும் யதார்த்தமான இலக்கு விளிம்பை வழங்குவது ShelfSense AI க்கு சிறந்த விலைநிர்ணய பரிந்துரைகளை உருவாக்கவும் லாபத்தை மேம்படுத்தவும் உதவுகிறது.",
    },
    "PRICING_HISTORY": {
        "en": "Price History", "ms": "Sejarah Harga", "zh": "价格历史", "ta": "விலை வரலாறு",
    },
    "DATE": {
        "en": "Date", "ms": "Tarikh", "zh": "日期", "ta": "தேதி",
    },
    "MARGIN": {
        "en": "Margin %", "ms": "Margin %", "zh": "利润率 %", "ta": "விளிம்பு %",
    },
    "COST_HISTORY_NOTE": {
        "en": "Every cost/margin change is logged so margin increases can be traced against cost justification.",
        "ms": "Setiap perubahan kos/margin direkodkan supaya peningkatan margin dapat dijejak terhadap justifikasi kos.",
        "zh": "每次成本/利润率变更都会被记录，以便追踪利润率增长是否合理。",
        "ta": "ஒவ்வொரு செலவு/விளிம்பு மாற்றமும் பதிவு செய்யப்படுகிறது, இதனால் விளிம்பு அதிகரிப்புகளை செலவு நியாயப்படுத்தலுடன் கண்காணிக்க முடியும்.",
    },

    # === Market Intelligence ===
    "MARKET_INTELLIGENCE": {
        "en": "Market Intelligence", "ms": "Perisikan Pasaran", "zh": "市场情报", "ta": "சந்தை நுண்ணறிவு",
    },
    "MARKET_SUMMARY": {
        "en": "Market Summary", "ms": "Ringkasan Pasaran", "zh": "市场摘要", "ta": "சந்தை சுருக்கம்",
    },
    "MARKET_LINKS": {
        "en": "Market Links", "ms": "Pautan Pasaran", "zh": "市场链接", "ta": "சந்தை இணைப்புகள்",
    },
    "SEARCH_MARKET_MATCHES": {
        "en": "Search Market Matches", "ms": "Cari Padanan Pasaran", "zh": "搜索市场匹配", "ta": "சந்தை பொருத்தங்களைத் தேடு",
    },
    "VERIFY": {
        "en": "Verify", "ms": "Sahkan", "zh": "验证", "ta": "சரிபார்",
    },
    "REJECT_MATCH": {
        "en": "Reject", "ms": "Tolak", "zh": "拒绝", "ta": "நிராகரி",
    },
    "REMOVE_MATCH": {
        "en": "Remove", "ms": "Buang", "zh": "移除", "ta": "நீக்கு",
    },
    "SUGGESTED_MATCHES": {
        "en": "Suggested Matches", "ms": "Padanan yang Dicadangkan", "zh": "建议匹配", "ta": "பரிந்துரைக்கப்பட்ட பொருத்தங்கள்",
    },
    "VERIFIED_MATCHES": {
        "en": "Verified Matches", "ms": "Padanan Disahkan", "zh": "已验证匹配", "ta": "சரிபார்க்கப்பட்ட பொருத்தங்கள்",
    },
    "NO_MARKET_LINKS": {
        "en": "No verified market links. Search for matches to connect this product with KPDN market data.",
        "ms": "Tiada pautan pasaran yang disahkan. Cari padanan untuk menghubungkan produk ini dengan data pasaran KPDN.",
        "zh": "暂无已验证的市场链接。搜索匹配项以将此产品与KPDN市场数据关联。",
        "ta": "சரிபார்க்கப்பட்ட சந்தை இணைப்புகள் இல்லை. இந்த தயாரிப்பை KPDN சந்தை தரவுடன் இணைக்க பொருத்தங்களைத் தேடுங்கள்.",
    },
    "NO_SUGGESTED_MATCHES": {
        "en": "No suggested matches found.",
        "ms": "Tiada padanan yang dicadangkan.",
        "zh": "未找到建议匹配。",
        "ta": "பரிந்துரைக்கப்பட்ட பொருத்தங்கள் இல்லை.",
    },
    "CONFIDENCE": {
        "en": "Confidence", "ms": "Keyakinan", "zh": "置信度", "ta": "நம்பிக்கை",
    },
    "CLOSE": {
        "en": "Close", "ms": "Tutup", "zh": "关闭", "ta": "மூடு",
    },

    # === Competitor Pricing Table ===
    "RECENT_COMPETITOR_PRICING": {
        "en": "Recent Competitor Pricing (KPDN Open Data)",
        "ms": "Harga Pesaing Terkini (Data Terbuka KPDN)",
        "zh": "近期竞争对手定价（KPDN公开数据）",
        "ta": "சமீபத்திய போட்டியாளர் விலை (KPDN திறந்த தரவு)",
    },
    "LOCATION": {
        "en": "Location", "ms": "Lokasi", "zh": "位置", "ta": "இடம்",
    },
    "PRICE_RM": {
        "en": "Price (RM)", "ms": "Harga (RM)", "zh": "价格 (RM)", "ta": "விலை (RM)",
    },
    "NO_COMPETITOR_DATA": {
        "en": "No detailed observations available.",
        "ms": "Tiada pemerhatian terperinci tersedia.",
        "zh": "暂无详细观测数据。",
        "ta": "விரிவான கண்காணிப்புகள் எதுவும் கிடைக்கவில்லை.",
    },

    # === Pricing Recommendation ===
    "PRICING_RECOMMENDATION": {
        "en": "Pricing Recommendation", "ms": "Cadangan Harga", "zh": "价格建议", "ta": "விலை பரிந்துரை",
    },
    "APPLY_RECOMMENDATION": {
        "en": "Apply Recommendation", "ms": "Guna Cadangan", "zh": "应用建议", "ta": "பரிந்துரையைப் பயன்படுத்து",
    },
    "AI_INSIGHT": {
        "en": "AI Assistant Insight", "ms": "Pandangan Pembantu AI", "zh": "AI助手洞察", "ta": "AI உதவியாளர் நுண்ணறிவு",
    },
    "RECOMMENDED_PRICE": {
        "en": "Recommended Price", "ms": "Harga Disyorkan", "zh": "推荐价格", "ta": "பரிந்துரைக்கப்பட்ட விலை",
    },
    "MARKET_MEDIAN": {
        "en": "Market Median", "ms": "Median Pasaran", "zh": "市场中位数", "ta": "சந்தை நடுத்தரம்",
    },

    # === Sales ===
    "RECORD_SALE": {
        "en": "Record Sale", "ms": "Rekod Jualan", "zh": "记录销售", "ta": "விற்பனையைப் பதிவு செய்",
    },
    "SALES_HISTORY": {
        "en": "Sales History", "ms": "Sejarah Jualan", "zh": "销售历史", "ta": "விற்பனை வரலாறு",
    },
    "QUANTITY_SOLD": {
        "en": "Quantity Sold", "ms": "Kuantiti Dijual", "zh": "销售数量", "ta": "விற்கப்பட்ட அளவு",
    },
    "SALE_DESC": {
        "en": "Log a completed sale. The selling price is a snapshot of what was actually charged at that time — it stays in history even if the product price changes later.",
        "ms": "Rekodkan jualan yang selesai. Harga jualan adalah gambaran apa yang sebenarnya dikenakan pada masa itu — ia kekal dalam sejarah walaupun harga produk berubah kemudian.",
        "zh": "记录已完成的销售。售价是当时实际收费的快照——即使产品价格后来发生变化，它仍保留在历史记录中。",
        "ta": "நிறைவடைந்த விற்பனையைப் பதிவு செய்யுங்கள். விற்பனை விலை என்பது அந்த நேரத்தில் உண்மையில் வசூலிக்கப்பட்டதன் படமாகும் — தயாரிப்பு விலை பின்னர் மாறினாலும் அது வரலாற்றில் இருக்கும்.",
    },
    "REVENUE": {
        "en": "Revenue", "ms": "Hasil", "zh": "收入", "ta": "வருவாய்",
    },
    "REVENUE_NOTE": {
        "en": "Revenue is calculated (quantity x per-unit price) — it is not stored.",
        "ms": "Hasil dikira (kuantiti x harga seunit) — ia tidak disimpan.",
        "zh": "收入按数量×单价计算，不会被存储。",
        "ta": "வருவாய் கணக்கிடப்படுகிறது (அளவு x யூனிட் விலை) — இது சேமிக்கப்படாது.",
    },
    "NO_SALES": {
        "en": "No sales recorded yet.", "ms": "Tiada jualan direkodkan lagi.", "zh": "暂无销售记录。", "ta": "இன்னும் விற்பனை பதிவு செய்யப்படவில்லை.",
    },
    "RECORD_FIRST_SALE": {
        "en": "Record the first sale", "ms": "Rekod jualan pertama", "zh": "记录第一笔销售", "ta": "முதல் விற்பனையைப் பதிவு செய்",
    },
    "SALE_HINT": {
        "en": "Defaults to the product's current price.",
        "ms": "Mengikut harga semasa produk secara lalai.",
        "zh": "默认为产品的当前价格。",
        "ta": "தயாரிப்பின் தற்போதைய விலையாக இருக்கும்.",
    },
    "NO_PRICE_SET": {
        "en": "This product has no current price set — enter the actual sale price.",
        "ms": "Produk ini tiada harga semasa ditetapkan — masukkan harga jualan sebenar.",
        "zh": "此产品未设置当前价格 - 请输入实际销售价格。",
        "ta": "இந்த தயாரிப்புக்கு தற்போதைய விலை நிர்ணயிக்கப்படவில்லை — உண்மையான விற்பனை விலையை உள்ளிடவும்.",
    },
    "RECEIVE_STOCK_HINT": {
        "en": "Use Receive when new stock arrives; Adjust for corrections (e.g. damaged or counted stock).",
        "ms": "Gunakan Terima apabila stok baharu tiba; Laraskan untuk pembetulan (cth. stok rosak atau dikira).",
        "zh": "新货到货时使用接收；用于修正（如损坏或盘点库存）。",
        "ta": "புதிய சரக்கு வரும்போது பெறுங்கள்; திருத்தங்களுக்கு சரிசெய்யுங்கள் (எ.கா. சேதமடைந்த அல்லது எண்ணப்பட்ட சரக்கு).",
    },

    # === Inventory ===
    "INVENTORY_TITLE": {
        "en": "Inventory", "ms": "Inventori", "zh": "库存", "ta": "சரக்கு",
    },
    "STOCK_LEVELS_FOR": {
        "en": "Stock levels for", "ms": "Paras stok untuk", "zh": "库存水平", "ta": "சரக்கு நிலைகள்",
    },
    "PRODUCT_SIZE": {
        "en": "Product Size", "ms": "Saiz Produk", "zh": "产品尺寸", "ta": "தயாரிப்பு அளவு",
    },
    "MINIMUM_STOCK": {
        "en": "Minimum Stock", "ms": "Stok Minimum", "zh": "最低库存", "ta": "குறைந்தபட்ச சரக்கு",
    },
    "LAST_UPDATED": {
        "en": "Last Updated", "ms": "Kemaskini Terakhir", "zh": "最后更新", "ta": "கடைசியாக புதுப்பிக்கப்பட்டது",
    },
    "LOW": {
        "en": "low", "ms": "rendah", "zh": "低", "ta": "குறைவு",
    },
    "RECEIVE": {
        "en": "Receive", "ms": "Terima", "zh": "接收", "ta": "பெறு",
    },
    "ADJUST": {
        "en": "Adjust", "ms": "Laraskan", "zh": "调整", "ta": "சரிசெய்",
    },
    "STOCK_MOVEMENTS": {
        "en": "Recent Stock Movements", "ms": "Pergerakan Stok Terkini", "zh": "最近库存变动", "ta": "சமீபத்திய சரக்கு நகர்வுகள்",
    },
    "CHANGE": {
        "en": "Change", "ms": "Perubahan", "zh": "变动", "ta": "மாற்றம்",
    },
    "REASON": {
        "en": "Reason", "ms": "Sebab", "zh": "原因", "ta": "காரணம்",
    },
    "BY": {
        "en": "By", "ms": "Oleh", "zh": "由", "ta": "மூலம்",
    },
    "STOCK_HINT": {
        "en": "Product Size (e.g. 1 kg) is the size of ONE package — it is not your stock. Current Stock is how many sellable units you have.",
        "ms": "Saiz Produk (cth. 1 kg) ialah saiz SATU bungkusan — ia bukan stok anda. Stok Semasa ialah berapa banyak unit yang boleh dijual.",
        "zh": "产品尺寸（如1公斤）是单个包装的大小，不是您的库存。当前库存是您拥有的可销售单位数量。",
        "ta": "தயாரிப்பு அளவு (எ.கா. 1 கிலோ) என்பது ஒரு பேக்கேஜின் அளவு — அது உங்கள் சரக்கு அல்ல. தற்போதைய சரக்கு என்பது நீங்கள் விற்கக்கூடிய யூனிட்களின் எண்ணிக்கை.",
    },
    "NO_INV_PRODUCTS": {
        "en": "No products yet.", "ms": "Tiada produk lagi.", "zh": "暂无产品。", "ta": "இன்னும் தயாரிப்புகள் இல்லை.",
    },

    # === Employees ===
    "EMPLOYEE_MANAGEMENT": {
        "en": "Employee Management", "ms": "Pengurusan Pekerja", "zh": "员工管理", "ta": "ஊழியர் நிர்வாகம்",
    },
    "OWNER_ONLY_INVITE": {
        "en": "only the Owner can invite employees.", "ms": "hanya Pemilik boleh menjemput pekerja.", "zh": "只有所有者可以邀请员工。", "ta": "உரிமையாளர் மட்டுமே ஊழியர்களை அழைக்க முடியும்.",
    },
    "CURRENT_EMPLOYEES": {
        "en": "Current Employees", "ms": "Pekerja Semasa", "zh": "当前员工", "ta": "தற்போதைய ஊழியர்கள்",
    },
    "INVITE_EMPLOYEE": {
        "en": "Invite Employee", "ms": "Jemput Pekerja", "zh": "邀请员工", "ta": "ஊழியரை அழையுங்கள்",
    },
    "INVITATIONS": {
        "en": "Invitations", "ms": "Jemputan", "zh": "邀请", "ta": "அழைப்புகள்",
    },
    "NO_EMPLOYEES_YET": {
        "en": "No employees yet — invite your first manager or staff member below.",
        "ms": "Tiada pekerja lagi — jemput pengurus atau ahli pasukan pertama anda di bawah.",
        "zh": "暂无员工 - 请在下方邀请您的第一位经理或员工。",
        "ta": "இன்னும் ஊழியர்கள் இல்லை - உங்கள் முதல் மேலாளர் அல்லது ஊழியரை கீழே அழையுங்கள்.",
    },
    "INVITE_INFO": {
        "en": "After creating an invitation, the generated link is shown on screen — share it with the employee (no email service in this phase). Invitations expire after 48 hours.",
        "ms": "Selepas membuat jemputan, pautan yang dijana ditunjukkan pada skrin — kongsikannya dengan pekerja (tiada perkhidmatan e-mel pada fasa ini). Jemputan tamat tempoh selepas 48 jam.",
        "zh": "创建邀请后，生成的链接将显示在屏幕上 - 与员工分享（此阶段不提供电子邮件服务）。邀请在48小时后过期。",
        "ta": "ஒரு அழைப்பை உருவாக்கிய பிறகு, உருவாக்கப்பட்ட இணைப்பு திரையில் காட்டப்படும் — ஊழியருடன் பகிர்ந்து கொள்ளுங்கள் (இந்த கட்டத்தில் மின்னஞ்சல் சேவை இல்லை). அழைப்புகள் 48 மணி நேரத்திற்குப் பிறகு காலாவதியாகும்.",
    },
    "INVITE_HINT": {
        "en": "Invitations expire after 48 hours.",
        "ms": "Jemputan tamat tempoh selepas 48 jam.",
        "zh": "邀请在48小时后过期。",
        "ta": "அழைப்புகள் 48 மணி நேரத்திற்குப் பிறகு காலாவதியாகும்.",
    },
    "NO_INVITATIONS": {
        "en": "No invitations yet.",
        "ms": "Tiada jemputan lagi.",
        "zh": "暂无邀请。",
        "ta": "இன்னும் அழைப்புகள் இல்லை.",
    },
    "REMOVE_CONFIRM": {
        "en": "Remove this employee from the shop? Their account is kept but unlinked — they can be re-invited later.",
        "ms": "Buang pekerja ini dari kedai? Akaun mereka disimpan tetapi tidak dipautkan — mereka boleh dijemput semula kemudian.",
        "zh": "将此员工从店铺移除？他们的账户将保留但断开连接 - 稍后可以重新邀请。",
        "ta": "இந்த ஊழியரை கடையிலிருந்து நீக்கவா? அவர்களின் கணக்கு பாதுகாக்கப்படும் ஆனால் துண்டிக்கப்படும் — அவர்களை பின்னர் மீண்டும் அழைக்கலாம்.",
    },

    # === Notifications ===
    "NOTIFICATIONS_TITLE": {
        "en": "Notifications", "ms": "Pemberitahuan", "zh": "通知", "ta": "அறிவிப்புகள்",
    },
    "NEW": {
        "en": "New", "ms": "Baharu", "zh": "新", "ta": "புதிய",
    },
    "INVITATION_ACCEPTED": {
        "en": "Invitation accepted", "ms": "Jemputan diterima", "zh": "邀请已接受", "ta": "அழைப்பு ஏற்கப்பட்டது",
    },
    "INVITATION_DECLINED": {
        "en": "Invitation declined", "ms": "Jemputan ditolak", "zh": "邀请已拒绝", "ta": "அழைப்பு நிராகரிக்கப்பட்டது",
    },
    "INVITATION_EXPIRED": {
        "en": "Invitation expired", "ms": "Jemputan tamat tempoh", "zh": "邀请已过期", "ta": "அழைப்பு காலாவதியானது",
    },
    "INVITE_NOT_LINKED_HINT": {
        "en": "Your account is not linked to a shop yet. When an Owner invites you, the invitation will appear here for you to accept.",
        "ms": "Akaun anda belum dipautkan ke kedai. Apabila Pemilik menjemput anda, jemputan akan muncul di sini untuk anda terima.",
        "zh": "您的账户尚未关联店铺。当所有者邀请您时，邀请将在此显示供您接受。",
        "ta": "உங்கள் கணக்கு இன்னும் கடையுடன் இணைக்கப்படவில்லை. ஒரு உரிமையாளர் உங்களை அழைக்கும்போது, அழைப்பு இங்கே தோன்றும்.",
    },
    "INVITATION_STATUS": {
        "en": "Invitation", "ms": "Jemputan", "zh": "邀请", "ta": "அழைப்பு",
    },

    # === Invite Accept / Status ===
    "JOIN": {
        "en": "Join", "ms": "Sertai", "zh": "加入", "ta": "சேர்",
    },
    "INVITE_DETAILS_UNCHANGEABLE": {
        "en": "You've been invited to join this shop. The details below come from the invitation and cannot be changed:",
        "ms": "Anda telah dijemput untuk menyertai kedai ini. Butiran di bawah datang dari jemputan dan tidak boleh diubah:",
        "zh": "您已被邀请加入此店铺。以下详细信息来自邀请，无法更改：",
        "ta": "இந்த கடையில் சேர நீங்கள் அழைக்கப்பட்டுள்ளீர்கள். கீழே உள்ள விவரங்கள் அழைப்பிலிருந்து வந்தவை மற்றும் மாற்ற முடியாதவை:",
    },
    "CREATE_ACCOUNT_HINT": {
        "en": "Create your account. It is created without shop membership — after that you accept this invitation explicitly from your notifications.",
        "ms": "Cipta akaun anda. Ia dicipta tanpa keahlian kedai — selepas itu anda menerima jemputan ini secara eksplisit dari pemberitahuan anda.",
        "zh": "创建您的账户。创建时不含店铺会员资格 - 之后您需要从通知中明确接受此邀请。",
        "ta": "உங்கள் கணக்கை உருவாக்குங்கள். இது கடை உறுப்பினர் இல்லாமல் உருவாக்கப்படும் — அதன் பிறகு உங்கள் அறிவிப்புகளிலிருந்து இந்த அழைப்பை வெளிப்படையாக ஏற்றுக்கொள்ளுங்கள்.",
    },
    "ACCEPT_INVITATION": {
        "en": "Accept Invitation", "ms": "Terima Jemputan", "zh": "接受邀请", "ta": "அழைப்பை ஏற்கவும்",
    },
    "ALREADY_ACCEPTED": {
        "en": "Already accepted.", "ms": "Sudah diterima.", "zh": "已接受。", "ta": "ஏற்கனவே ஏற்கப்பட்டது.",
    },
    "ALREADY_ACCEPTED_DETAIL": {
        "en": "This invitation has already been used.",
        "ms": "Jemputan ini telah digunakan.",
        "zh": "此邀请已被使用。",
        "ta": "இந்த அழைப்பு ஏற்கனவே பயன்படுத்தப்பட்டது.",
    },
    "REVOKED": {
        "en": "Revoked.", "ms": "Dibatalkan.", "zh": "已撤销。", "ta": "நீக்கப்பட்டது.",
    },
    "REVOKED_DETAIL": {
        "en": "This invitation was revoked by the shop owner.",
        "ms": "Jemputan ini dibatalkan oleh pemilik kedai.",
        "zh": "此邀请已被店铺所有者撤销。",
        "ta": "இந்த அழைப்பு கடை உரிமையாளரால் நீக்கப்பட்டது.",
    },
    "DECLINED": {
        "en": "Declined.", "ms": "Ditolak.", "zh": "已拒绝。", "ta": "நிராகரிக்கப்பட்டது.",
    },
    "DECLINED_DETAIL": {
        "en": "This invitation was declined by the invited employee.",
        "ms": "Jemputan ini ditolak oleh pekerja yang dijemput.",
        "zh": "此邀请已被受邀员工拒绝。",
        "ta": "இந்த அழைப்பு அழைக்கப்பட்ட ஊழியரால் நிராகரிக்கப்பட்டது.",
    },
    "EXPIRED_DETAIL": {
        "en": "This invitation has passed its 48-hour validity. Ask the shop owner to send a new one.",
        "ms": "Jemputan ini telah melepasi tempoh sah 48 jam. Minta pemilik kedai menghantar yang baharu.",
        "zh": "此邀请已超过48小时有效期。请店铺所有者重新发送。",
        "ta": "இந்த அழைப்பு 48 மணி நேர செல்லுபடியைக் கடந்துவிட்டது. கடை உரிமையாளரிடம் புதியதை அனுப்பச் சொல்லுங்கள்.",
    },
    "GO_TO_LOGIN": {
        "en": "Go to login", "ms": "Pergi ke log masuk", "zh": "前往登录", "ta": "உள்நுழைவுக்குச் செல்",
    },

    # === Login / Register ===
    "CREATE_ACCOUNT": {
        "en": "Create Account", "ms": "Cipta Akaun", "zh": "创建账户", "ta": "கணக்கை உருவாக்கு",
    },
    "NO_ACCOUNT": {
        "en": "No account?", "ms": "Tiada akaun?", "zh": "没有账户？", "ta": "கணக்கு இல்லையா?",
    },
    "HAVE_ACCOUNT": {
        "en": "Have account?", "ms": "Sudah ada akaun?", "zh": "已有账户？", "ta": "ஏற்கனவே கணக்கு உள்ளதா?",
    },
    "REGISTER_LINK": {
        "en": "Register", "ms": "Daftar", "zh": "注册", "ta": "பதிவு செய்",
    },
    "LOGIN_LINK": {
        "en": "Login", "ms": "Log Masuk", "zh": "登录", "ta": "உள்நுழை",
    },
    "CREATE_NEW_SHOP": {
        "en": "Create a new shop",
        "ms": "Cipta kedai baharu",
        "zh": "创建新店铺",
        "ta": "ஒரு புதிய கடையை உருவாக்கு",
    },
    "JOIN_EXISTING_SHOP": {
        "en": "Join an existing shop",
        "ms": "Sertai kedai sedia ada",
        "zh": "加入现有店铺",
        "ta": "ஏற்கனவே உள்ள கடையில் சேர்",
    },
    "NEW_SHOP_HINT": {
        "en": "Your new shop is created when you register — you become its Owner.",
        "ms": "Kedai baharu anda dicipta apabila anda mendaftar — anda menjadi Pemiliknya.",
        "zh": "注册时将创建您的新店铺 - 您将成为其所有者。",
        "ta": "நீங்கள் பதிவு செய்யும் போது உங்கள் புதிய கடை உருவாக்கப்படும் — நீங்கள் அதன் உரிமையாளர் ஆவீர்கள்.",
    },
    "LOCAL_PRICE_HINT": {
        "en": "Used to match local KPDN market prices.",
        "ms": "Digunakan untuk memadankan harga pasaran KPDN tempatan.",
        "zh": "用于匹配当地KPDN市场价格。",
        "ta": "உள்ளூர் KPDN சந்தை விலைகளுடன் பொருத்த பயன்படுத்தப்படுகிறது.",
    },
    "DISTRICT_HINT": {
        "en": "Optional — for hyper-local pricing.",
        "ms": "Pilihan — untuk hiper-tempatan harga.",
        "zh": "可选 - 用于超本地化定价。",
        "ta": "விருப்பமானது — மிக உள்ளூர் விலைநிர்ணயத்திற்கு.",
    },
    "EMPLOYEE_REG_NOTE": {
        "en": "Your account is created without shop membership. The shop Owner's invitation decides your role (Manager/Staff) — you accept it from your notifications once invited.",
        "ms": "Akaun anda dicipta tanpa keahlian kedai. Jemputan Pemilik kedai menentukan peranan anda (Pengurus/Kakitangan) — anda menerimanya dari pemberitahuan anda setelah dijemput.",
        "zh": "您的账户创建时不包含店铺会员资格。店铺所有者的邀请决定您的角色（经理/员工）——收到邀请后您从通知中接受。",
        "ta": "உங்கள் கணக்கு கடை உறுப்பினர் இல்லாமல் உருவாக்கப்படும். கடை உரிமையாளரின் அழைப்பு உங்கள் பங்கை நிர்ணயிக்கிறது (மேலாளர்/ஊழியர்) — அழைக்கப்பட்டதும் உங்கள் அறிவிப்புகளிலிருந்து அதை ஏற்றுக்கொள்ளுங்கள்.",
    },

    # === Profile & Settings ===
    "PROFILE": {
        "en": "Profile", "ms": "Profil", "zh": "个人资料", "ta": "சுயவிவரம்",
    },
    "ACCOUNT_DETAILS": {
        "en": "Account Details", "ms": "Butiran Akaun", "zh": "账户详情", "ta": "கணக்கு விவரங்கள்",
    },
    "SHOP_SETTINGS": {
        "en": "Shop Settings", "ms": "Tetapan Kedai", "zh": "店铺设置", "ta": "கடை அமைப்புகள்",
    },
    "LANGUAGE_PREFERENCES": {
        "en": "Language Preferences", "ms": "Pilihan Bahasa", "zh": "语言偏好", "ta": "மொழி விருப்பத்தேர்வுகள்",
    },
    "SAVE_CHANGES": {
        "en": "Save Changes", "ms": "Simpan Perubahan", "zh": "保存更改", "ta": "மாற்றங்களைச் சேமி",
    },
    "UPDATE_PASSWORD": {
        "en": "Update Password", "ms": "Kemas Kini Kata Laluan", "zh": "更新密码", "ta": "கடவுச்சொல்லைப் புதுப்பி",
    },
    "SHOP_NAME": {
        "en": "Shop Name", "ms": "Nama Kedai", "zh": "店铺名称", "ta": "கடையின் பெயர்",
    },
    "STATE": {
        "en": "State", "ms": "Negeri", "zh": "州", "ta": "மாநிலம்",
    },
    "DISTRICT": {
        "en": "District", "ms": "Daerah", "zh": "地区", "ta": "மாவட்டம்",
    },
    "ROLE": {
        "en": "Role", "ms": "Peranan", "zh": "角色", "ta": "பங்கு",
    },
    "OWNER": {
        "en": "Owner", "ms": "Pemilik", "zh": "所有者", "ta": "உரிமையாளர்",
    },
    "MANAGER": {
        "en": "Manager", "ms": "Pengurus", "zh": "经理", "ta": "மேலாளர்",
    },
    "STAFF": {
        "en": "Staff", "ms": "Kakitangan", "zh": "员工", "ta": "ஊழியர்",
    },
    "UNASSIGNED": {
        "en": "Unassigned", "ms": "Belum Ditugaskan", "zh": "未分配", "ta": "நியமிக்கப்படாத",
    },
    "EMAIL": {
        "en": "Email", "ms": "E-mel", "zh": "电子邮件", "ta": "மின்னஞ்சல்",
    },
    "LANGUAGE": {
        "en": "Language", "ms": "Bahasa", "zh": "语言", "ta": "மொழி",
    },
    "MEMBER_SINCE": {
        "en": "Member Since", "ms": "Ahli Sejak", "zh": "注册日期", "ta": "உறுப்பினர் தேதி",
    },
    "TEAM_MEMBERS": {
        "en": "Team Members", "ms": "Ahli Pasukan", "zh": "团队成员", "ta": "குழு உறுப்பினர்கள்",
    },
    "SHOP_INFORMATION": {
        "en": "Shop Information", "ms": "Maklumat Kedai", "zh": "店铺信息", "ta": "கடை தகவல்",
    },
    "NO_SHOP_LINKED": {
        "en": "No shop associated with this account. You are waiting for an invitation.",
        "ms": "Tiada kedai dikaitkan dengan akaun ini. Anda sedang menunggu jemputan.",
        "zh": "此账户没有关联的店铺。您正在等待邀请。",
        "ta": "இந்த கணக்குடன் எந்த கடையும் தொடர்புபடுத்தப்படவில்லை. நீங்கள் ஒரு அழைப்புக்காக காத்திருக்கிறீர்கள்.",
    },
    "ONLY_OWNER_EDIT_SHOP": {
        "en": "Only the shop owner can edit shop settings.",
        "ms": "Hanya pemilik kedai boleh mengedit tetapan kedai.",
        "zh": "只有店铺所有者可以编辑店铺设置。",
        "ta": "கடை உரிமையாளர் மட்டுமே கடை அமைப்புகளைத் திருத்த முடியும்.",
    },
    "DEFAULT_MARGIN_HINT": {
        "en": "Applied to new products. Existing products keep their own margins.",
        "ms": "Digunakan untuk produk baharu. Produk sedia ada mengekalkan margin mereka sendiri.",
        "zh": "应用于新产品。现有产品保留自己的利润率。",
        "ta": "புதிய தயாரிப்புகளுக்கு பயன்படுத்தப்படுகிறது. தற்போதைய தயாரிப்புகள் தங்கள் சொந்த விளிம்புகளை வைத்திருக்கின்றன.",
    },
    "PASSWORD_LEAVE_BLANK": {
        "en": "Leave blank to keep your current password.",
        "ms": "Biarkan kosong untuk mengekalkan kata laluan semasa anda.",
        "zh": "留空以保留当前密码。",
        "ta": "தற்போதைய கடவுச்சொல்லை வைத்திருக்க காலியாக விடவும்.",
    },
    "LANGUAGE_PREF_DESC": {
        "en": "Choose your preferred language for the ShelfSense AI interface. Your selection affects navigation labels, button text, and section headers.",
        "ms": "Pilih bahasa keutamaan anda untuk antaramuka ShelfSense AI. Pilihan anda mempengaruhi label navigasi, teks butang, dan tajuk bahagian.",
        "zh": "选择 ShelfSense AI 界面的首选语言。您的选择会影响导航标签、按钮文本和章节标题。",
        "ta": "ShelfSense AI இடைமுகத்திற்கான உங்கள் விருப்பமான மொழியைத் தேர்ந்தெடுக்கவும். உங்கள் தேர்வு வழிசெலுத்தல் லேபிள்கள், பொத்தான் உரை மற்றும் பிரிவு தலைப்புகளை பாதிக்கிறது.",
    },
    "DEFAULT_LANG_HINT": {
        "en": "Default language — English interface",
        "ms": "Bahasa lalai — antaramuka Bahasa Inggeris",
        "zh": "默认语言 - 英文界面",
        "ta": "இயல்புநிலை மொழி — ஆங்கில இடைமுகம்",
    },

    "TAB_DETAILS": {
        "en": "Details",
        "ms": "Butiran",
        "zh": "详情",
        "ta": "விவரங்கள்",
    },
    "TAB_MARKET_INTEL": {
        "en": "Market Intelligence",
        "ms": "Perisikan Pasaran",
        "zh": "市场情报",
        "ta": "சந்தை நுண்ணறிவு",
    },
    "TAB_PRICING": {
        "en": "Pricing Recommendation",
        "ms": "Cadangan Harga",
        "zh": "价格建议",
        "ta": "விலை பரிந்துரை",
    },
    "SIZE_PER_PACKAGE": {
        "en": "Size (per package)",
        "ms": "Saiz (sebungkusan)",
        "zh": "尺寸（每包装）",
        "ta": "அளவு (ஒரு பேக்கேஜ்)",
    },
    "PACKAGE_SIZE_NOTE": {
        "en": "the package size, not your stock",
        "ms": "saiz bungkusan, bukan stok anda",
        "zh": "包装大小，不是库存",
        "ta": "பேக்கேஜ் அளவு, உங்கள் சரக்கு அல்ல",
    },
    "CURRENT_PRICE": {
        "en": "Current Price",
        "ms": "Harga Semasa",
        "zh": "当前价格",
        "ta": "தற்போதைய விலை",
    },
    "COST_TIMES_MARGIN": {
        "en": "cost x margin",
        "ms": "kos x margin",
        "zh": "成本x利润率",
        "ta": "செலவு x விளிம்பு",
    },
    "SELLABLE_UNITS": {
        "en": "sellable units on hand",
        "ms": "unit boleh dijual",
        "zh": "可售库存",
        "ta": "விற்பனை யூனிட்கள்",
    },
    "NO_PRICE_CHANGES": {
        "en": "No price changes recorded yet.",
        "ms": "Tiada perubahan harga direkodkan.",
        "zh": "暂无价格变更记录。",
        "ta": "விலை மாற்றங்கள் பதிவு செய்யப்படவில்லை.",
    },
    "MARKET_RANGE": {
        "en": "Market Range",
        "ms": "Julat Pasaran",
        "zh": "市场价格范围",
        "ta": "சந்தை வரம்பு",
    },
    "OBSERVATIONS": {
        "en": "observations",
        "ms": "pemerhatian",
        "zh": "观测数据",
        "ta": "கண்காணிப்புகள்",
    },
    "MEDIAN": {
        "en": "Median",
        "ms": "Median",
        "zh": "中位数",
        "ta": "நடுத்தரம்",
    },
    "MEAN": {
        "en": "Mean",
        "ms": "Purata",
        "zh": "平均值",
        "ta": "சராசரி",
    },
    "SPREAD": {
        "en": "Spread",
        "ms": "Julat",
        "zh": "价差",
        "ta": "விரிவாக்கம்",
    },
    "YOUR_PRICE_VS_MEDIAN": {
        "en": "Your price vs median",
        "ms": "Harga anda vs median",
        "zh": "您的价格vs中位数",
        "ta": "உங்கள் விலை vs நடுத்தரம்",
    },
    "AT_MARKET_MEDIAN": {
        "en": "at market median",
        "ms": "pada median pasaran",
        "zh": "处于市场中位数",
        "ta": "சந்தை நடுத்தரத்தில்",
    },
    "ABOVE_MEDIAN": {
        "en": "above median",
        "ms": "di atas median",
        "zh": "高于中位数",
        "ta": "நடுத்தரத்திற்கு மேல்",
    },
    "BELOW_MEDIAN": {
        "en": "below median",
        "ms": "di bawah median",
        "zh": "低于中位数",
        "ta": "நடுத்தரத்திற்கு கீழ்",
    },
    "SET_PRICE_TO_COMPARE": {
        "en": "Set a selling price to compare",
        "ms": "Tetapkan harga jualan",
        "zh": "设置售价以进行比较",
        "ta": "ஒப்பிட விலையை நிர்ணயிக்கவும்",
    },
    "NO_MARKET_DATA_YET": {
        "en": "No verified market data yet - verify a suggestion above.",
        "ms": "Tiada data pasaran disahkan.",
        "zh": "暂无已验证的市场数据。",
        "ta": "சரிபார்க்கப்பட்ட சந்தை தரவு இல்லை.",
    },
    "MARKET_ITEM": {
        "en": "Market Item",
        "ms": "Item Pasaran",
        "zh": "市场商品",
        "ta": "சந்தை பொருள்",
    },
    "MARKET_PRICE": {
        "en": "Market Price (RM)",
        "ms": "Harga Pasaran (RM)",
        "zh": "市场价格 (RM)",
        "ta": "சந்தை விலை (RM)",
    },
    "PER_UNIT": {
        "en": "Per Unit",
        "ms": "Seunit",
        "zh": "每单位",
        "ta": "ஒரு யூனிட்டுக்கு",
    },
    "NO_VERIFIED_LINKS": {
        "en": "No verified market links yet.",
        "ms": "Tiada pautan pasaran disahkan.",
        "zh": "暂无已验证的市场链接。",
        "ta": "சரிபார்க்கப்பட்ட சந்தை இணைப்புகள் இல்லை.",
    },
    "KPDN_OPEN_DATA": {
        "en": "KPDN Open Data",
        "ms": "Data Terbuka KPDN",
        "zh": "KPDN公开数据",
        "ta": "KPDN திறந்த தரவு",
    },
    "SHOWING_X_OF_Y": {
        "en": "Showing",
        "ms": "Menunjukkan",
        "zh": "显示",
        "ta": "காட்டுகிறது",
    },
    "OF": {
        "en": "of",
        "ms": "daripada",
        "zh": "共",
        "ta": "இல்",
    },
    "USED_TO_CALCULATE": {
        "en": "observations used to calculate the market summary above.",
        "ms": "pemerhatian digunakan untuk mengira ringkasan pasaran.",
        "zh": "观测数据用于计算市场摘要。",
        "ta": "சந்தை சுருக்கத்தைக் கணக்கிட பயன்படுத்தப்பட்ட கண்காணிப்புகள்.",
    },
    "NO_COMPETITOR_DATA": {
        "en": "No detailed observations available.",
        "ms": "Tiada pemerhatian terperinci.",
        "zh": "暂无详细观测数据。",
        "ta": "விரிவான கண்காணிப்புகள் இல்லை.",
    },
    "SUGGESTED_MATCHES": {
        "en": "Suggested Matches",
        "ms": "Padanan Dicadangkan",
        "zh": "建议匹配",
        "ta": "பரிந்துரைக்கப்பட்ட பொருத்தங்கள்",
    },
    "CONFIDENCE_LABEL": {
        "en": "Confidence",
        "ms": "Keyakinan",
        "zh": "置信度",
        "ta": "நம்பிக்கை",
    },
    "NO_SUGGESTIONS_YET": {
        "en": "No suggestions yet.",
        "ms": "Tiada cadangan lagi.",
        "zh": "暂无建议。",
        "ta": "இன்னும் பரிந்துரைகள் இல்லை.",
    },
    "CLICK_SEARCH_TO_FIND": {
        "en": "Click Search to find candidates.",
        "ms": "Klik Cari untuk mencari calon.",
        "zh": "点击搜索以查找候选。",
        "ta": "வேட்பாளர்களைக் கண்டறிய தேடு என்பதைக் கிளிக் செய்யுங்கள்.",
    },
    "AI_PRICE_RECOMMENDATION": {
        "en": "AI Price Recommendation",
        "ms": "Cadangan Harga AI",
        "zh": "AI价格建议",
        "ta": "AI விலை பரிந்துரை",
    },
    "YOUR_CURRENT_PRICE": {
        "en": "Your Current Price",
        "ms": "Harga Semasa Anda",
        "zh": "您的当前价格",
        "ta": "உங்கள் தற்போதைய விலை",
    },
    "DIFFERENCE": {
        "en": "Difference",
        "ms": "Perbezaan",
        "zh": "差异",
        "ta": "வேறுபாடு",
    },
    "BELOW_YOUR_PRICE": {
        "en": "below your price",
        "ms": "di bawah harga anda",
        "zh": "低于您的价格",
        "ta": "உங்கள் விலையை விட குறைவு",
    },
    "ABOVE_YOUR_PRICE": {
        "en": "above your price",
        "ms": "di atas harga anda",
        "zh": "高于您的价格",
        "ta": "உங்கள் விலையை விட அதிகம்",
    },
    "SAME_AS_YOUR_PRICE": {
        "en": "same as your price",
        "ms": "sama dengan harga anda",
        "zh": "与您的价格相同",
        "ta": "உங்கள் விலையுடன் ஒத்தது",
    },
    "NO_CURRENT_PRICE_SET": {
        "en": "No current price set",
        "ms": "Tiada harga semasa",
        "zh": "未设置当前价格",
        "ta": "தற்போதைய விலை நிர்ணயிக்கப்படவில்லை",
    },
    "HIGH": {
        "en": "High",
        "ms": "Tinggi",
        "zh": "高",
        "ta": "உயர்",
    },
    "MEDIUM": {
        "en": "Medium",
        "ms": "Sederhana",
        "zh": "中",
        "ta": "நடுத்தர",
    },
    "LOW_CONFIDENCE": {
        "en": "Low",
        "ms": "Rendah",
        "zh": "低",
        "ta": "குறைவு",
    },
    "COST_FLOOR": {
        "en": "Cost floor",
        "ms": "Harga minimum",
        "zh": "成本底线",
        "ta": "செலவு தளம்",
    },
    "STOCK": {
        "en": "Stock",
        "ms": "Stok",
        "zh": "库存",
        "ta": "சரக்கு",
    },
    "VELOCITY": {
        "en": "Velocity",
        "ms": "Halaju",
        "zh": "销量",
        "ta": "வேகம்",
    },
    "PER_DAY": {
        "en": "/day",
        "ms": "/hari",
        "zh": "/天",
        "ta": "/நாள்",
    },
    "PRICING_UNAVAILABLE": {
        "en": "Pricing recommendation unavailable.",
        "ms": "Cadangan harga tidak tersedia.",
        "zh": "定价建议不可用。",
        "ta": "விலைநிர்ணய பரிந்துரை கிடைக்கவில்லை.",
    },
    "WHY_THIS_PRICE": {
        "en": "Why this price?",
        "ms": "Mengapa harga ini?",
        "zh": "为什么是这个价格？",
        "ta": "ஏன் இந்த விலை?",
    },
    "KEY_FACTORS": {
        "en": "Key Factors",
        "ms": "Faktor Utama",
        "zh": "关键因素",
        "ta": "முக்கிய காரணிகள்",
    },
    "SEARCHING": {
        "en": "Searching...",
        "ms": "Mencari...",
        "zh": "搜索中...",
        "ta": "தேடுகிறது...",
    },
    "FOUND_SUGGESTIONS": {
        "en": "Found",
        "ms": "Dijumpai",
        "zh": "找到",
        "ta": "கண்டறியப்பட்டது",
    },
    "SUGGESTION_PLURAL": {
        "en": "suggestions",
        "ms": "cadangan",
        "zh": "建议",
        "ta": "பரிந்துரைகள்",
    },
    "NO_MATCHES_FOUND": {
        "en": "No matches found.",
        "ms": "Tiada padanan.",
        "zh": "未找到匹配项。",
        "ta": "பொருத்தங்கள் இல்லை.",
    },
    "VERIFIED_ACTION": {
        "en": "Verified.",
        "ms": "Disahkan.",
        "zh": "已验证。",
        "ta": "சரிபார்க்கப்பட்டது.",
    },
    "REJECTED_ACTION": {
        "en": "Rejected.",
        "ms": "Ditolak.",
        "zh": "已拒绝。",
        "ta": "நிராகரிக்கப்பட்டது.",
    },
    "REMOVED_ACTION": {
        "en": "Removed.",
        "ms": "Dibuang.",
        "zh": "已移除。",
        "ta": "நீக்கப்பட்டது.",
    },
    "ERROR": {
        "en": "Error",
        "ms": "Ralat",
        "zh": "错误",
        "ta": "பிழை",
    },
    "LATEST": {
        "en": "latest",
        "ms": "terkini",
        "zh": "最新",
        "ta": "சமீபத்திய",
    },
    "AVG": {
        "en": "avg",
        "ms": "purata",
        "zh": "平均",
        "ta": "சராசரி",
    },
    "OBS": {
        "en": "obs",
        "ms": "pemerhatian",
        "zh": "观测",
        "ta": "கண்காணிப்பு",
    },
    # === Footer ===
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
