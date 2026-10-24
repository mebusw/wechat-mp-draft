---
name: wechat-mp-draft
description: 代写微信公众号文章并保存到公众号草稿箱（每篇正文配图不少于 4 张）。使用场景：用户需要撰写公众号文章并直接发布到微信公众号后台草稿箱；或排查公众号草稿箱里的「标题在文章顶部重复显示两次」「标题行既出现在 title 字段又出现在 body」等 markdown-renderer 标题重复问题。触发词："写公众号文章"、"保存到公众号草稿"、"微信文章"、"公众号发文"、"标题重复"、"草稿标题显示两次"。
---

# 微信公众号草稿 Skill

编写微信公众号文章并保存到草稿箱，流程如下：


1. 基于用户输入，调用SKILL `/jackyshen-write-wechat-article` 或其他类似的技能，改写为微信公众号风格的文章，md格式（首行为 `# 标题`）
2. **从 markdown 中删除首行 `# 标题`**，避免标题在 body 里重复显示（详见「问题 5」）
3. 调用SKILL `/wechat-markdown-html-render` 来分别渲染文本和代码样式，得到html格式的输出文件。
4. 调用SKILL `/huny-img` 或其他任何可用的文生图片(image generation)SKILL来配**1张封面图**（ar 2.35:1）
5. 调用SKILL `/huny-img` 或其他任何可用的文生图片(image generation)SKILL来生成 **至少 4 张内容图片**，然后插入渲染后的正文 HTML 中。位置分布建议：头部一张（尽早吸引读者兴趣）、中前段一张、中后段一张、结尾收束前一张（让读者能读到最后）。具体位置和图片主题由 AI 根据文章内容来决定。**正文图不得少于 4 张**（不含封面）。
6. 继续调用本SKILL的工作流程来访问微信公众号文章草稿箱，标题作为 `title` 参数传入，body 只包含正文（`add_draft.sh` 也会自动剥离 body 开头 H1 作为最后一道防线）


## 前置条件

需要以下凭证（需自行配置）：
- AppID: `wxYOUR_APPID_HERE`
- AppSecret: `YOUR_SECRET_HERE`

**获取方式：** 微信公众平台 → 设置与开发 → 基本配置

**重要：** 服务器 IP 必须添加到公众号后台的 IP 白名单中。

## 工作流程

### 1. 获取 Access Token

```bash
./scripts/get_access_token.sh
```

### 2. 上传封面图片（获取 thumb_media_id）

**封面图片是必填项！**

```bash
./scripts/upload_image.sh <access_token> <图片路径>
```

返回示例：
```json
{"media_id":"xxx","url":"http://mmbiz.qpic.cn/..."}
```

### 3. 将撰写的文章HTML保存到草稿箱

**⚠️ 标题分离规则（防止标题在 body 里重复显示）：**

- 微信公众号后台从 `title` 字段渲染文章标题，并自动显示在文章顶部
- 如果 markdown 源文件以 `# 标题` 开头（来自 `/jackyshen-write-wechat-article` 的标准输出），`/wechat-markdown-html-render` 会把它渲染成 body 里的 `<h1>`
- 必须**只把标题传给 `title` 参数**，**不能让它留在 body 里**，否则用户会在文章顶部看到两次标题
- `add_draft.sh` 已内置**自动剥离 body 开头 H1** 的 sanitizer，作为最后一道防线：即使你没注意把 `# 标题` 留在了 markdown 里，脚本也会自动剥离并打印 ✅ `标题去重` 提示

```bash
./scripts/add_draft.sh <access_token> <标题> <HTML内容> <thumb_media_id> [AUTHOR] [摘要]
```

### 4. 在正文 HTML 中插入至少 4 张新生成的内容图片

> **数量下限：正文图 ≥ 4 张**（不含封面）。每张图都要独立走完 4.1 → 4.2 → 4.3：混元生图 → 下载到本地 → 换成微信永久 URL → 插入正文。
> 少于 4 张会被判为配图不足：读者在长文里滑动时没有视觉锚点，中段容易划走。

> **为什么必须用本接口**：微信规定正文里的图片 url 必须来自 `cgi-bin/media/uploadimg`，外部图片 url 会被过滤掉。本步骤会调 `/huny-img` 生图 → 下载到本地 → 上传获取永久 URL → 按 AI 建议位置插入 HTML。

