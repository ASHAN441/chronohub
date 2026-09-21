"""与浏览器无关的纯逻辑单元测试。"""

from __future__ import annotations

import unittest

from app.web import DEFAULT_LOGIN_URL, SYSTEM_URLS, is_login_page_url, timetable_url


class WebHelpersTest(unittest.TestCase):
    def test_is_login_page_url(self) -> None:
        self.assertTrue(
            is_login_page_url(
                "http://jwxt.synu.edu.cn/jwglxt/xtgl/login_slogin.html"
            )
        )
        self.assertFalse(
            is_login_page_url(
                "http://jwxt.synu.edu.cn/jwglxt/xtgl/index_initMenu.html"
            )
        )

    def test_default_url_is_preset(self) -> None:
        urls = [url for _label, url in SYSTEM_URLS]
        self.assertIn(DEFAULT_LOGIN_URL, urls)

    def test_presets_are_login_pages(self) -> None:
        for _label, url in SYSTEM_URLS:
            self.assertTrue(is_login_page_url(url), url)

    def test_timetable_url_uses_same_host(self) -> None:
        url = timetable_url("http://192.168.186.200/jwglxt/xtgl/login_slogin.html")
        self.assertEqual(
            url,
            "http://192.168.186.200/jwglxt/kbcx/xskbcx_cxXskbcxIndex.html"
            "?gnmkdm=N2151&layout=default",
        )


if __name__ == "__main__":
    unittest.main()
