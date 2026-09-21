"""教务系统内嵌浏览器 —— 嵌在应用主窗口内，不弹独立窗口。

移植自原「教务系统管理器」的 app/ui/main_window.py，四处关键改动：

1. **不弹窗**：原来浏览器是独立的 QMainWindow。现在利用「pywebview 的窗口就是
   QMainWindow、webview 是 centralWidget」这一点，往主窗口挂一个覆盖客户区的
   子控件来承载教务系统页面。**刻意不改动 pywebview 的 centralWidget**——只做只读
   访问，尽量不碰它的内部结构，这样 pywebview 升级时不容易坏。

2. **导出不再弹保存对话框**：直接落到 data/downloads/ 暂存，由上层自动解析入库。

3. **导出时的新窗口不能关**：教务系统点「导出 PDF」会开一个新窗口来承载下载，
   必须留住它、但不显示。早期版本把它 close 掉了，结果下载被掐死、点了没反应。

4. **状态通过带锁的字典回传**：Qt 调用必须在主线程，而 pywebview 的 js_api 方法
   运行在子线程，所以这里只写状态，由 api.py 在子线程轮询读取。
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtWebEngineCore import (
    QWebEngineDownloadRequest,
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineScript,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .system_urls import DEFAULT_LOGIN_URL, is_login_page_url, timetable_url

LOGIN_TIMEOUT_MS = 30000
LOGIN_POLL_MS = 500
EXPORT_CLICK_DELAY_MS = 1500
EXPORT_TIMEOUT_MS = 60000
FIND_HOST_RETRY_MS = 200
FIND_HOST_MAX_TRIES = 25


def data_root() -> Path:
    return Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def staging_dir() -> str:
    """导出的 PDF 暂存目录（位于 data/ 下，已被 gitignore）。"""
    path = data_root() / "data" / "downloads"
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


class SchoolPage(QWebEnginePage):
    """拦截页面 alert 与 window.open，交给控制器处理。"""

    alert_received = Signal(str)
    popup_requested = Signal(object)

    def javaScriptAlert(self, security_origin: QUrl, msg: str) -> None:  # noqa: N802
        self.alert_received.emit(msg)

    def createWindow(self, _window_type) -> QWebEnginePage:  # noqa: N802
        # 弹窗的宿主控件由控制器决定怎么处理（导出时必须留住但不显示）
        holder = QWebEngineView(self.view())
        holder.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        # 注意：PySide6 的 QWebEnginePage 只接受 (profile, parent) 位置参数，
        # 写成 profile= 关键字会抛 AttributeError
        popup_page = SchoolPage(self.profile(), holder)
        holder.setPage(popup_page)
        holder.hide()
        self.popup_requested.emit(holder)
        return popup_page

    def view(self) -> QWidget | None:
        parent = self.parent()
        return parent if isinstance(parent, QWidget) else None


class EmbeddedBrowser(QObject):
    """把教务系统页面嵌进 pywebview 主窗口。

    **所有方法都必须在 Qt 主线程调用**（由 app/main.py 注入 QApplication 所在线程）。
    """

    status_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._host: QMainWindow | None = None
        self._container: QWidget | None = None
        self._view: QWebEngineView | None = None
        self._page: SchoolPage | None = None
        self._profile: QWebEngineProfile | None = None
        self._url_label: QLabel | None = None
        self._status_label: QLabel | None = None
        self._find_tries = 0

        # 导出期间教务系统开的窗口：必须留引用，否则会被 GC 掉、下载随之丢失
        self._popup_holders: list[QWebEngineView] = []

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
        self._export_click_tries = 0

        self._login_timeout = QTimer(self)
        self._login_timeout.setSingleShot(True)
        self._login_timeout.timeout.connect(self._on_login_timeout)

        self._login_poll = QTimer(self)
        self._login_poll.setInterval(LOGIN_POLL_MS)
        self._login_poll.timeout.connect(self._poll_login_result)

        self._export_timeout = QTimer(self)
        self._export_timeout.setSingleShot(True)
        self._export_timeout.timeout.connect(self._on_export_timeout)

        # 内嵌页面会盖住整个应用界面，状态必须显示在它自己的工具条上，
        # 否则用户看不到任何进度或错误——这正是「点了导出没反应」的观感来源。
        self.status_changed.connect(self._refresh_status_label)

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------

    def status(self) -> dict:
        with self._lock:
            return dict(self._status)

    def _get(self, key: str, default=None):
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
    # 找到 pywebview 的主窗口并把界面挂上去
    # ------------------------------------------------------------------

    @staticmethod
    def _find_host() -> QMainWindow | None:
        """找出 pywebview 创建的那个 QMainWindow（内含 QWebEngineView）。"""
        app = QApplication.instance()
        if app is None:
            return None
        for widget in app.topLevelWidgets():
            if isinstance(widget, QMainWindow) and widget.isVisible():
                if widget.findChild(QWebEngineView) is not None:
                    return widget
        return None

    def _ensure_embedded(self) -> bool:
        """把容器控件挂到主窗口上。主窗口还没建好时会重试。"""
        if self._container is not None:
            return True
        host = self._find_host()
        if host is None:
            self._find_tries += 1
            if self._find_tries <= FIND_HOST_MAX_TRIES:
                QTimer.singleShot(FIND_HOST_RETRY_MS, self._ensure_embedded)
            else:
                self._fail("没能找到应用主窗口，无法内嵌浏览器")
            return False

        self._host = host

        container = QWidget(host)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        bar = QWidget()
        bar.setStyleSheet("background:#f3f3f3; border-bottom:1px solid #d9d9d9;")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(8, 5, 8, 5)
        bar_layout.setSpacing(6)

        back = QPushButton("← 返回 SYNUTV")
        back.setFixedHeight(28)
        back.clicked.connect(self.hide)
        bar_layout.addWidget(back)

        for text, handler in (
            ("‹", lambda: self._view and self._view.back()),
            ("›", lambda: self._view and self._view.forward()),
            ("⟳", lambda: self._view and self._view.reload()),
        ):
            button = QPushButton(text)
            button.setFixedSize(28, 28)
            button.clicked.connect(handler)
            bar_layout.addWidget(button)

        url_label = QLabel("")
        url_label.setStyleSheet("color:#5f5f5f; padding:0 8px;")
        bar_layout.addWidget(url_label, 1)

        status_label = QLabel("")
        status_label.setStyleSheet("color:#5f5f5f; padding:0 8px;")
        bar_layout.addWidget(status_label)

        export = QPushButton("导出课表 PDF")
        export.setFixedHeight(28)
        export.clicked.connect(lambda: self.export_schedule())
        bar_layout.addWidget(export)

        layout.addWidget(bar)

        view = QWebEngineView()
        # 独立的 profile：不干扰 pywebview 自己的 profile，也便于自己接管下载
        profile_path = str(data_root() / "data" / "jwglxt-profile")
        os.makedirs(profile_path, exist_ok=True)
        profile = QWebEngineProfile("synutv-jwglxt", self)
        profile.setPersistentStoragePath(profile_path)
        profile.downloadRequested.connect(self._on_download_requested)

        page = SchoolPage(profile, view)
        view.setPage(page)
        page.scripts().insert(self._new_tab_script())
        view.loadFinished.connect(self._on_load_finished)
        view.urlChanged.connect(self._on_url_changed)
        page.alert_received.connect(self._on_alert)
        page.popup_requested.connect(self._on_popup)
        layout.addWidget(view, 1)

        container.hide()
        host.installEventFilter(self)
        self._sync_geometry()

        self._container = container
        self._view = view
        self._page = page
        self._profile = profile
        self._url_label = url_label
        self._status_label = status_label
        self._refresh_status_label()
        return True

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self._host and event.type() == QEvent.Type.Resize:
            self._sync_geometry()
        return super().eventFilter(obj, event)

    def _sync_geometry(self) -> None:
        """让容器始终铺满主窗口客户区。"""
        if self._host is None or self._container is None:
            return
        self._container.setGeometry(0, 0, self._host.width(), self._host.height())

    def _refresh_status_label(self) -> None:
        """把当前状态显示到内嵌页面的工具条上。"""
        if self._status_label is None:
            return
        status = self.status()
        if status.get("error"):
            text = "⚠ " + status["error"]
            color = "#c42b1c"
        elif status.get("busy"):
            text = status.get("stage") or "处理中…"
            if status.get("message"):
                text += " · " + status["message"]
            color = "#0a5aa8"
        else:
            text = status.get("message") or ""
            color = "#107c10" if status.get("logged_in") else "#5f5f5f"
        self._status_label.setText(text)
        self._status_label.setStyleSheet(f"color:{color}; padding:0 8px;")
        self._status_label.setToolTip(text)

    # ------------------------------------------------------------------
    # 显示 / 隐藏
    # ------------------------------------------------------------------

    def show(self) -> None:
        if not self._ensure_embedded():
            return
        self._sync_geometry()
        self._container.show()
        self._container.raise_()
        self._set(open=True, error="")

    def hide(self) -> None:
        if self._container is not None:
            self._container.hide()
        self._set(open=False)

    def open_page(self, url: str = "") -> None:
        """显示内嵌页面并载入地址。"""
        self.show()
        if self._view is not None:
            self._view.load(QUrl(url or DEFAULT_LOGIN_URL))

    def close(self) -> None:
        self._login_timeout.stop()
        self._login_poll.stop()
        self._export_timeout.stop()
        self._pending_login = None
        self._login_in_progress = False
        self._reset_export()
        self._release_popups()
        self.hide()

    # ------------------------------------------------------------------
    # 登录
    # ------------------------------------------------------------------

    def login(
        self, nickname: str, username: str, password: str, login_url: str = ""
    ) -> None:
        if not self._ensure_embedded():
            return
        self.show()
        self._pending_login = (username, password)
        self._login_in_progress = True
        self._set(
            busy=True,
            stage="正在登录",
            message=f"正在以「{nickname}」登录…",
            account=nickname,
            error="",
        )
        if self._profile is not None:
            self._profile.cookieStore().deleteAllCookies()
        if self._view is not None:
            self._view.load(QUrl(login_url or DEFAULT_LOGIN_URL))
        self._login_timeout.start(LOGIN_TIMEOUT_MS)

    def _new_tab_script(self) -> QWebEngineScript:
        """让 target=_blank 的链接在当前页面打开，减少弹窗。"""
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
            "if(el.tagName&&el.tagName.toLowerCase()==='a'"
            "&&el.getAttribute('target')==='_blank'){"
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
            if self._view is not None and is_login_page_url(
                self._view.url().toString()
            ):
                self._fail("请先登录后再导出课表")
                return
            self._export_state = "exporting"
            self._export_click_tries = 0
            self._set(stage="正在导出", message="正在等待课表页的导出按钮…")
            QTimer.singleShot(EXPORT_CLICK_DELAY_MS, self._trigger_export_click)

    def _after_captcha_probe(
        self, need_captcha: bool, username: str, password: str
    ) -> None:
        captcha = None
        if need_captcha:
            text, accepted = QInputDialog.getText(
                self._container,
                "需要验证码",
                "系统要求输入验证码，请查看页面中的验证码图片后输入：",
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
            f"var y=document.getElementById('yzm');"
            f"if(y&&{captcha_js}!==null){{y.value={captcha_js};}}"
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
        if self._url_label is not None:
            self._url_label.setText(url.toString())
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
        if not self._ensure_embedded():
            return
        if self._view is None:
            return
        if not self._get("logged_in"):
            self._fail("请先登录后再导出课表")
            return

        self.show()
        self._reset_export()
        self._export_state = "loading_timetable"
        self._set(busy=True, stage="加载课表页", message="正在打开课表页面…", error="")
        base = login_url or self._get("url") or DEFAULT_LOGIN_URL
        self._view.load(QUrl(timetable_url(base)))
        self._export_timeout.start(EXPORT_TIMEOUT_MS)

    def _trigger_export_click(self) -> None:
        """找导出按钮并点击。页面可能还没渲染完，所以轮询重试。"""
        if self._page is None or self._export_state != "exporting":
            return
        self._export_click_tries += 1
        script = (
            "(function(){var b=document.getElementById('shcPDF');"
            "if(b){b.click();return 'clicked';}return 'no-button';})()"
        )
        self._page.runJavaScript(script, self._on_export_clicked)

    def _on_export_clicked(self, result: object) -> None:
        if result != "no-button":
            self._set(stage="等待下载", message="已触发导出，正在等待 PDF 生成…")
            return
        if self._export_click_tries < 10:
            # 最多再试 10 次（每次 1.5 秒），覆盖页面渲染慢的情况
            QTimer.singleShot(EXPORT_CLICK_DELAY_MS, self._trigger_export_click)
            return
        self._fail("没找到页面上的「导出 PDF」按钮，请确认已登录且课表页已打开")

    def _release_popups(self) -> None:
        for holder in self._popup_holders:
            try:
                holder.deleteLater()
            except Exception:  # noqa: BLE001
                pass
        self._popup_holders = []

    def _on_popup(self, view: QWebEngineView) -> None:
        """教务系统开的新窗口。

        导出期间**绝不能关掉它**——PDF 下载就发生在那个页面上，关掉等于掐死下载。
        做法是留着但不显示，并保存引用避免被 GC。非导出期间直接丢弃，实现「不弹窗」。
        """
        if self._export_state == "exporting":
            self._popup_holders.append(view)
            return
        view.deleteLater()

    def _on_download_requested(self, download: QWebEngineDownloadRequest) -> None:
        """静默接管下载：落到暂存目录，交给上层自动解析入库。"""
        suggested = download.suggestedFileName() or "课表.pdf"
        if not suggested.lower().endswith(".pdf"):
            suggested += ".pdf"
        target = Path(staging_dir()) / suggested
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

    def _on_download_finished(
        self, download: QWebEngineDownloadRequest, path: str
    ) -> None:
        self._export_timeout.stop()
        state = download.state()
        completed = (
            state == QWebEngineDownloadRequest.DownloadState.DownloadCompleted
        )
        self._reset_export()
        self._release_popups()
        if not completed or not os.path.exists(path):
            reason = {
                QWebEngineDownloadRequest.DownloadState.DownloadCancelled: "下载被取消",
                QWebEngineDownloadRequest.DownloadState.DownloadInterrupted: "下载中断",
            }.get(state, "下载未完成")
            self._fail(f"课表 PDF {reason}（{download.interruptReason()}），请重试")
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
            self._fail("导出超时。请确认课表页已打开、且页面上有「导出 PDF」按钮")

    def _reset_export(self) -> None:
        self._export_state = None
        self._export_timeout.stop()

    # ------------------------------------------------------------------
    # 供 api.py 在子线程安全调用
    # ------------------------------------------------------------------

    def take_pending_import(self) -> str | None:
        with self._lock:
            path = self._status.get("pending_import")
            self._status["pending_import"] = None
            return path

    def note_imported(self, name: str, courses: int) -> None:
        self._set(stage="", message=f"已入库：{name}（{courses} 门课程）", error="")
        # 入库完成后自动收起内嵌页面，把界面还给应用
        self.hide()

    def note_import_failed(self, message: str) -> None:
        self._set(stage="", message="", error=f"课表入库失败：{message}")
