# Ollama Translation API

这是一个使用本地 Ollama 服务进行文件翻译的 API 服务。

## 功能特点

- 支持多种文件格式：HTML、Word、Markdown 和 PDF
- 使用本地 Ollama 服务进行翻译
- RESTful API 接口
- 文件上传和下载功能
- 健康检查接口

## 安装要求

1. Python 3.8+
2. 本地安装的 Ollama 服务

## 安装步骤

1. 克隆仓库：
```bash
git clone [repository-url]
cd ollama-translate
```

2. 创建并激活虚拟环境：
```bash
python -m venv venv
# Windows
venv\Scripts\activate
# Linux/Mac
source venv/bin/activate
```

3. 安装依赖：
```bash
pip install -r requirements.txt
```

4. 启动服务：
```bash
python main.py
```

服务将在 http://localhost:8000 上运行。

## API 接口

### 文件翻译

**POST /translate-file**

请求参数：
- `file`: 要翻译的文件（multipart/form-data）
- `source_lang`: 源语言代码
- `target_lang`: 目标语言代码

响应示例：
```json
{
    "success": true,
    "download_link": "/download/translated_file.html"
}
```

### 健康检查

**GET /health**

响应示例：
```json
{
    "status": "healthy",
    "ollama_version": "1.0.0"
}
```

## 注意事项

1. 确保本地 Ollama 服务在端口 11434 上运行
2. 上传文件大小限制为 100MB
3. 支持的文件格式：.html, .docx, .md, .pdf

## 错误处理

服务会返回适当的 HTTP 状态码和错误消息：
- 400: 请求参数错误或不支持的文件格式
- 500: 服务器内部错误
- 503: Ollama 服务不可用
