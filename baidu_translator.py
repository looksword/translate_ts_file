#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import json
import hashlib
import random
import shutil
import requests
import xml.etree.ElementTree as ET
from xml.dom import minidom
from datetime import datetime

# ========== 全局变量（先定义）==========
APP_ID = None
SECRET_KEY = None
DEFAULT_TARGET_LANG = 'en'
REQUEST_DELAY = 0.2
MAX_RETRIES = 3
OUTPUT_DIR = 'translated_results'

# ========== 配置加载 ==========
def load_config(config_file='config.json'):
    """加载配置文件"""
    global APP_ID, SECRET_KEY, DEFAULT_TARGET_LANG, REQUEST_DELAY, MAX_RETRIES, OUTPUT_DIR
    
    # 获取脚本所在目录
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, config_file)
    
    if not os.path.exists(config_path):
        print(f"❌ 配置文件不存在: {config_path}")
        print("请创建 config.json 文件，格式如下：")
        print("""
{
    "baidu_api": {
        "app_id": "你的APP_ID",
        "secret_key": "你的SECRET_KEY"
    },
    "translation_settings": {
        "default_target_lang": "en",
        "request_delay": 0.2,
        "max_retries": 3,
        "output_dir": "translated_results"
    }
}
        """)
        sys.exit(1)
    
    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    # 读取配置
    APP_ID = config['baidu_api']['app_id']
    SECRET_KEY = config['baidu_api']['secret_key']
    
    settings = config.get('translation_settings', {})
    DEFAULT_TARGET_LANG = settings.get('default_target_lang', 'en')
    REQUEST_DELAY = settings.get('request_delay', 0.2)
    MAX_RETRIES = settings.get('max_retries', 3)
    OUTPUT_DIR = settings.get('output_dir', 'translated_results')
    
    print(f"✅ 配置文件加载成功")
    print(f"   APP_ID: {APP_ID[:10]}...")
    print(f"   输出目录: {OUTPUT_DIR}")
    print(f"   目标语言: {DEFAULT_TARGET_LANG}")
    print(f"   请求延迟: {REQUEST_DELAY}秒")

