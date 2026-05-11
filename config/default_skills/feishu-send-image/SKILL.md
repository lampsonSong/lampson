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
