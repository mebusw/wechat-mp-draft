#!/usr/bin/env python3
"""
将 <img> 标签插入到公众号文章 HTML 中。

微信公众号规定：正文中的图片 url 必须来自 "上传图文消息内的图片获取URL" 接口，
外部 url 会被过滤。本工具接收上传后返回的永久 URL，并按 AI 建议的位置插入正文。

两种调用方式：
  1) 单张插入（命令行参数）
     insert_content_image.py --input article.html --url https://... --after-paragraph 3

  2) 批量插入（JSON spec）
     insert_content_image.py --input article.html --spec spec.json
     spec.json 内容：
       [
         {"url": "https://...", "after_paragraph": 3},
         {"url": "https://...", "after_marker": "<h2>第二章</h2>"}
       ]

插入模式（每条 spec 可独立指定，缺省 after_paragraph）：
  - after_paragraph N   在第 N 个 <p>...</p> 段落后插入（1-indexed）
  - after_marker STR    在 HTML 中首次出现 STR 的位置之后插入
  - append              追加到末尾

生成的 <img> 标签紧凑无多余空格：<img src="URL"/>
"""

import argparse
import json
import re
import sys
from pathlib import Path


IMG_TEMPLATE = '<img src="{url}"/>'


def _wrap_paragraph(p_text: str, img_html: str) -> str:
    """在指定 <p>...</p> 后插入 <img>，不引入多余空白。"""
    return f"{p_text}{img_html}"


def _split_paragraphs(html: str):
    """把 HTML 切成 (段落文本, 段落结束位置) 的列表。段落定义为 <p>...</p>。"""
    paragraphs = []
    for m in re.finditer(r"<p\b[^>]*>.*?</p>", html, flags=re.DOTALL | re.IGNORECASE):
        paragraphs.append(m)
    return paragraphs


def insert_after_paragraph(html: str, n: int, img_html: str) -> str:
    """在第 n 个 <p>...</p> 段落后插入 <img>（1-indexed）。"""
    if n < 1:
        raise ValueError(f"after_paragraph 必须 >= 1，得到 {n}")
    paragraphs = _split_paragraphs(html)
    if not paragraphs:
        raise ValueError("HTML 中未找到任何 <p> 段落")
    if n > len(paragraphs):
        raise ValueError(
            f"after_paragraph={n} 超出范围，HTML 仅有 {len(paragraphs)} 个 <p> 段落"
        )
    target = paragraphs[n - 1]
    insertion = _wrap_paragraph(target.group(0), img_html)
    return html[: target.start()] + insertion + html[target.end() :]


def insert_after_marker(html: str, marker: str, img_html: str) -> str:
    """在 HTML 中首次出现 marker 的位置之后插入 <img>。"""
    idx = html.find(marker)
    if idx < 0:
        raise ValueError(f"after_marker 未在 HTML 中找到: {marker!r}")
    cut = idx + len(marker)
    return html[:cut] + img_html + html[cut:]


def insert_append(html: str, img_html: str) -> str:
    """追加到 HTML 末尾。"""
    return html + img_html


def apply_spec(html: str, spec: list) -> str:
    """按顺序应用 spec 中的所有插入操作。"""
    for i, item in enumerate(spec, 1):
        url = item.get("url")
        if not url:
            raise ValueError(f"spec[{i}] 缺少 url 字段")
        img_html = IMG_TEMPLATE.format(url=url)

        if "after_paragraph" in item:
            html = insert_after_paragraph(html, int(item["after_paragraph"]), img_html)
        elif "after_marker" in item:
            html = insert_after_marker(html, item["after_marker"], img_html)
        elif item.get("mode") == "append":
            html = insert_append(html, img_html)
        else:
            raise ValueError(
                f"spec[{i}] 必须包含 after_paragraph / after_marker / mode=append 之一"
            )
    return html


def read_input(path: str | None) -> str:
    if path is None or path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def write_output(text: str, path: str | None) -> None:
    if path is None or path == "-":
        sys.stdout.write(text)
    else:
        Path(path).write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="在公众号文章 HTML 中插入 <img> 标签")
    parser.add_argument("--input", "-i", default="-", help="输入 HTML 文件路径，- 表示 stdin")
    parser.add_argument("--output", "-o", default="-", help="输出 HTML 文件路径，- 表示 stdout")
    parser.add_argument("--url", help="单张插入：图片 URL")
    parser.add_argument("--after-paragraph", type=int, help="单张插入：在第 N 个 <p> 之后插入")
    parser.add_argument("--after-marker", help="单张插入：在指定 marker 之后插入")
    parser.add_argument(
        "--spec", help="批量插入：JSON spec 文件路径，列表中每项含 url + 定位字段"
    )
    args = parser.parse_args()

    html = read_input(args.input)

    if args.spec:
        spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
        if not isinstance(spec, list):
            raise ValueError("spec 必须是 JSON 数组")
        html = apply_spec(html, spec)
    else:
        if not args.url:
            raise SystemExit("错误：必须提供 --url 或 --spec")
        img_html = IMG_TEMPLATE.format(url=args.url)
        if args.after_paragraph is not None:
            html = insert_after_paragraph(html, args.after_paragraph, img_html)
        elif args.after_marker:
            html = insert_after_marker(html, args.after_marker, img_html)
        else:
            raise SystemExit("错误：单张插入必须指定 --after-paragraph 或 --after-marker")

    write_output(html, args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())