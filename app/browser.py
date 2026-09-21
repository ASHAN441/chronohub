"""教务系统内嵌浏览器（PySide6 + QtWebEngine）。

移植自原「教务系统管理器」的 app/ui/main_window.py，三处关键改动：

1. **窗口独立**：原来浏览器和账号列表挤在同一个 QMainWindow 里，现在
   SYNUTV 主界面由 pywebview 渲染，浏览器作为独立窗口按需打开。
2. **导出不再弹保存对话框**：原来用 QFileDialog 让用户选保存位置，现在直接
   落到 data/downloads/ 暂存，然后交给上层自动解析入库——这正是合并的卖点。
3. **结果通过状态字典回传**：所有 Qt 调用都必须在主线程执行，而 pywebview 的
   js_api 方法运行在子线程。所以本类只负责把进度写进 `_status`（带锁），
   由 api.py 在子线程轮询读取，避免跨线程直接触碰 Qt 对象。
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtWebEngineCore import (
    QWebEngineDownloadRequest,
    QWebEnginePage,
    QWebEngineScript,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .system_urls import DEFAULT_LOGIN_URL, is_login_page_url, timetable_url

# 超时（毫秒）
LOGIN_TIMEOUT_MS = 30000
LOGIN_POLL_MS = 500
EXPORT_CLICK_DELAY_MS = 1500
EXPORT_TIMEOUT_MS = 60000


def staging_dir() -> str:
    """导出的 PDF 暂存目录（位于 data/ 下，已被 gitignore）。"""
    root = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    path = root / "data" / "downloads"
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


class LoginPage(QWebEnginePage):
    """拦截页面 alert 与弹出窗口，交给控制器处理。"""

    alert_received = Signal(str)
    popup_requested = Signal(object)

    def javaScriptAlert(self, security_origin: QUrl, msg: str) -> None:  # noqa: N802
        self.alert_received.emit(msg)

    def createWindow(self, _window_type) -> QWebEnginePage:  # noqa: N802
        view = QWebEngineView()
        view.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        popup_page = QWebEnginePage(self.profile(), view)
        view.setPage(popup_page)
        self.popup_requested.emit(view)
        return popup_page


class BrowserController(QObject):
    """教务系统浏览器窗口。

    **所有方法都必须在 Qt 主线程调用**（由 app/main.py 注入的 QApplication 所在线程）。
    api.py 侧请通过 `run_on_main_thread()` 投递。
    """

    status_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._window: QMainWindow | None = None
        self._view: QWebEngineView | None = None
        self._page: LoginPage | None = None

        self._lock = threading.Lock()
        self._status = {
            "open": False,
            "logged_in": False,
            "account": "",
            "url": "",
            "busy": False,
            "stage": "",
            "message": "",
            "pending_import": None,
            "exported_count": 0,
            "error": "",
        }

        self._pending_login: tuple[str, str] | None = None
        self._login_in_progress = False
        self._export_state: str | None = None
        self._export_target: str | None = None

        self._login_timeout = QTimer(self)
        self._login_timeout.setSingleShot(True)
        self._login_timeout.timeout.connect(self._on_login_timeout)

        self._login_poll = QTimer(self)
        self._login_poll.setInterval(LOGIN_POLL_MS)
        self._login_poll.timeout.connect(self._poll_login_result)

        self._export_timeout = QTimer(self)
        self._export_timeout.setSingleShot(True)
        self._export_timeout.timeout.connect(self._on_export_timeout)

    # ------------------------------------------------------------------
    # 状态（线程安全：api.py 在子线程轮询）
    # ------------------------------------------------------------------

    def status(self) -> dict:
        with self._lock:
            return dict(self._status)

    def _get(self, key: str, default=None):
        """主线程内读状态时用，避免直接碰到半更新的字典。"""
        with self._lock:
            return self._status.get(key, default)

    def _set(self, **fields) -> None:
        with self._lock:
            self._status.update(fields)
        self.status_changed.emit()

    def _fail(self, message: str) -> None:
        self._set(error=message, busy=False, stage="")
        self._reset_export()

    # ------------------------------------------------------------------
    # 开窗 / 关窗
    # ------------------------------------------------------------------

    def open(self, login_url: str = "") -> None:
        """打开浏览器窗口（已存在则置前）。"""
        if self._window is not None:
            self._window.show()
            self._window.raise_()
            self._window.activateWindow()
            return

        window = QMainWindow()
        window.setWindowTitle("SYNUTV · 教务系统")
        window.resize(1100, 740)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        bar = QToolBar()
        bar.setMovable(False)
        for text, handler in (
            ("‹", lambda: self._view and self._view.back()),
            ("›", lambda: self._view and self._view.forward()),
            ("⟳", lambda: self._view and self._view.reload()),
        ):
            button = QPushButton(text)
            button.setFixedSize(30, 26)
            button.clicked.connect(handler)
            bar.addWidget(button)

        self._url_label = QLabel("")
        self._url_label.setStyleSheet("color:#666; padding:0 8px;")
        bar.addWidget(self._url_label)
        layout.addWidget(bar)

        view = QWebEngineView()
        page = LoginPage(self)
        view.setPage(page)
        page.scripts().insert(self._new_tab_script())
        view.loadFinished.connect(self._on_load_finished)
        view.urlChanged.connect(self._on_url_changed)
        page.alert_received.connect(self._on_alert)
        page.popup_requested.connect(self._on_popup)
        page.profile().downloadRequested.connect(self._on_download_requested)
        layout.addWidget(view, 1)

        window.setCentralWidget(central)
        window.show()

        self._window = window
        self._view = view
        self._page = page

        self._set(open=True, error="", account="", logged_in=False)
        view.load(QUrl(login_url or DEFAULT_LOGIN_URL))

    def close(self) -> None:
        self._login_timeout.stop()
        self._login_poll.stop()
        self._export_timeout.stop()
        self._pending_login = None
        self._reset_export()
        if self._window is not None:
            self._window.close()
            self._window.deleteLater()
        self._window = None
        self._view = None
        self._page = None
        self._set(
            open=False,
            logged_in=False,
            busy=False,
            stage="",
            url="",
            account="",
        )

    def is_open(self) -> bool:
        return self._window is not None

    # ------------------------------------------------------------------
    # 登录
    # ------------------------------------------------------------------

    def login(self, nickname: str, username: str, password: str, login_url: str = "") -> None:
        """打开窗口并自动填入账号密码登录。"""
        self.open(login_url)
        self._pending_login = (username, password)
        self._login_in_progress = True
        self._set(
            busy=True,
            stage="正在登录",
            message=f"正在以「{nickname}」登录…",
            account=nickname,
            error="",
        )
        if self._page is not None:
            self._page.profile().cookieStore().deleteAllCookies()
        if self._view is not None:
            self._view.load(QUrl(login_url or DEFAULT_LOGIN_URL))
        self._login_timeout.start(LOGIN_TIMEOUT_MS)

    def _new_tab_script(self) -> QWebEngineScript:
        """让 target=_blank 的链接在当前窗口打开。"""
        script = QWebEngineScript()
        script.setName("open-links-in-place")
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(True)
        script.setSourceCode(
            "(function(){"
            "document.addEventListener('click',function(e){"
            "var el=e.target;"
            "while(el&&el!==document){"
            "if(el.tagName&&el.tagName.toLowerCase()==='a'&&el.getAttribute('target')==='_blank'){"
            "el.setAttribute('target','_self');break;}"
            "el=el.parentNode;"
            "}"
            "},true);"
            "})();"
        )
        return script

    def _on_load_finished(self, ok: bool) -> None:
        if self._page is None:
            return

        if self._pending_login is not None:
            if not ok:
                self._login_failed("页面加载失败，请检查系统地址和网络")
                return
            username, password = self._pending_login
            self._pending_login = None
            probe = (
                "(function(){var d=document.getElementById('yzmDiv');"
                "return !!(d && (d.offsetWidth>0||d.offsetHeight>0));})()"
            )
            self._page.runJavaScript(
                probe,
                lambda need: self._after_captcha_probe(bool(need), username, password),
            )
            return

        if self._export_state == "loading_timetable":
            if not ok:
                self._fail("课表页面加载失败，请检查网络")
                return
            if is_login_page_url(self._view.url().toString()):
                self._fail("请先登录后再导出课表")
                return
            self._export_state = "exporting"
            self._set(stage="正在导出", message="正在触发课表 PDF 导出…")
            QTimer.singleShot(EXPORT_CLICK_DELAY_MS, self._trigger_export_click)

    def _after_captcha_probe(self, need_captcha: bool, username: str, password: str) -> None:
        captcha = None
        if need_captcha:
            text, accepted = QInputDialog.getText(
                self._window,
                "需要验证码",
                "系统要求输入验证码，请查看浏览器窗口中的验证码图片后输入：",
            )
            if not accepted or not text.strip():
                self._login_failed("已取消登录（需要验证码）")
                return
            captcha = text.strip()
        self._submit_login(username, password, captcha)

    def _submit_login(self, username: str, password: str, captcha: str | None) -> None:
        if self._page is None:
            return
        self._page.runJavaScript(
            self._build_login_script(username, password, captcha),
            self._on_login_injected,
        )
        self._login_poll.start()

    @staticmethod
    def _build_login_script(username: str, password: str, captcha: str | None) -> str:
        """与教务系统页面约定的自动登录脚本（沿用原实现，勿随意改动）。"""
        captcha_js = "null" if captcha is None else json.dumps(captcha)
        return (
            "(function(){"
            "var u=document.getElementById('yhm'),p=document.getElementById('mm');"
            "if(!u||!p){return 'fields-missing';}"
            f"u.value={json.dumps(username)};"
            "var base=(typeof _path!=='undefined'?_path:'/jwglxt');"
            "fetch(base+'/xtgl/login_getPublicKey.html?time='+Date.now())"
            ".then(function(r){return r.json();})"
            ".then(function(key){"
            "var rsa=new RSAKey();"
            "rsa.setPublic(b64tohex(key.modulus),b64tohex(key.exponent));"
            f"var enc=hex2b64(rsa.encrypt({json.dumps(password)}));"
            "p.value=enc;"
            "var h=document.getElementById('hidMm');if(h){h.value=enc;}"
            f"var y=document.getElementById('yzm');if(y&&{captcha_js}!==null){{y.value={captcha_js};}}"
            "var f=u.form;if(f){f.submit();return 'submitted';}"
            "return 'no-form';"
            "})"
            ".catch(function(e){console.log('login-err',e.message);});"
            "return 'started';"
            "})()"
        )

    def _on_login_injected(self, result: object) -> None:
        if result in ("fields-missing", "no-form"):
            self._login_failed("登录页结构异常，请确认系统地址是否正确")

    def _on_url_changed(self, url: QUrl) -> None:
        self._set(url=url.toString())
        if self._login_in_progress and not is_login_page_url(url.toString()):
            self._login_success()

    def _on_alert(self, message: str) -> None:
        if self._login_in_progress:
            self._login_failed(message or "登录失败，请检查用户名或密码")
        elif message:
            self._set(message=message)

    def _on_login_timeout(self) -> None:
        if self._login_in_progress:
            self._login_failed("登录超时，请检查网络或账号密码")

    def _poll_login_result(self) -> None:
        if self._page is None or not self._login_in_progress:
            self._login_poll.stop()
            return
        script = (
            "(document.getElementById('tips')"
            "?(document.getElementById('tips').innerText||'').trim():'')"
        )
        self._page.runJavaScript(script, self._on_tips_result)

    def _on_tips_result(self, text: object) -> None:
        if not self._login_in_progress:
            return
        message = str(text).strip()
        if message:
            self._login_failed(message)

    def _login_success(self) -> None:
        self._login_in_progress = False
        self._login_timeout.stop()
        self._login_poll.stop()
        self._set(
            busy=False,
            logged_in=True,
            stage="",
            message="已登录教务系统",
            error="",
        )

    def _login_failed(self, message: str) -> None:
        self._login_in_progress = False
        self._pending_login = None
        self._login_timeout.stop()
        self._login_poll.stop()
        self._set(busy=False, logged_in=False, stage="", error=message, message="")

    # ------------------------------------------------------------------
    # 导出课表 PDF
    # ------------------------------------------------------------------

    def export_schedule(self, login_url: str = "") -> None:
        """跳到课表页并触发导出；PDF 下载完成后落到 pending_import。"""
        if self._window is None:
            self.open(login_url)
        if self._view is None:
            return
        if not self._status.get("logged_in"):
            self._fail("请先登录后再导出课表")
            return

        self._reset_export()
        self._export_state = "loading_timetable"
        self._set(busy=True, stage="加载课表页", message="正在打开课表页面…", error="")
        base = login_url or self._status.get("url") or DEFAULT_LOGIN_URL
        self._view.load(QUrl(timetable_url(base)))
        self._export_timeout.start(EXPORT_TIMEOUT_MS)

    def _trigger_export_click(self) -> None:
        if self._page is None:
            return
        script = (
            "(function(){var b=document.getElementById('shcPDF');"
            "if(b){b.click();return 'clicked';}return 'no-button';})()"
        )
        self._page.runJavaScript(script, self._on_export_clicked)

    def _on_export_clicked(self, result: object) -> None:
        if result == "no-button":
            self._fail("未找到导出 PDF 按钮，页面结构可能已变化")
        else:
            self._set(stage="等待下载", message="已触发导出，正在等待 PDF 生成…")

    def _on_popup(self, view: QWebEngineView) -> None:
        # 导出过程中教务系统会开新窗口承载 PDF，别让它弹出来
        if self._export_state == "exporting":
            view.close()
            return
        view.setWindowTitle("SYNUTV · 教务系统")
        view.resize(1000, 700)
        view.show()

    def _on_download_requested(self, download: QWebEngineDownloadRequest) -> None:
        """接管下载：不再弹保存对话框，直接落到暂存目录。"""
        suggested = download.suggestedFileName() or "课表.pdf"
        if not suggested.lower().endswith(".pdf"):
            suggested += ".pdf"
        target = Path(staging_dir()) / suggested
        # 避免重名互相覆盖
        stem, suffix, index = target.stem, target.suffix, 1
        while target.exists():
            target = target.with_name(f"{stem}({index}){suffix}")
            index += 1

        download.setDownloadDirectory(str(target.parent))
        download.setDownloadFileName(target.name)
        download.accept()
        self._set(stage="下载中", message=f"正在保存 {target.name} …")
        download.isFinishedChanged.connect(
            lambda d=download, p=str(target): self._on_download_finished(d, p)
        )

    def _on_download_finished(self, download: QWebEngineDownloadRequest, path: str) -> None:
        self._export_timeout.stop()
        completed = (
            download.state()
            == QWebEngineDownloadRequest.DownloadState.DownloadCompleted
        )
        self._reset_export()
        if not completed or not os.path.exists(path):
            self._fail("课表 PDF 下载未完成，请重试")
            return

        with self._lock:
            self._status["exported_count"] += 1
        self._set(
            busy=False,
            stage="待入库",
            message=f"已导出 {os.path.basename(path)}，正在解析入库…",
            pending_import=path,
            error="",
        )

    def _on_export_timeout(self) -> None:
        if self._export_state is not None:
            self._fail("导出超时，请检查网络后重试")

    def _reset_export(self) -> None:
        self._export_state = None
        self._export_timeout.stop()

    # ------------------------------------------------------------------
    # 供 api.py 在子线程安全调用
    # ------------------------------------------------------------------

    def take_pending_import(self) -> str | None:
        """取走待入库的 PDF 路径（取一次即清空，保证只入库一次）。"""
        with self._lock:
            path = self._status.get("pending_import")
            self._status["pending_import"] = None
            return path

    def note_imported(self, name: str, courses: int) -> None:
        self._set(
            stage="",
            message=f"已入库：{name}（{courses} 门课程）",
            error="",
        )

    def note_import_failed(self, message: str) -> None:
        self._set(stage="", message="", error=f"课表入库失败：{message}")
