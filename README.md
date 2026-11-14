# 时枢 ChronoHub

> 用影像记录师大

SYNUTV 的内部工具，开放给所有使用正方教务系统的高校团队。  
它把「教务系统导课表 → 多人课表叠加找共同空闲 → 排值班表」这一整套繁琐流程，收进了一个开箱即用的桌面小工具里。

![版本](https://img.shields.io/badge/version-v3.6.3-blue)

![平台](https://img.shields.io/badge/platform-Windows%2010%2F11-lightgrey)

![Python](https://img.shields.io/badge/python-3.13-3776ab)

![License](https://img.shields.io/badge/license-MIT-green)

---

## 适用条件

- 学校的教务系统为**正方教务管理系统**（登录地址形如 `.../jwglxt/`），且能导出课表 PDF。
- Windows 10 / 11。
- 程序内置了沈阳师范大学的内网地址；其他学校请在「账号」页填写本校的登录地址，之后照常使用。

> 课表解析按正方教务导出的 PDF 版式适配（网格版、列表版均支持）。若贵校导出的版式有差异，欢迎反馈样本。

---

## 核心功能

| 功能        | 说明                                                    |
| --------- | ----------------------------------------------------- |
| **教务账号**  | 账号保存在本地 SQLite 中，经 Windows DPAPI 加密，仅本机当前账户可解密。       |
| **自动入库**  | 内嵌 Chromium 自动登录教务系统，导出的课表 PDF 直接解析入库，无需手动点选。         |
| **课表解析**  | 支持网格与列表两种版式，自动展开单双周及周次区间。                             |
| **多人空课表** | 多份课表一键叠加，找出共同空闲时段，并支持按周统计；分组可折叠，拖拽即可调整组员。             |
| **特殊安排**  | 例会、值班、调课停课等课表之外的占用，手动标记即可剔除。                          |
| **排值班表**  | 1–8 节按人补位，9–10 节设固定岗，每人每周最多一次；支持换人、标记「本周免值班」以及多周批量清除。 |
| **导出值班表** | 按 Word 模板一键填表，字体字号保持一致；支持手动微调次数，所有改动均有记录可查。           |
| **本地优先**  | 数据只存放在程序目录的 `data/` 下，不上传任何第三方服务器。                    |

> 排班规则（1–8 节按人轮值、9–10 节固定岗）源自电视台的值班制度，可直接使用，也可按自己团队的习惯调整。

---

## 技术栈

- **Python 3.13**
- **PySide6**（Qt 6）—— 窗口与内嵌浏览器（QtWebEngine）
- **pywebview**（`gui="qt"` 后端）—— 让 HTML 前端运行在同一个 Qt 事件循环中
- **pdfplumber** —— 课表 PDF 解析
- **python-docx** —— 值班表 Word 导出

---

## 目录结构

```

app/            Python 后端
api.py          js_api 桥（前端可调用的各类后端方法）
main.py         启动流程、窗口、启动图
accounts.py     教务账号（DPAPI 加密）
parser.py       课表 PDF 解析
duty_*.py       值班表：排班算法 / 存储 / 导出
paths.py        打包前后的路径规则（关键，见文件头注释）
web/            前端（单页 index.html + 静态素材）
packaging/      PyInstaller 配置与图标
tests/          单元测试 + 端到端自检
tools/          图标生成、依赖检查
值班表.docx      值班表导出用的 Word 模板

```

---

## 开发与运行

```bash
pip install -r requirements.txt
python run.py

```

## 打包

```bash
python -m PyInstaller packaging/ChronoHub.spec --noconfirm --clean

```

产物为 `dist/ChronoHub/` **文件夹**（onedir 模式）。分发时请打包整个文件夹，不能只发单个 exe。

## 测试

```bash
python -m unittest discover -s tests

```

---

## 制作安装程序

```bash
python tools/build_installer.py          # 使用现有的 dist/ChronoHub
python tools/build_installer.py --build  # 先重新打包，再编译
```

产物为 `dist/installer/ChronoHub-Setup-v{版本}.exe`（约 162 MB，lzma2 极限压缩）。  
需预先安装 [Inno Setup 6](https://jrsoftware.org/isdl.php)；版本号自动从  
`app/config.py` 读取，无需在两处手动修改。

### 为什么安装到用户目录

安装路径是 **`%LOCALAPPDATA%\Programs\ChronoHub`**，而非 `Program Files`。

原因在于程序采用便携式布局，数据写在 exe 旁边的 `data\` 里。若装到  
`Program Files`，普通用户对该目录没有写权限，**程序将无法保存任何数据**。  
装到用户目录既不需要管理员权限，`data\` 也能正常写入。

### 安装包不含数据，也不会删除数据

- 打包时排除 `data\`：发布包始终是干净的，不含开发机上的课表与账号
- **覆盖安装**：`data\` 不在安装清单中，原样保留
- **卸载**：**完全不触碰 `data\`**。如需彻底删除，请自行删除安装目录下的 data 文件夹

> 卸载时不弹出「是否删除数据」的询问，是刻意设计：覆盖升级时安装程序会在后台  
> 静默调用一次旧版卸载，该过程无人值守，弹窗会把整个升级流程卡死（实测卡 7 分钟后超时）。

## 升级指南（如何保留数据）

这个软件没有复杂的安装程序，也没有乱七八糟的注册表项，本质上就是一个干净的文件夹。核心数据全部存在 `ChronoHub\data\` 里，因此升级时只要**护好这个目录**即可。

### 推荐：就地覆盖（省去搬数据）

1. 完全退出程序（在任务管理器中确认没有残留的 `ChronoHub.exe`）。
2. 把新版本的 zip 解压到**另一个临时文件夹**。
3. 将临时文件夹中的 `ChronoHub.exe` 和 `_internal\` 整体复制过去，覆盖旧的同名文件。
4. 删除临时文件夹，完成。

`data\` 完好如初，人员、账号、历史值班表全部都在。

### 备选：新文件夹 + 手动迁移数据

1. 将新版本解压到一个全新文件夹。
2. 把旧文件夹中的 `data\` 整个复制过去（覆盖新的空 `data\`）。
3. 确认无误后删除旧文件夹。

### ⚠️ 注意事项

- **必须先退出程序**。程序运行时 `data\` 内的文件被占用，直接复制可能导致数据丢失或报错。
- **不要同时运行两个副本**，否则两者会各自读写 `data\`，造成数据分叉。
- **换电脑后必须重新录入密码**：账号密码经 Windows DPAPI 加密，已绑定当前电脑与当前 Windows 账户。更换机器或账户后，`accounts.db` 中的密码将无法解密（这是系统层面的安全设计，并非 bug）。
- 若仍有顾虑，可在升级前把整个 `data\` 复制一份作为备份（其中真正的核心只有 `people.json`、`groups.json`、`specials.json`、`duty.json`、`accounts.db` 这几个文件）。

---

## 数据与隐私

- 所有数据（账号库、课表、分组、排班、日志）都存放在程序目录的 `data/` 下。
- `data/` 已列入 `.gitignore`，不会被提交到代码仓库。
- 账号密码通过 Windows DPAPI 加密并绑定本机，换电脑后无法解密。
- 除登录贵校教务系统外，程序不会向任何服务器发送数据。

---

## 第三方资源

- 开屏字体：[得意黑 Smiley Sans](https://github.com/atelier-anchor/smiley-sans)，基于 SIL Open Font License 1.1 开源。
- 其他依赖详见 `requirements.txt`。

---

## 许可

本项目采用 **MIT 许可证** —— 详见 [LICENSE](LICENSE)。  
简言之：可自由使用、修改、再发布，包括商用，只需保留版权声明。

### 第三方组件的许可

| 组件                         | 许可              | 说明                                                            |
| -------------------------- | --------------- | ------------------------------------------------------------- |
| PySide6 / Qt               | **LGPLv3**      | 本项目以「exe + 独立的 `_internal\*.dll`」动态链接方式分发，符合 LGPL 要求；请勿改为静态链接 |
| 得意黑 Smiley Sans            | **SIL OFL 1.1** | 可随软件分发，需保留字体许可声明                                              |
| pdfplumber / python-docx 等 | MIT / BSD       | 见 `requirements.txt`                                          |

---

## 关于

**时枢 ChronoHub** 由 **SYNUTV 校园电视台** 出品。

> 用影像记录师大，用青春诠释梦想。

最初只是为台内排班写的小工具，后来发现“正方教务 + 学生团队排班”这个组合到处都用得上，于是整理出来对外开放。

开发者：阿山 · <ashan441@163.com> · © 2026 阿山
