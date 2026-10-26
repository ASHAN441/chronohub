# 时枢 ChronoHub

> 用影像记录师大，用青春诠释梦想。

SYNUTV 校园电视台的内部工具：把「教务系统导课表 → 多人课表叠加找共同空闲 → 排值班表」这条流水线拧成一个桌面应用。

![版本](https://img.shields.io/badge/version-v3.4.1-blue)
![平台](https://img.shields.io/badge/platform-Windows%2010%2F11-lightgrey)
![Python](https://img.shields.io/badge/python-3.13-3776ab)

---

## 它能做什么

| 功能 | 说明 |
|---|---|
| **教务账号** | 账号存本地 SQLite，密码用 Windows DPAPI 加密，只有本机这台电脑能解开 |
| **自动入库** | 内嵌 Chromium 自动登录教务系统，导出的课表 PDF 直接解析入库，不用手动选文件 |
| **课表解析** | 支持网格版式与列表版式两种 PDF，自动展开单双周与周次区间 |
| **多人空课表** | 叠加多人课表找共同空闲，可按周次统计；分组可折叠，拖一拖就能换组 |
| **特殊安排** | 例会、值班、调课停课这类课表里没有的占用，手动标一下就按没空算 |
| **排值班表** | 1-8 节按人补位、9-10 节部长岗，每人每周最多一次；支持换人、标「本周不用人值班」、多周批量清除 |
| **导出值班表** | 按 Word 模板填表，字体字号与模板一致；也能手动改次数，改动都留有记录 |
| **本地优先** | 全部数据存在程序目录下的 `data/`，不上传任何服务器 |

---

## 技术栈

- **Python 3.13**
- **PySide6**（Qt 6）—— 窗口、内嵌浏览器（QtWebEngine）
- **pywebview**（`gui="qt"` 后端）—— 让 HTML 前端跑在同一个 Qt 事件循环里
- **pdfplumber** —— 课表 PDF 解析
- **python-docx** —— 值班表导出

界面是自己画的 HTML + CSS（Fluent 风格，明暗主题），没有用前端框架。

---

## 目录结构

```
app/            Python 后端
  api.py          js_api 桥（前端能调用的一切）
  main.py         启动流程、窗口、启动图
  accounts.py     教务账号（DPAPI 加密）
  parser.py       课表 PDF 解析
  duty_*.py       值班表：排班算法 / 存储 / 导出
  paths.py        打包前后的路径规则（关键，见文件头注释）
web/            前端（单页 index.html + 素材）
packaging/      PyInstaller 配置、图标
tests/          单元测试 + 端到端自检
tools/          图标生成、依赖检查
值班表.docx      值班表导出用的 Word 模板
```

---

## 开发运行

```bash
pip install -r requirements.txt
python run.py
```

## 打包

```bash
python -m PyInstaller packaging/ChronoHub.spec --noconfirm --clean
```

产出 `dist/ChronoHub/` **文件夹**（onedir）。要整个文件夹一起分发，不能只发 exe。

> 为什么不用 onefile：onefile 每次启动都要把 242MB 解压到临时目录，实测「双击 → 窗口」要 16 秒；onedir 是 2.4~3.1 秒。

## 测试

```bash
python -m unittest discover -s tests
```

---

## 升级版本（保留原有数据）

这个软件**没有安装程序**，也没有注册表项——它就是一个文件夹。数据全部在
`ChronoHub\data\` 里，所以升级时只要**别动这个目录**就行。

### 推荐：就地覆盖（不用搬数据）

```
1. 完全退出程序（任务管理器里确认没有 ChronoHub.exe）
2. 把新版本 zip 解压到**另一个临时文件夹**
3. 把临时文件夹里的 ChronoHub.exe 和 _internal\ 整个复制过去，
   覆盖旧的同名文件和目录
4. 删除临时文件夹，启动
```

`data\` 完全没有被碰过，人、账号、值班表都在。

### 另一种：新文件夹 + 搬数据

```
1. 新版本解压到一个新文件夹
2. 把旧文件夹里的 data\ 整个复制进新文件夹（覆盖空的 data\）
3. 旧文件夹可以删掉
```

### 注意事项

- **必须先退出程序**。程序运行时 `data\` 里的文件被占用，复制会失败或丢数据
- **两个副本不要同时运行**，会各写各的 `data\`，数据就分叉了
- **换电脑要重新录入密码**：账号密码用 Windows DPAPI 加密，绑定当前电脑和
  当前 Windows 账户。换机器/换账户后 `accounts.db` 里的密码解不开（这是设计）
- 实在担心的话，升级前把整个 `data\` 复制一份留底（不含水分的核心其实只有
  `people.json`、`groups.json`、`specials.json`、`duty.json`、`accounts.db` 这几个文件）

## 数据与隐私

- 所有数据都在程序目录下的 `data/`：账号库、课表、分组、值班表、日志
- **`data/` 已在 `.gitignore` 里**，不会进仓库
- 账号密码用 Windows DPAPI 加密，换一台电脑解不开（这是设计，不是缺陷）

---

## 第三方资源

- 开屏字体：[得意黑 Smiley Sans](https://github.com/atelier-anchor/smiley-sans)，SIL Open Font License 1.1
- 其余依赖见 `requirements.txt`

---

## 关于

SYNUTV 出品 · 开发者：阿山 · ashan441@163.com · © 2026 阿山
