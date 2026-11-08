#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""微信公众号正文 HTML 共享工具库（本地机 + 远程发布服务器通用）。

约束：必须能在 Python 3.6 上运行（远程发布服务器是 CentOS 7 / Python 3.6.8），
因此本文件不使用 3.9+ 的内置泛型注解（list[str]）与 3.10+ 的 `X | None` 注解。

提供的函数：
    strip_leading_h1(html)      -> (html, stripped_text)  剥离正文开头的 <h1>（防标题重复）
    sanitize_lists(html)        -> html                    清理列表内空白（防公众号编辑器列表崩坏）
    self_check(html)            -> dict                   发送前自检
    compress_html(html)         -> (html, stats)          等价压缩，撑过 2 万字符硬限制
    insert_images(html, specs)  -> html                    按位置插入 <img>
    extract_image_srcs(html)    -> list                   列出正文中的图片地址

命令行：
    python3 wechat_html.py compress <in.html> [out.html]
    python3 wechat_html.py check    <in.html>
    python3 wechat_html.py insert   <in.html> <out.html> --spec spec.json
"""

from __future__ import print_function

import argparse
import io
import json
import re
import sys

# 微信正文硬限制：2 万字符
CONTENT_CHAR_LIMIT = 20000

# ---------------------------------------------------------------------------
# 1. 标题去重：剥离正文开头的 <h1>
# ---------------------------------------------------------------------------
_LEADING_H1_RE = re.compile(
    r"\s*(?:<(?:section|div)\b[^>]*>\s*)?<h1\b[^>]*>(.*?)</h1>\s*",
    re.I | re.S,
)


def _unescape_text(text):
    return (
        text.replace("&nbsp;", " ")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&amp;", "&")
        .strip()
    )


def strip_leading_h1(html):
    """剥离正文开头的 <h1>（标题只走 title 字段）。

    返回 (新 html, 被剥离的标题文本或 None)。
    """
    m = _LEADING_H1_RE.match(html)
    if not m:
        return html, None
    text = _unescape_text(re.sub(r"<[^>]+>", "", m.group(1)))
    return html[m.end():], text


# ---------------------------------------------------------------------------
# 2. 列表清洗：公众号编辑器把 <ol>/<ul> 里的空白文本节点当成空的 <li>
# ---------------------------------------------------------------------------
def sanitize_lists(html):
    html = re.sub(r"(<(?:ol|ul)\b[^>]*>)\s+(<li\b)", r"\1\2", html, flags=re.I)
    html = re.sub(r"(</li>)\s+(<li\b)", r"\1\2", html, flags=re.I)
    html = re.sub(r"(</li>)\s+(</(?:ol|ul)>)", r"\1\2", html, flags=re.I)
    return html


def self_check(html):
    """发送前自检：统计已知的反模式。"""
    return {
        "chars": len(html),
        "over_limit": len(html) > CONTENT_CHAR_LIMIT,
        "li": len(re.findall(r"<li\b[^>]*>", html, re.I)),
        "ol": len(re.findall(r"<ol\b[^>]*>", html, re.I)),
        "ul": len(re.findall(r"<ul\b[^>]*>", html, re.I)),
        "section_in_li": len(re.findall(r"<li\b[^>]*>\s*<section\b", html, re.I)),
        "whitespace_between_li": len(re.findall(r"</li>\s+<li\b", html, re.I)),
        "whitespace_ol_to_li": len(re.findall(r"<(?:ol|ul)\b[^>]*>\s+<li\b", html, re.I)),
        "whitespace_li_to_ol": len(re.findall(r"</li>\s+</(?:ol|ul)>", html, re.I)),
        "img_count": len(re.findall(r"<img\b", html, re.I)),
        "external_img": len(
            [
                s
                for s in extract_image_srcs(html)
                if "mmbiz.qpic.cn" not in s
            ]
        ),
    }


def extract_image_srcs(html):
    return re.findall(r'<img\b[^>]*\bsrc="([^"]*)"', html, re.I)


# ---------------------------------------------------------------------------
# 3. 等价压缩：把渲染器输出的冗余内联样式换成等价的短写法
#    视觉不变，实测可减重 40%+（详见 SKILL.md「字符限制」）
# ---------------------------------------------------------------------------
BASE_RECIPES = [
    (r'\s+data-tool="[^"]*"', ""),
    (r"padding-top: 8px; padding-bottom: 8px;", "padding: 8px 0;"),
    (r"word-spacing: 0px; letter-spacing: 0px;", ""),
    (r"word-break: break-all;", ""),
]

EQUIV_RECIPES = [
    # <p> 74 → 44 字符（font-size/line-height 由外层 section 继承）
    (
        r'<p style="font-size: 16px; padding: 8px 0; margin: 0; line-height: 26px; color: #666;">',
        '<p style="padding:8px 0;margin:0;color:#666;">',
    ),
    (
        r'<p style="font-size: 16px; padding: 8px 0; margin: 0px; line-height: 26px; color: #999; padding: 3px 0;">',
        '<p style="padding:3px 0;margin:0;color:#999;">',
    ),
    # <li> 75 → 18 字符
    (
        r'<li style="line-height: 26px; text-align: left; color: rgb\(1,1,1\); font-weight: 500">',
        '<li style="color:rgb(1,1,1)">',
    ),
    # 标题装饰 span
    (r'<span class="prefix" style="display: none;"></span>', ""),
    (r'<span class="suffix"></span>', ""),
    (
        r'<span class="content" style="border-bottom: 1px solid hsl\(216, 100%, 68%\);">',
        "<span>",
    ),
    # 标题 / 列表 / 引用块
    (
        r'<h2 style="margin-top: 30px; margin-bottom: 15px; padding: 0px; font-weight: normal; color: #333; font-size: 1\.4em; border-bottom: 1px solid hsl\(216, 100%, 68%\);">',
        '<h2 style="margin:30px 0 15px;font-weight:normal;color:#333;font-size:1.4em;border-bottom:1px solid hsl(216,100%,68%);">',
    ),
    (
        r'<h3 style="margin-top: 25px; margin-bottom: 12px; padding: 0px; font-weight: normal; color: #333; font-size: 1\.2em;">',
        '<h3 style="margin:25px 0 12px;font-weight:normal;color:#333;font-size:1.2em;">',
    ),
    (
        r'<ul style="margin-top: 8px; margin-bottom: 8px; color: black; list-style-type: disc; padding-left: 2em;">',
        '<ul style="margin:8px 0;padding-left:2em;list-style-type:disc;">',
    ),
    (
        r'<ol style="margin-top: 8px; margin-bottom: 8px; color: black; list-style-type: decimal; padding-left: 2em;">',
        '<ol style="margin:8px 0;padding-left:2em;list-style-type:decimal;">',
    ),
    (
        r'<blockquote style="display: block; font-size: 0\.9em; overflow: auto; overflow-scrolling: touch; border-left: 3px solid rgba\(0, 0, 0, 0\.4\); color: #6a737d; padding-top: 10px; padding-bottom: 10px; padding-left: 20px; padding-right: 10px; margin-bottom: 20px; margin-top: 20px; background: #f9f9f9; border-left-color: hsl\(216, 100%, 68%\);">',
        '<blockquote style="font-size:.9em;border-left:3px solid hsl(216,100%,68%);color:#6a737d;padding:10px 10px 10px 20px;margin:20px 0;background:#f9f9f9;">',
    ),
]


def compress_html(html):
    """做视觉等价的压缩，返回 (新 html, 统计信息)。"""
    before = len(html)
    for pattern, replacement in BASE_RECIPES:
        html = re.sub(pattern, replacement, html)
    for pattern, replacement in EQUIV_RECIPES:
        html = re.sub(pattern, replacement, html)
    # 收敛连续空白（不动 <pre> / <code> 里的换行）
    html = re.sub(r"[ \t]{2,}", " ", html)
    after = len(html)
    stats = {
        "before": before,
        "after": after,
        "saved": before - after,
        "ratio": round(1.0 - (float(after) / before), 4) if before else 0.0,
        "over_limit": after > CONTENT_CHAR_LIMIT,
    }
    return html, stats


# ---------------------------------------------------------------------------
# 4. 插入正文图片
# ---------------------------------------------------------------------------
IMG_TEMPLATE = '<img src="{url}"/>'


def _paragraphs(html):
    return list(re.finditer(r"<p\b[^>]*>.*?</p>", html, flags=re.S | re.I))


def list_headings(html):
    """列出正文里的标题（纯文本），用于给 AI 提示可用的插入锚点。"""
    out = []
    for m in re.finditer(r"<h([1-6])\b[^>]*>(.*?)</h\1>", html, flags=re.S | re.I):
        text = _unescape_text(re.sub(r"<[^>]+>", "", m.group(2)))
        if text:
            out.append("h{0}: {1}".format(m.group(1), text))
    return out


def find_marker(html, marker):
    """定位 marker 的插入位置（返回切点下标，-1 表示没找到）。

    两级匹配：
      1) 精确子串（marker 是完整 HTML 片段时）
      2) 纯文本回退（marker 只写了标题文字时，插到包含该文字的那个元素的标签之后）
    """
    if not marker:
        return -1
    idx = html.find(marker)
    if idx >= 0:
        return idx + len(marker)
    text = _unescape_text(re.sub(r"<[^>]+>", "", marker))
    if not text:
        return -1
    pos = html.find(text)
    if pos < 0:
        return -1
    gt = html.find(">", pos)
    if gt < 0:
        return -1
    return gt + 1


def insert_images(html, specs):
    """按 spec 列表插入 <img>。

    spec 每项：
        {"url": ..., "after_paragraph": N}   在第 N 个 <p> 之后插入（1-indexed）
        {"url": ..., "after_marker": "..."}  在 HTML 中首次出现该子串之后插入
        {"url": ..., "mode": "append"}        追加到末尾
    """
    for index, item in enumerate(specs, 1):
        url = item.get("url")
        if not url:
            raise ValueError("spec[{}] 缺少 url 字段".format(index))
        tag = IMG_TEMPLATE.format(url=url)
        if "after_paragraph" in item:
            n = int(item["after_paragraph"])
            paragraphs = _paragraphs(html)
            if not paragraphs:
                raise ValueError("HTML 中未找到任何 <p> 段落")
            if n < 1 or n > len(paragraphs):
                raise ValueError(
                    "after_paragraph={} 超出范围，HTML 仅有 {} 个 <p> 段落".format(
                        n, len(paragraphs)
                    )
                )
            target = paragraphs[n - 1]
            html = html[: target.start()] + target.group(0) + tag + html[target.end():]
        elif "after_marker" in item:
            marker = item["after_marker"]
            cut = find_marker(html, marker)
            if cut < 0:
                headings = list_headings(html)
                raise ValueError(
                    "after_marker 未在 HTML 中找到: {!r}。当前正文可选的标题：{}"
                    "（可直接用标题文本，或带上完整标签如 <h2>核心观点</h2>）".format(
                        marker, headings if headings else "（无）"
                    )
                )
            html = html[:cut] + tag + html[cut:]
        elif item.get("mode") == "append":
            html = html + tag
        else:
            raise ValueError(
                "spec[{}] 必须包含 after_paragraph / after_marker / mode=append 之一".format(
                    index
                )
            )
    return html


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _read(path):
    if path in (None, "-"):
        return sys.stdin.read()
    with io.open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _write(path, text):
    if path in (None, "-"):
        sys.stdout.write(text)
    else:
        with io.open(path, "w", encoding="utf-8") as handle:
            handle.write(text)


def main(argv=None):
    parser = argparse.ArgumentParser(description="公众号正文 HTML 工具库")
    sub = parser.add_subparsers(dest="cmd")

    p_comp = sub.add_parser("compress", help="等价压缩正文 HTML")
    p_comp.add_argument("input")
    p_comp.add_argument("output", nargs="?", default="-")

    p_check = sub.add_parser("check", help="发送前自检")
    p_check.add_argument("input")

    p_ins = sub.add_parser("insert", help="按 spec 插入图片")
    p_ins.add_argument("input")
    p_ins.add_argument("output")
    p_ins.add_argument("--spec", required=True)

    p_str = sub.add_parser("strip-h1", help="剥离正文开头的 <h1>")
    p_str.add_argument("input")
    p_str.add_argument("output", nargs="?", default="-")

    p_san = sub.add_parser("sanitize", help="标题去重 + 列表清洗（发稿前统一预处理）")
    p_san.add_argument("input")
    p_san.add_argument("output", nargs="?", default="-")

    args = parser.parse_args(argv)

    if args.cmd == "sanitize":
        html = _read(args.input)
        html, stripped = strip_leading_h1(html)
        html = sanitize_lists(html)
        _write(args.output, html)
        print(json.dumps({"h1_stripped_from_body": stripped}, ensure_ascii=False))
        return 0

    if args.cmd == "compress":
        html = _read(args.input)
        html, stats = compress_html(html)
        _write(args.output, html)
        print(json.dumps(stats, ensure_ascii=False))
        return 0

    if args.cmd == "check":
        html = _read(args.input)
        print(json.dumps(self_check(html), ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "insert":
        html = _read(args.input)
        with io.open(args.spec, "r", encoding="utf-8") as handle:
            spec = json.load(handle)
        html = insert_images(html, spec)
        _write(args.output, html)
        return 0

    if args.cmd == "strip-h1":
        html = _read(args.input)
        html, text = strip_leading_h1(html)
        _write(args.output, html)
        print(json.dumps({"h1_stripped_from_body": text}, ensure_ascii=False))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
