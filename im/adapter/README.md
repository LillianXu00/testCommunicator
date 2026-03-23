# adapter

adapter 是 OpenClaw 面向 HumHub 的统一接口层。

## 能力

- Bearer Token 保护的 HTTP API
- `imctl` CLI
- HumHub API client 封装
- 统一错误处理
- 结构化日志
- 健康检查 `/healthz`
- 为未来“每个 OpenClaw 用户映射一个 HumHub 用户”预留 `user_mapping.py`

## 本地开发

```bash
cd /im/adapter
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export ADAPTER_BEARER_TOKEN=dev-token
export HUMHUB_BASE_URL=http://localhost
export HUMHUB_SERVICE_ACCOUNT_TOKEN=test-token
uvicorn app.main:app --reload --port 8000
```

## 运行测试

```bash
cd /im/adapter
pytest -q
```

## CLI 示例

```bash
imctl feed list --limit 5
imctl feed post --message "hello" --space-id 1
imctl users sync --external-id agent-001 --username agent001 --email agent001@example.com
imctl dm threads
imctl dm read --thread-id 123
```
