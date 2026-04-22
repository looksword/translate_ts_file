import argparse
import asyncio
import os
import xml.etree.ElementTree as ET
from xml.dom import minidom
from opencc import OpenCC
from deep_translator import GoogleTranslator
import time
import random
from functools import wraps

# 限流装饰器
def rate_limited(max_per_second):
    min_interval = 1.0 / max_per_second
    def decorator(func):
        last_time_called = [0.0]
        @wraps(func)
        async def wrapper(*args, **kwargs):
            elapsed = time.time() - last_time_called[0]
            left_to_wait = min_interval - elapsed
            if left_to_wait > 0:
                await asyncio.sleep(left_to_wait)
            ret = await func(*args, **kwargs)
            last_time_called[0] = time.time()
            return ret
        return wrapper
    return decorator

class RateLimitedTranslator:
    def __init__(self, source_lang, target_lang, max_requests_per_second=1, max_retries=3):
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.max_requests_per_second = max_requests_per_second
        self.max_retries = max_retries
        self.last_request_time = 0
        self.translator = GoogleTranslator(source=source_lang, target=target_lang)
    
    async def translate_with_retry(self, text, retry_count=0):
        # 跳过空文本
        if not text or not text.strip():
            return text
        
        # 限流：确保每秒不超过指定请求数
        now = time.time()
        time_since_last = now - self.last_request_time
        if time_since_last < (1.0 / self.max_requests_per_second):
            await asyncio.sleep((1.0 / self.max_requests_per_second) - time_since_last)
        
        try:
            self.last_request_time = time.time()
            # 添加随机延迟避免规律性
            await asyncio.sleep(random.uniform(0.1, 0.3))
            
            result = await asyncio.to_thread(self.translator.translate, text)
            
            # 确保返回字符串
            if result is None:
                return text
            return str(result)
            
        except Exception as e:
            if retry_count < self.max_retries:
                # 指数退避重试
                wait_time = (2 ** retry_count) + random.uniform(0, 1)
                print(f"翻译失败，{wait_time:.1f}秒后重试 ({retry_count + 1}/{self.max_retries}): {text[:30]}...")
                await asyncio.sleep(wait_time)
                return await self.translate_with_retry(text, retry_count + 1)
            else:
                print(f"翻译失败，已达到最大重试次数: {text[:30]}... 错误: {e}")
                return text  # 返回原文

async def translate_batch(texts, translator, batch_size=5):
    """分批翻译，每批之间添加延迟"""
    results = []
    total_batches = (len(texts) + batch_size - 1) // batch_size
    
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        batch_num = i // batch_size + 1
        
        print(f"翻译批次 {batch_num}/{total_batches} ({len(batch)} 个字符串)")
        
        # 串行处理批次内的每个字符串（更稳定）
        for idx, text in enumerate(batch):
            if text and text.strip():
                translated = await translator.translate_with_retry(text)
                # 确保翻译结果不是 None
                if translated is None:
                    translated = text
                results.append(translated)
            else:
                results.append(text)
            
            # 显示进度
            current_total = len(results)
            print(f"  进度: {current_total}/{len(texts)}")
        
        # 批次之间添加延迟，避免触发限流
        if i + batch_size < len(texts):
            await asyncio.sleep(2)  # 批次间休息2秒
    
    return results

def get_opencc_config(source_lang, target_lang):
    config_map = {
        ('zh-CN', 'zh-TW'): 's2tw',
        ('zh-CN', 'zh-HK'): 's2hk',
        ('zh-CN', 'zh-SG'): 's2sg',
        ('zh-TW', 'zh-CN'): 't2s',
        ('zh-HK', 'zh-CN'): 't2s',
        ('zh-SG', 'zh-CN'): 't2s',
        ('zh-TW', 'zh-HK'): 't2hk',
        ('zh-TW', 'zh-SG'): 't2sg',
    }
    return config_map.get((source_lang, target_lang))

def safe_truncate(text, max_len=50):
    """安全地截断文本，处理 None 值"""
    if text is None:
        return "None"
    text_str = str(text)
    if len(text_str) > max_len:
        return text_str[:max_len] + "..."
    return text_str

