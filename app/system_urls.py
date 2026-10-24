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


def normalize_login_url(url: str) -> tuple:
    """校验并规整用户自己填的教务系统地址。

    返回 (ok, 规整后的地址, 错误说明)。空字符串是合法的——表示「跟随默认」。

    宽松但有底线：
    - 没写 http(s) 的自动补 http://（老师/同学一般只会粘 `jwxt.xxx.edu.cn/...`）
    - 只接受 http / https
    - 主机名里必须有 `.`（排除 `随便写的` 这种），或者就是 localhost
    """
    text = (url or "").strip()
    if not text:
        return True, "", ""

    if "://" not in text:
        text = "http://" + text

    parts = urlsplit(text)
    if parts.scheme not in ("http", "https"):
        return False, "", "只支持 http / https 开头的地址"
    host = parts.hostname or ""
    if not host or (host != "localhost" and "." not in host):
        return False, "", "地址里没有有效的主机名"

    return True, urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, parts.fragment)), ""


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
