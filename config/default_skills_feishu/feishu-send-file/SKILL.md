---
created_at: '2026-05-11'
description: 通过飞书发送图片文件给用户或群聊。

前置条件：飞书凭证位于 ~/.lamix/config.yaml

步骤：
1. 从 ~/.lamix/config.yaml 读取飞书 app_id 和 app_secret
2. 调用飞书 API 获取 tenant_access_token：POST https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal
3. 上传图片：POST https://open.feishu.cn/open-apis/im/v1/images，Header 带 Authorization: Bearer {token}，body 用 form-data 传 image_type=message 和 image 文件，返回 image_key
4. 发送图片消息：POST https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={id_type}，Header 带 Authorization: Bearer {token}，body 中 msg_type=image，content 为 {"image_key": "{image_key}"}

注意：feishu_send 工具仅支持 text 和 card 类型，不支持 image，必须直接调用飞书 API。
---

通过飞书发送图片文件给用户或群聊。

前置条件：飞书凭证位于 ~/.lamix/config.yaml

步骤：
1. 从 ~/.lamix/config.yaml 读取飞书 app_id 和 app_secret
2. 调用飞书 API 获取 tenant_access_token：POST https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal
3. 上传图片：POST https://open.feishu.cn/open-apis/im/v1/images，Header 带 Authorization: Bearer {token}，body 用 form-data 传 image_type=message 和 image 文件，返回 image_key
4. 发送图片消息：POST https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={id_type}，Header 带 Authorization: Bearer {token}，body 中 msg_type=image，content 为 {"image_key": "{image_key}"}

注意：feishu_send 工具仅支持 text 和 card 类型，不支持 image，必须直接调用飞书 API。

---

---
created_at: '2026-05-11'
description: 通过飞书发送图片文件给用户或群聊。

前置条件：飞书凭证位于 ~/.lamix/config.yaml

步骤：
1. 从 ~/.lamix/config.yaml 读取飞书 app_id 和 app_secret
2. 调用飞书 API 获取 tenant_access_token：POST https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal
3. 上传图片：POST https://open.feishu.cn/open-apis/im/v1/images，Header 带 Authorization: Bearer {token}，body 用 form-data 传 image_type=message 和 image 文件，返回 image_key
4. 发送图片消息：POST https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={id_type}，Header 带 Authorization: Bearer {token}，body 中 msg_type=image，content 为 {"image_key": "{image_key}"}

注意：feishu_send 工具仅支持 text 和 card 类型，不支持 image，必须直接调用飞书 API。
---

通过飞书发送图片文件给用户或群聊。

前置条件：飞书凭证位于 ~/.lamix/config.yaml

步骤：
1. 从 ~/.lamix/config.yaml 读取飞书 app_id 和 app_secret
2. 调用飞书 API 获取 tenant_access_token：POST https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal
3. 上传图片：POST https://open.feishu.cn/open-apis/im/v1/images，Header 带 Authorization: Bearer {token}，body 用 form-data 传 image_type=message 和 image 文件，返回 image_key
4. 发送图片消息：POST https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={id_type}，Header 带 Authorization: Bearer {token}，body 中 msg_type=image，content 为 {"image_key": "{image_key}"}

注意：feishu_send 工具仅支持 text 和 card 类型，不支持 image，必须直接调用飞书 API。

---

---
created_at: '2026-05-04'
description: 通过飞书发送文件类消息（图片、音频）。使用场景：发送 TTS/语音克隆结果、发送图片文件。
invocation_count: 0
name: feishu-send-file
triggers:
- 发图片
- 发音频
- 发语音
- 发文件
- 发 audio
- 发送文件
---

# 飞书发送文件类消息

**注意**：feishu_send 工具仅支持 text 和 card 类型，不支持文件，必须直接调用飞书 API。

## 前置条件

- 飞书凭证位于 ~/.lamix/config.yaml
- lark-cli 已安装：`/opt/homebrew/bin/lark-cli`
- PATH 中需要包含 `/opt/homebrew/bin`

## 1. 发送图片

### 步骤
1. 从 config.yaml 读取飞书 app_id 和 app_secret
2. 获取 tenant_access_token：POST https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal
3. 上传图片获取 image_key：POST https://open.feishu.cn/open-apis/im/v1/images
   - Header: `Authorization: Bearer {token}`
   - body: form-data，image_type=message，image 文件
4. 发送图片消息：POST https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={id_type}
   - Header: `Authorization: Bearer {token}`
   - body: msg_type=image，content=`{"image_key": "{image_key}"}`

## 2. 发送音频（opus 格式）

飞书音频消息只支持 opus 格式，必须先转码。

### 步骤
1. 转码为 opus
```bash
/opt/homebrew/bin/ffmpeg -i input.wav -c:a libopus -b:a 32k output.opus -y
```

2. 上传文件获取 file_key
```bash
export PATH="/opt/homebrew/bin:$PATH"
cd /tmp  # 必须在文件所在目录
lark-cli api POST '/open-apis/im/v1/files' \
  --data '{"file_type":"opus","file_name":"display_name.opus"}' \
  --file 'output.opus'
```
返回：`{"code":0,"data":{"file_key":"file_v3_xxxxx"}}`

3. 发送音频消息
```bash
lark-cli api POST '/open-apis/im/v1/messages' \
  --params '{"receive_id_type":"chat_id"}' \
  --data '{"receive_id":"<chat_id>","msg_type":"audio","content":"{\"file_key\":\"file_v3_xxxxx\"}"}'
```

## 踩过的坑

| 坑 | 现象 | 原因 |
|---|---|---|
| lark-cli `--file` 用绝对路径 | "cannot open file" | lark-cli 只认当前目录的文件名，必须先 cd |
| `--params` 传 file_type | "Invalid request param" | file_type 和 file_name 必须放 `--data` 里 |
| 发 wav/mp3 | "Invalid request param" | 飞书音频只支持 opus 格式，必须先转码 |
| 发图片用 feishu_send | 不支持 | 只能用 API |

## 正确的参数组合

**音频上传**：
- `--data` 传 JSON body（含 file_type、file_name）
- `--file` 传当前目录下的文件名（不带路径）
- `--params` 只放 receive_id_type
- file_type 固定为 `"opus"`

**音频发送**：
- msg_type 为 `"audio"`
- content 为 `{"file_key": "..."}`