#### 4.1 调用 `/huny-img` 生成图片（获取临时 URL，1 小时内有效）

```bash
~/.pyenv/versions/py312-huny-img/bin/python ~/.agents/skills/huny-img/scripts/hunyuan3-text-to-image.py \
  -p "<AI 根据文章上下文设计的第一张图 prompt>" \
  -r 16:9
```

输出形如 `图片URL: https://...`，记录备用。

> **批量时格式不同（实测 2026-09-24）**：用 `hunyuan3-batch.py` 串行批量出图时，日志里打印的是
> `  URL=['https://aiart-...cos.ap-guangzhou.myqcloud.com/...']`（Python list repr 的 str()），
> 不是 `图片URL:`。解析正则用：
> ```python
> re.search(r"URL=\[?['\"]?(https://[^\s'\"]+)", line)
> ```
> 注意别写成 `URL=\[?'?(\S+?)'?\]?` —— `(\S+?)` 惰性 + 两个 `?` 可选后缀会只匹配到 1 个字符，
> 下载出 0 字节文件且 curl 不报错，很容易被误判为"链接失效"。

#### 4.2 下载到本地（临时 URL 1 小时后过期，必须立刻下载）

```bash
curl -s -o /tmp/content_img_1.jpg "<huny-img 返回的临时 URL>"
```

#### 4.3 调用 `upload_content_image.sh` 上传到微信（获取正文可用 URL）

```bash
RESP=$(./scripts/upload_content_image.sh "$TOKEN" /tmp/content_img_1.jpg | tail -1)
URL_1=$(echo "$RESP" | jq -r '.url')
```

> 与 `upload_image.sh`（封面永久素材）的区别：本接口返回 `url` 字段（不是 `media_id`），且**不占公众号 10 万永久素材额度**。仅支持 JPG/PNG，文件 ≤1MB。

> ⚠️ **拿到 URL 后必须还原 JSON 转义**（实测 2026-10-02 踩坑）：如果不用 `jq` 而是用
> `grep -o '"url":"[^"]*"'` / `sed` 之类从原始输出里抠 URL，拿到的是**仍带反斜杠的**
> `http:\/\/mmbiz.qpic.cn\/...`。把它直接写进 `<img src="...">` 后，微信取不到图——
> 后台看正文像"没插图"，但 `<img>` 标签其实在（`draft/get` 能查到）。
> 用 `jq -r` 会自动还原转义，是**首选**；若必须手工抠，记得 `s.replace('\\/','/')`。
> 自检：插入前 `grep -o '<img src="[^"]*"' article.html`，URL 里**不允许**出现 `\`。

> ⚠️ **混元的临时 URL 绝对不能直接写进 `<img src>`**（实测 2026-10-24 踩坑，症状极隐蔽）：
> `aiart-*.cos.ap-guangzhou.myqcloud.com` 是 1 小时失效的**外链**地址，微信会把它当外链**静默过滤掉**——
> 本地 HTML 里明明 4 张图，草稿里却只剩 2 张（还是上一轮的旧图），`draft/get` 查出来数量也不对。
> 逐张走 4.3 换成 `mmbiz.qpic.cn` 永久 URL 再插入；插入后必查：
> ```bash
> grep -o '<img src="[^"]*"' article_final.html | grep -v 'mmbiz.qpic.cn'   # 必须为空
> ```

重复 4.1–4.3，得到第 2 / 3 / 4 张的 `URL_2`、`URL_3`、`URL_4`。**每张都要单独上传**——混元的临时 URL 不可复用，也不可直接插入。

#### 4.4 用 `insert_content_image.py` 按 AI 建议的位置插入到 HTML

AI 先通读 HTML，挑选 **4 个**插入点（覆盖头部 / 中前段 / 中后段 / 结尾收束前），每个插入点要与上下文自然衔接，可用以下任一方式指定：

**单张插入（命令行）**

```bash
# 在第 N 个 <p> 段落后插入
./scripts/insert_content_image.py \
  --input article.html --output article_with_imgs.html \
  --url "$URL_1" --after-paragraph 3

# 或在指定 marker（HTML 子串）后插入
./scripts/insert_content_image.py \
  --input article_with_imgs.html --output article_final.html \
  --url "$URL_2" --after-marker "<h2>核心观点</h2>"
