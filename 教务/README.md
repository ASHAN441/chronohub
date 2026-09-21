# 教务系统管理器

一个面向 Windows 的本地桌面工具，用于统一管理同一套教务系统的多个账号，支持快速切换，并在软件内嵌浏览器中直接打开教务系统、自动登录。当前为 MVP 版本。

## 功能

- 新增、编辑、删除账号（昵称、用户名、密码、备注）
- 列表搜索过滤
- 将某个账号“设为当前”，并持久化保存；双击账号也可快速切换
- 当前账号用绿色高亮和 ★ 标记
- 复制用户名 / 复制密码（密码复制后 30 秒自动清空剪贴板）
- 批量导入 CSV、导出 CSV（导出不包含密码）
- 内嵌浏览器（Chromium 内核），在软件内直接打开教务系统
- 选中账号点击“打开并登录”，自动填入账号密码并登录
- “导出课表”按钮（左侧列表底部），登录后一键把当前账号的学生课表导出为 PDF；保存时可选择位置，默认使用教务系统自带的文件名
- 支持教务系统中“新标签页”式的点击，自动在当前浏览器窗口打开
- 系统地址可选外网 / 内网，也支持手动输入其他地址

## 运行

项目使用 Python 标准库 + PySide6（含 QtWebEngine）。已提供 `run.bat`，双击即可启动：

```powershell
run.bat
```

也可以手动运行：

```powershell
python run.py
```

> 注意：本机存在多个 Python，`run.bat` 会优先使用已装好依赖的标准 Python 3.13（含 PySide6）。

依赖安装（若环境里还没有 PySide6）：

```powershell
python -m pip install PySide6
```

QtWebEngine 自带 Chromium 内核，无需额外安装浏览器。

## CSV 格式

导入的 CSV 需要包含表头，至少包含以下三列：

```csv
nickname,username,password,notes
张三,zhangsan,123456,一班
李四,lisi,abcdef,
```

其中 `notes` 为可选列。文件编码支持 UTF-8 或带 BOM 的 UTF-8（可直接导入 Excel 另存的 CSV）。

## 数据与安全

- 账号数据保存在项目下的 `data/accounts.db`（SQLite），该目录已被 `.gitignore` 排除，不会提交到版本库。
- 密码使用 Windows DPAPI 以当前 Windows 用户为作用域加密，只有登录到同一个 Windows 账户时才能解密。
- 出于安全考虑，导出 CSV 不会包含密码。
- 自动登录在内嵌浏览器页面内完成，密码由教务系统页面自身的脚本处理，不会明文落地。

## 测试

```powershell
python -m unittest discover -s tests -t . -v
```

## 后续规划

- 抓取成绩、课表、考试安排等数据
- 新成绩 / 新通知提醒
- 打包为独立的 Windows `.exe`
