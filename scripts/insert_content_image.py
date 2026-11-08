#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""将 <img> 标签插入到公众号文章 HTML 中（与远程发布共用 wechat_html.py 的插入逻辑）。

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
         {"url": "https://...", "after_marker": "核心观点"}
       ]

插入模式（每条 spec 可独立指定，缺省 after_paragraph）：
  - after_paragraph N   在第 N 个 <p>...</p> 段落后插入（1-indexed）
  - after_marker STR    在 HTML 中首次出现 STR 之后插入（只写标题文字也可以，会自动定位）
  - append              追加到末尾

生成的 <img> 标签紧凑无多余空格：<img src="URL"/>
"""

from __future__ import print_function

import argparse
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import wechat_html  # noqa: E402


def main():
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

    if args.input in (None, "-"):
        html = sys.stdin.read()
    else:
        with io.open(args.input, "r", encoding="utf-8") as handle:
            html = handle.read()

    if args.spec:
        with io.open(args.spec, "r", encoding="utf-8") as handle:
            spec = json.load(handle)
        if not isinstance(spec, list):
            raise ValueError("spec 必须是 JSON 数组")
        html = wechat_html.insert_images(html, spec)
    else:
        if not args.url:
            raise SystemExit("错误：必须提供 --url 或 --spec")
        entry = {"url": args.url}
        if args.after_paragraph is not None:
            entry["after_paragraph"] = args.after_paragraph
        elif args.after_marker:
            entry["after_marker"] = args.after_marker
        else:
            raise SystemExit("错误：单张插入必须指定 --after-paragraph 或 --after-marker")
        html = wechat_html.insert_images(html, [entry])

    if args.output in (None, "-"):
        sys.stdout.write(html)
    else:
        with io.open(args.output, "w", encoding="utf-8") as handle:
            handle.write(html)
    return 0


if __name__ == "__main__":
    sys.exit(main())
