from fastapi import FastAPI, UploadFile, HTTPException, Request, Form
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import httpx
import os
import logging
from logging.handlers import RotatingFileHandler
from typing import Optional
from utils import process_file, save_translated_file, convert_pdf_to_markdown
import re
import asyncio
import json
from typing import List

# 配置日志
def setup_logger():
    # 创建logs目录
    os.makedirs('logs', exist_ok=True)
    
    # 创建logger
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.DEBUG)
    
    # 创建文件处理器，使用RotatingFileHandler自动轮转日志文件
    file_handler = RotatingFileHandler(
        'logs/app.log',
        maxBytes=1024*1024,  # 1MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.DEBUG)
    
    # 创建控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    
    # 创建格式器
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s\n'
        'File: %(filename)s:%(lineno)d\n'
        'Function: %(funcName)s\n'
        '%(message)s\n'
    )
    
    # 设置格式器
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    
    # 添加处理器
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

# 初始化日志
logger = setup_logger()

app = FastAPI(title="Ollama Translation API")

# 配置静态文件和模板
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

OLLAMA_BASE_URL = "http://localhost:11434"

# 获取可用的Ollama模型列表
async def get_available_models() -> List[str]:
    try:
        # 使用异步方式运行ollama list命令
        process = await asyncio.create_subprocess_exec(
            'ollama', 'list',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            logger.error(f"获取模型列表失败: {stderr.decode()}")
            return []
            
        # 解析输出
        output = stdout.decode().strip()
        if not output:
            logger.warning("ollama list 命令返回空输出")
            return []
            
        # 跳过标题行，处理每一行
        lines = output.split('\n')[1:]  # 跳过 NAME TAG SIZE MODIFIED 这一行
        models = []
        for line in lines:
            if line.strip():
                # 第一列是模型名称
                parts = line.split()
                if len(parts) >= 1:
                    model_name = parts[0].strip()
                    if model_name:
                        models.append(model_name)
                        logger.info(f"找到模型: {model_name}")
        
        logger.info(f"总共找到 {len(models)} 个模型")
        return models
    except Exception as e:
        logger.error(f"执行ollama list命令失败: {str(e)}")
        return []

@app.get("/api/models")
async def get_models():
    models = await get_available_models()
    return {"models": models}

# 创建必要的目录
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
TRANSLATED_DIR = os.path.join(os.path.dirname(__file__), "translated")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(TRANSLATED_DIR, exist_ok=True)

def split_text_into_chunks(text: str, chunk_size: int = 2000) -> list:
    """
    将文本分割成较小的块
    """
    # 按段落分割
    paragraphs = text.split('\n\n')
    chunks = []
    current_chunk = ""
    
    for para in paragraphs:
        # 如果段落本身超过chunk_size，按句子分割
        if len(para) > chunk_size:
            sentences = re.split(r'(?<=[.!?])\s+', para)
            for sentence in sentences:
                if len(current_chunk) + len(sentence) + 2 <= chunk_size:
                    current_chunk += sentence + " "
                else:
                    if current_chunk:
                        chunks.append(current_chunk.strip())
                    current_chunk = sentence + " "
        # 如果当前段落加入后不超过chunk_size
        elif len(current_chunk) + len(para) + 2 <= chunk_size:
            current_chunk += para + "\n\n"
        else:
            chunks.append(current_chunk.strip())
            current_chunk = para + "\n\n"
    
    if current_chunk:
        chunks.append(current_chunk.strip())
    
    return chunks

async def translate_chunk(client: httpx.AsyncClient, text: str, source_lang: str, target_lang: str, model: str) -> str:
    """
    翻译单个文本块
    """
    prompt = f"""
    请将以下文本从{source_lang}翻译成{target_lang}。
    保持原文的格式和段落结构，只翻译文本内容。
    原文：
    {text}
    """
    
    response = await client.post(
        f"{OLLAMA_BASE_URL}/api/generate",
        json={
            "model": model,
            "prompt": prompt,
            "stream": False
        },
        timeout=120.0
    )
    
    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="翻译服务出错，请稍后重试")
    
    response_json = response.json()
    if "response" not in response_json:
        raise HTTPException(status_code=500, detail="翻译服务返回格式错误")
    
    return response_json["response"].strip()

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    models = await get_available_models()
    return templates.TemplateResponse("index.html", {"request": request, "models": models})