```

**批量插入（JSON spec，一次完成多张）**

```bash
cat > /tmp/img_spec.json <<EOF
[
  {"url": "$URL_1", "after_paragraph": 3},
  {"url": "$URL_2", "after_marker": "<h2>核心观点</h2>"},
  {"url": "$URL_3", "after_paragraph": 12},
  {"url": "$URL_4", "after_marker": "<h2>写在最后</h2>"}
]
EOF

./scripts/insert_content_image.py \
  --input article.html --output article_final.html \
  --spec /tmp/img_spec.json
```

> 生成的 `<img>` 标签**紧凑无多余空格**：`<img src="URL"/>`，直接可用。

#### 4.5 将含图的最终 HTML 提交草稿

```bash
FINAL_CONTENT=$(cat article_final.html)
./scripts/add_draft.sh "$TOKEN" "标题" "$FINAL_CONTENT" "$THUMB_ID" "作者" "摘要"
```

#### 4.6 必做：回查草稿确认图片真的进去了

发完别急着报成功。`draft/add` 返回 `media_id` **只代表入库**，不代表图片可用。
用 `draft/get` 回查，核对 `<img>` 数量与 URL：

```bash
curl -s -X POST "https://api.weixin.qq.com/cgi-bin/draft/get?access_token=$TOKEN" \
  -H "Content-Type: application/json" -d "{\"media_id\":\"$MEDIA_ID\"}" \
| python3 -c "import json,sys,re;c=json.load(sys.stdin)['news_item'][0]['content'];\
print(len(re.findall(r'<img',c)));[print(m[:90]) for m in re.findall(r'<img[^>]*>',c)]"
```

- 正常：数量 = 预期张数（**≥ 4**），URL 以 `https://mmbiz.qpic.cn/...` 开头。
- 数量少于预期 → 多半是混元临时 URL 没换永久链接（见 4.3 补注）或 URL 带转义反斜杠；先跑 `grep -v 'mmbiz.qpic.cn'` 自检。
- 微信会把 `src` 改写成 `data-src`（编辑器懒加载，属正常，不是丢图）。
- 数量对但图不显示 → 多半是 4.3 的转义问题，见下文「问题 6」。

## 关键问题与解决方案

### ❌ 问题 1：40007 invalid media_id

**两种触发场景：**
- **封面 `thumb_media_id`：** 是必填字段；封面图片必须是**永久素材**（通过 `add_material` 接口上传），不能直接用外部 URL。
- **草稿 `media_id` 已失效：** 草稿在公众号后台被打开 / 编辑 / 删除后，`media_id` 立即失效（即使是几分钟前刚返回的）。`draft/update` 和 `draft/get` 都会返回 40007。

**解决：**
- 封面图：先上传永久素材：
  ```bash
  curl -F "media=@cover.jpg" "https://api.weixin.qq.com/cgi-bin/material/add_material?access_token=TOKEN&type=image"
  ```
- 草稿失效：调 `cgi-bin/draft/batchget` 拿当前 id 列表（可能为空，说明被删了），再决定是 `update` 已有还是重新 `add`：
  ```bash
  curl -s -X POST "https://api.weixin.qq.com/cgi-bin/draft/batchget?access_token=$TOKEN" \
    -H "Content-Type: application/json" -d '{"offset":0,"count":10,"no_content":1}'
  ```

### ❌ 问题 2：IP 不在白名单（40164）

**症状：** `cgi-bin/token` 返回 `{"errcode":40164,"errmsg":"invalid ip 111.x.x.x ipv6 ::ffff:111.x.x.x, not in whitelist"}`。

**解决：** 登录微信公众平台 → 设置与开发 → **基本配置** → 公众号开发信息 → IP白名单 → 添加服务器 IP

**注意：**
- 不要错加到「**安全中心** → 登录IP白名单」—— 那个只控制后台登录，对 `cgi-bin/token` API 无效。
- **以报错里那个 IP 为准**，不是 `ipify` 查出来的（两者可能不同：代理规则只对部分域名生效，例如 ClashX 里 `qq.com` 走 DIRECT，微信看到的 IP 就是本机当前公网 IP）。
- 出口 IP 会变（换网络、宽带重拨、代理换节点），**一次把新旧两个都加进白名单**。
- 新加的 IP 通常立即生效，但偶尔有 1–2 分钟传播延迟（实测最长约 8 分钟），失败时先等再重试，别误判成加错了地方。
- 多账号情况：IP、AppID、白名单位置都核对过仍然失败，多半是加到了错误的公众号账号，对照 `config.sh` 里的 AppID 前缀检查。

