---
created_at: '2026-05-04'
description: 通过飞书发送音频文件给用户或群聊。使用场景：发送语音克隆结果、发送 TTS 产出、发送任何音频文件。
invocation_count: 9
name: feishu-send-audio
triggers:
- 发音频
- 发语音
- 发 audio
- 发文件
- 声音克隆结果
---
# 飞书发送音频文件

## 完整流程（3 步）

### 1. 转码为 opus（飞书音频消息只支持 opus）

```bash
/opt/homebrew/bin/ffmpeg -i input.wav -c:a libopus -b:a 32k output.opus -y
```

### 2. 上传文件获取 file_key

```bash
export PATH="/opt/homebrew/bin:$PATH"
cd /tmp  # 必须在文件所在目录，lark-cli --file 只认当前目录的文件名
lark-cli api POST '/open-apis/im/v1/files' \
  --data '{"file_type":"opus","file_name":"display_name.opus"}' \
  --file 'output.opus'
```

返回：
```json
{"code":0,"data":{"file_key":"file_v3_xxxxx"}}
```

### 3. 发送音频消息

```bash
lark-cli api POST '/open-apis/im/v1/messages' \
  --params '{"receive_id_type":"chat_id"}' \
  --data '{"receive_id":"oc_xxx","msg_type":"audio","content":"{\"file_key\":\"file_v3_xxxxx\"}"}'
```

## 踩过的坑

| 坑 | 现象 | 原因 |
|---|------|------|
| lark-cli `--file` 用绝对路径 | "cannot open file" | lark-cli 只认当前目录的文件名，必须先 cd 到文件目录 |
| `--params` 传 file_type | "Invalid request param" | file_type 和 file_name 必须放 `--data` 里，不是 query param |
| 发 wav/mp3 | "Invalid request param" | 飞书音频消息只支持 opus 格式，必须先转码 |
| feishu_send 工具 | 只支持 text 和 card | 不支持发文件，必须用 lark-cli API |
| dry-run 成功但实际失败 | --file 找不到文件 | dry-run 不实际读文件，只有真正执行时才暴露路径问题 |

## 正确的参数组合（唯一可行）

- `--data` 传 JSON body（含 file_type、file_name）
- `--file` 传当前目录下的文件名（不带路径）
- `--params` 只放 receive_id_type
- file_type 固定为 `"opus"`