@app.post("/translate-file")
async def translate_file(
    request: Request,
    file: UploadFile,
    output_format: str = Form(...),
    need_translate: str = Form(...),  # 改为字符串类型
    source_lang: str = Form(""),
    target_lang: str = Form(""),
    model: str = Form("")
):
    try:
        # 验证并转换need_translate为布尔值
        need_translate = need_translate.lower() == 'true'
        
        # 验证output_format
        if output_format not in ['same', 'markdown']:
            raise HTTPException(status_code=422, detail="无效的输出格式。必须是 'same' 或 'markdown'")
            
        # 创建上传目录
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        os.makedirs(TRANSLATED_DIR, exist_ok=True)
        
        # 获取文件扩展名
        file_extension = file.filename.split('.')[-1].lower()
        logger.info(f"原始文件扩展名: {file_extension}")
        
        # 验证文件类型
        supported_extensions = ['pdf', 'html', 'docx', 'md', 'epub']
        if file_extension not in supported_extensions:
            raise HTTPException(
                status_code=422,
                detail=f"不支持的文件类型。支持的类型有: {', '.join(supported_extensions)}"
            )
        
        # 保存上传的文件
        file_path = os.path.join(UPLOAD_DIR, file.filename)
        logger.info(f"上传文件保存路径: {file_path}")
        
        with open(file_path, "wb") as buffer:
            content = await file.read()
            buffer.write(content)
        
        # 如果是PDF转Markdown，直接使用convert_pdf_to_markdown
        if file_extension == 'pdf' and output_format == 'markdown':
            try:
                logger.info(f"开始将PDF转换为Markdown: {file.filename}")
                text = convert_pdf_to_markdown(file_path)
                logger.info(f"PDF转换为Markdown成功: {file.filename}")
            except Exception as e:
                logger.error(f"PDF转Markdown失败: {str(e)}")
                raise HTTPException(status_code=400, detail=f"PDF转Markdown失败: {str(e)}")
        else:
            # 处理其他文件内容
            try:
                logger.info(f"开始处理文件: {file.filename}")
                text = process_file(file_path, file_extension)
                logger.info(f"文件处理成功: {file.filename}")
            except Exception as e:
                logger.error(f"文件处理失败: {str(e)}")
                raise HTTPException(status_code=400, detail=f"文件处理失败: {str(e)}")
        
        # 如果需要翻译
        if need_translate:
            if not all([source_lang, target_lang, model]):
                raise HTTPException(status_code=422, detail="翻译需要提供源语言、目标语言和模型")
                
            # 准备翻译请求
            chunks = split_text_into_chunks(text)
            translated_chunks = []
            
            # 翻译每个文本块
            async with httpx.AsyncClient() as client:
                for chunk in chunks:
                    prompt = f"Please translate the following text from {source_lang} to {target_lang}. Maintain any special formatting or technical terms:\n\n{chunk}"
                    
                    try:
                        response = await client.post(
                            f"{OLLAMA_BASE_URL}/api/generate",
                            json={
                                "model": model,
                                "prompt": prompt,
                                "stream": False
                            },
                            timeout=300.0
                        )
                        
                        if response.status_code == 200:
                            result = response.json()
                            translated_text = result.get('response', '').strip()
                            translated_chunks.append(translated_text)
                        else:
                            raise HTTPException(status_code=500, detail=f"翻译服务错误: {response.text}")
                            
                    except Exception as e:
                        raise HTTPException(status_code=500, detail=f"翻译请求失败: {str(e)}")
            
            # 使用翻译后的文本
            final_text = "\n\n".join(translated_chunks)
        else:
            # 不需要翻译，直接使用原文
            final_text = text
        
        # 确定输出文件扩展名
        output_extension = 'md' if output_format == 'markdown' else file_extension
        logger.info(f"输出文件扩展名: {output_extension}")
        
        # 保存处理后的文件
        output_filename = f"processed_{os.path.splitext(file.filename)[0]}.{output_extension}"
        output_path = os.path.join(TRANSLATED_DIR, output_filename)
        logger.info(f"输出文件路径: {output_path}")
        
        try:
            final_path = save_translated_file(final_text, output_path, output_extension, file_path)
            logger.info(f"保存文件返回路径: {final_path}")
            
            if not final_path:
                raise ValueError("保存文件失败：未返回有效的文件路径")
                
            if not os.path.exists(final_path):
                raise ValueError(f"保存的文件不存在: {final_path}")
                
        except Exception as e:
            logger.error(f"保存文件失败: {str(e)}")
            raise HTTPException(status_code=500, detail=f"保存文件失败: {str(e)}")
        
        result = {
            "message": "处理完成",
            "filename": os.path.basename(final_path)
        }
        logger.info(f"处理完成，返回结果: {result}")
        return JSONResponse(result)
        
    except Exception as e:
        logger.error(f"处理过程发生错误: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/download/{filename}")
async def download_file(filename: str):
    file_path = os.path.join(TRANSLATED_DIR, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="文件未找到")
    return FileResponse(
        file_path,
        filename=filename,
        media_type='application/octet-stream'
    )

@app.get("/health")
async def health_check():
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{OLLAMA_BASE_URL}/api/version")
            if response.status_code == 200:
                return {"status": "healthy", "ollama_version": response.json().get("version")}
    except Exception:
        raise HTTPException(status_code=503, detail="Ollama service is not available")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