**连带坑（一起踩过）：**
- ⚠️ **access_token 与申请它的 IP 绑定**：IP 一变，立刻报 42001 / 40001。取 token 要用**带重试的循环**，拿到就马上用。
- ⚠️ **别反复刷新 token**：每次调 `cgi-bin/token` 都会让上一个 token 失效。取一次、用到底；过期了再取。
- ⚠️ **token 有效期 2 小时**，发多篇稿时复用同一个 token，别每篇取一次。

### ❌ 问题 3：HTML 内容格式错误

**解决：**
- HTML 中的换行符会导致 JSON 解析失败
- 必须将换行符替换为空格
- 使用 `tr '\n' ' '` 处理

### ❌ 问题 4：有序/无序列表在公众号编辑器里渲染异常

公众号编辑器的 HTML ingester 有两个非标准行为，会让 `<ol>` / `<ul>` 列表静默崩坏。`add_draft.sh` 已内置防御性 sanitizer 处理后者并对前者发出警告，但理解病根才能不再踩坑：

| 症状 | 根因 | 修复 |
|---|---|---|
| 项目左侧没有 `1. 2. 3.` 编号或 `•` 符号，行间还多出空行 | `<li>` 内含块级子元素 `<section>` —— marker 被嵌套块"抢走"，section 的 `margin-top/bottom` 还顶出空行 | `<li>` 直接放文字，section 样式合并到 `<li>` 上 |
| 编号有了但变成 `1. (空) / 2. real / 3. (空) / 4. real / …`，N 个真实项目渲染成 ~2N+1 行 | `<ol>` / `<ul>` 内 `<li>` 兄弟之间的空白文本节点（空格、换行、Tab）被当成额外空 `<li>` | `<ol>↔<li>`、`</li>↔<li>`、`</li>↔</ol>` 三处空白全部 strip，**只 strip `\n` 不够**，普通空格也会触发 bug |

正确做法是用 [wechat-markdown-html-render](../wechat-markdown-html-render/) 渲染（该 skill 已内置这两条规则）。如果 HTML 来自其他源，`add_draft.sh` 会自动 strip 列表内空白，并在检测到 `<li><section>` 时打印 WARN（不会阻断发布，因为有时是有意为之）。

**辨别 marker 是否真的缺失：** 从公众号编辑器复制出来的纯文本本来就**不带** `1. 2. 3.` —— 那些是 CSS `::marker` 伪元素生成的、不参与剪贴板。一定要看编辑器**视觉预览区**，不要拿复制出的文本下结论。

### ❌ 问题 5：标题在 body 里重复显示（顶部出现两次）

**症状：** 公众号草稿里，文章顶部标题下面，紧接着又出现一行大号彩色「标题」字样；视觉上同一行字渲染了两次。

**根因：** markdown 源文件以 `# 标题` 开头（来自 `/jackyshen-write-wechat-article` 的标准输出），`/wechat-markdown-html-render` 把这个 H1 渲染进 body 里的 `<h1>` 节点；但 `add_draft.sh` 又把同一字符串作为 `title` 字段传给 API。公众号后台把 `title` 字段渲染为文章标题、把 `content` 字段直接显示在标题下面 —— 所以同一标题出现两次。

**解决：** 永远只把标题作为 `title` 参数传入，body 必须从非 H1 内容开始。`add_draft.sh` 已内置自动剥离：检测到 body 开头是 `<h1>`（可能嵌在 `<section id="nice">` 包装里）就把它移除，并打印：

```
==> Title-h1 self-check: {"h1_stripped_from_body": "xxx", "matches_title_param": true}
✅ 标题去重: body 顶部的 <h1> 已自动剥离（与 title 参数一致），避免重复显示
```

**不要绕过这个剥离。** 如果你想在 body 顶部显示一个不同的大标题（例如「卷首语」或栏目名），用 `## 二级标题` 写 —— 它不会被剥离，也不会和 `title` 冲突。

**辨别脚本是否真的没剥离：** `add_draft.sh` 输出的 JSON payload 里搜 `<h1` —— 应该是 0 次。

### ❌ 问题 6：正文看起来「没插图」，但 `<img>` 标签其实在

