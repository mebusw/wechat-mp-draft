#!/bin/bash
# 上传图文消息内的图片（获取可在 <img> 标签中使用的永久 URL）
#
# 与 upload_image.sh（永久素材，封面图用）的区别：
#   - 本接口返回的不是 media_id，而是可直接放在正文 HTML <img src="..."> 中的 url
#   - 上传的图片不占用公众号 10 万永久素材的额度
#   - 仅支持 JPG/PNG，文件 ≤1MB
#   - 外部图片 URL 会被微信过滤，正文必须使用本接口返回的 URL
#
# 使用方法: ./upload_content_image.sh <access_token> <图片路径>

set -e

ACCESS_TOKEN=$1
IMAGE_PATH=$2

# 参数检查
if [ -z "$ACCESS_TOKEN" ] || [ -z "$IMAGE_PATH" ]; then
    echo "错误：缺少参数"
    echo "用法: $0 <access_token> <image_path>"
    exit 1
fi

# 检查文件是否存在
if [ ! -f "$IMAGE_PATH" ]; then
    echo "错误：图片文件不存在: $IMAGE_PATH"
    exit 1
fi

# 检查文件类型（只接受图片）
FILE_TYPE=$(file -b --mime-type "$IMAGE_PATH")
if [[ ! "$FILE_TYPE" =~ ^image/ ]]; then
    echo "错误：文件不是图片类型: $FILE_TYPE"
    exit 1
fi

# 检查文件大小（接口限制 ≤1MB）
FILE_SIZE=$(stat -f%z "$IMAGE_PATH" 2>/dev/null || stat -c%s "$IMAGE_PATH")
MAX_SIZE=$((1024 * 1024))
if [ "$FILE_SIZE" -gt "$MAX_SIZE" ]; then
    echo "错误：图片文件超过 1MB 上限（实际: ${FILE_SIZE} 字节）"
    exit 1
fi

echo "正在上传正文图片: $IMAGE_PATH (${FILE_SIZE} 字节)"

# 调用微信 API：上传图文消息内的图片
RESPONSE=$(curl -s -F "media=@$IMAGE_PATH" \
    "https://api.weixin.qq.com/cgi-bin/media/uploadimg?access_token=${ACCESS_TOKEN}")

# 检查返回结果：成功时返回 {"url":"http://mmbiz.qpic.cn/..."}
if echo "$RESPONSE" | grep -q '"url"'; then
    echo "✅ 正文图片上传成功"
    echo "$RESPONSE"
else
    echo "❌ 正文图片上传失败"
    echo "$RESPONSE"
    exit 1
fi