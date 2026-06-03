#!/bin/bash
# 保存文章到微信公众号草稿箱
# 使用方法: ./add_draft.sh <access_token> <title> <content> <thumb_media_id> [author] [digest]

set -e

ACCESS_TOKEN=$1
TITLE=$2
CONTENT=$3
THUMB_MEDIA_ID=$4
AUTHOR=${5:-""}
DIGEST=${6:-""}

# 参数检查
if [ -z "$ACCESS_TOKEN" ] || [ -z "$TITLE" ] || [ -z "$CONTENT" ] || [ -z "$THUMB_MEDIA_ID" ]; then
    echo "错误：缺少必填参数"
    echo "用法: $0 <access_token> <title> <content> <thumb_media_id> [author] [digest]"
    echo ""
    echo "参数说明:"
    echo "  access_token    - 接口调用凭证"
    echo "  title           - 文章标题（必填）"
    echo "  content         - HTML格式正文（必填）"
    echo "  thumb_media_id  - 封面图片media_id（必填，需先上传）"
    echo "  author          - 作者名（可选）"
    echo "  digest          - 文章摘要（可选）"
    exit 1
fi

# 检查 jq 是否安装
if ! command -v jq &> /dev/null; then
    echo "错误：需要安装 jq 工具"
    echo "安装命令: apt-get install jq"
    exit 1
fi

# 检查 python3 是否可用（用于 HTML normalization + 自检）
if ! command -v python3 &> /dev/null; then
    echo "错误：需要 python3（用于 WeChat HTML normalization）"
    exit 1
fi

# ---------------------------------------------------------------------------
# WeChat MP editor list normalization — defensive sanitizer.
#
# Mirrors the post-processing built into wechat-markdown-html-render. Re-applied
# here as defense-in-depth because content may be hand-written, copy-pasted, or
# routed through a pretty-printer that re-introduces inter-<li> whitespace
# between rendering and publishing — both symptoms produce silently broken
# ordered lists in the published article. See SKILL.md "Troubleshooting" for
# the underlying MP editor quirks.
# ---------------------------------------------------------------------------
SANITIZED=$(printf '%s' "$CONTENT" | python3 -c '
import re, sys
html = sys.stdin.read()

# 1. Strip whitespace between <ol>/<ul> and <li>, between sibling <li>s, and
#    before the closing </ol>/</ul>. The MP editor treats those whitespace
#    text nodes as additional empty list items.
html = re.sub(r"(<(?:ol|ul)\b[^>]*>)\s+(<li\b)", r"\1\2", html, flags=re.I)
html = re.sub(r"(</li>)\s+(<li\b)", r"\1\2", html, flags=re.I)
html = re.sub(r"(</li>)\s+(</(?:ol|ul)>)", r"\1\2", html, flags=re.I)

sys.stdout.write(html)
')

# Pre-send self-check — abort if the payload still has the known anti-patterns,
# so the user gets a clear error instead of a silently-broken article.
CHECK=$(printf '%s' "$SANITIZED" | python3 -c '
import re, sys, json
h = sys.stdin.read()
li_count    = len(re.findall(r"<li\b[^>]*>", h, re.I))
ol_count    = len(re.findall(r"<ol\b[^>]*>", h, re.I))
ul_count    = len(re.findall(r"<ul\b[^>]*>", h, re.I))
sec_in_li   = len(re.findall(r"<li\b[^>]*>\s*<section\b", h, re.I))
gap_li_li   = len(re.findall(r"</li>\s+<li\b", h, re.I))
gap_ol_li   = len(re.findall(r"<(?:ol|ul)\b[^>]*>\s+<li\b", h, re.I))
gap_li_ol   = len(re.findall(r"</li>\s+</(?:ol|ul)>", h, re.I))
print(json.dumps({
    "li": li_count, "ol": ol_count, "ul": ul_count,
    "section_in_li": sec_in_li,
    "whitespace_between_li": gap_li_li,
    "whitespace_ol_to_li":   gap_ol_li,
    "whitespace_li_to_ol":   gap_li_ol,
}))
')
echo "==> WeChat HTML self-check: $CHECK"
SEC_IN_LI=$(echo "$CHECK" | jq -r '.section_in_li')
WS_LI_LI=$(echo "$CHECK"  | jq -r '.whitespace_between_li')
WS_OL_LI=$(echo "$CHECK"  | jq -r '.whitespace_ol_to_li')
WS_LI_OL=$(echo "$CHECK"  | jq -r '.whitespace_li_to_ol')
if [ "$SEC_IN_LI" != "0" ]; then
    echo "⚠️  Warning: $SEC_IN_LI <li> still contain <section> wrappers — MP editor will drop the list markers and add blank rows."
    echo "   Fix: re-render with wechat-markdown-html-render (the renderer flattens these automatically)."
fi
if [ "$WS_LI_LI" != "0" ] || [ "$WS_OL_LI" != "0" ] || [ "$WS_LI_OL" != "0" ]; then
    echo "❌ Internal error: whitespace between list elements was not stripped (li↔li=$WS_LI_LI, ol→li=$WS_OL_LI, li→ol=$WS_LI_OL)."
    exit 1
fi

CONTENT="$SANITIZED"

# 构造 JSON 请求体
JSON=$(jq -n \
    --arg title "$TITLE" \
    --arg content "$CONTENT" \
    --arg author "$AUTHOR" \
    --arg digest "$DIGEST" \
    --arg thumb "$THUMB_MEDIA_ID" \
    '{
        articles: [{
            title: $title,
            content: $content,
            author: $author,
            digest: $digest,
            thumb_media_id: $thumb,
            show_cover_pic: 1,
            need_open_comment: 1,
            only_fans_can_comment: 0
        }]
    }')

# Debug
echo "----"
echo "$JSON"
echo "----"

# 调用 API
RESPONSE=$(curl -s -X POST "https://api.weixin.qq.com/cgi-bin/draft/add?access_token=${ACCESS_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "$JSON")

# 检查返回结果
if echo "$RESPONSE" | jq -e '.media_id' > /dev/null 2>&1; then
    echo "✅ 草稿保存成功"
    echo "$RESPONSE" | jq .
else
    echo "❌ 草稿保存失败"
    echo "$RESPONSE" | jq .
    exit 1
fi
