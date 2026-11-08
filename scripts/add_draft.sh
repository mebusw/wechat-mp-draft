#!/bin/bash
# 保存文章到微信公众号草稿箱
#
# 使用方法: ./add_draft.sh <access_token> <title> <content> <thumb_media_id> [author] [digest]
#
# ⚠️ 本脚本在本机调用微信 API，要求**本机出口 IP 在公众号白名单里**。
#    本机 IP 常变时，改用 scripts/remote_publish.py —— 由固定 IP 的服务器发稿。
#
# 预处理（标题去重 / 列表清洗）已抽到 wechat_html.py，与远程发布共用同一套逻辑，
# 保证本地发布与远程发布的正文完全一致。

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ACCESS_TOKEN=$1
TITLE=$2
CONTENT=$3
THUMB_MEDIA_ID=$4
AUTHOR=${5:-""}
DIGEST=${6:-""}

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

if ! command -v jq &> /dev/null; then
    echo "错误：需要安装 jq 工具"
    echo "安装命令: apt-get install jq"
    exit 1
fi

if ! command -v python3 &> /dev/null; then
    echo "错误：需要 python3（用于 WeChat HTML normalization）"
    exit 1
fi

# ---------------------------------------------------------------------------
# WeChat MP editor normalization — 标题去重 + 列表清洗
# ---------------------------------------------------------------------------
# 1) 标题只走 title 字段：正文开头的 <h1> 必须剥离，否则文章顶部会出现两次标题。
#    markdown 渲染器会把整篇包在 <section id="nice"> 里，H1 往往是这个包装的
#    第一个子元素，所以正则允许一个可选的 <section|div> 开标签。
#
# 2) 公众号编辑器会把 <ol>/<ul> 里的空白文本节点当成额外的空 <li>，
#    导致编号错乱、列表项之间多出空行 —— 必须把列表内空白全部清掉。
#
# 详见 wechat_html.py 与 SKILL.md「关键问题与解决方案」。
# ---------------------------------------------------------------------------
TMP_IN=$(mktemp -t wechat_content_in.XXXXXX)
TMP_OUT=$(mktemp -t wechat_content_out.XXXXXX)
trap 'rm -f "$TMP_IN" "$TMP_OUT"' EXIT

printf '%s' "$CONTENT" > "$TMP_IN"
SANITIZE_JSON=$(python3 "$SCRIPT_DIR/wechat_html.py" sanitize "$TMP_IN" "$TMP_OUT")
CONTENT=$(cat "$TMP_OUT")

H1_STRIPPED=$(printf '%s' "$SANITIZE_JSON" | jq -r '.h1_stripped_from_body // "null"')
H1_MATCHES=$(printf '%s' "$SANITIZE_JSON" | jq -r --arg t "$TITLE" \
    '(.h1_stripped_from_body // "") as $s | ($s != "" and $s == $t)')

if [ "$H1_STRIPPED" = "null" ] || [ -z "$H1_STRIPPED" ]; then
    :
elif [ "$H1_MATCHES" = "true" ]; then
    echo "✅ 标题去重: body 顶部的 <h1> 已自动剥离（与 title 参数一致），避免重复显示"
else
    echo "⚠️  body 顶部的 <h1>「$H1_STRIPPED」与 title 参数「$TITLE」不一致 —— 已剥离 body 的 H1，但请检查是否本意如此"
fi

# Pre-send self-check — abort if the payload still has the known anti-patterns,
# so the user gets a clear error instead of a silently-broken article.
CHECK=$(printf '%s' "$CONTENT" | python3 -c '
import sys, json, os
sys.path.insert(0, os.environ["SCRIPT_DIR"])
import wechat_html
print(json.dumps(wechat_html.self_check(sys.stdin.read()), ensure_ascii=False))
' SCRIPT_DIR="$SCRIPT_DIR" 2>/dev/null || printf '%s' "$CONTENT" | python3 -c '
import re, sys, json
h = sys.stdin.read()
print(json.dumps({
    "li": len(re.findall(r"<li\b[^>]*>", h, re.I)),
    "section_in_li": len(re.findall(r"<li\b[^>]*>\s*<section\b", h, re.I)),
    "whitespace_between_li": len(re.findall(r"</li>\s+<li\b", h, re.I)),
    "whitespace_ol_to_li": len(re.findall(r"<(?:ol|ul)\b[^>]*>\s+<li\b", h, re.I)),
    "whitespace_li_to_ol": len(re.findall(r"</li>\s+</(?:ol|ul)>", h, re.I)),
}))
')
echo "==> WeChat HTML self-check: $CHECK"

SEC_IN_LI=$(echo "$CHECK" | jq -r '.section_in_li // 0')
WS_LI_LI=$(echo "$CHECK" | jq -r '.whitespace_between_li // 0')
WS_OL_LI=$(echo "$CHECK" | jq -r '.whitespace_ol_to_li // 0')
WS_LI_OL=$(echo "$CHECK" | jq -r '.whitespace_li_to_ol // 0')
if [ "$SEC_IN_LI" != "0" ]; then
    echo "⚠️  Warning: $SEC_IN_LI <li> still contain <section> wrappers — MP editor will drop the list markers and add blank rows."
    echo "   Fix: re-render with wechat-markdown-html-render (the renderer flattens these automatically)."
fi
if [ "$WS_LI_LI" != "0" ] || [ "$WS_OL_LI" != "0" ] || [ "$WS_LI_OL" != "0" ]; then
    echo "❌ Internal error: whitespace between list elements was not stripped (li↔li=$WS_LI_LI, ol→li=$WS_OL_LI, li→ol=$WS_LI_OL)."
    exit 1
fi

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

if echo "$RESPONSE" | jq -e '.media_id' > /dev/null 2>&1; then
    echo "✅ 草稿保存成功"
    echo "$RESPONSE" | jq .
else
    echo "❌ 草稿保存失败"
    echo "$RESPONSE" | jq .
    exit 1
fi
