"""按「值班表.docx」模板填充导出。

为什么是「填充」而不是「重新生成一份 docx」：
模板里有固定字号、列宽、5 条注意事项和一张图片。用代码重画必然走样，
最后拿到的是"看起来像但不一样"的东西。所以直接打开模板、只往单元格里写人名。

模板查找顺序（先用户可替换的，再打包内置的）：
1. exe 同级的 `值班表.docx` —— 想换模板不用重新打包
2. 打包内置的副本
"""

from __future__ import annotations

import os
import re

from .paths import base_dir, resource_dir

TEMPLATE_NAME = "值班表.docx"


def template_path() -> str:
    """返回可用的模板路径，找不到返回空串。"""
    candidates = [
        os.path.join(base_dir(), TEMPLATE_NAME),
        os.path.join(resource_dir(), TEMPLATE_NAME),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return ""


def _set_paragraph_text(paragraph, text: str) -> None:
    """替换段落文字，同时保留格式（字号 / 字体）。

    有 run 时沿用第一个 run 的格式；空段落没有 run，直接 add_run 会掉到
    文档默认字体（实测：模板表格是黑体，新加的字变成了 Arial）。
    模板作者在段落标记（pPr/rPr）上留了格式，复制给新 run 即可一致。
    """
    if paragraph.runs:
        paragraph.runs[0].text = text
        for run in paragraph.runs[1:]:
            run.text = ""
        return

    from copy import deepcopy

    from docx.oxml.ns import qn

    run = paragraph.add_run(text)
    pPr = paragraph._p.pPr
    if pPr is None:
        return
    mark_rpr = pPr.find(qn("w:rPr"))
    if mark_rpr is not None:
        run._element.insert(0, deepcopy(mark_rpr))


def _set_cell_text(cell, text: str) -> None:
    """替换单元格文字，保留原有段落对齐与首个 run 的格式。"""
    paragraph = cell.paragraphs[0]
    _set_paragraph_text(paragraph, text)
    # 单元格里若被塞了多个段落，清掉多余的
    for extra in cell.paragraphs[1:]:
        _set_paragraph_text(extra, "")


def _set_cell_lines(cell, lines) -> None:
    """把单元格写成多行：第一行沿用原格式，后面的行照抄格式但字号收小。

    「人名 + （分组）」要挤在一个高度有限的格子里，第二行必须比第一行小，
    所以不能直接复用 `_set_cell_text`。
    """
    lines = [line for line in lines if line]
    if not lines:
        _set_cell_text(cell, "")
        return

    _set_cell_text(cell, lines[0])
    if len(lines) == 1:
        return

    first = cell.paragraphs[0]
    base_size = None
    if first.runs and first.runs[0].font.size is not None:
        base_size = first.runs[0].font.size

    for index, text in enumerate(lines[1:], start=1):
        if index < len(cell.paragraphs):
            paragraph = cell.paragraphs[index]
            _set_paragraph_text(paragraph, text)
        else:
            paragraph = cell.add_paragraph()
            _set_paragraph_text(paragraph, text)
        _tighten(paragraph)
        if base_size is not None and paragraph.runs:
            from docx.shared import Pt

            size = min(base_size.pt * 0.7, 9.0)
            paragraph.runs[0].font.size = Pt(round(size, 1))

    # 第一行也别留着大段行距，否则两行撑得比三行还高
    _tighten(first)


def _tighten(paragraph) -> None:
    """去掉段前段后间距、压成单倍行距，让两行字贴在一起。"""
    from docx.shared import Pt

    paragraph.paragraph_format.space_before = Pt(0)
    paragraph.paragraph_format.space_after = Pt(0)
    paragraph.paragraph_format.line_spacing = 1.0


def _group_text(group: str, label: str = "") -> str:
    """分组的显示形式：（xxx）。

    label 是「部长：」这类前缀的标签 —— 部长的分组本来就是「部长」，
    再在下面写一遍（部长）是废话，跳过。
    """
    group = (group or "").strip()
    if not group:
        return ""
    if label and label in group:
        return ""
    return f"（{group}）"


def _fix_minister_label(row) -> None:
    """把模板里 9-10 节那行的「(台务/部长)」改成「(部长)」。

    台务不再参与排班，导出的表如果还写着「台务/部长」就和内容对不上了。
    只改含「台务」的那个段落，保留它原有的字号字体。
    """
    try:
        paragraphs = row.cells[0].paragraphs
    except IndexError:
        return
    for paragraph in paragraphs:
        text = paragraph.text or ""
        if "台务" in text:
            _set_paragraph_text(paragraph, text.replace("台务/", "").replace("台务", "部长"))


def _row_label(row) -> str:
    """第一列的文字，去掉空白便于匹配。"""
    try:
        text = row.cells[0].text or ""
    except IndexError:
        return ""
    return re.sub(r"\s+", "", text)


def _block_start(label: str):
    """从「第1、2节」里取出起始节次；9-10 节的部长行返回 None。"""
    if "台务" in label or "部长" in label:
        return None
    match = re.search(r"第(\d+)", label)
    if match:
        return int(match.group(1))
    return None


def export_roster(
    roster: dict,
    names: dict,
    out_path: str,
    template: str = None,
    groups: dict = None,
) -> str:
    """把一张值班表填进模板并另存。

    roster  {"week": 3, "assignments": [...]}
    names   {人员key: 显示名}
    groups  {人员key: 分组名} —— 传了就在人名下面多加一行（分组）
    """
    from docx import Document

    source = template or template_path()
    if not source:
        raise FileNotFoundError(
            f"找不到值班表模板「{TEMPLATE_NAME}」，请把它放在程序所在目录"
        )

    document = Document(source)
    week = roster.get("week")
    groups = groups or {}

    # 1) 标题里的周次
    for paragraph in document.paragraphs:
        text = paragraph.text or ""
        if "值班记录" in text and "第" in text:
            new_text = re.sub(r"第\s*[（(]\s*[）)]\s*周", f"第（{week}）周", text)
            if new_text == text:
                new_text = re.sub(r"第[（(][^）)]*[）)]周", f"第（{week}）周", text)
            _set_paragraph_text(paragraph, new_text)
            break

    # 2) 表格
    if not document.tables:
        raise ValueError("模板里没有表格，可能不是预期的值班表模板")

    table = document.tables[0]

    # 按「星期 + 节次」索引本次排班结果
    by_day_block = {}
    by_day_minister = {}
    for item in roster.get("assignments") or []:
        if item.get("kind") == "minister":
            by_day_minister[item["day"]] = item
        else:
            by_day_block[(item["day"], item.get("start"))] = item

    def name_of(key: str) -> str:
        key = str(key or "").strip()
        if not key:
            return ""
        return names.get(key, key)

    def group_of(key: str) -> str:
        key = str(key or "").strip()
        if not key:
            return ""
        return groups.get(key, "")

    filled = 0
    for row in table.rows:
        label = _row_label(row)
        if not label:
            continue
        # 跳过「联系方式」「值班记录」等非人名行
        if "联系方式" in label or "值班记录" in label:
            continue
        if "星期" in label:
            continue

        cells = row.cells
        if len(cells) < 6:
            continue

        start = _block_start(label)
        is_minister = start is None

        if is_minister:
            _fix_minister_label(row)

        for day in range(1, 6):
            if day >= len(cells):
                break
            cell = cells[day]
            if is_minister:
                item = by_day_minister.get(day)
                who = name_of(item.get("person")) if item else ""
                text = f"部长：{who}" if who else ""
                _set_cell_lines(
                    cell,
                    [text, _group_text(group_of(item.get("person")), "部长") if item else ""],
                )
                if text:
                    filled += 1
            else:
                item = by_day_block.get((day, start))
                text = name_of(item.get("person")) if item else ""
                _set_cell_lines(
                    cell,
                    [text, _group_text(group_of(item.get("person"))) if item else ""],
                )
                if text:
                    filled += 1

    directory = os.path.dirname(os.path.abspath(out_path))
    if directory:
        os.makedirs(directory, exist_ok=True)

    document.save(out_path)
    return out_path