**症状：** 草稿保存成功、`draft/get` 也能查到 `<img>`，但公众号后台/预览里图片不显示，正文像纯文字。

**根因：** 图片 URL 里残留了 JSON 转义的反斜杠 —— `http:\/\/mmbiz.qpic.cn\/...`。
`upload_content_image.sh` 的原始输出是 JSON，URL 中的 `/` 被写成 `\/`。用 `jq -r` 取会自动还原，
但用 `grep -o '"url":"[^"]*"' | sed` 手工抠出来的是**未还原**的字符串，直接拼进 `<img src="...">`
后浏览器/微信无法解析，静默失败（不报错、不占位）。

**排查：** `grep -o '<img src="[^"]*"' article_final.html` —— URL 里只要出现 `\` 就是这个问题。

**修复：**
```python
html = html.replace('\\/', '/')   # 或一开始就用 jq -r '.url' 取值
```
然后用 `draft/update`（media_id 未失效）或重新 `draft/add` 覆盖。

### ❌ 问题 7：配图数量不足 4 张 / 改稿时草稿更新失败

**症状 A：草稿里的图比本地少。** 本地 HTML 4 张图，`draft/get` 只剩 2 张（甚至是上一轮的旧图）。
**根因：** 混元给的是 `aiart-*.cos.ap-guangzhou.myqcloud.com` 临时外链，微信**静默过滤**外链图片。
**解决：** 每张图必须先 `upload_content_image.sh` 换成 `mmbiz.qpic.cn` 永久 URL 再插入（见 4.3）；插入后 `grep -v 'mmbiz.qpic.cn'` 自检必须为空。

**症状 B：`draft/update` 返回 47001（数据格式错误）。**
**根因：** 正文里带单引号（`font-family: 'PingFang SC'`）时，经 shell 引号拼 JSON 会被截断。
**解决：** 把 JSON 写进文件再传参，正文里的换行符替换成空格：
```bash
python3 - << 'PY'
import json, io
html = io.open("article_final.html", encoding="utf-8").read().replace("\n", " ")
payload = {"articles": [{"title": "标题", "author": "作者", "digest": "摘要",
                        "content": html, "content_source_url": "",
                        "thumb_media_id": THUMB_ID, "need_open_comment": 1,
                        "only_fans_can_comment": 0, "show_cover_pic": 0}]}
io.open("payload.json", "w", encoding="utf-8").write(json.dumps(payload, ensure_ascii=False))
PY
curl -s -X POST "https://api.weixin.qq.com/cgi-bin/draft/update?access_token=$TOKEN" \
  -H "Content-Type: application/json" -d @payload.json
```
> 同理，`draft/add` 也要用文件传参；`add_draft.sh` 用的是 `"$CONTENT"` 位置参数，正文太长时也可能被 shell 截断 —— 正文超过 1 万字符时改走 payload 文件。

**症状 C：`draft/add` 报 40007 invalid media_id，但封面明明传了。**
**根因：** 封面 `thumb_media_id` 无效。常见原因是按名字查素材时用了错方法：`material/batchget_material` **必须用 POST**（GET 返回 43002），查不到就重复上传，素材库里堆一堆同名图。
**解决：** 用 POST 查素材库复用已有封面：
```bash
curl -s -X POST "https://api.weixin.qq.com/cgi-bin/material/batchget_material?access_token=$TOKEN" \
  -H "Content-Type: application/json" -d '{"type":"image","count":20,"offset":0}'
```

**症状 D：改稿后草稿箱里出现两份。**
**根因：** `draft/update` 失败后改走 `draft/add`，旧草稿没删。
**解决：** 重建后立刻删旧的（删不掉说明它已失效，那也正常）：
```bash
curl -s -X POST "https://api.weixin.qq.com/cgi-bin/draft/delete?access_token=$TOKEN" \
  -H "Content-Type: application/json" -d '{"media_id":"旧MEDIA_ID"}'
