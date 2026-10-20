"""应用元信息与品牌配置。"""

# 产品名（界面展示用；打包出的文件名是 ASCII 的 ChronoHub，见 packaging/ChronoHub.spec）
APP_NAME = "时枢 ChronoHub"
VERSION = "3.3.2"

# 组织 / 项目线标识。产品名独立，但组织标识仍保留在「关于」页展示。
ORG_NAME = "SYNUTV"

DEVELOPER = "阿山"
CONTACT = "ashan441@163.com"
COPYRIGHT = f"© 2026 {DEVELOPER}"

# 合并来源。仅作为应用元数据保留（app_info() 会返回它），
# 「关于」页目前**不再展示**这一行——用户改版时把主标题下方精简成了纯版本号。
MERGED_FROM = "由「教务系统管理器 v1.0.0」与「多人空课表 v2.1」合并"


def app_info() -> dict:
    """返回应用和开发者信息，供关于界面展示。"""
    return {
        "name": APP_NAME,
        "version": VERSION,
        "org": ORG_NAME,
        "developer": DEVELOPER,
        "contact": CONTACT,
        "copyright": COPYRIGHT,
        "merged_from": MERGED_FROM,
    }
