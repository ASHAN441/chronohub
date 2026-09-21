"""教务系统地址等与浏览器无关的常量与纯逻辑。"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

SYSTEM_URLS = [
    ("外网", "http://jwxt.synu.edu.cn/jwglxt/xtgl/login_slogin.html"),
    ("内网", "http://192.168.186.200/jwglxt/xtgl/login_slogin.html"),
]

DEFAULT_LOGIN_URL = SYSTEM_URLS[0][1]
TIMETABLE_PATH = "/kbcx/xskbcx_cxXskbcxIndex.html"
TIMETABLE_QUERY = "gnmkdm=N2151&layout=default"


def is_login_page_url(url: str) -> bool:
    """判断给定 URL 是否仍停留在登录页。"""
    return "login_slogin" in url


def timetable_url(login_url: str) -> str:
    """根据登录页地址推导出同一主机的学生课表查询地址。"""
    parts = urlsplit(login_url)
    prefix = "/jwglxt"
    if "/xtgl/" in parts.path:
        prefix = parts.path.split("/xtgl/")[0]
    return urlunsplit(
        (parts.scheme, parts.netloc, f"{prefix}{TIMETABLE_PATH}", TIMETABLE_QUERY, "")
    )
