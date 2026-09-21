"""应用元信息与品牌配置。"""

APP_NAME = "SYNUTV"
VERSION = "3.0"
DEVELOPER = "阿山"
CONTACT = "ashan441@163.com"
COPYRIGHT = f"© 2026 {DEVELOPER}"

# 合并来源（「关于」界面展示用）
MERGED_FROM = "由「教务系统管理器 v1.0.0」与「多人空课表 v2.1」合并"


def app_info() -> dict:
    """返回应用和开发者信息，供关于界面展示。"""
    return {
        "name": APP_NAME,
        "version": VERSION,
        "developer": DEVELOPER,
        "contact": CONTACT,
        "copyright": COPYRIGHT,
        "merged_from": MERGED_FROM,
    }
