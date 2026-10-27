这版修改帮你去掉了原版中偶爾带有的一点点生硬感和模版化痕迹，让文字读起来更像是一个真实的学生开发者在认真介绍自己的社团工具——语气更自然、干练，也更有温度。

你可以直接复制使用：

```markdown
# 时枢 ChronoHub

> 用影像记录师大，用青春诠释梦想。

SYNUTV 校园电视台的内部工具：把「教务系统导课表 → 多人课表叠加找共同空闲 → 排值班表」这一套繁琐流程，直接拧成了一个开箱即用的桌面端小工具。

![版本](https://img.shields.io/badge/version-v3.5.1-blue)
![平台](https://img.shields.io/badge/platform-Windows%2010%2F11-lightgrey)
![Python](https://img.shields.io/badge/python-3.13-3776ab)

---

## 核心功能

| 功能        | 说明                                                  |
| --------- | --------------------------------------------------- |
| **教务账号**  | 账号存放在本地 SQLite，通过 Windows DPAPI 加密，只有本机当前账户能解开。 |
| **自动入库**  | 内嵌 Chromium 自动登录教务系统，导出的课表 PDF 直接解析入库，告别手动点选。 |
| **课表解析**  | 支持网格与列表两种版式的 PDF，自动展开单双周及周次区间。           |
| **多人空课表** | 多人课表一键叠加找共同空闲，支持按周统计；分组支持折叠，拖拽即可调整组员。 |
| **特殊安排**  | 例会、值班、调课停课等课表外的占用，手动标记一下即可剔除。             |
| **排值班表**  | 1-8 节按人补位、9-10 节部长岗，每人每周最多一次；支持换人、标「本周免值班」及多周批量清除。 |
| **导出值班表** | 按 Word 模板一键填表，字体字号保持一致；支持手动微调次数，所有改动皆有迹可循。 |
| **本地优先**  | 所有数据只存放在程序目录的 `data/` 下，绝不上传任何第三方服务器。         |

---

## 技术栈

- **Python 3.13**
- **PySide6**（Qt 6）—— 窗口与内嵌浏览器（QtWebEngine）
- **pywebview**（`gui="qt"` 后端）—— 让 HTML 前端跑在同一个 Qt 事件循环里
- **pdfplumber** —— 课表 PDF 解析
- **python-docx** —— 值班表 Word 导出

---

## 目录结构


```

app/            Python 后端
api.py          js_api 桥（前端能调用的各种后端方法）
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

产出 `dist/ChronoHub/` **文件夹**（onedir 模式）。请把整个文件夹一起打包分发，不能只发单个 exe。

## 测试

```bash
python -m unittest discover -s tests

```

---

## 升级指南（如何保留数据）

这个软件没有复杂的安装程序，也没有乱七八糟的注册表项，本质上就是一个纯净的文件夹。所有的核心数据都存在 `ChronoHub\data\` 里，所以升级时只要**护好这个目录**就行。

### 推荐：就地覆盖（省去搬数据）

1. 完全退出程序（在任务管理器里确认没有残留的 `ChronoHub.exe`）。
2. 把新版本的 zip 解压到**另一个临时文件夹**。
3. 把临时文件夹里的 `ChronoHub.exe` 和 `_internal\` 整个复制过去，覆盖掉旧的同名文件。
4. 删掉临时文件夹，搞定。

`data\` 完好如初，人员、账号、历史值班表全部都在。

### 备选：新文件夹 + 手动挪数据

1. 新版本解压到一个全新文件夹。
2. 把旧文件夹里的 `data\` 整个复制过去（覆盖掉新的空 `data\`）。
3. 确认无误后删掉旧文件夹。

### ⚠️ 注意事项

* **必须先退出程序**。运行时光盘里的 `data\` 文件被占用，直接复制可能会导致数据丢失或报错。
* **不要同时运行两个副本**，否则它们会各自读写 `data\` 导致数据分叉。
* **换电脑后必须重新录入密码**：账号密码用了 Windows DPAPI 加密，绑定了当前电脑和当前的 Windows 账户。换机器或换账号后，`accounts.db` 里的密码会解不开（这是系统层面的安全设计，不是 bug）。
* 实在不放心的话，升级前把整个 `data\` 复制一份当备份（其中核心其实只有 `people.json`、`groups.json`、`specials.json`、`duty.json`、`accounts.db` 这几个文件）。

---

## 数据与隐私

* 所有数据（账号库、课表、分组、排班、日志）全在程序目录的 `data/` 下。
* `data/` 已经在 `.gitignore` 里，绝不会被提交到代码仓库。
* 账号密码通过 Windows DPAPI 加密绑定本机，换电脑解不开。

---

## 第三y方资源

* 开屏字体：[得意黑 Smiley Sans](https://github.com/atelier-anchor/smiley-sans)，基于 SIL Open Font License 1.1 开源。
* 其他依赖详见 `requirements.txt`。

---

## 关于

SYNUTV 出品 · 开发者：阿山 · [ashan441@163.com](https://www.google.com/search?q=mailto%3Aashan441%40163.com) · © 2026 阿山

```

```
