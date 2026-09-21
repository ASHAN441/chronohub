"""教务系统管理器的主窗口（PySide6 + QtWebEngine）。"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWebEngineCore import (
    QWebEngineDownloadRequest,
    QWebEnginePage,
    QWebEngineScript,
)
from PySide6.QtWebEngineWidgets import QWebEngineView

from ..meta import APP_NAME, APP_VERSION, DEVELOPER, EMAIL
from ..security import EncryptionError
from ..storage import AccountStore
from ..web import DEFAULT_LOGIN_URL, SYSTEM_URLS, is_login_page_url, timetable_url


class AccountDialog(QDialog):
    """用于新增或编辑账号的模态对话框。"""

    def __init__(self, parent, title: str, initial: dict | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.result: dict | None = None

        initial = initial or {}
        self._nickname = QLineEdit(initial.get("nickname", ""))
        self._username = QLineEdit(initial.get("username", ""))
        self._password = QLineEdit(initial.get("password", ""))
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        self._notes = QLineEdit(initial.get("notes", ""))

        form = QFormLayout()
        form.addRow("昵称", self._nickname)
        form.addRow("用户名", self._username)
        form.addRow("密码", self._password)
        form.addRow("备注", self._notes)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

        self._nickname.setFocus()

    def _accept(self) -> None:
        nickname = self._nickname.text().strip()
        username = self._username.text().strip()
        password = self._password.text()
        if not nickname or not username or not password:
            QMessageBox.warning(self, "提示", "昵称、用户名和密码不能为空")
            return
        self.result = {
            "nickname": nickname,
            "username": username,
            "password": password,
            "notes": self._notes.text().strip(),
        }
        self.accept()


class LoginPage(QWebEnginePage):
    """拦截弹窗与新建窗口，交给主窗口处理。"""

    alert_received = Signal(str)
    popup_requested = Signal(object)

    def javaScriptAlert(self, security_origin: QUrl, msg: str) -> None:
        self.alert_received.emit(msg)

    def createWindow(self, _window_type) -> QWebEnginePage:
        view = QWebEngineView()
        view.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        popup_page = QWebEnginePage(self.profile(), view)
        view.setPage(popup_page)
        self.popup_requested.emit(view)
        return popup_page


class MainWindow(QMainWindow):
    def __init__(self, store: AccountStore) -> None:
        super().__init__()
        self._store = store
        self._login_in_progress = False
        self._pending_login: tuple[str, str] | None = None
        self._export_state: str | None = None
        self._export_popup: QWebEngineView | None = None

        self._login_timeout = QTimer(self)
        self._login_timeout.setSingleShot(True)
        self._login_timeout.timeout.connect(self._on_login_timeout)

        self._login_poll = QTimer(self)
        self._login_poll.setInterval(500)
        self._login_poll.timeout.connect(self._poll_login_result)

        self._clipboard_timer = QTimer(self)
        self._clipboard_timer.setSingleShot(True)
        self._clipboard_timer.timeout.connect(self._clear_clipboard)

        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.resize(1120, 720)
        self._build_ui()
        self._refresh_accounts()
        self._webview.load(QUrl(self._load_url()))

    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(splitter)

        sidebar = QWidget()
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(10, 10, 10, 10)
        side.setSpacing(8)

        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索昵称或用户名…")
        self._search.textChanged.connect(self._refresh_accounts)
        side.addWidget(self._search)

        self._account_list = QListWidget()
        self._account_list.itemDoubleClicked.connect(self._on_item_double_clicked)
        side.addWidget(self._account_list, 1)

        self._login_button = QPushButton("打开并登录")
        self._login_button.setObjectName("primary")
        self._login_button.clicked.connect(self._open_and_login)
        side.addWidget(self._login_button)

        grid = QGridLayout()
        actions = [
            ("设为当前", self._set_active),
            ("复制用户名", self._copy_username),
            ("复制密码", self._copy_password),
            ("添加", self._add_account),
            ("编辑", self._edit_account),
            ("删除", self._delete_account),
            ("导入 CSV", self._import_csv),
            ("导出 CSV", self._export_csv),
        ]
        for index, (text, handler) in enumerate(actions):
            button = QPushButton(text)
            button.clicked.connect(handler)
            grid.addWidget(button, index // 2, index % 2)
        side.addLayout(grid)

        self._export_button = QPushButton("导出课表")
        self._export_button.setObjectName("export")
        self._export_button.clicked.connect(self._open_timetable)
        side.addWidget(self._export_button)

        self._about_button = QPushButton("关于")
        self._about_button.clicked.connect(self._show_about)
        side.addWidget(self._about_button)

        sidebar.setMinimumWidth(260)
        sidebar.setMaximumWidth(340)

        browser_panel = QWidget()
        browser = QVBoxLayout(browser_panel)
        browser.setContentsMargins(0, 0, 0, 0)
        browser.setSpacing(0)

        bar = QWidget()
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(8, 6, 8, 6)
        bar_layout.setSpacing(6)

        back_button = QPushButton("‹")
        forward_button = QPushButton("›")
        reload_button = QPushButton("⟳")
        for button in (back_button, forward_button, reload_button):
            button.setFixedSize(32, 30)
            bar_layout.addWidget(button)
        back_button.clicked.connect(self._webview_back)
        forward_button.clicked.connect(self._webview_forward)
        reload_button.clicked.connect(self._webview_reload)

        self._url_combo = QComboBox()
        self._url_combo.setEditable(True)
        self._url_combo.addItems([url for _label, url in SYSTEM_URLS])
        self._url_combo.setCurrentText(self._load_url())
        self._url_combo.currentTextChanged.connect(self._save_url)
        bar_layout.addWidget(self._url_combo, 1)
        browser.addWidget(bar)

        self._webview = QWebEngineView()
        self._page = LoginPage(self)
        self._webview.setPage(self._page)
        self._page.scripts().insert(self._new_tab_script())
        self._webview.loadFinished.connect(self._on_load_finished)
        self._webview.urlChanged.connect(self._on_url_changed)
        self._page.alert_received.connect(self._on_alert)
        self._page.popup_requested.connect(self._on_popup)
        self._page.profile().downloadRequested.connect(self._on_download_requested)
        browser.addWidget(self._webview, 1)

        splitter.addWidget(sidebar)
        splitter.addWidget(browser_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([300, 820])

    def _new_tab_script(self) -> QWebEngineScript:
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

    def _webview_back(self) -> None:
        self._webview.back()

    def _webview_forward(self) -> None:
        self._webview.forward()

    def _webview_reload(self) -> None:
        self._webview.reload()

    def _open_timetable(self) -> None:
        self._export_state = "loading_timetable"
        base = self._url_combo.currentText().strip() or DEFAULT_LOGIN_URL
        self._webview.load(QUrl(timetable_url(base)))

    def _show_about(self) -> None:
        text = (
            f"<h3>{APP_NAME}</h3>"
            f"<p>版本：v{APP_VERSION}</p>"
            f"<p>开发者：{DEVELOPER}</p>"
            f"<p>邮箱：{EMAIL}</p>"
            f"<p>© 2026 {DEVELOPER} · 保留所有权利</p>"
        )
        QMessageBox.about(self, f"关于 {APP_NAME}", text)

    def _trigger_export_click(self) -> None:
        script = (
            "(function(){var b=document.getElementById('shcPDF');"
            "if(b){b.click();return 'clicked';}return 'no-button';})()"
        )
        self._page.runJavaScript(script, self._on_export_clicked)

    def _on_export_clicked(self, result: object) -> None:
        if result != "no-button":
            return
        self._export_state = None
        QMessageBox.warning(self, "下载失败", "未找到导出 PDF 按钮")

    def _on_popup(self, view: QWebEngineView) -> None:
        if self._export_state == "exporting":
            self._export_popup = view
        else:
            view.setWindowTitle("教务系统")
            view.resize(1100, 720)
            view.show()

    def _on_download_requested(self, download: QWebEngineDownloadRequest) -> None:
        suggested = download.suggestedFileName() or "课表.pdf"
        initial_path = str(Path.home() / "Downloads" / suggested)
        chosen, _selected = QFileDialog.getSaveFileName(
            self, "保存课表 PDF", initial_path, "PDF 文件 (*.pdf)"
        )
        if not chosen:
            download.cancel()
            self._reset_export()
            return
        if not chosen.lower().endswith(".pdf"):
            chosen += ".pdf"
        target = Path(chosen)
        target.parent.mkdir(parents=True, exist_ok=True)
        download.setDownloadDirectory(str(target.parent))
        download.setDownloadFileName(target.name)
        download.accept()
        download.isFinishedChanged.connect(
            lambda d=download, p=str(target): self._on_download_finished(d, p)
        )

    def _reset_export(self) -> None:
        if self._export_popup is not None:
            try:
                self._export_popup.close()
            except Exception:  # noqa: BLE001
                pass
            self._export_popup = None
        self._export_state = None

    def _on_download_finished(
        self, download: QWebEngineDownloadRequest, path: str
    ) -> None:
        self._reset_export()
        if download.state() == QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
            QMessageBox.information(self, "下载完成", f"课表 PDF 已保存：\n{path}")
        else:
            QMessageBox.warning(self, "下载未完成", "PDF 下载未完成，请重试")

    def _refresh_accounts(self) -> None:
        query = self._search.text().strip().lower()
        active_id = self._store.get_active_account_id()
        self._account_list.clear()
        for account in self._store.list_accounts():
            searchable = (
                f"{account['nickname']} {account['username']} {account['notes']}"
            ).lower()
            if query and query not in searchable:
                continue
            name = account["nickname"] + (" ★" if account["id"] == active_id else "")
            item = QListWidgetItem(f"{name}\n{account['username']}")
            item.setData(Qt.ItemDataRole.UserRole, account["id"])
            if account["id"] == active_id:
                item.setBackground(QColor("#e9f7ef"))
            self._account_list.addItem(item)

    def _selected_account_id(self) -> int | None:
        item = self._account_list.currentItem()
        if item is None:
            QMessageBox.information(self, "提示", "请先在列表中选择一个账号")
            return None
        return int(item.data(Qt.ItemDataRole.UserRole))

    def _load_url(self) -> str:
        saved = self._store.get_setting("login_url")
        if saved:
            return saved
        self._store.set_setting("login_url", DEFAULT_LOGIN_URL)
        return DEFAULT_LOGIN_URL

    def _save_url(self, text: str | None = None) -> None:
        value = (text if text is not None else self._url_combo.currentText()).strip()
        self._store.set_setting("login_url", value or DEFAULT_LOGIN_URL)

    def _open_and_login(self) -> None:
        if self._login_in_progress:
            return
        account_id = self._selected_account_id()
        if account_id is None:
            return
        account = self._store.get_account(account_id)
        if account is None:
            return
        try:
            password = self._store.get_password(account_id)
        except EncryptionError as exc:
            QMessageBox.critical(self, "错误", f"无法解密密码：{exc}")
            return

        self._save_url()
        self._store.set_active_account_id(account_id)
        self._refresh_accounts()

        self._pending_login = (account["username"], password)
        self._login_in_progress = True
        self._login_button.setEnabled(False)

        self._page.profile().cookieStore().deleteAllCookies()
        url = self._url_combo.currentText().strip() or DEFAULT_LOGIN_URL
        self._webview.load(QUrl(url))
        self._login_timeout.start(30000)

    def _on_load_finished(self, ok: bool) -> None:
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
                probe, lambda need: self._after_captcha_probe(bool(need), username, password)
            )
            return

        if self._export_state == "loading_timetable":
            if not ok:
                self._export_state = None
                QMessageBox.warning(self, "下载失败", "课表页面加载失败，请检查网络")
                return
            if is_login_page_url(self._webview.url().toString()):
                self._export_state = None
                QMessageBox.warning(self, "提示", "请先登录后再下载课表 PDF")
                return
            self._export_state = "exporting"
            QTimer.singleShot(1500, self._trigger_export_click)

    def _after_captcha_probe(self, need_captcha: bool, username: str, password: str) -> None:
        captcha: str | None = None
        if need_captcha:
            text, accepted = QInputDialog.getText(
                self,
                "需要验证码",
                "系统要求输入验证码，请查看右侧浏览器中的验证码图片后输入：",
            )
            if not accepted or not text.strip():
                self._login_failed("已取消登录（需要验证码）")
                return
            captcha = text.strip()
        self._submit_login(username, password, captcha)

    def _submit_login(self, username: str, password: str, captcha: str | None) -> None:
        self._page.runJavaScript(
            self._build_login_script(username, password, captcha),
            self._on_login_injected,
        )
        self._login_poll.start()

    def _build_login_script(self, username: str, password: str, captcha: str | None) -> str:
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
        if self._login_in_progress and not is_login_page_url(url.toString()):
            self._login_success()

    def _on_alert(self, message: str) -> None:
        if self._login_in_progress:
            self._login_failed(message or "登录失败，请检查用户名或密码")

    def _on_login_timeout(self) -> None:
        if self._login_in_progress:
            self._login_failed("登录超时，请检查网络或账号密码")

    def _poll_login_result(self) -> None:
        if not self._login_in_progress:
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
        self._login_button.setEnabled(True)
        QMessageBox.information(self, "登录成功", "已登录教务系统。")

    def _login_failed(self, message: str) -> None:
        self._login_in_progress = False
        self._pending_login = None
        self._login_timeout.stop()
        self._login_poll.stop()
        self._login_button.setEnabled(True)
        QMessageBox.warning(self, "登录失败", message)

    def _on_item_double_clicked(self, _item: QListWidgetItem) -> None:
        self._set_active()

    def _add_account(self) -> None:
        dialog = AccountDialog(self, "添加账号")
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.result:
            self._store.add_account(**dialog.result)
            self._refresh_accounts()

    def _edit_account(self) -> None:
        account_id = self._selected_account_id()
        if account_id is None:
            return
        account = self._store.get_account(account_id)
        if account is None:
            self._refresh_accounts()
            return
        try:
            password = self._store.get_password(account_id)
        except EncryptionError as exc:
            QMessageBox.critical(self, "错误", f"无法解密密码：{exc}")
            return
        dialog = AccountDialog(self, "编辑账号", initial={**account, "password": password})
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.result:
            self._store.update_account(account_id, **dialog.result)
            self._refresh_accounts()

    def _delete_account(self) -> None:
        account_id = self._selected_account_id()
        if account_id is None:
            return
        answer = QMessageBox.question(
            self, "确认删除", "确定要删除该账号吗？此操作不可撤销。"
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._store.delete_account(account_id)
        self._refresh_accounts()

    def _set_active(self) -> None:
        account_id = self._selected_account_id()
        if account_id is None:
            return
        self._store.set_active_account_id(account_id)
        self._refresh_accounts()

    def _copy_username(self) -> None:
        account_id = self._selected_account_id()
        if account_id is None:
            return
        account = self._store.get_account(account_id)
        if account:
            self._copy_to_clipboard(account["username"])

    def _copy_password(self) -> None:
        account_id = self._selected_account_id()
        if account_id is None:
            return
        try:
            password = self._store.get_password(account_id)
        except EncryptionError as exc:
            QMessageBox.critical(self, "错误", f"无法解密密码：{exc}")
            return
        self._copy_to_clipboard(password)

    def _copy_to_clipboard(self, text: str) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(text)
        self._clipboard_timer.start(30000)

    def _clear_clipboard(self) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().clear()

    def _import_csv(self) -> None:
        path, _selected_filter = QFileDialog.getOpenFileName(
            self, "选择要导入的 CSV 文件", "", "CSV 文件 (*.csv);;所有文件 (*.*)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                required = {"nickname", "username", "password"}
                if not required.issubset(reader.fieldnames or []):
                    QMessageBox.critical(
                        self,
                        "格式错误",
                        "CSV 表头必须包含 nickname、username、password（notes 可选）。",
                    )
                    return
                imported = 0
                for row in reader:
                    nickname = (row.get("nickname") or "").strip()
                    username = (row.get("username") or "").strip()
                    password = row.get("password") or ""
                    notes = (row.get("notes") or "").strip()
                    if not nickname or not username or not password:
                        continue
                    self._store.add_account(nickname, username, password, notes)
                    imported += 1
        except OSError as exc:
            QMessageBox.critical(self, "导入失败", str(exc))
            return
        self._refresh_accounts()
        QMessageBox.information(self, "导入完成", f"成功导入 {imported} 个账号。")

    def _export_csv(self) -> None:
        path, _selected_filter = QFileDialog.getSaveFileName(
            self, "导出账号列表", "accounts.csv", "CSV 文件 (*.csv)"
        )
        if not path:
            return
        accounts = self._store.list_accounts()
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["nickname", "username", "notes"])
                for account in accounts:
                    writer.writerow(
                        [account["nickname"], account["username"], account["notes"]]
                    )
        except OSError as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        QMessageBox.information(
            self, "导出完成", f"已导出 {len(accounts)} 个账号（出于安全考虑不包含密码）。"
        )