```

### ❌ 问题 8：清理素材库时把草稿一起删没了

**症状：** 清理重复封面素材后，草稿箱里的文章少了；或删完素材再 `draft/get` 报 40007。

**根因：** 两件事叠加——
1. **用户在后台打开/编辑过草稿后，WeChat 会重新上传封面**，`thumb_media_id` 就变了（草稿 `media_id` 不变）。
   拿旧的 `thumb_media_id` 去判断「这个素材还有没有人用」，会误判成孤儿。
2. **删除被草稿引用的永久素材，会连带把草稿删掉** —— 草稿的封面就是那个素材。

**解决：** 清理前先取当前真实引用关系，只删真正的孤儿：
```bash
# 1) 取所有草稿当前的 thumb_media_id
curl -s -X POST "https://api.weixin.qq.com/cgi-bin/draft/batchget?access_token=$TOKEN" \
  -H "Content-Type: application/json" -d '{"offset":0,"count":30,"no_content":0}' \
| python3 -c "import json,sys;[print(i['content']['news_item'][0]['thumb_media_id']) for i in json.load(sys.stdin)['item']]"

# 2) 素材库按名字分组（注意 batchget_material 必须 POST）
curl -s -X POST "https://api.weixin.qq.com/cgi-bin/material/batchget_material?access_token=$TOKEN" \
  -H "Content-Type: application/json" -d '{"type":"image","count":20,"offset":0}'

# 3) 只删「不在上面那组 id 里」的同名素材；删完立刻 batchget 复核草稿数量
```
> 更稳的做法：**先删草稿、再删素材**；或先备份草稿内容（本地留一份 final.html），万一被连带删掉可以一键重建
> （正文图的 `mmbiz.qpic.cn` 链接不会因删素材而失效，只有封面需要重传）。

**症状 E：草稿「自己不见了」。** 后台编辑过、或者本地记录过久的草稿，可能已经不在 `batchget` 列表里。
**处理：** 以 `batchget` 的实时结果为准，不要依赖之前记下的 `media_id`；本地保留每篇的 final.html + 配图，可随时重建。

## 成稿自检（发稿前过一遍）

- **文字润色**：写完稿子用 `/humanizer-zh` SKILL 过一遍 —— 删空泛铺垫与重复、去掉原稿之外的编造细节、统一中文标点、保留事实、确定程度与第一人称语气（不要把「可能是」改成「是」）。
- **标题一致性**：`title` 参数、摘要、正文首句、评论区引导里的说法一致；标题只进 `title`。
- **数据一致性**：正文里出现的数字与来源稿一致（系列稿还要保证脱敏后的口径一致）。
- **配图**：正文图 ≥ 4 张、全部是 `mmbiz.qpic.cn` 永久链接、插入点与上下文自然衔接。

## 完整使用示例

```bash
# 1. 获取 token
TOKEN=$(./scripts/get_access_token.sh | tail -1 | jq -r '.access_token')

# 2. 上传封面图
THUMB_RESPONSE=$(./scripts/upload_image.sh "$TOKEN" /path/to/cover.jpg | tail -1)
THUMB_ID=$(echo "$THUMB_RESPONSE" | jq -r '.media_id')

# 3. 准备文章内容（HTML 格式，body 不能含 <h1>，见「问题 5」）
CONTENT='<p>这里是文章内容...</p>'

