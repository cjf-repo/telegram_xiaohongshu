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
