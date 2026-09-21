# 多人空课表

一个 Windows 桌面软件：导入同一教务系统导出的课表 PDF，统计多人共同空闲时间。每个时间段直接显示有空的人名，支持按周次查看（含单双周）和「全程有空」汇总。

## 功能

- 解析教务系统导出的课表 PDF（文本型 PDF）。
- 自动展开 `1-14周`、`2-18周(双)`、`1-15周(单)` 等周次表达。
- 多人空闲统计：按「星期 × 节次」展示空闲人员姓名。
- 按周切换查看，以及「全程有空」汇总。

## 支持的课表格式

同一教务系统可能导出两种版式，本软件都能识别：

- **网格格式**：横向为周一至周日、纵向为节次，包含周次、场地、教师等完整信息。
- **列表格式**：逐行列出「星期 / 节次 / 课程」，不含周次和场地；此类课表默认按 1-18 周（整学期）计算。

## 技术栈

- Python 3 + `pdfplumber`（PDF 解析）
- `pywebview`（把 HTML 界面包装成原生 Windows 窗口）
- 原生 HTML/CSS/JavaScript 前端

## 运行

最简单的方式：双击 `启动.bat`。首次运行会自动安装依赖到本机 Python，之后直接打开软件。

也可以手动运行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m app.main
```

## 测试

```powershell
python -m unittest discover -s tests
```