# 4. 保存草稿（标题仅作为 title 参数传入）
./scripts/add_draft.sh "$TOKEN" "文章标题" "$CONTENT" "$THUMB_ID" "作者" "摘要"
```

## API 参数说明

### 新增草稿必填字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| title | string | 是 | 标题，不超过32字 |
| content | string | 是 | HTML内容，不超过2万字符 |
| thumb_media_id | string | 是 | 封面图片永久素材ID |
| author | string | 否 | 作者，不超过16字 |
| digest | string | 否 | 摘要，不超过128字 |
| show_cover_pic | number | 否 | 是否显示封面，0/1 |
| need_open_comment | number | 否 | 是否打开评论，0/1 |
| only_fans_can_comment | number | 否 | 是否仅粉丝可评论，0/1 |

### 支持的 HTML 标签

- `<p>` - 段落
- `<br>` - 换行
- `<section>` - 区块
- `<img>` - 图片（正文 url 必须来自 `media/uploadimg`，外部 url 会被过滤）
- `<strong>`, `<b>` - 加粗
- `<span style="...">` - 带样式的文本
- `<a href="...">` - 链接
- `<h1>`-`<h6>` - 标题

## 错误码速查

| 错误码 | 说明 | 解决 |
|--------|------|------|
| 40001 | access_token 过期 | 重新获取 |
| 40005 | 文件类型非法（uploadimg） | 仅支持 JPG/PNG |
| 40007 | media_id 无效 | 检查封面图是否上传正确 |
| 40009 | 图片尺寸非法（uploadimg） | 压缩图片至 ≤1MB |
| 40164 | IP 不在白名单 | 添加 IP 到白名单 |
| 44002 | POST 数据为空 | 检查请求体 |
| 47001 | 数据格式错误 | 检查 JSON 格式 |

## 注意事项

1. **token 有效期 2 小时**，过期需重新获取
2. **封面图必须先上传**，不能直接引用外部 URL
3. **HTML 内容需转义**，避免 JSON 解析失败
4. **IP 白名单必须配置**，否则无法调用 API
5. **内容大小限制**：正文 < 2万字符，< 1MB
6. **OL/UL 列表会被公众号编辑器篡改**：`add_draft.sh` 已内置 sanitizer 自动 strip 列表内空白；若用其他渠道上传，必须确保 `<li>` 内**不嵌套块级元素**（特别是 `<section>`），并且 `<ol>/<ul>` 与 `<li>` 之间、`<li>` 兄弟之间**零空白**。详见「问题 4」。
7. **草稿 `media_id` 在用户后台操作后立即失效**：`draft/update` 链路只在「同一会话、刚 add 完、用户未介入」时可靠；间隔较长或不确定时，先 `batchget` 再决定 update 还是 add。
8. **标题分离**（核心约定）：标题**只**作为 `title` 参数传入，**不允许**以 `<h1>` 形式留在 body 里。`/jackyshen-write-wechat-article` 输出的 markdown 第一行 `# 标题` 既是标题来源，也必须被剔除。`add_draft.sh` 会自动剥离 body 开头的 `<h1>`（即使嵌在 `<section id="nice">` 包装里），但更稳妥的做法是**渲染前手动从 markdown 中删除 `# 标题` 行**。详见「问题 5」。
9. **正文图片 url 必须来自 `media/uploadimg`**，外部图片 url 会被微信过滤（混元临时链接也会被过滤，见 4.3 补注）
9.1 **每篇正文至少 4 张图**：正文图数量下限是 4 张（不含封面），插入点要覆盖头部 / 中前段 / 中后段 / 结尾收束前
10. **2 万字符是硬限制，且渲染后极易超出**：`wechat-markdown-html-render` 的输出**没有 `<style>` 块**，样式全部内联在元素的 `style` 属性里（实测占 71%）。一篇 ~2800 字的 md 渲染后 22.5k 字符，直接提交会被拒。无损压缩 recipe（视觉不变，实测 22538 → 19314）：
    ```python
    import re
    h = re.sub(r'\s+data-tool="[^"]*"', '', h)                                  # 去掉 mdnice 元信息属性
    h = re.sub(r'padding-top: 8px; padding-bottom: 8px;', 'padding: 8px 0;', h) # 合并等价写法
    h = re.sub(r'word-spacing: 0px; letter-spacing: 0px;', '', h)
    h = re.sub(r'word-break: break-all;', '', h)
    ```
    插入 4 张正文图会再增加约 900 字符（每张约 220 字符），压缩时要留余量。

    **实测（2026-09-24，6 篇批量发布）：上面的基础 recipe 只减 14%，对 3000+ 字的长文不够用。**
    markup（内联 style）占总体积 ~83%，真正的空间在那里。在基础 recipe 之上再叠加下面这组
    **视觉等价替换**（把冗余属性删掉：font-size/line-height/text-align 从 `<section id="nice">` 继承，
    padding:0px 无意义），实测减重 **45%**，且渲染结果肉眼无差异：

    ```python
    # <p> 74→44 字符（font-size/line-height 已由外层 section 继承）
    (r'<p style="font-size: 16px; padding: 8px 0; margin: 0; line-height: 26px; color: #666;">',
     '<p style="padding:8px 0;margin:0;color:#666;">'),
    (r'<p style="font-size: 16px; padding: 8px 0; margin: 0px; line-height: 26px; color: #999; padding: 3px 0;">',
     '<p style="padding:3px 0;margin:0;color:#999;">'),
    # <li> 75→18 字符
    (r'<li style="line-height: 26px; text-align: left; color: rgb\(1,1,1\); font-weight: 500">',
     '<li style="color:rgb(1,1,1)">'),
    # 标题装饰 span：prefix 是 display:none 的废节点，suffix 是空节点，content 的 border 与 h2 自身重复
    (r'<span class="prefix" style="display: none;"></span>', ''),
    (r'<span class="suffix"></span>', ''),
    (r'<span class="content" style="border-bottom: 1px solid hsl\(216, 100%, 68%\);">', '<span>'),
    # 标题 / 列表 / 引用块 的等价简写
    (r'<h2 style="margin-top: 30px; margin-bottom: 15px; padding: 0px; font-weight: normal; color: #333; font-size: 1\.4em; border-bottom: 1px solid hsl\(216, 100%, 68%\);">',
     '<h2 style="margin:30px 0 15px;font-weight:normal;color:#333;font-size:1.4em;border-bottom:1px solid hsl(216,100%,68%);">'),
    (r'<h3 style="margin-top: 25px; margin-bottom: 12px; padding: 0px; font-weight: normal; color: #333; font-size: 1\.2em;">',
     '<h3 style="margin:25px 0 12px;font-weight:normal;color:#333;font-size:1.2em;">'),
    (r'<ul style="margin-top: 8px; margin-bottom: 8px; color: black; list-style-type: disc; padding-left: 2em;">',
     '<ul style="margin:8px 0;padding-left:2em;list-style-type:disc;">'),
    (r'<ol style="margin-top: 8px; margin-bottom: 8px; color: black; list-style-type: decimal; padding-left: 2em;">',
     '<ol style="margin:8px 0;padding-left:2em;list-style-type:decimal;">'),
    (r'<blockquote style="display: block; font-size: 0\.9em; overflow: auto; overflow-scrolling: touch; border-left: 3px solid rgba\(0, 0, 0, 0\.4\); color: #6a737d; padding-top: 10px; padding-bottom: 10px; padding-left: 20px; padding-right: 10px; margin-bottom: 20px; margin-top: 20px; background: #f9f9f9; border-left-color: hsl\(216, 100%, 68%\);">',
     '<blockquote style="font-size:.9em;border-left:3px solid hsl(216,100%,68%);color:#6a737d;padding:10px 10px 10px 20px;margin:20px 0;background:#f9f9f9;">'),
    ```

    实测 6 篇：30198→16662、22214→12501、18266→10471、17794→10087、17323→10317、9348→5280。
    **注意顺序**：先跑基础 recipe，再跑上面的替换（基础 recipe 生成的 `padding: 8px 0;` 是上面 `<p>` 规则的匹配前提）。