async def convert_ts_file_async(file, source_lang, target_lang):
    """异步版本的转换函数"""
    # 读取文件
    tree = ET.parse(file)
    root = tree.getroot()
    
    # 收集需要翻译的文本
    texts_to_translate = []
    message_refs = []
    
    for context in root.findall('context'):
        for message in context.findall('message'):
            source = message.find('source')
            translation = message.find('translation')
            type_attr = translation.get('type')
            
            if source is None or source.text is None:
                continue
                
            text = source.text.strip()
            if not text:
                continue
                
            # 只翻译未完成的
            if type_attr == 'unfinished':
                texts_to_translate.append(text)
                message_refs.append((context, message, source, translation, text))
    
    print(f"找到 {len(texts_to_translate)} 个需要翻译的字符串")
    
    if len(texts_to_translate) == 0:
        print("没有需要翻译的字符串")
        return
    
    # 确定使用哪种转换方法
    if source_lang.startswith('zh') and target_lang.startswith('zh'):
        # 中文简繁转换
        opencc_config = get_opencc_config(source_lang, target_lang)
        if opencc_config:
            cc = OpenCC(opencc_config)
            for i, (ctx, msg, src, trans, text) in enumerate(message_refs):
                try:
                    translated = cc.convert(text)
                    trans.text = translated
                    if 'type' in trans.attrib:
                        del trans.attrib['type']
                    print(f"{i+1}/{len(message_refs)}: {safe_truncate(text)} -> {safe_truncate(translated)}")
                except Exception as e:
                    print(f"转换失败: {text} - 错误: {e}")
                    trans.text = text  # 保留原文
                await asyncio.sleep(0.01)  # 轻微延迟
        else:
            raise ValueError(f"Unsupported Chinese conversion: {source_lang} to {target_lang}")
    else:
        # 使用 Google 翻译
        translator = RateLimitedTranslator(source_lang, target_lang, 
                                          max_requests_per_second=1,  # 每秒1个请求
                                          max_retries=3)
        
        # 分批翻译
        translated_texts = await translate_batch(texts_to_translate, translator, batch_size=5)
        
        # 确保数量匹配
        if len(translated_texts) != len(message_refs):
            print(f"警告: 翻译结果数量({len(translated_texts)})与原始数量({len(message_refs)})不匹配")
            # 补齐缺失的翻译
            while len(translated_texts) < len(message_refs):
                translated_texts.append(message_refs[len(translated_texts)][4])
        
        # 更新 XML
        success_count = 0
        for i, (ctx, msg, src, trans, original) in enumerate(message_refs):
            try:
                translated = translated_texts[i]
                # 确保翻译结果有效
                if translated is None:
                    translated = original
                elif not isinstance(translated, str):
                    translated = str(translated)
                
                trans.text = translated
                if 'type' in trans.attrib:
                    del trans.attrib['type']
                
                success_count += 1
                
                # 显示进度（每10个显示一次，避免刷屏）
                if (i + 1) % 10 == 0 or i + 1 == len(message_refs):
                    print(f"进度: {i+1}/{len(message_refs)} - 最后: {safe_truncate(original)} -> {safe_truncate(translated)}")
                    
            except Exception as e:
                print(f"更新失败 (索引 {i}): {original[:30]}... 错误: {e}")
                trans.text = original  # 保留原文
        
        print(f"成功翻译 {success_count}/{len(message_refs)} 个字符串")
    
    # 保存文件到 google 文件夹
    google_dir = "google"
    # 确保 google 文件夹存在
    if not os.path.exists(google_dir):
        os.makedirs(google_dir)
        print(f"创建 google 文件夹: {google_dir}")
    
    # 构建输出文件路径
    original_filename = os.path.basename(file)
    output_file = os.path.join(google_dir, original_filename)
    
    print(f"正在保存文件到: {output_file}")
    ugly_xml = ET.tostring(root, encoding='utf-8').decode('utf-8')
    pretty_xml = minidom.parseString(ugly_xml).toprettyxml(indent="", newl="", encoding='utf-8')
    
    # 清理多余的空行
    lines = pretty_xml.decode('utf-8').split('\n')
    cleaned_lines = [line for line in lines if line.strip()]
    cleaned_xml = '\n'.join(cleaned_lines).encode('utf-8')
    
    with open(output_file, "wb") as f:
        f.write(cleaned_xml)
    
    print(f"完成！已保存到 {output_file}")

def convert_ts_file(file, source_lang, target_lang):
    """同步包装器"""
    asyncio.run(convert_ts_file_async(file, source_lang, target_lang))

def main():
    parser = argparse.ArgumentParser(description='Convert Qt Linguist TS files between different languages.')
    parser.add_argument('file', help='TS file')
    parser.add_argument('source_lang', help='Source language code (e.g., zh-CN, zh-HK, ja, ko, th, vi, hi)', default='zh-CN')
    parser.add_argument('target_lang', help='Target language code (e.g., en, fr, de, es, it, ru, pt)')
    parser.add_argument('--src_dir', help='Source directory containing TS files', default='.')
    
    args = parser.parse_args()
    
    # 将脚本文件所在目录设置为当前目录
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if script_dir:
        os.chdir(script_dir)
    
    try:
        # 切换到包含TS文件的目录
        if args.src_dir and args.src_dir != '.':
            if os.path.exists(args.src_dir):
                os.chdir(args.src_dir)
            else:
                print(f"警告: 目录不存在 {args.src_dir}，使用当前目录")
        
        print(f"开始转换: {args.file}")
        print(f"从 {args.source_lang} 到 {args.target_lang}")
        print(f"工作目录: {os.getcwd()}")
        print("=" * 50)
        
        # 检查文件是否存在
        if not os.path.exists(args.file):
            print(f"错误: 文件不存在 {args.file}")
            return
        
        convert_ts_file(args.file, args.source_lang, args.target_lang)
        
    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()