# ========== 工具函数 ==========
def ensure_output_dir():
    """确保输出目录存在"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_path = os.path.join(script_dir, OUTPUT_DIR)
    
    if not os.path.exists(output_path):
        os.makedirs(output_path)
        print(f"📁 创建输出目录: {output_path}")
    
    return output_path

def get_output_file_path(input_file, target_lang):
    """
    生成输出文件路径
    格式: translated_results/原文件名_目标语言_时间戳.ts
    """
    output_dir = ensure_output_dir()
    
    # 获取原文件名（不含扩展名）
    base_name = os.path.splitext(os.path.basename(input_file))[0]
    
    # 生成时间戳
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # 输出文件名
    output_name = f"{base_name}.ts"
    output_path = os.path.join(output_dir, output_name)
    
    return output_path

def save_backup(original_file):
    """保存原始文件的备份"""
    output_dir = ensure_output_dir()
    backup_dir = os.path.join(output_dir, "backups")
    
    if not os.path.exists(backup_dir):
        os.makedirs(backup_dir)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = os.path.basename(original_file)
    backup_name = f"{base_name}_{timestamp}.bak"
    backup_path = os.path.join(backup_dir, backup_name)
    
    shutil.copy2(original_file, backup_path)
    print(f"💾 已备份原文件: {backup_path}")
    
    return backup_path

# ========== 翻译函数 ==========
def baidu_translate(text, from_lang='auto', to_lang='en'):
    """调用百度翻译 API"""
    url = 'https://fanyi-api.baidu.com/api/trans/vip/translate'
    salt = str(random.randint(32768, 65536))
    sign_str = APP_ID + text + salt + SECRET_KEY
    sign = hashlib.md5(sign_str.encode('utf-8')).hexdigest()
    
    params = {
        'q': text,
        'from': from_lang,
        'to': to_lang,
        'appid': APP_ID,
        'salt': salt,
        'sign': sign
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        result = response.json()
        
        if 'trans_result' in result:
            return result['trans_result'][0]['dst']
        else:
            error_msg = f"API错误码: {result.get('error_code', '未知')}"
            if result.get('error_code') == '52003':
                error_msg += " (请检查 APP_ID 和密钥是否正确)"
            elif result.get('error_code') == '54003':
                error_msg += " (访问频率超限，请降低请求频率)"
            raise Exception(error_msg)
            
    except requests.exceptions.RequestException as e:
        raise Exception(f"网络请求失败: {e}")

def translate_with_retry(text, to_lang='en', max_retries=None):
    """带重试的翻译"""
    if max_retries is None:
        max_retries = MAX_RETRIES
        
    for attempt in range(max_retries):
        try:
            return baidu_translate(text, to_lang=to_lang)
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"  ❌ 翻译失败: {text[:30]}...")
                print(f"     错误: {e}")
                return text  # 返回原文
            wait_time = 2 ** attempt
            print(f"  ⚠️ 重试 {attempt+1}/{max_retries}，等待 {wait_time} 秒...")
            time.sleep(wait_time)
    return text

# ========== TS 文件处理 ==========
def translate_ts_file(input_file, target_lang=None):
    """
    翻译 TS 文件，输出到独立目录
    
    Args:
        input_file: 输入的 TS 文件路径
        target_lang: 目标语言（en/ko/ja等），默认使用配置中的设置
    """
    if target_lang is None:
        target_lang = DEFAULT_TARGET_LANG
    
    # 检查输入文件
    if not os.path.exists(input_file):
        print(f"❌ 文件不存在: {input_file}")
        return False
    
    print(f"\n{'='*60}")
    print(f"📖 读取文件: {input_file}")
    print(f"🎯 目标语言: {target_lang}")
    
    # 保存备份
    #backup_path = save_backup(input_file)
    
    # 生成输出文件路径
    output_file = get_output_file_path(input_file, target_lang)
    print(f"📝 输出文件: {output_file}")
    
    # 解析 TS 文件
    try:
        tree = ET.parse(input_file)
        root = tree.getroot()
    except Exception as e:
        print(f"❌ 解析 XML 失败: {e}")
        return False
    
    # 收集需要翻译的条目
    to_translate = []
    for context in root.findall('context'):
        for message in context.findall('message'):
            trans = message.find('translation')
            if trans is not None and trans.get('type') == 'unfinished':
                source = message.find('source')
                if source is not None and source.text:
                    original_text = source.text.strip()
                    if original_text:  # 非空文本
                        to_translate.append((context, message, source, trans, original_text))
    
    if not to_translate:
        print("✅ 没有需要翻译的字符串")
        # 直接复制原文件到输出目录
        shutil.copy2(input_file, output_file)
        print(f"📁 已复制原文件到: {output_file}")
        return True
    
    print(f"📝 找到 {len(to_translate)} 个待翻译字符串")
    print(f"{'='*60}\n")
    
    # 翻译
    success_count = 0
    for i, (ctx, msg, src, trans, text) in enumerate(to_translate, 1):
        # 显示进度
        print(f"[{i}/{len(to_translate)}] {text[:50]}...")
        
        # 翻译
        translated = translate_with_retry(text, to_lang=target_lang)
        
        if translated != text:
            success_count += 1
        
        # 更新翻译
        trans.text = translated
        if 'type' in trans.attrib:
            del trans.attrib['type']
        
        # 控制请求频率
        time.sleep(REQUEST_DELAY)
        
        # 显示翻译结果
        print(f"      → {translated[:50]}...")
    
    # 保存输出文件
    try:
        # 格式化 XML
        xml_str = ET.tostring(root, encoding='utf-8')
        pretty_xml = minidom.parseString(xml_str).toprettyxml(indent="  ")
        
        # 清理多余的空行
        lines = pretty_xml.split('\n')
        cleaned_lines = [line for line in lines if line.strip()]
        cleaned_xml = '\n'.join(cleaned_lines)
        
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(cleaned_xml)
        
        print(f"\n{'='*60}")
        print(f"✅ 翻译完成！")
        print(f"   📊 成功翻译: {success_count}/{len(to_translate)} 个字符串")
        print(f"   📁 输出文件: {output_file}")
        #print(f"   💾 备份文件: {backup_path}")
        
        return True
        
    except Exception as e:
        print(f"❌ 保存文件失败: {e}")
        return False

# ========== 批量处理 ==========
def batch_translate(input_files, target_lang=None):
    """批量翻译多个 TS 文件"""
    if target_lang is None:
        target_lang = DEFAULT_TARGET_LANG
    
    results = {}
    for input_file in input_files:
        success = translate_ts_file(input_file, target_lang)
        results[input_file] = success
    
    # 输出汇总
    print(f"\n{'='*60}")
    print("📊 批量翻译汇总")
    print(f"{'='*60}")
    for file, success in results.items():
        status = "✅ 成功" if success else "❌ 失败"
        print(f"  {status}: {file}")
    
    return results

# ========== 主函数 ==========
def main():
    import argparse
    global OUTPUT_DIR
    
    parser = argparse.ArgumentParser(description='百度翻译 TS 文件')
    parser.add_argument('files', nargs='+', help='TS 文件路径（支持多个）')
    parser.add_argument('-t', '--target', default=None, help='目标语言代码 (en/ko/ja等)')
    parser.add_argument('-o', '--output-dir', default=None, help='输出目录（覆盖配置文件）')
    
    args = parser.parse_args()
    
    # 加载配置
    load_config()
    
    # 检查 API 配置
    if APP_ID == '你的APP_ID' or SECRET_KEY == '你的SECRET_KEY':
        print("❌ 请在 config.json 中配置正确的 APP_ID 和 SECRET_KEY")
        sys.exit(1)
    
    print(f"\n🚀 百度翻译工具启动")
    print(f"📁 输出目录: {OUTPUT_DIR}")
    print(f"🎯 默认目标语言: {DEFAULT_TARGET_LANG}")
    
    # 覆盖输出目录
    if args.output_dir:
        OUTPUT_DIR = args.output_dir
        ensure_output_dir()
    
    # 执行翻译
    if len(args.files) == 1:
        translate_ts_file(args.files[0], args.target)
    else:
        batch_translate(args.files, args.target)

if __name__ == '__main__':
    main()