11. **配图分辨率（huny-img 实测）**：比例字符串（`16:9`、`2.35:1`）会被 API 以 `InvalidParameter` 拒绝，**必须传像素**；`2.35:1` 的封面不要用 `1024:436`（短边 <512 被拒），用 `1203:512`。正文图 `1280:720` 可用，但生成的是 PNG 约 2.7MB，**超过 `uploadimg` 的 1MB 上限**，需先转 JPEG：`sips -s format jpeg -s formatOptions 72 in.png --out out.jpg`（约 100KB）。
12. **脚本输出混了状态行，取字段必须 `| tail -1 | jq`**：`get_access_token.sh`、`upload_image.sh`、`upload_content_image.sh` 都会先打印「✅ xxx 成功」这类中文状态行，再打印 JSON。直接 `| jq -r '.xxx'` 会报 `parse error: Invalid numeric literal`，必须用 `| tail -1 | jq -r '...'`（本文件里所有示例已按此写法修正）。
13. **IP 白名单生效有延迟**：出口 IP 变化后调用 `cgi-bin/token` 会返回 `40164`，在公众号后台添加 IP 后**约 8 分钟才生效**，刚加完立刻重试仍会失败，别误判为加错了地方。沙箱环境里 `env -u HTTP_PROXY...` 直连与换 ClashX 代理都改不了出口 IP（实测两者出口 IP 相同），只能走白名单。

## 文件结构

```
wechat-mp-draft/
├── SKILL.md                    # 本文件
├── scripts/
│   ├── get_access_token.sh     # 获取 token
│   ├── upload_image.sh         # 上传封面图片（永久素材）
│   ├── upload_content_image.sh # 上传正文图片（获取可在 <img> 中使用的 URL）
│   ├── insert_content_image.py # 将 <img> 标签按指定位置插入 HTML
│   └── add_draft.sh            # 保存草稿
└── references/
    └── api_reference.md        # API 详细文档
```
