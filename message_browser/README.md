# Message Browser

独立于 `telegram_media_downloader` 的图文浏览项目。  
它只读取数据库（MySQL/SQLite），用于按“文本-图片关联分组”展示，并提供基础筛选和搜索。

## 功能

- 读取索引表：`messages` / `media_files` / `message_links`（支持 `table_prefix`）
- 分组展示图文（优先用 `message_links.text_message_id` 作为锚点）
- 筛选：`chat_id`、日期范围、媒体/文本类型、是否包含分隔图
- 搜索：文本、caption、文件名、保存路径
- 分页浏览
- 本地文件预览（图片/视频/其他文件下载）
- 选中一个或多个分组，融合后提交“小红书上架”payload（`mock/webhook`）
- 选中分组后基于“文本+图片”生成 AI 爆款文案（支持页面改 Prompt）

## 目录

```text
message_browser/
  app/
    main.py
    config.py
    db.py
    static/
      index.html
      app.js
      style.css
  .env.example
  requirements.txt
```

## 启动

1. 安装依赖

```bash
cd message_browser
python -m pip install -r requirements.txt
```

2. 配置环境变量

```bash
cp .env.example .env
```

按你的 MySQL 实际信息修改 `.env`：

- `DB_ADAPTER=mysql`
- `DB_TABLE_PREFIX=tdl_`
- `MYSQL_HOST`
- `MYSQL_PORT`
- `MYSQL_USER`
- `MYSQL_PASSWORD`
- `MYSQL_DATABASE`

3. 运行

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8090 --reload
```

浏览器打开：`http://127.0.0.1:8090`

## 小红书上架融合

页面上可以勾选多组数据，填写商品链接（可选），然后：

- `预览融合`：只看融合结果
- `提交上架`：按配置模式发送

### 发布模式

`.env`:

```env
XHS_PUBLISH_MODE=mock
XHS_WEBHOOK_URL=
XHS_WEBHOOK_TOKEN=
XHS_TIMEOUT=20
XHS_OUTPUT_DIR=./output/xhs_publish
XHS_CREATOR_URL=https://creator.xiaohongshu.com/publish/publish
XHS_USER_DATA_DIR=./browser_data
XHS_AUTO_CLICK_PUBLISH=false
XHS_PUBLISH_BUTTON_TEXT=发布
XHS_WAIT_TIMEOUT_MS=90000
XHS_PROXY_SERVER=
XHS_PROXY_USERNAME=
XHS_PROXY_PASSWORD=
AI_ENABLED=true
AI_API_KEY=
AI_BASE_URL=https://api-inference.modelscope.cn/v1
AI_TEXT_MODEL=Qwen/Qwen3-1.7B
AI_VISION_MODEL=moonshotai/Kimi-K2.5
AI_DEFAULT_USE_VISION=true
AI_MAX_IMAGES=4
AI_TEMPERATURE=0.7
AI_TIMEOUT=120
AI_SYSTEM_PROMPT=你是资深小红书电商文案策划，擅长将图文信息提炼为转化导向文案。
AI_DEFAULT_PROMPT=请根据提供的商品文本和图片信息，生成适合上架前使用的爆款风格文案。输出严格JSON，字段包含title, content, highlights, hashtags。title控制在18-22字，content控制在120-220字，中文输出，不要编造未提供的商品参数。
```

- `mock`：不调用外部服务，只把融合后的 JSON 写到 `XHS_OUTPUT_DIR`
- `webhook`：POST 到 `XHS_WEBHOOK_URL`，可带 `Bearer` token
- `playwright`：浏览器自动化真实发布（需要本地登录态）

说明：当前项目只负责“融合+投递 payload”，真正调用小红书发布接口建议在你的独立发布服务里实现（这样更稳，也方便处理登录态/风控/重试）。

### Playwright 真实发布步骤

1. 安装依赖和浏览器

```bash
cd message_browser
python -m pip install -r requirements.txt
python -m playwright install chromium
```

2. 配置 `.env`

```env
XHS_PUBLISH_MODE=playwright
XHS_AUTO_CLICK_PUBLISH=false
```

- 第一次建议 `XHS_AUTO_CLICK_PUBLISH=false`，先验证自动填充是否正常。
- 确认无误后改成 `true` 执行真实点击发布。

3. 登录小红书创作中心（保存登录态）

```bash
python scripts/xhs_login.py
```

4. 回到页面点“检查XHS状态”，显示“就绪”后即可发布。

### 常见问题：创作中心超时

如果出现 `ERR_TIMED_OUT`：

1. 先在 WSL 测试连通性：

```bash
curl -I https://creator.xiaohongshu.com/publish/publish
```

2. 如你本机有代理（Clash/V2Ray），在 `.env` 设置：

```env
XHS_PROXY_SERVER=http://172.25.80.1:7890
```

然后重试 `python scripts/xhs_login.py`。

## AI 文案生成

页面新增了：

- `Prompt(可编辑)` 输入框
- `生成爆款文案` 按钮
- `应用到上架输入` 按钮（将 AI 标题/文案回填到发布表单）

建议流程：

1. 勾选分组
2. 点击 `生成爆款文案`
3. 人工微调文案
4. 点击 `提交上架`

注意：`AI_API_KEY` 仅放在 `.env`，不要写死在代码里。

## 表要求

默认读取下面三张表（带前缀）：

- `tdl_messages`
- `tdl_media_files`
- `tdl_message_links`

如果你前缀不是 `tdl_`，改 `.env` 的 `DB_TABLE_PREFIX` 即可。

## 媒体预览路径限制（可选）

`.env` 可配置：

```env
MEDIA_ROOTS=/data/media,/mnt/e/github/telegram_media_downloader
```

- 留空：不限制路径（默认）
- 设置后：只允许访问这些目录下的文件
